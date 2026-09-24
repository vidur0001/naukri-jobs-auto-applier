import os
import json
import secrets
import subprocess
import time
import threading
from datetime import datetime, date
from flask import Flask, request, render_template_string, abort, jsonify, send_from_directory
from dotenv import load_dotenv
from mailer import send_email

load_dotenv()

app = Flask(__name__)

# System Configurations
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.getenv("CONFIG_PATH", os.path.join(BASE_DIR, "config.json"))
DECISIONS_DIR = os.getenv("DECISIONS_DIR", os.path.join(BASE_DIR, "decisions"))
os.makedirs(DECISIONS_DIR, exist_ok=True)
PID_FILE = os.path.join(BASE_DIR, "bot.pid")
AUTO_APPLY_PAUSE_FLAG = os.path.join(BASE_DIR, "auto_apply_paused.flag")
# Set by /stop, cleared by /start (or /approve). While present, the
# auto-restart scheduler leaves the bot off - it only kicks back in once you
# deliberately start it again, so "Stop Bot" from the email still works as
# an actual off switch.
BOT_AUTO_DISABLED_FLAG = os.path.join(BASE_DIR, "bot_auto_disabled.flag")
# Tracks when the bot subprocess was last launched and how long its last
# run took, so auto_restart_scheduler() can relaunch it automatically once a
# scan finishes (bot.py exits after one full pass) instead of sitting idle
# until someone clicks "Start Bot" by hand.
AUTO_RESTART_STATE_PATH = os.path.join(BASE_DIR, "auto_restart_state.json")
# Gap before relaunching after a normal, full-length run. Kept fairly long
# so the bot doesn't scan too frequently from the same cloud IP (reduces how
# often Akamai's risk scoring flags the traffic as suspicious).
RESTART_INTERVAL_MINUTES = int(os.getenv("RESTART_INTERVAL_MINUTES", "60"))
# Gap before relaunching after a run that ended almost immediately (no
# session file / expired session - bot.py already emails you when that
# happens; Akamai challenges no longer abort the run so they don't hit this
# path anymore). Set to 0 (retry on the scheduler's very next 60s tick) so
# that once you upload a refreshed session it's picked up immediately
# instead of sitting idle - the 10-minute alert-email throttle already
# prevents inbox spam if the session is still broken.
FAILURE_BACKOFF_MINUTES = int(os.getenv("FAILURE_BACKOFF_MINUTES", "0"))
QUICK_FAIL_SECONDS = 180
# Written by this Flask process when the "I've solved it, resume" button is
# clicked; bot.py polls for this file while it's paused on an Akamai
# human-verification challenge, waiting for a person to click through it via
# the noVNC viewer this app serves at /solve-challenge.
HUMAN_VERIFIED_FLAG = os.path.join(BASE_DIR, "human_verified.flag")
APPLICATIONS_LOG_PATH = os.getenv("APPLICATIONS_LOG_PATH", os.path.join(BASE_DIR, "applications_log.json"))
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:5000")
# Portable Naukri session used by bot.py (see login_setup.py / login_capture.py).
STORAGE_STATE_PATH = os.getenv("STORAGE_STATE_PATH", os.path.join(BASE_DIR, "naukri_storage_state.json"))
LOGIN_CAPTURE_PID_FILE = os.path.join(BASE_DIR, "login_capture.pid")
LOGIN_SAVE_FLAG = os.getenv("LOGIN_SAVE_FLAG", os.path.join(BASE_DIR, "login_save.flag"))
# Pre-built static React dashboard (see dashboard/, `npm run build`), served
# read-only at /dashboard so the existing "/" email-trigger route is untouched.
DASHBOARD_DIST_DIR = os.path.join(BASE_DIR, "dashboard", "dist")
# noVNC/websockify (started by the container's entrypoint alongside Xvfb)
# serves a live view of the SAME display the bot's browser renders to, so a
# human can watch and click the "I'm human" checkbox remotely without SSH
# access or running any scripts locally.
NOVNC_PORT = os.getenv("NOVNC_PORT", "6080")
# Same password x11vnc was started with (see Dockerfile CMD) - required so
# the noVNC viewer can auto-fill it and connect without prompting.
VNC_PASSWORD = os.getenv("VNC_PASSWORD", "changeme")
# Random per-deploy token gating access to /solve-challenge*. Without the
# correct ?token=... in the URL, the route 403s - this is what keeps the
# live-browser viewer private even if the port/security-group is opened to
# 0.0.0.0/0 for phone access, since only the email link carries the token.
CHALLENGE_ACCESS_TOKEN = os.getenv("CHALLENGE_ACCESS_TOKEN") or secrets.token_urlsafe(24)

