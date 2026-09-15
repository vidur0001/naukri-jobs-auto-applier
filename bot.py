import asyncio
import random
import os
import json
from playwright.async_api import async_playwright

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
USER_DATA_DIR = os.getenv("USER_DATA_DIR", os.path.join(BASE_DIR, "naukri_profile"))
CONFIG_PATH = os.getenv("CONFIG_PATH", os.path.join(BASE_DIR, "config.json"))

def load_config():
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)

async def human_delay(min_sec=2, max_sec=5):
    await asyncio.sleep(random.uniform(min_sec, max_sec))

async def handle_application_form(page, config):
    try:
        form_container = page.locator(".chatbot-container, .apply-form, .modal-content, .questionnaire-container").first
        if await form_container.count() > 0:
            print("   [!] Custom form dialogue encountered. Matching fields...")
            inputs = await page.locator("input[type='text'], textarea").all()
            for inp in inputs:
                label = (await inp.evaluate("el => el.placeholder || el.getAttribute('name') || ''")).lower()
                
                if "ctc" in label or "salary" in label:
                    if "expected" in label:
                        await inp.fill(config["questionnaire_answers"]["expected_ctc"])
                    else:
                        await inp.fill(config["questionnaire_answers"]["current_ctc"])
                elif "notice" in label:
                    await inp.fill(config["questionnaire_answers"]["notice_period"])
                elif "skill" in label:
                    await inp.fill(config["questionnaire_answers"]["skills"][:100])
            
            submit_btn = page.locator("button:has-text('Submit'), button:has-text('Confirm'), .save-button").first
            if await submit_btn.count() > 0:
                await submit_btn.click()
                print("   [+] Questionnaire dynamic values dispatched.")
    except Exception as e:
        print(f"   [X] Error resolving multi-layer fields framework: {e}")

async def run_auto_apply():
    config = load_config()
    async with async_playwright() as p:
        # Crucial configuration switches for execution stability inside Linux Containers
        context = await p.chromium.launch_persistent_context(
            user_data_dir=USER_DATA_DIR,
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"],
            viewport={"width": 1280, "height": 800}
        )
        page = await context.new_page()

        await page.goto("https://naukri.com")
        await human_delay(3, 5)

        if await page.locator("a:has-text('Login')").count() > 0:
            print("[X] Execution Halted: The Docker profile token has expired. Please run mapping setup locally.")
            await context.close()
            return

        print("[+] Token storage authenticated. Launching systematic sequence processing...")

        for location in config["filters"]["locations"]:
            for role in config["target_roles"]:
                print(f"\n⚡ BATCH: Scanning for {role} vacancies inside {location.upper()}...")
                formatted_role = role.lower().replace(" ", "-")
                query_param = role.replace(" ", "%20")
                
                # Generates dynamic URL mapping target structures safely across Indian metropolitan nodes
                search_url = f"https://naukri.com/{formatted_role}-jobs-in-{location}?k={query_param}&experience={config['filters']['experience_years']}"
                await page.goto(search_url)
                await human_delay(4, 6)

                job_tuples = await page.locator(".srp-jobtuple").all()
                for card in job_tuples[:10]:  # Limit top 10 items per cycle array to preserve behavioral integrity
                    try:
                        comp_loc = card.locator("a.comp-name")
                        if await comp_loc.count() > 0:
                            comp_name = await comp_loc.first.inner_text()
                            if any(bc.lower() in comp_name.lower() for bc in config["blacklist_companies"]):
                                continue

                        title_loc = card.locator("a.title")
                        if await title_loc.count() > 0:
                            link = await title_loc.first.attribute_value("href")
                            
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
                                    await handle_application_form(job_page, config)
                            await job_page.close()
                    except Exception as err:
                        print(f"   [-] Processing issue on unique post: {err}")
        await context.close()

if __name__ == "__main__":
    asyncio.run(run_auto_apply())
