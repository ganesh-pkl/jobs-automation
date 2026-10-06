"""
Hirist.tech Apply script — High-Accuracy Direct Apply Engine with Multi-Step Screening Support.
Requires session_hirist.json from login_capture.py.

Usage:
    python hirist_apply.py
    python hirist_apply.py --limit 5
"""
import argparse
import csv
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from common.profile import Profile
from common.answers import get_screening_answer, is_sensitive_field
from common.human_input import ask_user
from common.external_tracker import log_external_job
from common import stats_tracker

SESSION_FILE = "session_hirist.json"
LOG_FILE = "applications_log.csv"


def log_row(row: list):
    """Appends an application attempt to applications_log.csv in a process-safe manner."""
    new_file = not Path(LOG_FILE).exists()
    if new_file:
        Path(LOG_FILE).touch(mode=0o600)
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        try:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        except Exception:
            pass
        try:
            w = csv.writer(f)
            if new_file and f.tell() == 0:
                w.writerow(["timestamp", "source", "title", "company", "status", "reason"])
            w.writerow(row)
            f.flush()
        finally:
            try:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass


def _job_key(title: str | None, company: str | None) -> tuple[str, str]:
    return (
        " ".join((title or "").lower().split()),
        " ".join((company or "").lower().split()),
    )


def load_applied_job_keys(path: str = LOG_FILE) -> set[tuple[str, str]]:
    log_path = Path(path)
    if not log_path.exists():
        return set()
    try:
        with log_path.open(newline="", encoding="utf-8") as f:
            return {
                _job_key(row.get("title"), row.get("company"))
                for row in csv.DictReader(f)
                if row.get("status") == "applied"
            }
    except (OSError, csv.Error):
        return set()


def handle_hirist_screening(job_page, profile: Profile, title: str, company: str) -> bool:
    """
    Handles Hirist screening forms and multi-step questionnaires.
    Loops through form steps (Next -> Submit) and fills all inputs, radios, and selects.
    """
    job_context = f"{title} at {company}"
    max_steps = 5

    for step in range(max_steps):
        time.sleep(1.5)
        
        # Check if already applied or navigated away from screening
        page_url = job_page.url.lower()
        page_text = ""
        try:
            page_text = job_page.locator("body").inner_text(timeout=1000).lower()
        except Exception:
            pass

        if "/job/applied" in page_url or any(w in page_text for w in ["applied successfully", "application submitted", "thank you for applying"]):
            return True

        # 1. Answer text inputs & textareas
        try:
            inputs = job_page.locator("textarea, input[type='text'], input[type='number'], input:not([type]):not([hidden])").all()
            for inp in inputs:
                if not inp.is_visible() or inp.is_disabled():
                    continue
                curr = inp.input_value()
                if curr and len(curr.strip()) > 0:
                    continue

                label = ""
                try:
                    # Look for preceding label or parent question text
                    label_el = inp.locator("xpath=../../preceding-sibling::* | ../preceding-sibling::* | preceding-sibling::* | ../label | ancestor::div[contains(@class, 'question') or contains(@class, 'form')][1]").first
                    if label_el.count() > 0:
                        label = label_el.inner_text().strip()
                except Exception:
                    pass

                if not label:
                    label = inp.get_attribute("placeholder") or inp.get_attribute("aria-label") or inp.get_attribute("name") or "Question"

                if is_sensitive_field(label):
                    ans = ask_user(f"Hirist screening asks sensitive question:\n{label}")
                else:
                    ans = get_screening_answer(label, profile, job_context)

                if ans:
                    inp.scroll_into_view_if_needed()
                    inp.fill(str(ans))
                    time.sleep(0.3)
                    print(f"    Auto-filled '{label[:30]}': {str(ans)[:30]}")
        except Exception as e:
            print(f"    (Note on Hirist text fields: {e})")

        # 2. Answer Radio Buttons
        try:
            question_blocks = job_page.locator(".question-text, .form-group, div[class*='question' i]").all()
            for q_block in question_blocks:
                if not q_block.is_visible():
                    continue
                q_text = q_block.inner_text().strip()
                container = q_block.locator("xpath=..")
                
                radios = container.locator("input[type='radio']").all()
                if radios:
                    ans = get_screening_answer(f"Question: {q_text}. Options: Yes, No.", profile, job_context)
                    if ans and "yes" in str(ans).lower():
                        yes_r = container.locator("label:has-text('Yes'), span:has-text('Yes'), input[type='radio'][value*='yes' i]").first
                        if yes_r.is_visible():
                            yes_r.click(force=True)
                            print(f"    Auto-selected 'Yes' for '{q_text[:35]}'")
                    elif ans and "no" in str(ans).lower():
                        no_r = container.locator("label:has-text('No'), span:has-text('No'), input[type='radio'][value*='no' i]").first
                        if no_r.is_visible():
                            no_r.click(force=True)
                            print(f"    Auto-selected 'No' for '{q_text[:35]}'")
                    else:
                        first_r = radios[0]
                        if first_r.is_visible():
                            first_r.click(force=True)
        except Exception:
            pass

        # 3. Answer Select Dropdowns
        try:
            selects = job_page.locator("select").all()
            for sel in selects:
                if sel.is_visible():
                    opts = sel.locator("option").all()
                    if len(opts) > 1:
                        sel.select_option(index=1)
                        time.sleep(0.2)
        except Exception:
            pass

        # 4. Click Next / Save & Next / Submit / Apply Action Button
        action_selectors = [
            "button:has-text('Next')",
            "button:has-text('Next Step')",
            "button:has-text('Save & Next')",
            "button:has-text('Submit Application')",
            "button:has-text('Submit')",
            "button:has-text('Apply')",
            "button:has-text('Save & Continue')",
            "button:has-text('Proceed')",
            "button:has-text('Confirm')",
            "input[type='submit']",
            "button[type='submit']",
            "button.btn-primary",
            "button[class*='primary' i]"
        ]

        action_clicked = False
        for sel in action_selectors:
            btns = job_page.locator(sel).all()
            for btn in btns:
                if btn.is_visible() and not btn.is_disabled():
                    btn_text = btn.inner_text().strip()
                    print(f"    Clicking Hirist action: '{btn_text}' (Step {step + 1})...")
                    try:
                        btn.scroll_into_view_if_needed()
                        time.sleep(0.2)
                        btn.click(timeout=2500, force=True)
                        time.sleep(2)
                        action_clicked = True
                        break
                    except Exception:
                        try:
                            btn.evaluate("el => el.click()")
                            time.sleep(2)
                            action_clicked = True
                            break
                        except Exception:
                            pass
            if action_clicked:
                break

        if not action_clicked:
            break

    return True


