import os
import json
import secrets
import subprocess
import time
import threading
from datetime import datetime, date
from flask import Flask, request, render_template_string, abort, jsonify
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
# Written by this Flask process when the "I've solved it, resume" button is
# clicked; bot.py polls for this file while it's paused on an Akamai
# human-verification challenge, waiting for a person to click through it via
# the noVNC viewer this app serves at /solve-challenge.
HUMAN_VERIFIED_FLAG = os.path.join(BASE_DIR, "human_verified.flag")
SEEN_JOBS_PATH = os.getenv("SEEN_JOBS_PATH", os.path.join(BASE_DIR, "seen_jobs.json"))
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:5000")
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
    if request.args.get("token") != CHALLENGE_ACCESS_TOKEN:
        abort(403)

DAILY_STATS_PATH = os.path.join(BASE_DIR, "daily_stats.json")
DEFAULT_PROFILE = {
    "target_roles": ["Software Engineer", "Associate Software Engineer", "Backend Developer", "SRE Engineer", "DevOps Engineer"],
    "filters": {
        "experience_years": "0",
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
    <h2 style="color: #4CAF50;">📋 Naukri Auto-Apply Monitoring Check</h2>
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
        <a href="{{ base_url }}/start" style="background-color: #4CAF50; color: white; padding: 10px 20px; text-decoration: none; border-radius: 4px; font-weight: bold; margin-right: 10px;">▶️ Start Bot</a>
        <a href="{{ base_url }}/stop" style="background-color: #f44336; color: white; padding: 10px 20px; text-decoration: none; border-radius: 4px; font-weight: bold; margin-right: 10px;">⏹️ Stop Bot</a>
        <a href="{{ base_url }}/edit" style="background-color: #2196F3; color: white; padding: 10px 20px; text-decoration: none; border-radius: 4px; font-weight: bold;">📝 Edit Configuration Details</a>
    </div>
    <div style="margin-top: 10px;">
        <a href="{{ base_url }}/auto-apply/pause" style="background-color: #FF9800; color: white; padding: 10px 20px; text-decoration: none; border-radius: 4px; font-weight: bold; margin-right: 10px;">⏸️ Pause Auto-Apply</a>
        <a href="{{ base_url }}/auto-apply/resume" style="background-color: #9C27B0; color: white; padding: 10px 20px; text-decoration: none; border-radius: 4px; font-weight: bold;">▶️ Resume Auto-Apply</a>
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

def start_bot():
    if is_bot_running():
        return False
    bot_path = os.path.join(BASE_DIR, "bot.py")
    proc = subprocess.Popen(["python", bot_path])
    with open(PID_FILE, "w") as f:
        f.write(str(proc.pid))
    return True

def stop_bot():
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

def send_approval_email(config):
    from jinja2 import Template
    html_body = Template(EMAIL_HTML_TEMPLATE).render(
        roles=", ".join(config["target_roles"]),
        locations=", ".join(config["filters"]["locations"]),
        current_ctc=config["questionnaire_answers"]["current_ctc"],
        expected_ctc=config["questionnaire_answers"]["expected_ctc"],
        skills=config["questionnaire_answers"]["skills"],
        base_url=APP_BASE_URL
    )
    send_email(config["email_target"], "🐳 Naukri Bot is running via Docker - control it here", html_body)

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

    html_body = f"""
    <html><body style="font-family: Arial, sans-serif; color: #333;">
        <h2 style="color: #2196F3;">📊 Daily Summary - {for_date}</h2>
        <table style="border-collapse: collapse; width: 100%; max-width: 500px;">
            <tr><td style="padding:8px;border:1px solid #ddd;"><b>Decision emails received</b></td><td style="padding:8px;border:1px solid #ddd;">{found}</td></tr>
            <tr><td style="padding:8px;border:1px solid #ddd;"><b>Companies applied to</b></td><td style="padding:8px;border:1px solid #ddd;">{applied}</td></tr>
            <tr><td style="padding:8px;border:1px solid #ddd;"><b>Skipped / no response</b></td><td style="padding:8px;border:1px solid #ddd;">{skipped_or_pending}</td></tr>
        </table>
        <p style="margin-top:15px;color:#888;">Applied {applied} out of {found} jobs the bot found and emailed you about today.</p>
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
    try:
        with open(SEEN_JOBS_PATH, "r") as f:
            seen_jobs = json.load(f)
    except (json.JSONDecodeError, OSError, FileNotFoundError):
        seen_jobs = {}
    applications = [
        {"link": link, "decision": decision}
        for link, decision in seen_jobs.items()
    ]
    return jsonify({"count": len(applications), "applications": applications})

@app.route("/")
def index():
    config = load_config()
    send_approval_email(config)
    return "<h3>Email notification sent to vidursharma8035@gmail.com. Monitor active.</h3>"

@app.route("/start")
@app.route("/approve")
def approve_and_run():
    started = start_bot()
    if started:
        print("[+] Start webhook received. Bot subprocess launched.")
        return "<h2>Bot started! It will begin scanning and email you when it finds jobs.</h2>"
    return "<h2>Bot is already running.</h2>"

@app.route("/stop")
def stop_and_halt():
    stopped = stop_bot()
    if stopped:
        print("[+] Stop webhook received. Bot subprocess terminated.")
        return "<h2>Bot stopped. Use the Start Bot link anytime to resume.</h2>"
    return "<h2>Bot was not running.</h2>"

@app.route("/auto-apply/pause")
def pause_auto_apply():
    with open(AUTO_APPLY_PAUSE_FLAG, "w") as f:
        f.write("paused")
    print("[+] Auto-apply paused via webhook.")
    return "<h2>Auto-apply paused. The bot will keep scanning/emailing but won't submit any applications until you resume.</h2>"

@app.route("/auto-apply/resume")
def resume_auto_apply():
    if os.path.exists(AUTO_APPLY_PAUSE_FLAG):
        os.remove(AUTO_APPLY_PAUSE_FLAG)
    print("[+] Auto-apply resumed via webhook.")
    return "<h2>Auto-apply resumed. The bot will apply to matching jobs again.</h2>"

@app.route("/solve-challenge")
def solve_challenge():
    _require_challenge_token()
    # Derive the noVNC host from whatever host the request came in on (works
    # whether APP_BASE_URL is an IP, a domain, or localhost during testing).
    host = request.host.split(":")[0]
    novnc_url = (
        f"http://{host}:{NOVNC_PORT}/vnc.html?autoconnect=true&resize=scale"
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
            Your answer: <input type="text" name="value" style="width:300px;"><br><br>
            <input type="submit" value="Send Answer">
        </form>
    """, label=label, job_title=job_title, company=company)

@app.route("/edit", methods=["GET", "POST"])
def edit_config():
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
            Expected CTC: <input type="text" name="expected_ctc" value="{{ ctc }}"><br><br>
            Skills Stack: <textarea name="skills" rows="4" cols="50">{{ skills }}</textarea><br><br>
            <input type="submit" value="Save Changes & Update Batch">
        </form>
    """, ctc=config["questionnaire_answers"]["expected_ctc"], skills=config["questionnaire_answers"]["skills"])

if __name__ == "__main__":
    # Notify immediately on container startup so you don't have to visit the site manually.
    # Sent in a background thread so a slow/unreachable SMTP server never delays Flask binding to the port.
    threading.Thread(target=send_approval_email, args=(load_config(),), daemon=True).start()
    threading.Thread(target=daily_summary_scheduler, daemon=True).start()
    app.run(host="0.0.0.0", port=5000)