def _require_challenge_token():
    # Checks both the query string (GET links from emails) and form body
    # (POST submissions, e.g. /clarify and /edit, which carry the token via
    # a hidden field since query strings aren't reliably preserved across
    # a same-URL form POST in every browser).
    token = request.args.get("token") or request.form.get("token")
    if token != CHALLENGE_ACCESS_TOKEN:
        abort(403)

DAILY_STATS_PATH = os.path.join(BASE_DIR, "daily_stats.json")
DEFAULT_PROFILE = {
    "target_roles": ["Software Engineer", "Associate Software Engineer", "Backend Developer", "SRE Engineer", "DevOps Engineer"],
    "filters": {
        "experience_years": ["0", "1", "2", "3"],
        "locations": ["bangalore", "pune", "hyderabad", "mumbai", "gurgaon", "noida", "delhi", "chandigarh", "chennai"]
    },
    "questionnaire_answers": {
        "current_ctc": "7,50,000 INR",
        "expected_ctc": "6,00,000 INR",
        "notice_period": "Immediate",
        "skills": "Python, Java, Rust, C, SQL, REST APIs, Docker, AWS (EC2/S3), Git, Log Aggregation, Telemetry, Lustre Filesystem, HPC, Linux Admin, Diagnostics, Databases, PostgreSQL, MongoDB"
    },
    "blacklist_companies": ["Generic Placement Agency", "Third Party Consultants"],
    "email_target": "vidursharma8035@gmail.com"
}

# Ensure persistent configurations state
if not os.path.exists(CONFIG_PATH):
    with open(CONFIG_PATH, "w") as f:
        json.dump(DEFAULT_PROFILE, f, indent=4)

def load_config():
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)

def save_config(data):
    with open(CONFIG_PATH, "w") as f:
        json.dump(data, f, indent=4)

# Webhook configuration for approval emails
EMAIL_HTML_TEMPLATE = """
<html>
<body style="font-family: Arial, sans-serif; color: #333; line-height: 1.6;">
    <h2 style="color: #4CAF50;">Naukri Auto-Apply Monitoring Check</h2>
    <p>Hello Vidur, your monitoring system is prepared to run automatic applications with the following criteria:</p>
    <table style="border-collapse: collapse; width: 100%; max-width: 600px;">
        <tr style="background-color: #f2f2f2;"><th style="padding: 8px; border: 1px solid #ddd; text-align: left;">Detail Name</th><th style="padding: 8px; border: 1px solid #ddd; text-align: left;">Value</th></tr>
        <tr><td style="padding: 8px; border: 1px solid #ddd;"><b>Target Roles</b></td><td style="padding: 8px; border: 1px solid #ddd;">{{ roles }}</td></tr>
        <tr><td style="padding: 8px; border: 1px solid #ddd;"><b>Locations</b></td><td style="padding: 8px; border: 1px solid #ddd;">{{ locations }}</td></tr>
        <tr><td style="padding: 8px; border: 1px solid #ddd;"><b>Current / Expected CTC</b></td><td style="padding: 8px; border: 1px solid #ddd;">{{ current_ctc }} / {{ expected_ctc }}</td></tr>
        <tr><td style="padding: 8px; border: 1px solid #ddd;"><b>Core Technical Stack</b></td><td style="padding: 8px; border: 1px solid #ddd;">{{ skills }}</td></tr>
    </table>
    <br/>
    <div style="margin-top: 15px;">
        <a href="{{ base_url }}/start?token={{ token }}" style="background-color: #4CAF50; color: white; padding: 10px 20px; text-decoration: none; border-radius: 4px; font-weight: bold; margin-right: 10px;">▶️ Start Bot</a>
        <a href="{{ base_url }}/stop?token={{ token }}" style="background-color: #f44336; color: white; padding: 10px 20px; text-decoration: none; border-radius: 4px; font-weight: bold; margin-right: 10px;">⏹️ Stop Bot</a>
        <a href="{{ base_url }}/edit?token={{ token }}" style="background-color: #2196F3; color: white; padding: 10px 20px; text-decoration: none; border-radius: 4px; font-weight: bold;">📝 Edit Configuration Details</a>
    </div>
    <div style="margin-top: 10px;">
        <a href="{{ base_url }}/auto-apply/pause?token={{ token }}" style="background-color: #FF9800; color: white; padding: 10px 20px; text-decoration: none; border-radius: 4px; font-weight: bold; margin-right: 10px;">⏸️ Pause Auto-Apply</a>
        <a href="{{ base_url }}/auto-apply/resume?token={{ token }}" style="background-color: #9C27B0; color: white; padding: 10px 20px; text-decoration: none; border-radius: 4px; font-weight: bold;">▶️ Resume Auto-Apply</a>
    </div>
</body>
</html>
"""

