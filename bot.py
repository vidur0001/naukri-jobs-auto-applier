import asyncio
import random
import re
import os
import json
import time
from playwright.async_api import async_playwright
from pw_stealth_enhanced import apply_stealth
from dotenv import load_dotenv
from mailer import send_email
import ai_helper

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
USER_DATA_DIR = os.getenv("USER_DATA_DIR", os.path.join(BASE_DIR, "naukri_profile"))
# Portable session (see login_setup.py) - plain JSON cookies/localStorage that
# work identically whether created on Windows or replayed on Linux (unlike the
# raw user_data_dir profile, whose cookies are OS-encrypted and unreadable
# across platforms).
STORAGE_STATE_PATH = os.getenv("STORAGE_STATE_PATH", os.path.join(BASE_DIR, "naukri_storage_state.json"))
CONFIG_PATH = os.getenv("CONFIG_PATH", os.path.join(BASE_DIR, "config.json"))
DECISIONS_DIR = os.getenv("DECISIONS_DIR", os.path.join(BASE_DIR, "decisions"))
SEEN_JOBS_PATH = os.getenv("SEEN_JOBS_PATH", os.path.join(BASE_DIR, "seen_jobs.json"))
# Tracks which companies a cold outreach email has already been sent to, so
# a recruiter contact repeated across multiple job postings only gets one
# follow-up email instead of one per posting.
COLD_EMAIL_LOG_PATH = os.getenv("COLD_EMAIL_LOG_PATH", os.path.join(BASE_DIR, "cold_emails_sent.json"))
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:5000")
DECISION_POLL_INTERVAL = 3      # seconds between checks for your response
SKIP_GRACE_PERIOD = 45          # seconds to allow a "Skip" click before auto-applying
AUTO_APPLY_PAUSE_FLAG = os.path.join(BASE_DIR, "auto_apply_paused.flag")
# Written by app.py's /solve-challenge/resume webhook once a human has
# clicked through Akamai's verification checkbox via the noVNC viewer.
HUMAN_VERIFIED_FLAG = os.path.join(BASE_DIR, "human_verified.flag")
# Must match the token app.py checks on /solve-challenge* (set explicitly in
# .env so both processes agree - if left unset here while app.py falls back
# to a random one, the emailed link would 403).
CHALLENGE_ACCESS_TOKEN = os.getenv("CHALLENGE_ACCESS_TOKEN", "")

os.makedirs(DECISIONS_DIR, exist_ok=True)

def auto_apply_paused():
    return os.path.exists(AUTO_APPLY_PAUSE_FLAG)

# Akamai (Naukri's bot-management WAF) sometimes serves an interstitial
# "check the box to let us know you're human" challenge page instead of the
# real search results. When that happens, .srp-jobtuple-wrapper legitimately
# has 0 matches, which used to get silently misread as "0 jobs found" or
# "no Apply button" - burning through the whole batch list against a wall.
# Detect it explicitly so we can bail out early and alert instead.
CHALLENGE_MARKERS = [
    "check the box to let us know you're human",
    "check the box to let us know you\u2019re human",
    "verify you are human",
    "additional verification required",
]

async def is_challenge_page(page):
    try:
        body_text = (await page.locator("body").inner_text(timeout=5000)).lower()
    except Exception:
        return False
    return any(marker in body_text for marker in CHALLENGE_MARKERS)

def alert_session_expired(email_target):
    """Notifies that the saved Naukri session is invalid/expired and the bot
    has stopped. Since re-logging in from this cloud server tends to trigger
    repeated Akamai puzzles, the fix is to refresh the session from a home/
    personal network via login_setup.py, then upload the resulting file here
    - no manual scp needed."""
    upload_url = f"{APP_BASE_URL}/session/upload?token={CHALLENGE_ACCESS_TOKEN}"
    try:
        send_email(
            email_target,
            "🛑 Naukri Bot Stopped: Session expired",
            "<html><body style='font-family:Arial,sans-serif;color:#333;'>"
            "<h2 style='color:#c0392b;'>Bot stopped - login session expired</h2>"
            "<p>The saved Naukri session is no longer valid, so the bot could "
            "not continue. To avoid repeated Akamai puzzle loops, please "
            "refresh the session from your own computer instead of this "
            "server:</p>"
            "<ol>"
            "<li>On your laptop, run <code>python login_setup.py</code> and "
            "log in to Naukri normally.</li>"
            "<li>This creates/updates <code>naukri_storage_state.json</code> "
            "in that folder.</li>"
            "<li>Click the button below and upload that file - no terminal/"
            "scp needed.</li>"
            "</ol>"
            f"<p><a href='{upload_url}' style='background-color:#4CAF50;"
            "color:white;padding:10px 20px;text-decoration:none;border-radius:4px;"
            "font-weight:bold;'>⬆️ Upload Refreshed Session</a></p>"
            "<p style='color:#888;'>Once uploaded, just start the bot again "
            "from your usual control email/dashboard.</p>"
            "</body></html>"
        )
    except Exception as e:
        print(f"   [-] Could not send session-expired alert email: {e}")

_last_challenge_alert_ts = 0

