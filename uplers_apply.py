"""
Uplers Apply script. Requires session_uplers.json from login_capture.py.

Usage:
    python uplers_apply.py
"""
import csv
import re
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from common.profile import Profile
from common import stats_tracker

SESSION_FILE = "session_uplers.json"
LOG_FILE = "applications_log.csv"

def log_row(row: list):
    new_file = not Path(LOG_FILE).exists()
    if new_file:
        Path(LOG_FILE).touch(mode=0o600)
    with open(LOG_FILE, "a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["timestamp", "platform", "job_title", "company", "status", "notes"])
        w.writerow([str(x) for x in row])

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
                _job_key(row.get("title") or row.get("job_title"), row.get("company"))
                for row in csv.DictReader(f)
                if row.get("status") == "applied"
            }
    except (OSError, csv.Error):
        return set()

def count_applications_today() -> int:
    if not Path(LOG_FILE).exists():
        return 0
    today = datetime.now().date()
    count = 0
    with open(LOG_FILE, "r") as f:
        r = csv.reader(f)
        next(r, None)
        for row in r:
            if not row or len(row) < 5:
                continue
            if row[1] == "uplers" and row[4] == "applied":
                try:
                    ts = datetime.fromisoformat(row[0]).date()
                    if ts == today:
                        count += 1
                except ValueError:
                    pass
    return count