def is_bot_running():
    if not os.path.exists(PID_FILE):
        return False
    try:
        with open(PID_FILE, "r") as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)  # signal 0: just checks if the process exists
        # A PID existing is not enough - PIDs get reused (e.g. by Xvfb) after a
        # container restart or a crashed bot process. Verify the process is
        # actually our bot.py before trusting the PID file.
        cmdline_path = f"/proc/{pid}/cmdline"
        if os.path.exists(cmdline_path):
            with open(cmdline_path, "rb") as cf:
                cmdline = cf.read().decode(errors="ignore")
            if "bot.py" not in cmdline:
                os.remove(PID_FILE)
                return False
        return True
    except (OSError, ValueError):
        try:
            os.remove(PID_FILE)
        except OSError:
            pass
        return False

def _load_restart_state():
    try:
        with open(AUTO_RESTART_STATE_PATH, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError, FileNotFoundError):
        return {}

def _save_restart_state(state):
    try:
        with open(AUTO_RESTART_STATE_PATH, "w") as f:
            json.dump(state, f)
    except OSError:
        pass

def start_bot():
    if is_bot_running():
        return False
    # A deliberate start (button/email link) always re-enables auto-restart,
    # even if the bot had previously been stopped manually.
    if os.path.exists(BOT_AUTO_DISABLED_FLAG):
        os.remove(BOT_AUTO_DISABLED_FLAG)
    bot_path = os.path.join(BASE_DIR, "bot.py")
    proc = subprocess.Popen(["python", bot_path])
    with open(PID_FILE, "w") as f:
        f.write(str(proc.pid))
    start_time = time.time()
    state = _load_restart_state()
    state["last_start"] = start_time
    state["last_run_seconds"] = None
    _save_restart_state(state)

    def _wait_and_record():
        proc.wait()
        s = _load_restart_state()
        s["last_run_seconds"] = time.time() - start_time
        _save_restart_state(s)
    threading.Thread(target=_wait_and_record, daemon=True).start()
    return True

def stop_bot():
    # Stopping is always a deliberate action - disable auto-restart until the
    # bot is started again explicitly.
    with open(BOT_AUTO_DISABLED_FLAG, "w") as f:
        f.write("disabled")
    if not os.path.exists(PID_FILE):
        return False
    try:
        with open(PID_FILE, "r") as f:
            pid = int(f.read().strip())
        os.kill(pid, 15)  # SIGTERM
    except (OSError, ValueError):
        pass
    finally:
        os.remove(PID_FILE)
    return True

def is_login_capture_running():
    if not os.path.exists(LOGIN_CAPTURE_PID_FILE):
        return False
    try:
        with open(LOGIN_CAPTURE_PID_FILE, "r") as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        cmdline_path = f"/proc/{pid}/cmdline"
        if os.path.exists(cmdline_path):
            with open(cmdline_path, "rb") as cf:
                cmdline = cf.read().decode(errors="ignore")
            if "login_capture.py" not in cmdline:
                os.remove(LOGIN_CAPTURE_PID_FILE)
                return False
        return True
    except (OSError, ValueError):
        try:
            os.remove(LOGIN_CAPTURE_PID_FILE)
        except OSError:
            pass
        return False

def start_login_capture():
    if is_login_capture_running():
        return False
    script_path = os.path.join(BASE_DIR, "login_capture.py")
    proc = subprocess.Popen(["python", script_path])
    with open(LOGIN_CAPTURE_PID_FILE, "w") as f:
        f.write(str(proc.pid))
    return True

def send_approval_email(config):
    from jinja2 import Template
    html_body = Template(EMAIL_HTML_TEMPLATE).render(
        roles=", ".join(config["target_roles"]),
        locations=", ".join(config["filters"]["locations"]),
        current_ctc=config["questionnaire_answers"]["current_ctc"],
        expected_ctc=config["questionnaire_answers"]["expected_ctc"],
        skills=config["questionnaire_answers"]["skills"],
        base_url=APP_BASE_URL,
        token=CHALLENGE_ACCESS_TOKEN
    )
    send_email(config["email_target"], "🐳 Naukri Bot is running via Docker - control it here", html_body)