def alert_challenge_detected(email_target):
    # Throttle to at most one alert per 10 minutes so we don't spam the inbox
    # while repeated runs keep hitting the same block.
    global _last_challenge_alert_ts
    now = time.time()
    if now - _last_challenge_alert_ts < 600:
        return
    _last_challenge_alert_ts = now
    # Solving Akamai's puzzle live via noVNC from a phone is unreliable (touch
    # telemetry + cloud IP keep re-triggering it), so instead of waiting for a
    # puzzle-solve, the bot aborts this run immediately and asks for a
    # locally-refreshed session (same flow as alert_session_expired) - that
    # consistently clears the challenge without ever solving it from here.
    upload_url = f"{APP_BASE_URL}/session/upload?token={CHALLENGE_ACCESS_TOKEN}"
    try:
        send_email(
            email_target,
            "🛑 Naukri Bot Stopped: Verification challenge detected",
            "<html><body style='font-family:Arial,sans-serif;color:#333;'>"
            "<h2 style='color:#c0392b;'>Bot stopped - verification challenge blocked it</h2>"
            "<p>Naukri/Akamai served a bot-verification challenge page instead "
            "of real results, so the run was stopped. Solving this puzzle "
            "remotely (e.g. from a phone) tends to just trigger it again, so "
            "the reliable fix is to refresh the session from your own "
            "computer instead:</p>"
            "<ol>"
            "<li>On your laptop, run <code>python login_setup.py</code> and "
            "log in to Naukri normally.</li>"
            "<li>This creates/updates <code>naukri_storage_state.json</code> "
            "in that folder.</li>"
            "<li>Click the button below and upload that file.</li>"
            "</ol>"
            f"<p><a href='{upload_url}' style='background-color:#4CAF50;"
            "color:white;padding:10px 20px;text-decoration:none;border-radius:4px;"
            "font-weight:bold;'>⬆️ Upload Refreshed Session</a></p>"
            "<p style='color:#888;'>Once uploaded, start the bot again from "
            "your usual control email/dashboard.</p>"
            "</body></html>"
        )
    except Exception as e:
        print(f"   [-] Could not send challenge-alert email: {e}")

async def wait_for_human_verification(timeout=1200, poll_interval=5):
    """Blocks (async-friendly) until app.py's /solve-challenge/resume webhook
    writes HUMAN_VERIFIED_FLAG, or until `timeout` seconds elapse.

    Returns True if a human confirmed they solved the challenge, False on
    timeout.
    """
    # Clear any stale flag left over from a previous, unrelated resume click.
    if os.path.exists(HUMAN_VERIFIED_FLAG):
        os.remove(HUMAN_VERIFIED_FLAG)
    waited = 0
    while waited < timeout:
        if os.path.exists(HUMAN_VERIFIED_FLAG):
            os.remove(HUMAN_VERIFIED_FLAG)
            return True
        await asyncio.sleep(poll_interval)
        waited += poll_interval
    return False

def load_config():
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)

def load_seen_jobs():
    if os.path.exists(SEEN_JOBS_PATH):
        try:
            with open(SEEN_JOBS_PATH, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}

def mark_job_seen(seen_jobs, link, decision):
    seen_jobs[link] = decision
    with open(SEEN_JOBS_PATH, "w") as f:
        json.dump(seen_jobs, f, indent=2)

# Matches a recruiter/HR contact email if the job posting itself explicitly
# lists one (common on "walk-in"/consultancy-style postings). Naukri does not
# expose recruiter emails otherwise, so this only fires opportunistically.
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Generic Naukri/noreply addresses that occasionally leak into JD boilerplate
# footers and should never be treated as a real recruiter contact.
_EMAIL_IGNORE_DOMAINS = ("naukri.com", "example.com", "noreply", "no-reply")

def extract_contact_email(jd_text):
    if not jd_text:
        return None
    for candidate in _EMAIL_RE.findall(jd_text):
        if not any(bad in candidate.lower() for bad in _EMAIL_IGNORE_DOMAINS):
            return candidate
    return None

def load_cold_email_log():
    if os.path.exists(COLD_EMAIL_LOG_PATH):
        try:
            with open(COLD_EMAIL_LOG_PATH, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}

def mark_cold_email_sent(log, company):
    log[company.strip().lower()] = int(time.time())
    with open(COLD_EMAIL_LOG_PATH, "w") as f:
        json.dump(log, f, indent=2)

