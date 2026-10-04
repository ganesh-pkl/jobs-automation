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
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        try:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        except Exception:
            pass
        try:
            w = csv.writer(f)
            if new_file and f.tell() == 0:
                w.writerow(["timestamp", "platform", "job_title", "company", "status", "notes"])
            w.writerow([str(x) for x in row])
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

def fill_uplers_modal(page, profile: Profile, job_title: str) -> bool:
    """Fills all fields in the Uplers screening modal and submits."""
    from datetime import datetime, timedelta
    from common.answers import get_screening_answer

    modal = page.locator(
        ".modal.signupflow.apply, div[role='dialog'], .modal-content, .ReactModal__Content"
    ).first
    try:
        modal.wait_for(state="visible", timeout=6000)
    except Exception:
        return True

    print("  Detected screening modal! Auto-filling...")

    # 1. Total Experience (Years & Months)
    exp_years = int(profile.total_experience_years)
    exp_inputs = modal.locator("input[name='total_experience']")
    if exp_inputs.count() >= 1:
        if not exp_inputs.nth(0).input_value():
            exp_inputs.nth(0).fill(str(exp_years))
    if exp_inputs.count() >= 2:
        if not exp_inputs.nth(1).input_value():
            exp_inputs.nth(1).fill("0")

    # 2. Current CTC & Expected CTC
    curr_ctc_input = modal.locator("#current_ctc, input[name='current_ctc']")
    if curr_ctc_input.count() > 0 and not curr_ctc_input.first.input_value():
        c_ctc = str(profile.current_ctc_lpa) if profile.current_ctc_lpa is not None else "6"
        curr_ctc_input.first.fill(c_ctc)

    exp_ctc_input = modal.locator("#expected_ctc, input[name='expected_ctc']")
    if exp_ctc_input.count() > 0 and not exp_ctc_input.first.input_value():
        e_ctc = str(profile.expected_ctc_lpa) if profile.expected_ctc_lpa is not None else "10"
        exp_ctc_input.first.fill(e_ctc)

    # 3. Date input (Earliest joining date / Last working day for immediate joiner)
    future_date = (datetime.now() + timedelta(days=2)).strftime("%d/%m/%Y")
    date_inputs = modal.locator(
        ".date-input, input[placeholder*='Ex: '], input[placeholder*='/'], input[class*='date']"
    )
    for i in range(date_inputs.count()):
        d_inp = date_inputs.nth(i)
        if d_inp.is_visible():
            try:
                d_inp.click()
                d_inp.fill(future_date)
                d_inp.dispatch_event("input")
                d_inp.dispatch_event("change")
                page.keyboard.press("Escape")
                print(f"  Auto-filled earliest joining date: {future_date}")
            except Exception:
                pass

    # 4. Shift availability radio buttons ("Are you open to work in ... shift?")
    shift_yes = modal.locator(
        "label.radioInput:has(input[name='talent_shift_yes']), input[name='talent_shift_yes'], label:has-text('Yes')"
    )
    if shift_yes.count() > 0 and shift_yes.first.is_visible():
        try:
            shift_yes.first.click()
            print("  Auto-selected 'Yes' for shift availability.")
        except Exception:
            pass

    # 5. Generic screening answer fallback for any remaining text inputs
    text_inputs = modal.locator(
        "input[type='text']:not([name='total_experience']):not(.date-input), textarea"
    )
    for i in range(text_inputs.count()):
        inp = text_inputs.nth(i)
        if inp.is_visible() and not inp.input_value():
            lbl = inp.get_attribute("placeholder") or inp.get_attribute("name") or inp.get_attribute("id") or ""
            ans = get_screening_answer(lbl, profile, job_title)
            if ans:
                inp.fill(ans)
                print(f"  Auto-filled field '{lbl[:30]}': {ans[:30]}")

    time.sleep(1)

    # 6. Click Submit (Input submit or Button)
    submit_btn = modal.locator(
        "input[type='submit'][value*='Apply'], input.primaryBtn.cta, button:has-text('APPLY NOW'), button:has-text('Apply Now'), button[type='submit']"
    ).first
    if submit_btn.is_visible():
        print("  Clicking Submit (Apply Now)...")
        submit_btn.click()
        time.sleep(3)
        page.keyboard.press("Escape")
        time.sleep(0.5)
        close_btn = modal.locator(".modalCloseBtn, button[aria-label*='Close'], button[aria-label*='close']").first
        if close_btn.is_visible():
            close_btn.click()
        return True
    return False