def get_external_site_jobs_for_date(for_date):
    """Returns applications_log.json entries with status external_site_skip
    whose timestamp falls on for_date (an ISO date string, local/IST time),
    so the daily digest can list jobs that need a manual apply."""
    try:
        with open(APPLICATIONS_LOG_PATH, "r") as f:
            entries = json.load(f)
    except (json.JSONDecodeError, OSError, FileNotFoundError):
        entries = []

    matches = []
    for entry in entries:
        if entry.get("status") != "external_site_skip":
            continue
        ts = entry.get("timestamp")
        if not ts:
            continue
        entry_date = datetime.fromtimestamp(ts).date().isoformat()
        if entry_date == for_date:
            matches.append(entry)
    return matches

def send_daily_summary_email(email_target, for_date=None):
    for_date = for_date or date.today().isoformat()
    try:
        with open(DAILY_STATS_PATH, "r") as f:
            stats = json.load(f)
    except (json.JSONDecodeError, OSError, FileNotFoundError):
        stats = {}

    day_stats = stats.get(for_date, {"found": 0, "applied": 0})
    found = day_stats.get("found", 0)
    applied = day_stats.get("applied", 0)
    skipped_or_pending = max(found - applied, 0)

    external_jobs = get_external_site_jobs_for_date(for_date)
    external_html = ""
    if external_jobs:
        rows = "".join(
            f"""<tr>
                <td style="padding:8px;border:1px solid #ddd;">{e.get('title','')}</td>
                <td style="padding:8px;border:1px solid #ddd;">{e.get('company','')}</td>
                <td style="padding:8px;border:1px solid #ddd;">{e.get('location','')}</td>
                <td style="padding:8px;border:1px solid #ddd;"><a href="{e.get('link','')}">Open</a></td>
            </tr>"""
            for e in external_jobs
        )
        external_html = f"""
        <h3 style="color:#FF9800;margin-top:20px;">🔗 Needs Manual Apply (Company Site) - {len(external_jobs)}</h3>
        <p style="color:#888;">These jobs redirect to the company's own external career site, which the bot skips automatically. Apply manually if interested.</p>
        <table style="border-collapse: collapse; width: 100%; max-width: 600px;">
            <tr>
                <th style="padding:8px;border:1px solid #ddd;text-align:left;">Title</th>
                <th style="padding:8px;border:1px solid #ddd;text-align:left;">Company</th>
                <th style="padding:8px;border:1px solid #ddd;text-align:left;">Location</th>
                <th style="padding:8px;border:1px solid #ddd;text-align:left;">Link</th>
            </tr>
            {rows}
        </table>
        """

    html_body = f"""
    <html><body style="font-family: Arial, sans-serif; color: #333;">
        <h2 style="color: #2196F3;">📊 Daily Summary - {for_date}</h2>
        <table style="border-collapse: collapse; width: 100%; max-width: 500px;">
            <tr><td style="padding:8px;border:1px solid #ddd;"><b>Decision emails received</b></td><td style="padding:8px;border:1px solid #ddd;">{found}</td></tr>
            <tr><td style="padding:8px;border:1px solid #ddd;"><b>Companies applied to</b></td><td style="padding:8px;border:1px solid #ddd;">{applied}</td></tr>
            <tr><td style="padding:8px;border:1px solid #ddd;"><b>Skipped / no response</b></td><td style="padding:8px;border:1px solid #ddd;">{skipped_or_pending}</td></tr>
        </table>
        <p style="margin-top:15px;color:#888;">Applied {applied} out of {found} jobs the bot found and emailed you about today.</p>
        {external_html}
    </body></html>
    """
    send_email(email_target, f"📊 Daily Summary ({for_date}): Applied {applied}/{found}", html_body)

def daily_summary_scheduler():
    # Sends a summary email once per day at 23:59 local time. The container
    # sets TZ=Asia/Kolkata, so datetime.now() already reflects IST - no
    # separate timezone conversion is needed.
    last_sent_date = None
    while True:
        now = datetime.now()
        if now.hour == 23 and now.minute == 59 and last_sent_date != now.date():
            try:
                config = load_config()
                send_daily_summary_email(config["email_target"])
                last_sent_date = now.date()
            except Exception as e:
                print(f"[-] Failed to send daily summary email: {e}")
        time.sleep(30)