def send_cold_email_if_possible(job_title, comp_name, jd_text, config, cold_log):
    """Opportunistically sends a personalized cold-outreach follow-up
    directly to a recruiter contact email found in the job description
    (only when the JD itself lists one - the bot never guesses/fabricates
    an address). No-ops entirely if disabled in config, no contact email is
    present, the company was already emailed before, or AI is unavailable."""
    cold_cfg = config.get("cold_email", {})
    if not cold_cfg.get("enabled", False):
        return
    if comp_name.strip().lower() in cold_log:
        return
    contact_email = extract_contact_email(jd_text)
    if not contact_email:
        return
    draft = ai_helper.compose_cold_email(job_title, comp_name, jd_text=jd_text)
    if not draft:
        return
    resume_path = cold_cfg.get("resume_path")
    if resume_path and not os.path.isabs(resume_path):
        resume_path = os.path.join(BASE_DIR, resume_path)
    sent = send_email(
        contact_email,
        draft["subject"],
        f"<html><body style='font-family:Arial,sans-serif;color:#333;'>{draft['body_html']}</body></html>",
        reply_to=cold_cfg.get("reply_to") or config.get("email_target"),
        attachment_path=resume_path,
    )
    mark_cold_email_sent(cold_log, comp_name)
    if sent:
        print(f"   [+] Cold outreach email sent to {contact_email} for {job_title} @ {comp_name}")
        try:
            send_email(
                config["email_target"],
                f"📧 Cold email sent: {job_title} @ {comp_name}",
                "<html><body style='font-family:Arial,sans-serif;color:#333;'>"
                f"<p>A follow-up outreach email was auto-sent to <b>{contact_email}</b> "
                f"(found in the job posting for {job_title} @ {comp_name}) using the draft below.</p>"
                f"<p><b>Subject:</b> {draft['subject']}</p>"
                f"<p>{draft['body_html']}</p>"
                "</body></html>"
            )
        except Exception as e:
            print(f"   [-] Could not send cold-email notification: {e}")

# Structured, per-job log (title/company/location/link/status/timestamp) for
# the future web dashboard's "Applications" view. daily_stats.json only has
# aggregate counts and seen_jobs.json only has link->decision, neither is
# enough to render a readable table, so this is additive and separate.
APPLICATIONS_LOG_PATH = os.getenv("APPLICATIONS_LOG_PATH", os.path.join(BASE_DIR, "applications_log.json"))
APPLICATIONS_LOG_MAX_ENTRIES = 1000

def log_application_event(job_title, comp_name, location, link, status, note=""):
    try:
        if os.path.exists(APPLICATIONS_LOG_PATH):
            with open(APPLICATIONS_LOG_PATH, "r") as f:
                entries = json.load(f)
        else:
            entries = []
    except (json.JSONDecodeError, OSError):
        entries = []

    entries.append({
        "timestamp": int(time.time()),
        "title": job_title,
        "company": comp_name,
        "location": location,
        "link": link,
        "status": status,  # e.g. ai_skip, skipped_by_user, applied, rejected_external_site, rejected_no_apply_button, error
        "note": note,
    })
    # Keep the file bounded so it doesn't grow unbounded over months of runs.
    entries = entries[-APPLICATIONS_LOG_MAX_ENTRIES:]

    with open(APPLICATIONS_LOG_PATH, "w") as f:
        json.dump(entries, f, indent=2)

DAILY_STATS_PATH = os.path.join(BASE_DIR, "daily_stats.json")

def _today_key():
    # Container runs with TZ=Asia/Kolkata, so local date already reflects IST.
    from datetime import date
    return date.today().isoformat()

def record_stat(kind):
    # kind is either "found" (decision email sent) or "applied" (application submitted).
    try:
        if os.path.exists(DAILY_STATS_PATH):
            with open(DAILY_STATS_PATH, "r") as f:
                stats = json.load(f)
        else:
            stats = {}
    except (json.JSONDecodeError, OSError):
        stats = {}

    today = _today_key()
    day_stats = stats.get(today, {"found": 0, "applied": 0})
    day_stats[kind] = day_stats.get(kind, 0) + 1
    stats[today] = day_stats

    with open(DAILY_STATS_PATH, "w") as f:
        json.dump(stats, f, indent=2)

