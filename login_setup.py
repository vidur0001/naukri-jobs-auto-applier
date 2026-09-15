"""
One-time interactive Naukri login setup.

Run this locally (NOT inside Docker) before using the bot for the first time,
or whenever your saved session expires:

    python login_setup.py

It opens a real (non-headless) Chromium window using the same persistent
profile directory that bot.py uses. Log into Naukri manually (including any
OTP / CAPTCHA), then come back to this terminal and press Enter to save the
session and close the browser.

IMPORTANT: Use Naukri's direct Email + Password login fields.
Do NOT click "Login with Google" / the Gmail button. A Google-linked session
is bound to this device's fingerprint (IP, browser signals, timezone) — Google
will silently invalidate it the moment the same cookies are replayed from a
different machine/environment (e.g. the Docker container), which is why the
bot reported "token expired" even though the profile files were present.
A direct email/password Naukri session is just a cookie and works from any
device that plays back the same browser fingerprint, which is why this script
matches the viewport/timezone/locale used by bot.py inside Docker.
"""
import asyncio
import os
from playwright.async_api import async_playwright

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
USER_DATA_DIR = os.getenv("USER_DATA_DIR", os.path.join(BASE_DIR, "naukri_profile"))
# Chrome/Chromium encrypts cookies using OS-specific keys (DPAPI on Windows,
# libsecret/plaintext on Linux). A user_data_dir profile created by Windows
# Chromium is therefore unreadable by the Linux Chromium running inside the
# Docker container - cookies "exist" but decrypt to garbage, which looks like
# a logged-out session. To work around this, we export the session as
# Playwright's portable storage_state (plain JSON, no OS encryption), which
# bot.py loads instead of relying on the raw profile directory's cookies.
STORAGE_STATE_PATH = os.getenv("STORAGE_STATE_PATH", os.path.join(BASE_DIR, "naukri_storage_state.json"))


async def main():
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=USER_DATA_DIR,
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
            ],
            # Match bot.py's Docker/Xvfb viewport, timezone and locale exactly so
            # Naukri sees the same "device" fingerprint in both environments.
            viewport={"width": 1280, "height": 800},
            timezone_id="Asia/Kolkata",
            locale="en-IN",
            ignore_default_args=["--enable-automation"],
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        # Hide the navigator.webdriver flag that sites use to detect automation
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        page = await context.new_page()
        await page.goto("https://www.naukri.com/nlogin/login")

        print("\n[*] A browser window has opened.")
        print("[*] IMPORTANT: Log in using the Email + Password fields.")
        print("[*] Do NOT use the 'Login with Google' / Gmail button - that session")
        print("[*] will NOT work once replayed inside the Docker container.")

        while True:
            input("[*] Once you are logged in and see your dashboard (your name/photo visible), press Enter here to verify... ")

            current_url = page.url
            # Naukri redirects logged-in users away from the login page, usually to
            # something like naukri.com/mnjuser/homepage.
            still_on_login = "login" in current_url.lower()
            profile_count = await page.locator(
                "[class*='nI-gNb-drawer'], [class*='user-name'], #root_drawerBtn"
            ).count()

            if still_on_login or profile_count == 0:
                print(f"[!] Doesn't look like you're logged in yet (URL: {current_url}).")
                print("[!] Please finish logging in (including OTP/CAPTCHA if any) and try again.")
                retry = input("[*] Press Enter to check again, or type 'force' to save anyway: ").strip().lower()
                if retry == "force":
                    break
                continue
            else:
                print(f"[+] Login looks confirmed (URL: {current_url}).")
                break

        # Give the browser a moment to flush cookies/local storage to disk
        # before we close the context.
        await page.wait_for_timeout(3000)
        await context.storage_state(path=STORAGE_STATE_PATH)
        await context.close()
        print(f"[+] Session saved to: {USER_DATA_DIR}")
        print(f"[+] Portable session state saved to: {STORAGE_STATE_PATH}")
        print("[+] You can now run the bot normally via app.py / bot.py.")


if __name__ == "__main__":
    asyncio.run(main())
