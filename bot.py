import asyncio
import random
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
APP_BASE_URL = os.getenv("APP_BASE_URL", "http://localhost:5000")
DECISION_POLL_INTERVAL = 3      # seconds between checks for your response
SKIP_GRACE_PERIOD = 45          # seconds to allow a "Skip" click before auto-applying
AUTO_APPLY_PAUSE_FLAG = os.path.join(BASE_DIR, "auto_apply_paused.flag")

os.makedirs(DECISIONS_DIR, exist_ok=True)

def auto_apply_paused():
    return os.path.exists(AUTO_APPLY_PAUSE_FLAG)

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
    decide_skip = f"{APP_BASE_URL}/decide/{job_id}?action=skip"
    pause_url = f"{APP_BASE_URL}/auto-apply/pause"
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

async def handle_application_form(page, config, job_title="", comp_name=""):
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
                    value = config["questionnaire_answers"]["skills"][:100]
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
            await context.close()
            await browser.close()
            return

        print("[+] Token storage authenticated. Launching systematic sequence processing...")

        for location in config["filters"]["locations"]:
            for role in config["target_roles"]:
                print(f"\n⚡ BATCH: Scanning for {role} vacancies inside {location.upper()}...")
                formatted_role = role.lower().replace(" ", "-")
                query_param = role.replace(" ", "%20")
                
                # Generates dynamic URL mapping target structures safely across Indian metropolitan nodes
                search_url = f"https://www.naukri.com/{formatted_role}-jobs-in-{location}?k={query_param}&experience={config['filters']['experience_years']}"

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
                            continue
                        print(f"   [+] Grace period elapsed, auto-applying: {job_title} @ {comp_name}")

                        job_page = await context.new_page()
                        await job_page.goto(link, timeout=30000)
                        await human_delay(3, 5)

                        apply_btn = job_page.locator("button:has-text('Apply')").first
                        if await apply_btn.count() > 0:
                            btn_text = await apply_btn.inner_text()
                            if "company site" not in btn_text.lower():
                                await apply_btn.click()
                                print(f"   [+] Processed direct apply submission at: {comp_name}")
                                await human_delay(2, 4)
                                filled_fields = await handle_application_form(job_page, config, job_title, comp_name)
                                send_application_confirmation_email(
                                    config["email_target"], job_title, comp_name, location, link, filled_fields, config
                                )
                                print(f"   [+] Confirmation email sent for: {job_title} @ {comp_name}")
                                record_stat("applied")
                                if filled_fields:
                                    for field, value in filled_fields.items():
                                        print(f"       - {field}: {value}")
                        await job_page.close()
                    except Exception as err:
                        print(f"   [-] Processing issue on unique post: {err}")
        # Persist any refreshed cookies/tokens back to the portable session file
        # so the next run picks up the latest state.
        await context.storage_state(path=STORAGE_STATE_PATH)
        await context.close()
        await browser.close()

if __name__ == "__main__":
    asyncio.run(run_auto_apply())