def auto_restart_scheduler():
    """Keeps the bot effectively running 24/7 without any manual clicking.

    bot.py does one full pass over every role/location/experience
    combination and then exits - there's no built-in loop. This background
    thread relaunches it automatically once it finishes, so the only gaps
    are short, deliberate cooldowns:
      - RESTART_INTERVAL_MINUTES after a normal full run.
      - FAILURE_BACKOFF_MINUTES after a run that ended almost immediately
        (expired session / Akamai challenge - bot.py already emails you
        about those cases), so it doesn't hammer Naukri or your inbox while
        waiting for a refreshed session upload.
    Stops entirely while BOT_AUTO_DISABLED_FLAG exists, i.e. after you click
    "Stop Bot" - it only resumes once you click "Start Bot" again.
    """
    while True:
        time.sleep(60)
        try:
            if is_bot_running() or os.path.exists(BOT_AUTO_DISABLED_FLAG):
                continue
            state = _load_restart_state()
            last_start = state.get("last_start")
            if last_start is None:
                start_bot()
                continue
            last_duration = state.get("last_run_seconds")
            if last_duration is None:
                # Previous run hasn't been recorded as finished yet (or the
                # process/container restarted mid-run) - leave it alone.
                continue
            gap_minutes = (
                FAILURE_BACKOFF_MINUTES if last_duration < QUICK_FAIL_SECONDS
                else RESTART_INTERVAL_MINUTES
            )
            if time.time() - last_start >= gap_minutes * 60:
                print("[+] auto_restart_scheduler: relaunching bot for the next scan.")
                start_bot()
        except Exception as e:
            print(f"[-] auto_restart_scheduler error: {e}")

# --- Read-only JSON API for the future web dashboard ---------------------
# Additive only: does not change any existing HTML webhook route/behavior.
# CORS is wide-open (GET, read-only, no secrets in the payloads) since this
# is just for a personal dashboard fetching public-to-you status/stats.
@app.after_request
def _add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response

@app.route("/api/status")
def api_status():
    config = load_config()
    return jsonify({
        "bot_running": is_bot_running(),
        "auto_apply_paused": os.path.exists(AUTO_APPLY_PAUSE_FLAG),
        "target_roles": config.get("target_roles", []),
        "locations": config.get("filters", {}).get("locations", []),
    })

@app.route("/api/pause-state")
def api_pause_state():
    return jsonify({"auto_apply_paused": os.path.exists(AUTO_APPLY_PAUSE_FLAG)})

@app.route("/api/stats")
def api_stats():
    try:
        with open(DAILY_STATS_PATH, "r") as f:
            stats = json.load(f)
    except (json.JSONDecodeError, OSError, FileNotFoundError):
        stats = {}
    today = date.today().isoformat()
    return jsonify({
        "today": today,
        "today_stats": stats.get(today, {"found": 0, "applied": 0}),
        "history": stats,
    })

@app.route("/api/applications")
def api_applications():
    # Structured per-job log (title/company/location/status/timestamp),
    # written by bot.py's log_application_event(). Newest first, optionally
    # limited via ?limit=N (default: all).
    try:
        with open(APPLICATIONS_LOG_PATH, "r") as f:
            applications = json.load(f)
    except (json.JSONDecodeError, OSError, FileNotFoundError):
        applications = []

    applications = list(reversed(applications))
    limit = request.args.get("limit", type=int)
    if limit:
        applications = applications[:limit]

    return jsonify({"count": len(applications), "applications": applications})

@app.route("/api/session-status")
def api_session_status():
    # Safe to expose publicly: no session contents, just whether a saved
    # Naukri session file exists and when it was last (re)written.
    exists = os.path.exists(STORAGE_STATE_PATH)
    last_updated = None
    if exists:
        last_updated = datetime.fromtimestamp(os.path.getmtime(STORAGE_STATE_PATH)).isoformat()
    return jsonify({
        "session_exists": exists,
        "last_updated": last_updated,
        "login_capture_running": is_login_capture_running(),
    })

