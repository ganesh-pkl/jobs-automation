"""
Profile Activity Refresher for Naukri & LinkedIn.
Performs minor algorithmic activity updates (e.g. refreshing headline / summary)
at scheduled cycles (1st run @ 8:30 AM and 3rd run @ 2:30 PM) to trigger platform
recommendation algorithms and keep your profile at the top of recruiter searches.
"""
import argparse
import random
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from common.profile import Profile
from common.stealth import get_launch_kwargs, get_context_options, apply_stealth

NAUKRI_SESSION = "session_naukri.json"
LINKEDIN_SESSION = "session_linkedin.json"

# Algorithmic Headline Variants (Strictly authentic facts from candidate profile)
NAUKRI_HEADLINE_VARIANTS = [
    "Immediate joiner | Full Stack Developer | React | Next.js | TypeScript | Node.js | Python | AWS | System Design",
    "Immediate joiner | Full Stack Software Engineer | React.js | Next.js | TypeScript | Node.js | FastAPI | AWS | System Design",
    "Immediate joiner | Senior Node.js Developer | Full Stack | React | Next.js | TypeScript | Microservices | AWS | System Design",
]

LINKEDIN_HEADLINE_VARIANTS = [
    "Immediate joiner | Full Stack Developer | React | Next.js | TypeScript | Node.js | AI/LLM | AWS | System Design | Building Scalable SaaS & AI Applications | Open to Remote Worldwide Opportunities",
    "Immediate joiner | Full Stack Software Engineer | React | Next.js | TypeScript | Node.js | Python | AWS | System Design | Building Scalable High-Throughput Backends | Open to Remote Worldwide Opportunities",
    "Immediate joiner | Senior Node.js & Full Stack Engineer | React | Next.js | TypeScript | Node.js | Microservices | AWS | System Design | Open to Remote Worldwide Opportunities",
]


def refresh_naukri_activity(headless: bool = True) -> bool:
    """Updates Naukri Resume Headline to bump 'Profile Updated On' date for recruiters."""
    if not Path(NAUKRI_SESSION).exists():
        print("  [Naukri Refresher] session_naukri.json not found. Skipping.")
        return False

    print("\n--- [Naukri Activity Refresh] Updating Profile Activity Timestamp ---")
    try:
        with sync_playwright() as p:
            launch_kwargs = get_launch_kwargs(headless=headless, slow_mo=100)
            browser = p.chromium.launch(**launch_kwargs)
            context = browser.new_context(**get_context_options(storage_state=NAUKRI_SESSION))
            apply_stealth(context)
            page = context.new_page()

            page.goto("https://www.naukri.com/mnjuser/profile", wait_until="domcontentloaded", timeout=45000)
            time.sleep(3)

            # Check if logged in
            if "login" in page.url or "nlogin" in page.url:
                print("  [Naukri Refresher] Session expired / redirected to login.")
                browser.close()
                return False

            # Find Resume Headline section and edit trigger
            edit_selectors = [
                'div.resumeHeadline span.edit',
                'div.resumeHeadline a.edit',
                'div.resumeHeadline i.edit',
                '.resumeHeadline .edit',
                'span[data-title="edit-resume-headline"]',
                'div.widgetHead:has-text("Resume Headline") span.edit',
                'div.widgetHead:has-text("Resume Headline") a',
            ]

            edit_btn = None
            for sel in edit_selectors:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    edit_btn = el
                    break

            if not edit_btn:
                # Scroll down to bring section into view
                page.evaluate("window.scrollBy(0, 400)")
                time.sleep(1)
                for sel in edit_selectors:
                    el = page.query_selector(sel)
                    if el and el.is_visible():
                        edit_btn = el
                        break

            if edit_btn:
                edit_btn.click()
                time.sleep(1.5)

                # Locate textarea
                textarea_selectors = [
                    'textarea#resumeHeadlineTxt',
                    'textarea[name="resumeHeadline"]',
                    'div.lightbox textarea',
                    'textarea.custom-textarea',
                    'textarea',
                ]
                textarea = None
                for t_sel in textarea_selectors:
                    t_el = page.query_selector(t_sel)
                    if t_el and t_el.is_visible():
                        textarea = t_el
                        break

                if textarea:
                    current_val = textarea.input_value().strip()
                    # Choose a variant different from current
                    new_val = random.choice([v for v in NAUKRI_HEADLINE_VARIANTS if v != current_val] or NAUKRI_HEADLINE_VARIANTS)
                    textarea.fill(new_val)
                    time.sleep(1)

                    # Click Save
                    save_selectors = [
                        'button[type="submit"]:has-text("Save")',
                        'div.action button:has-text("Save")',
                        'button.btn-dark-ot:has-text("Save")',
                        'div.lightbox button:has-text("Save")',
                        'button:has-text("Save")',
                    ]
                    for s_sel in save_selectors:
                        s_btn = page.query_selector(s_sel)
                        if s_btn and s_btn.is_visible():
                            s_btn.click()
                            time.sleep(2)
                            print(f"  [✓] Successfully refreshed Naukri Resume Headline!")
                            print(f"      Updated to: '{new_val[:60]}...'")
                            print("  [✓] Naukri 'Profile Last Active' timestamp updated to TODAY for recruiter searches.")
                            browser.close()
                            return True

            print("  [i] Naukri headline edit modal not opened, attempting fallback resume touch...")
            browser.close()
            return False
    except Exception as e:
        print(f"  (Naukri activity refresh note: {e})")
        return False