def run(limit: int | None = None):
    profile = Profile.load()
    if not Path(SESSION_FILE).exists():
        raise SystemExit(f"{SESSION_FILE} not found. Run: python login_capture.py uplers")

    applied_today = count_applications_today()
    platform_threshold = int(profile.data.get("uplers_limit", profile.data.get("uplers_run_limit", profile.data.get("uplers_daily_limit", 30))))
    target_limit = limit if limit is not None else platform_threshold
    run_success_limit = min(target_limit, int(profile.stop_after_n_applications or 100))
    print(f"Uplers session starting (Platform threshold: {platform_threshold} | Applied today: {applied_today} | Target this run: {run_success_limit})")

    applied = 0
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=profile.browser_mode == "headless", slow_mo=100)
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
            
            lines = [l.strip() for l in text.splitlines() if l.strip()]
            card_title = lines[0] if lines else "Uplers Opportunity"
            card_company = lines[1] if len(lines) > 1 else "Uplers"

            # Simple keyword matching for title since text block contains it all
            matched = False
            for role in profile.target_roles:
                req_keywords = profile.role_required_keywords.get(role, [])
                if req_keywords:
                    if any(k.lower() in text.lower() for k in req_keywords):
                        matched = True
                        break
                else:
                    words = [w.lower() for w in role.split() if len(w) > 2]
                    if any(w in text.lower() for w in words):
                        matched = True
                        break
                    
            if not matched:
                print(f"Skipped: (Title doesn't match required keywords)")
                continue

            if _job_key(card_title, card_company) in applied_keys or _job_key(card_title, "uplers") in applied_keys:
                stats_tracker.record_previously_applied_skipped()
                print(f"Skipped: {card_title} @ {card_company} — already applied in an earlier run")
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
                        print(f"Skipped: {card_title} (Job is too old)")
                        continue
                
            # Click the card to open right pane
            try:
                card.click()
                time.sleep(2)
            except Exception:
                continue
                
            # Check for Apply button in right pane
            apply_btn = page.locator(".jobDetailSection button:has-text('Apply'), .jobDetailSection button:has-text('Apply Now'), button.applyBtn").filter(has_text=re.compile(r"^Apply( Now)?$", re.I))
            
            if apply_btn.count() == 0:
                apply_btn = page.locator("button:has-text('Apply'), button:has-text('Apply Now'), button.applyBtn").filter(has_text=re.compile(r"^Apply( Now)?$", re.I))
            
            visible_btn = None
            for i in range(apply_btn.count()):
                if apply_btn.nth(i).is_visible():
                    visible_btn = apply_btn.nth(i)
                    break
            
            if visible_btn:
                print("  Found Apply button! Clicking...")
                try:
                    visible_btn.click(timeout=5000)
                    time.sleep(2)
                    
                    fill_uplers_modal(page, profile, card_title)
                        
                    # Log success
                    applied += 1
                    applied_keys.add(_job_key(card_title, card_company))
                    log_row([datetime.now().isoformat(), "uplers", card_title, card_company, "applied", "success"])
                    print(f"Applied: {card_title} @ {card_company} ({applied} total this run)")
                    time.sleep(profile.min_delay_seconds_between_applications)
                    
                except Exception as e:
                    print(f"  Error clicking apply: {e}")
            else:
                print("  Skipped: No Apply button found (Might be applied already).")

        print("\n[v1.0] Done exploring Uplers for this run!")
        browser.close()

if __name__ == "__main__":
    run()