@app.route("/login/start")
def login_start():
    _require_challenge_token()
    if is_bot_running():
        # Login capture and the bot share the same Xvfb display, so the bot
        # must be off first. Rather than blocking with a message and forcing
        # a separate manual "Stop Bot" click (a confusing dead-end when
        # arriving here from the "Stop Bot" email), stop it automatically and
        # give it a moment to fully exit before opening the login browser.
        stop_bot()
        for _ in range(20):
            if not is_bot_running():
                break
            time.sleep(0.5)
    started = start_login_capture()
    if not started and not is_login_capture_running():
        return "<h2>Could not start the login browser. Check container logs.</h2>", 500
    # Routed through Caddy on the same scheme/host as the page itself
    # (see Caddyfile's /vnc* + /websockify* proxy blocks) instead of a raw
    # http://host:6080 URL - a plain-HTTP iframe/websocket loaded from an
    # HTTPS page is blocked by browsers as mixed content, which left this
    # view blank when accessed via https://naukri-bot.duckdns.org.
    # Caddy terminates TLS and forwards plain HTTP internally, so
    # request.scheme always reports "http" here - use X-Forwarded-Proto
    # (set by Caddy's reverse_proxy by default) to know the scheme the
    # browser actually connected with.
    scheme = request.headers.get("X-Forwarded-Proto", request.scheme)
    novnc_url = (
        f"{scheme}://{request.host}/vnc.html?autoconnect=true&resize=scale"
        f"&password={VNC_PASSWORD}"
    )
    save_url = f"{APP_BASE_URL}/login/save?token={CHALLENGE_ACCESS_TOKEN}"
    return render_template_string("""
        <html><body style="font-family: Arial, sans-serif; color: #333; margin:0;">
            <div style="padding:12px 16px; background:#e3f2fd; border-bottom:1px solid #90caf9;">
                <b>🔐 Log in to Naukri below.</b>
                Use the direct Email + Password fields (not "Login with Google").
                Once you see your Naukri homepage/profile, click Save Session.
                Your password is never sent to this server - it only leaves your
                browser to reach Naukri, same as visiting naukri.com directly.
                <a href="{{ save_url }}" style="background-color:#4CAF50; color:white; padding:8px 16px;
                   text-decoration:none; border-radius:4px; font-weight:bold; margin-left:12px;">
                   ✅ Save Session
                </a>
            </div>
            <iframe src="{{ novnc_url }}" style="width:100%; height:90vh; border:none;"></iframe>
        </body></html>
    """, novnc_url=novnc_url, save_url=save_url)

def _start_bot_after_login_capture():
    # login_capture.py verifies login actually succeeded before writing the
    # session file, then exits on its own - poll for it to finish, then start
    # the bot automatically so a fresh login goes straight back to scanning
    # without a separate manual "Start Bot" click. If it's still running
    # after the wait (e.g. login took longer, or a premature "Save Session"
    # click was rejected and it's waiting for another attempt), don't start
    # the bot yet - it'll auto-start once login_capture actually exits, since
    # /login/save is called again after each Save Session click.
    for _ in range(120):  # up to ~60s, generous since OTP/CAPTCHA can be slow
        if not is_login_capture_running():
            break
        time.sleep(0.5)
    else:
        print("[-] Login capture still in progress after 60s - not starting the bot yet.")
        return
    if start_bot():
        print("[+] Bot auto-started after fresh login session was saved.")
    else:
        print("[-] Could not auto-start bot after login save (already running or failed).")

@app.route("/login/save")
def login_save():
    _require_challenge_token()
    with open(LOGIN_SAVE_FLAG, "w") as f:
        f.write("save")
    print("[+] Login save requested via webhook. Waiting for login_capture.py to write the session file.")
    threading.Thread(target=_start_bot_after_login_capture, daemon=True).start()
    return "<h2>Saving session... this closes the browser in a few seconds, then the bot will start automatically. You can close this tab and check the dashboard for confirmation.</h2>"