def send_job_decision_email(email_target, job_id, title, company, location, link,
                             experience="Not specified", salary="Not disclosed",
                             job_desc="Not available - see full posting via the link below.",
                             ai_score=None, ai_reasoning=None, ai_summary=None):
    decide_skip = f"{APP_BASE_URL}/decide/{job_id}?action=skip&token={CHALLENGE_ACCESS_TOKEN}"
    pause_url = f"{APP_BASE_URL}/auto-apply/pause?token={CHALLENGE_ACCESS_TOKEN}"
    ai_html = ""
    if ai_score is not None:
        ai_html = f"""
        <h3 style="color:#333;margin-top:15px;">🤖 AI Match Assessment</h3>
        <p style="max-width:600px;background:#eef7ee;padding:12px;border-radius:4px;">
            <b>Relevance Score:</b> {ai_score}/100<br/>
            <b>Reasoning:</b> {ai_reasoning or 'N/A'}
        </p>
        """
        if ai_summary:
            ai_html += f"""
            <h3 style="color:#333;margin-top:10px;">🤖 AI Summary</h3>
            <p style="max-width:600px;background:#eef2f7;padding:12px;border-radius:4px;white-space:pre-wrap;">{ai_summary}</p>
            """
    html_body = f"""
    <html><body style="font-family: Arial, sans-serif; color: #333;">
        <h2 style="color: #4CAF50;">🔎 New Job Found</h2>
        {ai_html}
        <table style="border-collapse: collapse; width: 100%; max-width: 600px;">
            <tr><td style="padding:8px;border:1px solid #ddd;"><b>Title</b></td><td style="padding:8px;border:1px solid #ddd;">{title}</td></tr>
            <tr><td style="padding:8px;border:1px solid #ddd;"><b>Company</b></td><td style="padding:8px;border:1px solid #ddd;">{company}</td></tr>
            <tr><td style="padding:8px;border:1px solid #ddd;"><b>Location</b></td><td style="padding:8px;border:1px solid #ddd;">{location}</td></tr>
            <tr><td style="padding:8px;border:1px solid #ddd;"><b>Experience Required</b></td><td style="padding:8px;border:1px solid #ddd;">{experience}</td></tr>
            <tr><td style="padding:8px;border:1px solid #ddd;"><b>CTC / Salary</b></td><td style="padding:8px;border:1px solid #ddd;">{salary}</td></tr>
            <tr><td style="padding:8px;border:1px solid #ddd;"><b>Link</b></td><td style="padding:8px;border:1px solid #ddd;"><a href="{link}">{link}</a></td></tr>
        </table>
        <h3 style="color:#333;margin-top:15px;">Job Description</h3>
        <p style="max-width:600px;background:#f7f7f7;padding:12px;border-radius:4px;white-space:pre-wrap;">{job_desc}</p>
        <br/>
        <a href="{decide_skip}" style="background-color:#f44336;color:white;padding:10px 20px;text-decoration:none;border-radius:4px;font-weight:bold;margin-right:10px;">❌ Skip This Job</a>
        <a href="{pause_url}" style="background-color:#FF9800;color:white;padding:10px 20px;text-decoration:none;border-radius:4px;font-weight:bold;">⏸️ Pause All Auto-Apply</a>
        <p style="margin-top:15px;color:#888;">This job passed the AI relevance filter and will be auto-applied in about {SKIP_GRACE_PERIOD} seconds unless you click Skip above. Use "Pause All Auto-Apply" to stop applying to any job until you resume.</p>
    </body></html>
    """
    send_email(email_target, f"🔎 Auto-Applying Soon: {title} @ {company}", html_body)

async def wait_for_skip(job_id):
    """Gives a short window for a 'Skip' click before auto-applying.
    Returns 'skip' if clicked within the grace period, if auto-apply is
    globally paused, or else 'apply' once the window elapses."""
    decision_file = os.path.join(DECISIONS_DIR, f"{job_id}.json")
    start = time.time()
    while time.time() - start < SKIP_GRACE_PERIOD:
        if os.path.exists(decision_file):
            with open(decision_file, "r") as f:
                data = json.load(f)
            os.remove(decision_file)
            return data.get("action", "apply")
        if auto_apply_paused():
            print(f"   [-] Auto-apply is paused. Skipping job {job_id} for now.")
            return "skip"
        await asyncio.sleep(DECISION_POLL_INTERVAL)
    if auto_apply_paused():
        print(f"   [-] Auto-apply is paused. Skipping job {job_id} for now.")
        return "skip"
    return "apply"

async def human_delay(min_sec=2, max_sec=5):
    await asyncio.sleep(random.uniform(min_sec, max_sec))

def send_clarification_email(email_target, question_id, label, job_title, company):
    from urllib.parse import quote
    clarify_url = (
        f"{APP_BASE_URL}/clarify/{question_id}"
        f"?label={quote(label)}&job_title={quote(job_title)}&company={quote(company)}"
        f"&token={CHALLENGE_ACCESS_TOKEN}"
    )
    html_body = f"""
    <html><body style="font-family: Arial, sans-serif; color: #333;">
        <h2 style="color: #FF9800;">❓ Bot needs your help</h2>
        <p>While applying to <b>{job_title}</b> @ <b>{company}</b>, the bot found a form field it couldn't
        confidently fill in on its own:</p>
        <p style="font-size:16px;"><b>{label}</b></p>
        <a href="{clarify_url}" style="background-color:#FF9800;color:white;padding:10px 20px;text-decoration:none;border-radius:4px;font-weight:bold;">✍️ Answer this field</a>
        <p style="margin-top:15px;color:#888;">The bot is paused on this application and will use your answer to
        continue (or skip the field after 30 minutes if there's no response).</p>
    </body></html>
    """
    send_email(email_target, f"❓ Bot needs input: {label} ({job_title} @ {company})", html_body)

async def wait_for_clarification(question_id, timeout=1800):
    clarify_file = os.path.join(DECISIONS_DIR, f"clarify_{question_id}.json")
    start = time.time()
    while time.time() - start < timeout:
        if os.path.exists(clarify_file):
            with open(clarify_file, "r") as f:
                data = json.load(f)
            os.remove(clarify_file)
            return data.get("value", "")
        await asyncio.sleep(DECISION_POLL_INTERVAL)
    print(f"   [!] No clarification received for '{question_id}' within timeout. Leaving field blank.")
    return ""