def run():
    profile = Profile.load()
    if not Path(SESSION_FILE).exists():
        raise SystemExit(f"{SESSION_FILE} not found. Run: python login_capture.py uplers")

    applied_today = count_applications_today()
    daily_limit = int(profile.data.get("daily_application_limit", 100))
    remaining_today = max(0, daily_limit - applied_today)
    run_success_limit = min(profile.stop_after_n_applications, remaining_today)
    
    if run_success_limit <= 0:
        print(f"Daily application limit reached ({applied_today}/{daily_limit}).")
        return

    applied = 0
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=100)
        context = browser.new_context(storage_state=SESSION_FILE)
        page = context.new_page()

        print("\n--- Starting Uplers Automation ---")
        page.goto("https://platform.uplers.com/talent/all-opportunities/")
        
        try:
            page.wait_for_selector(".jobCardContainer", timeout=15000)
        except PWTimeout:
            print("No job cards loaded or session invalid.")
            browser.close()
            return
            
        time.sleep(3) # Let all cards render

        cards = page.locator(".jobCardContainer")
        count = cards.count()
        print(f"Found {count} jobs on the page.")

        applied_keys = load_applied_job_keys()
        for idx in range(count):
            if applied >= run_success_limit:
                print(f"Reached local limit of {run_success_limit}. Stopping.")
                break
                
            card = cards.nth(idx)
            text = card.inner_text()
            stats_tracker.record_discovered()
            
            # Simple keyword matching for title since text block contains it all
            title = ""
            for role in profile.target_roles:
                req_keywords = profile.role_required_keywords.get(role, [])
                if req_keywords:
                    if any(k.lower() in text.lower() for k in req_keywords):
                        title = f"Matching Job ({role})"
                        break
                else:
                    words = [w.lower() for w in role.split() if len(w) > 2]
                    if any(w in text.lower() for w in words):
                        title = f"Matching Job ({role})"
                        break
                    
            if not title:
                print(f"Skipped: (Title doesn't match required keywords)")
                continue

            if _job_key(title, "uplers") in applied_keys or _job_key(title, "Unknown") in applied_keys:
                stats_tracker.record_previously_applied_skipped()
                print(f"Skipped: {title} — already applied in an earlier run")
                continue

            if text and profile.job_freshness_days:
                text_lower = text.lower()
                if "posted" in text_lower or "ago" in text_lower:
                    age_days = 0
                    nums = [int(s) for s in text_lower.split() if s.isdigit()]
                    num = nums[0] if nums else 0
                    if "month" in text_lower:
                        age_days = num * 30 if num else 30
                    elif "week" in text_lower:
                        age_days = num * 7 if num else 7
                    elif "day" in text_lower:
                        age_days = num if num else 1
                    if age_days > profile.job_freshness_days:
                        print(f"Skipped: {title} (Job is too old)")
                        continue
                
            # Click the card to open right pane
            try:
                card.click()
                time.sleep(2)
            except Exception:
                continue
                
            # Check for Apply button in right pane
            apply_btn = page.locator(".jobDetailSection button:has-text('Apply'), .jobDetailSection button:has-text('Apply Now')").filter(has_text=re.compile(r"^Apply( Now)?$", re.I))
            
            # If not found with class restriction, try globally but only visible ones
            if apply_btn.count() == 0:
                apply_btn = page.locator("button:has-text('Apply'), button:has-text('Apply Now')").filter(has_text=re.compile(r"^Apply( Now)?$", re.I))
            
            # Find the first visible one
            visible_btn = None
            for i in range(apply_btn.count()):
                if apply_btn.nth(i).is_visible():
                    visible_btn = apply_btn.nth(i)
                    break
            
            if visible_btn:
                print("  Found Apply button! Clicking...")
                try:
                    visible_btn.click(timeout=5000)
                    
                    # Wait for Uplers modal to appear
                    try:
                        modal_btn = page.locator("button:has-text('APPLY NOW'), button:has-text('Apply Now')").last
                        modal_btn.wait_for(state="visible", timeout=5000)
                        
                        print("  Detected screening modal! Auto-filling...")
                        from common.human_input import ask_user
                        import common.llm as llm
                        
                        # 1. Fill empty text inputs (like Full name, CTC, Location)
                        # We find all inputs inside the dialog
                        dialog = page.locator("div[role='dialog']").first
                        if not dialog.is_visible():
                            dialog = page
                            
                        from common.answers import get_screening_answer
                        texts = dialog.locator("input[type='text'], input:not([type]), textarea")
                        for i in range(texts.count()):
                            inp = texts.nth(i)
                            if inp.is_visible() and not inp.input_value():
                                try:
                                    lbl = inp.locator("xpath=../../preceding-sibling::label | ../preceding-sibling::label | preceding-sibling::label").first.inner_text()
                                except:
                                    lbl = inp.get_attribute("placeholder") or ""
                                    
                                if not lbl and "name" in (inp.get_attribute("name") or "").lower():
                                    lbl = "Full Name"
                                    
                                ans = get_screening_answer(lbl or "Question", profile, f"{title} at {company}")
                                if ans:
                                    inp.fill(ans)
                                    time.sleep(0.5)
                                    print(f"  Auto-filled field '{lbl[:30]}': {ans[:30]}")
                        
                        # 2. Handle Radio Buttons (like "Are you open to work...")
                        radios = dialog.locator("input[type='radio']")
                        if radios.count() > 0:
                            yes_labels = dialog.locator("label").filter(has_text=re.compile(r"^Yes$", re.I))
                            if yes_labels.count() > 0:
                                yes_labels.first.click()
                                print("  Auto-selected 'Yes' for radio questions.")
                        
                        # 3. Check for errors
                        time.sleep(2)
                        errors = dialog.locator(".error, [class*='error'], [class*='Error']")
                        if errors.count() > 0 and errors.first.is_visible():
                            print("  Uplers form has missing dropdowns or errors.")
                            ask_user("Please select them manually in the browser, then press Enter here.")
                        
                        print("  Clicking APPLY NOW inside modal...")
                        modal_btn.click()
                        time.sleep(3)
                        
                        # Close the modal on top right
                        print("  Closing modal...")
                        page.keyboard.press("Escape")
                        time.sleep(1)
                        # Also try clicking close button if escape didn't work
                        close_btn = page.locator("button[aria-label*='lose'], svg[data-testid='CloseIcon']").first
                        if close_btn.is_visible():
                            close_btn.click()
                        
                    except Exception as e:
                        # Timeout or other error, meaning no modal appeared or it was already handled
                        pass
                        
                    # Log success
                    applied += 1
                    log_row([datetime.now().isoformat(), "uplers", title, "Unknown", "applied", "success"])
                    print(f"Applied: {title} ({applied} total)")
                    time.sleep(profile.min_delay_seconds_between_applications)
                    
                except Exception as e:
                    print(f"  Error clicking apply: {e}")
            else:
                print("  Skipped: No Apply button found (Might be applied already).")

        print("\n[v1.0] Done exploring Uplers for this run!")
        browser.close()

if __name__ == "__main__":
    run()