@app.route("/session/upload", methods=["GET", "POST"])
def session_upload():
    _require_challenge_token()
    if request.method == "GET":
        return render_template_string("""
            <html><body style="font-family: Arial, sans-serif; color: #333; max-width:600px; margin:40px auto;">
                <h2>⬆️ Upload Refreshed Naukri Session</h2>
                <p>Run <code>python login_setup.py</code> on your own laptop, log in normally,
                   then select the <code>naukri_storage_state.json</code> it creates below.</p>
                <form method="POST" enctype="multipart/form-data">
                    <input type="hidden" name="token" value="{{ token }}">
                    <input type="file" name="session_file" accept="application/json" required>
                    <br/><br/>
                    <button type="submit" style="background-color:#4CAF50; color:white; padding:10px 20px;
                        border:none; border-radius:4px; font-weight:bold; cursor:pointer;">Upload</button>
                </form>
            </body></html>
        """, token=CHALLENGE_ACCESS_TOKEN)

    uploaded = request.files.get("session_file")
    if not uploaded or not uploaded.filename:
        return "<h2>No file selected. Go back and choose naukri_storage_state.json.</h2>", 400
    try:
        content = uploaded.read()
        data = json.loads(content)  # validate it's actually JSON before overwriting the live session
        if not isinstance(data, dict) or "cookies" not in data:
            return "<h2>That doesn't look like a valid Playwright storage_state.json file.</h2>", 400
        with open(STORAGE_STATE_PATH, "wb") as f:
            f.write(content)
    except (json.JSONDecodeError, OSError) as e:
        return f"<h2>Could not save session file: {e}</h2>", 400
    print("[+] Naukri session refreshed via /session/upload.")
    # Match the /login/save behavior - a freshly uploaded session should mean
    # the bot goes straight back to scanning, not sit idle waiting for a
    # separate manual "Start Bot" click.
    if start_bot():
        print("[+] Bot auto-started after fresh session upload.")
        return "<h2>✅ Session uploaded successfully. Bot is starting automatically.</h2>"
    return "<h2>✅ Session uploaded successfully. (Bot was already running - it will use the new session on its next restart, or click Stop then Start to apply it immediately.)</h2>"

@app.route("/")
def index():
    _require_challenge_token()
    config = load_config()
    send_approval_email(config)
    return "<h3>Email notification sent to vidursharma8035@gmail.com. Monitor active.</h3>"

@app.route("/dashboard")
@app.route("/dashboard/")
def dashboard():
    # Serves the pre-built React SPA (see dashboard/, `npm run build`). Kept
    # off "/" so it doesn't disturb the existing email-trigger route above.
    return send_from_directory(DASHBOARD_DIST_DIR, "index.html")

@app.route("/assets/<path:filename>")
def dashboard_assets(filename):
    # Vite emits asset references as absolute "/assets/..." paths, so they
    # must be served from the domain root regardless of the /dashboard route.
    return send_from_directory(os.path.join(DASHBOARD_DIST_DIR, "assets"), filename)

@app.route("/start")
@app.route("/approve")
def approve_and_run():
    _require_challenge_token()
    started = start_bot()
    if started:
        print("[+] Start webhook received. Bot subprocess launched.")
        return "<h2>Bot started! It will begin scanning and email you when it finds jobs.</h2>"
    return "<h2>Bot is already running.</h2>"

@app.route("/stop")
def stop_and_halt():
    _require_challenge_token()
    stopped = stop_bot()
    if stopped:
        print("[+] Stop webhook received. Bot subprocess terminated.")
        return "<h2>Bot stopped. Use the Start Bot link anytime to resume.</h2>"
    return "<h2>Bot was not running.</h2>"

@app.route("/auto-apply/pause")
def pause_auto_apply():
    _require_challenge_token()
    with open(AUTO_APPLY_PAUSE_FLAG, "w") as f:
        f.write("paused")
    print("[+] Auto-apply paused via webhook.")
    return "<h2>Auto-apply paused. The bot will keep scanning/emailing but won't submit any applications until you resume.</h2>"

@app.route("/auto-apply/resume")
def resume_auto_apply():
    _require_challenge_token()
    if os.path.exists(AUTO_APPLY_PAUSE_FLAG):
        os.remove(AUTO_APPLY_PAUSE_FLAG)
    print("[+] Auto-apply resumed via webhook.")
    return "<h2>Auto-apply resumed. The bot will apply to matching jobs again.</h2>"

@app.route("/solve-challenge")
def solve_challenge():
    _require_challenge_token()
    # Routed through Caddy on the same scheme/host as the page itself (see
    # Caddyfile's /vnc* + /websockify* proxy blocks) instead of a raw
    # http://host:6080 URL, which browsers block as mixed content when the
    # page itself is loaded over HTTPS.
    scheme = request.headers.get("X-Forwarded-Proto", request.scheme)
    novnc_url = (
        f"{scheme}://{request.host}/vnc.html?autoconnect=true&resize=scale"
        f"&password={VNC_PASSWORD}"
    )
    resume_url = f"{APP_BASE_URL}/solve-challenge/resume?token={CHALLENGE_ACCESS_TOKEN}"
    return render_template_string("""
        <html><body style="font-family: Arial, sans-serif; color: #333; margin:0;">
            <div style="padding:12px 16px; background:#fff3e0; border-bottom:1px solid #ffcc80;">
                <b>👀 You're viewing the bot's live browser.</b>
                Click the "I'm not a robot" / verification checkbox on the page below,
                the same way you would on any site. Once it clears and you see normal
                Naukri content, click the button to let the bot continue.
                <a href="{{ resume_url }}" style="background-color:#4CAF50; color:white; padding:8px 16px;
                   text-decoration:none; border-radius:4px; font-weight:bold; margin-left:12px;">
                   ✅ I've completed the verification, resume bot
                </a>
            </div>
            <iframe src="{{ novnc_url }}" style="width:100%; height:90vh; border:none;"></iframe>
        </body></html>
    """, novnc_url=novnc_url, resume_url=resume_url)