def run(limit: int | None = None):
    profile = Profile.load()
    roles = profile.target_roles
    platform_threshold = int(profile.data.get("hirist_limit", profile.data.get("hirist_run_limit", profile.data.get("hirist_daily_limit", 30))))
    target_limit = limit if limit is not None else platform_threshold
    run_limit = min(target_limit, int(profile.stop_after_n_applications or 100))
    print(f"Hirist session starting (Platform threshold: {platform_threshold} | Target this run: {run_limit})")
    applied = 0
    applied_keys = load_applied_job_keys()
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=profile.browser_mode == "headless")
        context = browser.new_context(storage_state=SESSION_FILE)
        page = context.new_page()
        
        for role in roles:
            if applied >= run_limit:
                break
            slug = role.lower().replace(".", "").replace(" ", "-")
            
            for page_no in range(1, 4):
                if applied >= run_limit:
                    break
                url = (
                    f"https://www.hirist.tech/search/{slug}-jobs"
                    if page_no == 1
                    else f"https://www.hirist.tech/search/{slug}-jobs-page-{page_no}"
                )
                
                print(f"\n--- Searching Hirist: {role} (Page {page_no}) ---")
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=20000)
                except PWTimeout:
                    print("  (Navigation timeout, continuing...)")
                
                time.sleep(2)
                
                # Scroll down to load dynamic cards
                try:
                    for _ in range(3):
                        page.evaluate("window.scrollBy(0, 1200)")
                        time.sleep(0.5)
                except Exception:
                    pass
                
                # Hirist wraps job cards in anchor tags containing /j/
                card_locators = page.locator("a[href*='/j/']").all()
                print(f"Found {len(card_locators)} jobs on page {page_no}.")
                if len(card_locators) == 0:
                    break
                
                for card in card_locators:
                    if applied >= target_limit:
                        break
                    href = card.get_attribute("href")
                    text = card.inner_text().strip()
                    if not text or not href:
                        continue
                        
                    lines = [line.strip() for line in text.split('\n') if line.strip()]
                    title = lines[0]
                    company = "Unknown"
                    exp_text = ""
                    posted_text = ""
                    
                    for line in lines:
                        if " yrs" in line.lower() or " yr" in line.lower():
                            exp_text = line
                        if "posted " in line.lower():
                            posted_text = line.lower()
                        if "@ " in line:
                            company = line.split("@")[-1].strip()
                    
                    stats_tracker.record_discovered()
                    if _job_key(title, company) in applied_keys:
                        stats_tracker.record_previously_applied_skipped()
                        print(f"Skipped: {title} @ {company} — already applied in an earlier run")
                        continue
                    
                    # Check experience limits
                    if exp_text:
                        digits = [int(s) for s in exp_text.lower().replace("yrs", "").replace("yr", "").replace("-", " ").split() if s.isdigit()]
                        if len(digits) >= 2:
                            lo, hi = digits[0], digits[-1]
                            if hi < profile.seniority_floor_years or lo > profile.seniority_ceiling_years:
                                print(f"Skipped: {title} @ {company} (Experience range mismatch: {exp_text})")
                                continue
                        elif len(digits) == 1:
                            if digits[0] > profile.seniority_ceiling_years:
                                print(f"Skipped: {title} @ {company} (Experience '{digits[0]}+ yrs' exceeds {profile.seniority_ceiling_years} yrs ceiling)")
                                continue
                                
                    # Check job age
                    if posted_text:
                        age_days = 0
                        nums = [int(s) for s in posted_text.split() if s.isdigit()]
                        num = nums[0] if nums else 0
                        if "week" in posted_text:
                            age_days = num * 7
                        elif "month" in posted_text:
                            age_days = num * 30
                        elif "day" in posted_text:
                            age_days = num
                        
                        if age_days > profile.job_freshness_days:
                            print(f"Skipped: {title} @ {company} (Job is too old: {posted_text.split('@')[0].strip()})")
                            continue
                    
                    # Filter by keywords
                    required_keywords = profile.role_required_keywords.get(role, [])
                    if required_keywords:
                        title_lower = title.lower()
                        if not any(kw.lower() in title_lower for kw in required_keywords):
                            print(f"Skipped: {title} @ {company} (Title doesn't match keywords)")
                            continue
                    
                    print(f"\nEvaluating: {title} @ {company}")
                    
                    # Navigate to the job page
                    job_url = href if href.startswith("http") else f"https://www.hirist.tech{href}"
                    job_page = context.new_page()
                    try:
                        job_page.goto(job_url, wait_until="domcontentloaded", timeout=15000)
                        time.sleep(2)
                        
                        apply_btn = job_page.locator("button:has-text('Apply'), a:has-text('Apply')").first
                        if apply_btn.count() > 0:
                            btn_text = apply_btn.inner_text().strip().lower()
                            if "company website" in btn_text or "external" in btn_text:
                                print("  External application. Logging to CSV.")
                                log_row([datetime.now().isoformat(), "hirist", title, company, "skipped", "external link"])
                                log_external_job("hirist", title, company, job_url, job_page.url, "", exp_text, posted_text)
                            else:
                                print("  Found Apply button! Clicking...")
                                apply_btn.click()
                                time.sleep(2)
                                
                                # Handle screening questionnaire (multi-step) if present
                                handle_hirist_screening(job_page, profile, title, company)
                                
                                # Check if successfully applied
                                time.sleep(2)
                                page_text = job_page.locator("body").inner_text(timeout=1000).lower()
                                if "/job/applied" in job_page.url or "applied" in page_text or "application submitted" in page_text:
                                    applied += 1
                                    applied_keys.add(_job_key(title, company))
                                    print(f"  ✅ Successfully applied: {title} @ {company} ({applied} total this run)")
                                    log_row([datetime.now().isoformat(), "hirist", title, company, "applied", "success"])
                                    time.sleep(profile.min_delay_seconds_between_applications)
                                else:
                                    print("  ❓ Clicked apply, but couldn't verify success. Logging attempt.")
                                    log_row([datetime.now().isoformat(), "hirist", title, company, "unknown", "could not verify success"])
                        else:
                            print("  No apply button found. Already applied?")
                            log_row([datetime.now().isoformat(), "hirist", title, company, "skipped", "no apply button"])
                            
                    except Exception as e:
                        print(f"Error evaluating job: {e}")
                    finally:
                        if not job_page.is_closed():
                            job_page.close()
                            
        print(f"\nDone. {applied} Hirist applications submitted this run.")
        browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hirist Job Application Automation")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of applications to submit")
    args = parser.parse_args()
    run(limit=args.limit)
