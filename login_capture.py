"""
Remote, noVNC-driven Naukri login/session capture.

Unlike login_setup.py (which must be run locally, interactively, via a
terminal), this script is spawned as a background subprocess by app.py's
/login/start route. It opens a real headed Chromium window on the
container's shared Xvfb display (DISPLAY=:99) - the same display x11vnc/
noVNC already streams for the Akamai challenge-solver - so you can log in
to Naukri by watching/clicking through the live browser feed in your
browser, exactly like solving a challenge.

Flow:
  1. app.py spawns this script and serves a noVNC iframe page.
  2. You log in to Naukri manually inside that iframe (handles OTP/CAPTCHA
     naturally since it's a real browser, no credentials ever touch the
     server).
  3. You click "Save Session" in the page, which makes app.py write the
     SAVE flag file below.
  4. This script notices the flag, saves storage_state.json, and exits.

No password is ever typed into, stored by, or transmitted through the
Flask backend - only the resulting session cookies (already how bot.py's
saved session works) are written to disk.
"""
import asyncio
import os
import time
from playwright.async_api import async_playwright

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STORAGE_STATE_PATH = os.getenv("STORAGE_STATE_PATH", os.path.join(BASE_DIR, "naukri_storage_state.json"))
LOGIN_SAVE_FLAG = os.getenv("LOGIN_SAVE_FLAG", os.path.join(BASE_DIR, "login_save.flag"))
# Safety net: exit and clean up automatically if nobody clicks "Save
# Session" within this window, so a forgotten tab doesn't leave a browser
# (and the display) tied up indefinitely.
TIMEOUT_SECONDS = int(os.getenv("LOGIN_CAPTURE_TIMEOUT", "600"))


async def main():
    # Clear any stale flag from a previous run before we start waiting.
    if os.path.exists(LOGIN_SAVE_FLAG):
        os.remove(LOGIN_SAVE_FLAG)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
        )
        # Same fingerprint (viewport/timezone/locale) bot.py replays the
        # saved session with, so Naukri treats it as the same "device".
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            timezone_id="Asia/Kolkata",
            locale="en-IN",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        page = await context.new_page()
        await page.goto("https://www.naukri.com/nlogin/login")
        print("[+] Login capture browser opened. Waiting for you to log in and click Save Session...")

        waited = 0
        poll_interval = 1
        while waited < TIMEOUT_SECONDS:
            if os.path.exists(LOGIN_SAVE_FLAG):
                os.remove(LOGIN_SAVE_FLAG)

                # Verify login actually succeeded before saving - clicking
                # "Save Session" too early (e.g. mid-OTP, or Akamai still
                # showing a challenge) would otherwise silently persist a
                # logged-out/invalid session that bot.py immediately rejects
                # as "expired" on its very next run. Same check login_setup.py
                # uses for its local/interactive flow.
                try:
                    current_url = page.url
                    still_on_login = "login" in current_url.lower()
                    profile_count = await page.locator(
                        "[class*='nI-gNb-drawer'], [class*='user-name'], #root_drawerBtn"
                    ).count()
                except Exception:
                    print("[!] Browser closed unexpectedly while checking login state.")
                    await context.close()
                    await browser.close()
                    return

                if still_on_login or profile_count == 0:
                    print(f"[!] Save Session clicked but login doesn't look complete yet "
                          f"(URL: {current_url}). Ignoring this click - finish logging in "
                          f"and click Save Session again.")
                    continue

                # Give cookies/localStorage a moment to flush before capturing.
                await page.wait_for_timeout(1500)
                await context.storage_state(path=STORAGE_STATE_PATH)
                print(f"[+] Session saved to {STORAGE_STATE_PATH}.")
                await context.close()
                await browser.close()
                return

            await asyncio.sleep(poll_interval)
            waited += poll_interval

        print("[!] Timed out waiting for Save Session click. Closing without saving.")
        await context.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