@app.route("/solve-challenge/resume")
def solve_challenge_resume():
    _require_challenge_token()
    with open(HUMAN_VERIFIED_FLAG, "w") as f:
        f.write("verified")
    print("[+] Human verification marked complete via webhook. Bot will resume shortly.")
    return "<h2>Got it! The bot will check again and resume scanning shortly. You can close this tab.</h2>"

@app.route("/decide/<job_id>")
def decide(job_id):
    _require_challenge_token()
    action = request.args.get("action")
    if action not in ("apply", "skip"):
        return "<h2>Invalid action.</h2>", 400

    decision_file = os.path.join(DECISIONS_DIR, f"{job_id}.json")
    with open(decision_file, "w") as f:
        json.dump({"action": action}, f)

    print(f"[+] Decision recorded for job {job_id}: {action}")
    label = "Apply" if action == "apply" else "Skip"
    return f"<h2>Recorded: {label} this job. The bot will continue shortly. You can close this tab.</h2>"

@app.route("/clarify/<question_id>", methods=["GET", "POST"])
def clarify(question_id):
    # Used when the bot hits a form field it doesn't recognize while applying
    # to a job. It pauses and waits here for you to type in the right answer.
    _require_challenge_token()
    clarify_file = os.path.join(DECISIONS_DIR, f"clarify_{question_id}.json")
    if request.method == "POST":
        value = request.form.get("value", "").strip()
        with open(clarify_file, "w") as f:
            json.dump({"value": value}, f)
        print(f"[+] Clarification recorded for {question_id}: {value}")
        return "<h2>Got it! The bot will use your answer and continue. You can close this tab.</h2>"

    label = request.args.get("label", "this field")
    job_title = request.args.get("job_title", "")
    company = request.args.get("company", "")
    return render_template_string("""
        <h3>Bot needs your help</h3>
        <p>While applying to <b>{{ job_title }}</b> @ <b>{{ company }}</b>, the bot found a form field it didn't
        recognize: <b>{{ label }}</b></p>
        <form method="POST">
            <input type="hidden" name="token" value="{{ token }}">
            Your answer: <input type="text" name="value" style="width:300px;"><br><br>
            <input type="submit" value="Send Answer">
        </form>
    """, label=label, job_title=job_title, company=company, token=CHALLENGE_ACCESS_TOKEN)

@app.route("/edit", methods=["GET", "POST"])
def edit_config():
    _require_challenge_token()
    config = load_config()
    if request.method == "POST":
        config["questionnaire_answers"]["expected_ctc"] = request.form.get("expected_ctc")
        config["questionnaire_answers"]["skills"] = request.form.get("skills")
        save_config(config)
        return "<h2>Configuration updated! Close this window and return to your application workflow.</h2>"

    # Quick inline form rendering for manual data updating overrides
    return render_template_string("""
        <h3>Edit Form-Filling Criteria</h3>
        <form method="POST">
            <input type="hidden" name="token" value="{{ token }}">
            Expected CTC: <input type="text" name="expected_ctc" value="{{ ctc }}"><br><br>
            Skills Stack: <textarea name="skills" rows="4" cols="50">{{ skills }}</textarea><br><br>
            <input type="submit" value="Save Changes & Update Batch">
        </form>
    """, ctc=config["questionnaire_answers"]["expected_ctc"], skills=config["questionnaire_answers"]["skills"],
        token=CHALLENGE_ACCESS_TOKEN)

if __name__ == "__main__":
    # Notify immediately on container startup so you don't have to visit the site manually.
    # Sent in a background thread so a slow/unreachable SMTP server never delays Flask binding to the port.
    threading.Thread(target=send_approval_email, args=(load_config(),), daemon=True).start()
    threading.Thread(target=daily_summary_scheduler, daemon=True).start()
    threading.Thread(target=auto_restart_scheduler, daemon=True).start()
    app.run(host="0.0.0.0", port=5000)