async def handle_application_form(page, config, job_title="", comp_name="", jd_text=""):
    # Tracks which questionnaire fields were detected and what value was filled
    # into each, so we can report exactly what was submitted on your behalf.
    filled_fields = {}
    try:
        form_container = page.locator(".chatbot-container, .apply-form, .modal-content, .questionnaire-container").first
        if await form_container.count() > 0:
            print("   [!] Custom form dialogue encountered. Matching fields...")
            inputs = await page.locator("input[type='text'], textarea").all()
            for inp in inputs:
                label = (await inp.evaluate("el => el.placeholder || el.getAttribute('name') || ''")).lower()

                if "ctc" in label or "salary" in label:
                    if "expected" in label:
                        value = config["questionnaire_answers"]["expected_ctc"]
                        await inp.fill(value)
                        filled_fields["Expected CTC"] = value
                    else:
                        value = config["questionnaire_answers"]["current_ctc"]
                        await inp.fill(value)
                        filled_fields["Current CTC"] = value
                elif "notice" in label:
                    value = config["questionnaire_answers"]["notice_period"]
                    await inp.fill(value)
                    filled_fields["Notice Period"] = value
                elif "skill" in label:
                    default_skills = config["questionnaire_answers"]["skills"]
                    tailored = default_skills
                    if config.get("ai", {}).get("auto_answer_questions", True):
                        tailored = ai_helper.tailor_skills_for_jd(
                            jd_text, fallback_skills=default_skills
                        )
                    value = tailored[:100]
                    await inp.fill(value)
                    filled_fields["Skills"] = value
                elif label.strip():
                    # Unrecognized field - try the AI helper first (grounded in your
                    # real resume profile) since these are usually open-ended
                    # questionnaire questions. Only fall back to emailing you for
                    # manual clarification if AI is unavailable or fails.
                    ai_value = None
                    if config.get("ai", {}).get("auto_answer_questions", True):
                        ai_value = ai_helper.answer_question(label, job_title=job_title, company=comp_name)

                    if ai_value:
                        await inp.fill(ai_value)
                        filled_fields[label] = ai_value
                        print(f"   [AI] Answered field '{label}' -> {ai_value[:80]}...")
                    else:
                        question_id = f"{int(time.time()*1000)}-{random.randint(1000,9999)}"
                        print(f"   [?] Unrecognized field '{label}'. Emailing you for clarification (question_id={question_id})...")
                        send_clarification_email(config["email_target"], question_id, label, job_title, comp_name)
                        value = await wait_for_clarification(question_id)
                        if value:
                            await inp.fill(value)
                            filled_fields[label] = value

            submit_btn = page.locator("button:has-text('Submit'), button:has-text('Confirm'), .save-button").first
            if await submit_btn.count() > 0:
                await submit_btn.click()
                print("   [+] Questionnaire dynamic values dispatched.")
    except Exception as e:
        print(f"   [X] Error resolving multi-layer fields framework: {e}")
    return filled_fields

def send_application_confirmation_email(email_target, title, company, location, link, filled_fields, config=None):
    # Naukri's "Apply" button submits your profile's existing resume
    # (the one already uploaded on naukri.com) automatically - the bot never
    # uploads a different file, so whatever resume is set as your default on
    # your Naukri profile is what got sent to this employer.
    if filled_fields:
        rows = "".join(
            f'<tr><td style="padding:8px;border:1px solid #ddd;"><b>{field}</b></td>'
            f'<td style="padding:8px;border:1px solid #ddd;">{value}</td></tr>'
            for field, value in filled_fields.items()
        )
        details_html = f"""
        <h3 style="color:#333;">Questionnaire details submitted:</h3>
        <table style="border-collapse: collapse; width: 100%; max-width: 600px;">{rows}</table>
        """
    else:
        details_html = "<p style='color:#888;'>No extra questionnaire fields were required for this application.</p>"

    # Always show the full profile/answer set used for this application,
    # regardless of whether Naukri actually presented a custom questionnaire
    # for this specific job, so you have the complete picture at a glance.
    qa = (config or {}).get("questionnaire_answers", {})
    experience_years = (config or {}).get("filters", {}).get("experience_years", "")
    if isinstance(experience_years, list):
        experience_years = "-".join(experience_years)
    profile_html = f"""
    <h3 style="color:#333;">Your profile details used:</h3>
    <table style="border-collapse: collapse; width: 100%; max-width: 600px;">
        <tr><td style="padding:8px;border:1px solid #ddd;"><b>Experience</b></td><td style="padding:8px;border:1px solid #ddd;">{experience_years} years</td></tr>
        <tr><td style="padding:8px;border:1px solid #ddd;"><b>Current CTC</b></td><td style="padding:8px;border:1px solid #ddd;">{qa.get('current_ctc', '-')}</td></tr>
        <tr><td style="padding:8px;border:1px solid #ddd;"><b>Expected CTC</b></td><td style="padding:8px;border:1px solid #ddd;">{qa.get('expected_ctc', '-')}</td></tr>
        <tr><td style="padding:8px;border:1px solid #ddd;"><b>Notice Period</b></td><td style="padding:8px;border:1px solid #ddd;">{qa.get('notice_period', '-')}</td></tr>
        <tr><td style="padding:8px;border:1px solid #ddd;"><b>Skills</b></td><td style="padding:8px;border:1px solid #ddd;">{qa.get('skills', '-')}</td></tr>
    </table>
    """

    html_body = f"""
    <html><body style="font-family: Arial, sans-serif; color: #333;">
        <h2 style="color: #4CAF50;">✅ Application Submitted</h2>
        <table style="border-collapse: collapse; width: 100%; max-width: 600px;">
            <tr><td style="padding:8px;border:1px solid #ddd;"><b>Title</b></td><td style="padding:8px;border:1px solid #ddd;">{title}</td></tr>
            <tr><td style="padding:8px;border:1px solid #ddd;"><b>Company</b></td><td style="padding:8px;border:1px solid #ddd;">{company}</td></tr>
            <tr><td style="padding:8px;border:1px solid #ddd;"><b>Location</b></td><td style="padding:8px;border:1px solid #ddd;">{location}</td></tr>
            <tr><td style="padding:8px;border:1px solid #ddd;"><b>Link</b></td><td style="padding:8px;border:1px solid #ddd;"><a href="{link}">{link}</a></td></tr>
            <tr><td style="padding:8px;border:1px solid #ddd;"><b>Resume Used</b></td><td style="padding:8px;border:1px solid #ddd;">Your default resume currently uploaded on your Naukri.com profile</td></tr>
        </table>
        {profile_html}
        {details_html}
    </body></html>
    """
    send_email(email_target, f"✅ Applied: {title} @ {company}", html_body)

