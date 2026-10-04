"""
Run this once per site (Naukri, LinkedIn) before running the apply scripts.

It opens a real, visible Chromium window. YOU log in by hand — type your
password, complete 2FA, solve any CAPTCHA yourself. Once you're on the
logged-in homepage, come back to this terminal and press Enter. The script
then saves your session (cookies + local storage) to a local file so the
apply scripts can reuse it without ever seeing your password.

Usage:
    python login_capture.py naukri
    python login_capture.py linkedin
"""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

SITES = {
    "naukri": "https://www.naukri.com/nlogin/login",
    "linkedin": "https://www.linkedin.com/login",
    "hirist": "https://www.hirist.tech/",
    "uplers": "https://platform.uplers.com/login",
    "instahyre": "https://www.instahyre.com/login/",
    "foundit": "https://www.foundit.in/auth/login",
    "wellfound": "https://wellfound.com/login",
    "glassdoor": "https://www.glassdoor.co.in/profile/login_input.htm",
    "apna": "https://apna.co/jobs",
}


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in SITES:
        print(f"Usage: python login_capture.py [{'|'.join(SITES)}]")
        sys.exit(1)

    site = sys.argv[1]
    url = SITES[site]
    out_path = f"session_{site}.json"

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )
        page = context.new_page()
        # Naukri and Foundit keep background requests open, so waiting for the full
        # `load` event can time out even when the login page is usable.
        page.goto(url, wait_until="domcontentloaded", timeout=60000)

        print(f"\nA browser window is open at {url}")
        print("Log in by hand: password, 2FA, any CAPTCHA — all of it.")
        input("Once you're on the logged-in homepage, press Enter here to save the session... ")

        context.storage_state(path=out_path)
        Path(out_path).chmod(0o600)
        print(f"Session saved to {out_path}. Keep this file private — it's equivalent to being logged in.")
        browser.close()


if __name__ == "__main__":
    main()