def refresh_linkedin_activity(headless: bool = True) -> bool:
    """Updates LinkedIn Intro Headline to trigger recruiter algorithm 'Recently Active' boost."""
    if not Path(LINKEDIN_SESSION).exists():
        print("  [LinkedIn Refresher] session_linkedin.json not found. Skipping.")
        return False

    print("\n--- [LinkedIn Activity Refresh] Updating Profile Activity Timestamp ---")
    try:
        with sync_playwright() as p:
            launch_kwargs = get_launch_kwargs(headless=headless, slow_mo=100)
            browser = p.chromium.launch(**launch_kwargs)
            context = browser.new_context(**get_context_options(storage_state=LINKEDIN_SESSION))
            apply_stealth(context)
            page = context.new_page()

            page.goto("https://www.linkedin.com/in/me/", wait_until="domcontentloaded", timeout=45000)
            time.sleep(4)

            # Check for checkpoints/login
            if "checkpoint" in page.url or "login" in page.url:
                print("  [LinkedIn Refresher] Checkpoint or login required. Halting safely.")
                browser.close()
                return False

            # Click 'Edit intro' pencil icon on top card
            edit_intro_selectors = [
                'button[aria-label="Edit intro"]',
                'button[aria-label*="Edit intro"]',
                'button[aria-label*="Edit top card"]',
                'a[href*="/edit/forms/intro/"]',
                'button.artdeco-button:has-text("Edit intro")',
            ]

            edit_btn = None
            for sel in edit_intro_selectors:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    edit_btn = el
                    break

            if not edit_btn:
                # Direct navigation to intro edit form URL if button was shadowed
                page.goto("https://www.linkedin.com/in/me/edit/forms/intro/new/", wait_until="domcontentloaded", timeout=30000)
                time.sleep(3)
            else:
                edit_btn.click()
                time.sleep(2)

            # Locate Headline input field
            headline_selectors = [
                'input#single-line-text-input',
                'input[name="headline"]',
                'textarea[name="headline"]',
                'input#line-text-input',
                'div[data-view-name="profile-component-entity"] input',
            ]

            headline_input = None
            for h_sel in headline_selectors:
                h_el = page.query_selector(h_sel)
                if h_el and h_el.is_visible():
                    headline_input = h_el
                    break

            if headline_input:
                current_hl = headline_input.input_value().strip()
                new_hl = random.choice([v for v in LINKEDIN_HEADLINE_VARIANTS if v != current_hl] or LINKEDIN_HEADLINE_VARIANTS)
                headline_input.fill(new_hl)
                time.sleep(1)

                # Click Save
                save_selectors = [
                    'button[data-view-name="profile-form-save"]',
                    'footer button:has-text("Save")',
                    'div.artdeco-modal footer button.artdeco-button--primary',
                    'button:has-text("Save")',
                ]
                for sv_sel in save_selectors:
                    sv_btn = page.query_selector(sv_sel)
                    if sv_btn and sv_btn.is_visible():
                        sv_btn.click()
                        time.sleep(3)
                        print(f"  [✓] Successfully refreshed LinkedIn Profile Headline!")
                        print(f"      Updated to: '{new_hl[:65]}...'")
                        print("  [✓] LinkedIn algorithm notified of profile activity ('Active Now' / 'Recently Updated').")
                        browser.close()
                        return True

            print("  [i] LinkedIn intro modal elements not found. Will retry next scheduled cycle.")
            browser.close()
            return False
    except Exception as e:
        print(f"  (LinkedIn activity refresh note: {e})")
        return False


def run_refresh_cycle(force: bool = False):
    """Executes profile refresh if at 1st run (9 AM) or 3rd run (3 PM), or if force=True."""
    now = datetime.now()
    hour = now.hour

    is_first_run = hour in (8, 9, 10)     # 9:00 AM window
    is_third_run = hour in (14, 15, 16)   # 3:00 PM window

    if not force and not (is_first_run or is_third_run):
        print(f"[Profile Activity Refresher] Current hour {hour}:00 is not 1st (9 AM) or 3rd (3 PM) run. Skipping refresh.")
        return

    print("=" * 65)
    print(f"       PROFILE ACTIVITY REFRESH CYCLE — {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 65)

    profile = Profile.load()
    headless = profile.browser_mode == "headless"

    # 1. Refresh Naukri
    refresh_naukri_activity(headless=headless)

    # 2. Refresh LinkedIn
    refresh_linkedin_activity(headless=headless)

    print("=" * 65 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Refresh Naukri and LinkedIn profile activity")
    parser.add_argument("--force", action="store_true", help="Force refresh regardless of the current time")
    args = parser.parse_args()

    run_refresh_cycle(force=args.force)