def send_application_rejected_email(email_target, title, company, location, link, reason=""):
    """Notifies immediately when a job could NOT be auto-applied to
    (external redirect, missing Apply button, or an error mid-application),
    so you know it needs manual attention instead of silently vanishing."""
    html_body = f"""
    <html><body style="font-family: Arial, sans-serif; color: #333;">
        <h2 style="color: #f44336;">🚫 Application Not Submitted</h2>
        <table style="border-collapse: collapse; width: 100%; max-width: 600px;">
            <tr><td style="padding:8px;border:1px solid #ddd;"><b>Title</b></td><td style="padding:8px;border:1px solid #ddd;">{title}</td></tr>
            <tr><td style="padding:8px;border:1px solid #ddd;"><b>Company</b></td><td style="padding:8px;border:1px solid #ddd;">{company}</td></tr>
            <tr><td style="padding:8px;border:1px solid #ddd;"><b>Location</b></td><td style="padding:8px;border:1px solid #ddd;">{location}</td></tr>
            <tr><td style="padding:8px;border:1px solid #ddd;"><b>Link</b></td><td style="padding:8px;border:1px solid #ddd;"><a href="{link}">{link}</a></td></tr>
        </table>
        <p style="margin-top:15px;background:#fdecea;padding:12px;border-radius:4px;"><b>Reason:</b> {reason}</p>
    </body></html>
    """
    send_email(email_target, f"🚫 Not Applied: {title} @ {company}", html_body)

async def run_auto_apply():
    config = load_config()
    seen_jobs = load_seen_jobs()
    async with async_playwright() as p:
        # Crucial configuration switches for execution stability inside Linux Containers
        # NOTE: Naukri's Akamai bot-protection blocks headless Chromium (403 Access
        # Denied) even with stealth patches applied. Headed (visible) mode passes.
        # This means the bot must run on a machine that can display a browser window.
        browser = await p.chromium.launch(
            headless=False,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
            ignore_default_args=["--enable-automation"],
        )
        if not os.path.exists(STORAGE_STATE_PATH):
            print(f"[X] Execution Halted: No session found at {STORAGE_STATE_PATH}. Please run login_setup.py locally.")
            alert_session_expired(config["email_target"])
            await browser.close()
            return
        context = await browser.new_context(
            storage_state=STORAGE_STATE_PATH,
            viewport={"width": 1280, "height": 800},
            # Must match login_setup.py exactly - Naukri/Google treats a mismatched
            # timezone/locale/viewport as a different "device" and invalidates the session.
            timezone_id="Asia/Kolkata",
            locale="en-IN",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        await apply_stealth(context)
        page = await context.new_page()

        # Navigate straight to the authenticated dashboard route - it redirects
        # to /nlogin/login if the session isn't valid, which is a much more
        # reliable signal than looking for a "Login" link on the public
        # homepage (which can render regardless of auth state).
        await page.goto("https://www.naukri.com/mnjuser/homepage")
        await human_delay(3, 5)

        if "login" in page.url.lower():
            print("[X] Execution Halted: The saved session has expired. Please run login_setup.py locally.")
            alert_session_expired(config["email_target"])
            await context.close()
            await browser.close()
            return

        print("[+] Token storage authenticated. Launching systematic sequence processing...")

        cold_log = load_cold_email_log()

        # Naukri's search only accepts a single "experience" value per request
        # (it returns postings whose range includes that value), so to cover
        # a broad band like "0 to 3 years" the bot loops over each value in
        # the list rather than a single number. Accepts either a list
        # (["0","1","2","3"]) or a legacy single string ("1") in config.json.
        experience_values = config["filters"]["experience_years"]
        if not isinstance(experience_values, list):
            experience_values = [experience_values]

        for location in config["filters"]["locations"]:
            for role in config["target_roles"]:
              for experience_years in experience_values:
                print(f"\n⚡ BATCH: Scanning for {role} vacancies inside {location.upper()} (experience={experience_years})...")
                formatted_role = role.lower().replace(" ", "-")
                query_param = role.replace(" ", "%20")

                # Generates dynamic URL mapping target structures safely across Indian metropolitan nodes
                search_url = f"https://www.naukri.com/{formatted_role}-jobs-in-{location}?k={query_param}&experience={experience_years}"

                navigated = False
                for attempt in range(3):
                    try:
                        await page.goto(search_url, timeout=30000)
                        navigated = True
                        break
                    except Exception as nav_err:
                        print(f"   [-] Navigation attempt {attempt + 1}/3 failed for {role} in {location}: {nav_err}")
                        await human_delay(3, 6)

                if not navigated:
                    print(f"   [X] Skipping {role} in {location} after repeated navigation failures.")
                    continue

                await human_delay(4, 6)

                if await is_challenge_page(page):
                    print(f"   [!] Akamai human-verification challenge detected on search page "
                          f"({role} in {location}). Aborting this run and requesting a session refresh.")
                    alert_challenge_detected(config["email_target"])
                    await context.close()
                    await browser.close()
                    return

                # Naukri's search results wrap each card in a "srp-jobtuple-wrapper"
                # div (the older "srp-jobtuple" class is no longer used).
                job_tuples = await page.locator(".srp-jobtuple-wrapper").all()
                print(f"   [i] Found {len(job_tuples)} job tuples on page (URL: {page.url}).")
                for card in job_tuples[:10]:  # Limit top 10 items per cycle array to preserve behavioral integrity
                    try:
                        comp_name = "Unknown Company"
                        comp_loc = card.locator("a.comp-name")
                        if await comp_loc.count() > 0:
                            comp_name = await comp_loc.first.inner_text()
                            if any(bc.lower() in comp_name.lower() for bc in config["blacklist_companies"]):
                                continue

                        title_loc = card.locator("a.title")
                        if await title_loc.count() == 0:
                            continue

                        link = await title_loc.first.get_attribute("href")
                        job_title = (await title_loc.first.inner_text()).strip()

                        # Skip jobs we've already decided on (applied or explicitly skipped) in a prior run
                        if link in seen_jobs:
                            print(f"   [=] Already processed earlier ({seen_jobs[link]}): {job_title} @ {comp_name}")
                            continue

                        # Pull the extra details Naukri already renders on the search-result
                        # card itself, so the approval email shows exact experience/CTC/JD
                        # instead of just title/company/location.
                        exact_experience = "Not specified"
                        exp_loc = card.locator(".exp-wrap .expwdth, span.expwdth, .exp")
                        if await exp_loc.count() > 0:
                            exact_experience = (await exp_loc.first.inner_text()).strip()

                        exact_salary = "Not disclosed"
                        sal_loc = card.locator(".sal-wrap .sal, span.sal, .sal")
                        if await sal_loc.count() > 0:
                            exact_salary = (await sal_loc.first.inner_text()).strip()

                        job_desc_snippet = "Not available - see full posting via the link below."
                        desc_loc = card.locator(".job-desc, .job-description")
                        if await desc_loc.count() > 0:
                            job_desc_snippet = (await desc_loc.first.inner_text()).strip()

                        # The card only shows a truncated JD teaser. Open the actual job
                        # posting briefly to pull the full description and confirm the
                        # exact experience/CTC shown on the detail page (more reliable
                        # than the search-card summary) before asking for your approval.
                        try:
                            detail_page = await context.new_page()
                            await detail_page.goto(link, timeout=20000)
                            await human_delay(2, 3)

                            full_desc_loc = detail_page.locator(".styles_JDC__dang-inner-html__h0K4t, .dang-inner-html, .job-desc")
                            if await full_desc_loc.count() > 0:
                                full_text = (await full_desc_loc.first.inner_text()).strip()
                                if full_text:
                                    job_desc_snippet = full_text[:3000]

                            detail_exp_loc = detail_page.locator(".styles_details__Y424J, .exp span, span.expwdth")
                            if await detail_exp_loc.count() > 0:
                                detail_exp_text = (await detail_exp_loc.first.inner_text()).strip()
                                if detail_exp_text:
                                    exact_experience = detail_exp_text

                            detail_sal_loc = detail_page.locator("span.sal, .sal-wrap .sal")
                            if await detail_sal_loc.count() > 0:
                                detail_sal_text = (await detail_sal_loc.first.inner_text()).strip()
                                if detail_sal_text:
                                    exact_salary = detail_sal_text

                            await detail_page.close()
                        except Exception as jd_err:
                            print(f"   [-] Could not fetch full JD for {job_title} @ {comp_name}: {jd_err}")

                        # AI relevance scoring + JD summarization (Groq/Llama). Degrades
                        # gracefully to a neutral score if GROQ_API_KEY is missing or the
                        # API call fails - never blocks the pipeline.
                        ai_result = ai_helper.score_relevance(job_desc_snippet)
                        ai_summary = ai_helper.summarize_jd(job_desc_snippet)
                        min_score = config.get("ai", {}).get("min_relevance_score", 0)
                        if ai_result["score"] < min_score:
                            print(f"   [AI] Skipping (score {ai_result['score']} < {min_score}): {job_title} @ {comp_name} - {ai_result['reasoning']}")
                            mark_job_seen(seen_jobs, link, "ai_skip")
                            log_application_event(job_title, comp_name, location, link, "ai_skip", note=ai_result["reasoning"])
                            continue

                        # Auto-apply by default, but give a short grace period
                        # for you to click "Skip" from the email before the bot
                        # actually submits - keeps throughput high while still
                        # letting you catch mistakes (bad match, blacklisted
                        # company that slipped through, etc.) before it's too late.
                        job_id = f"{int(time.time()*1000)}-{random.randint(1000,9999)}"
                        send_job_decision_email(
                            config["email_target"], job_id, job_title, comp_name, location, link,
                            experience=exact_experience, salary=exact_salary, job_desc=job_desc_snippet,
                            ai_score=ai_result["score"], ai_reasoning=ai_result["reasoning"], ai_summary=ai_summary
                        )
                        record_stat("found")
                        decision = await wait_for_skip(job_id)
                        mark_job_seen(seen_jobs, link, decision)

                        if decision != "apply":
                            print(f"   [-] Skipped by user: {job_title} @ {comp_name}")
                            log_application_event(job_title, comp_name, location, link, "skipped_by_user")
                            continue
                        print(f"   [+] Grace period elapsed, auto-applying: {job_title} @ {comp_name}")

                        job_page = await context.new_page()
                        await job_page.goto(link, timeout=30000)
                        await human_delay(3, 5)

                        if await is_challenge_page(job_page):
                            print(f"   [!] Akamai human-verification challenge detected on job page "
                                  f"({job_title} @ {comp_name}). Aborting this run early.")
                            alert_challenge_detected(config["email_target"])
                            await job_page.close()
                            await context.close()
                            await browser.close()
                            return

                        apply_btn = job_page.locator("button:has-text('Apply')").first
                        if await apply_btn.count() > 0:
                            btn_text = await apply_btn.inner_text()
                            if "company site" not in btn_text.lower():
                                await apply_btn.click()
                                print(f"   [+] Processed direct apply submission at: {comp_name}")
                                await human_delay(2, 4)
                                filled_fields = await handle_application_form(
                                    job_page, config, job_title, comp_name, jd_text=job_desc_snippet
                                )
                                send_application_confirmation_email(
                                    config["email_target"], job_title, comp_name, location, link, filled_fields, config
                                )
                                print(f"   [+] Confirmation email sent (applied): {job_title} @ {comp_name}")
                                record_stat("applied")
                                log_application_event(job_title, comp_name, location, link, "applied")
                                send_cold_email_if_possible(job_title, comp_name, job_desc_snippet, config, cold_log)
                                if filled_fields:
                                    for field, value in filled_fields.items():
                                        print(f"       - {field}: {value}")
                            else:
                                # Naukri redirects this job to the company's own careers
                                # site instead of a native in-app Apply flow - the bot
                                # can't submit it, so notify immediately instead of
                                # silently dropping it.
                                print(f"   [-] Rejected (external company-site apply not supported): {job_title} @ {comp_name}")
                                send_application_rejected_email(
                                    config["email_target"], job_title, comp_name, location, link,
                                    reason="This job redirects to the company's own external career site, "
                                           "which the bot cannot auto-apply to. Please apply manually if interested."
                                )
                                record_stat("rejected")
                                log_application_event(job_title, comp_name, location, link, "rejected_external_site")
                        else:
                            print(f"   [-] Rejected (no Apply button found): {job_title} @ {comp_name}")
                            send_application_rejected_email(
                                config["email_target"], job_title, comp_name, location, link,
                                reason="No Apply button could be found on the job page (listing may be expired, "
                                       "already applied elsewhere, or the page layout changed)."
                            )
                            record_stat("rejected")
                            log_application_event(job_title, comp_name, location, link, "rejected_no_apply_button")
                        await job_page.close()
                    except Exception as err:
                        print(f"   [-] Processing issue on unique post: {err}")
                        try:
                            send_application_rejected_email(
                                config["email_target"],
                                locals().get("job_title", "Unknown Title"),
                                locals().get("comp_name", "Unknown Company"),
                                location,
                                locals().get("link", "N/A"),
                                reason=f"The bot hit an error while trying to apply: {err}"
                            )
                            record_stat("rejected")
                            log_application_event(
                                locals().get("job_title", "Unknown Title"),
                                locals().get("comp_name", "Unknown Company"),
                                location,
                                locals().get("link", "N/A"),
                                "error",
                                note=str(err),
                            )
                        except Exception as notify_err:
                            print(f"   [-] Could not send rejection email either: {notify_err}")
        # Persist any refreshed cookies/tokens back to the portable session file
        # so the next run picks up the latest state.
        await context.storage_state(path=STORAGE_STATE_PATH)
        await context.close()
        await browser.close()

if __name__ == "__main__":
    asyncio.run(run_auto_apply())
