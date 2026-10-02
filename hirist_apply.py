"""
Hirist.tech Apply script. Requires session_hirist.json from login_capture.py.

Usage:
    python hirist_apply.py
"""
import csv
import re
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from common.profile import Profile
from common import llm
from common import stats_tracker

SESSION_FILE = "session_hirist.json"
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

def run(limit: int | None = None):
    profile = Profile.load()
    roles = profile.target_roles
    applied_keys = load_applied_job_keys()
    target_limit = limit if limit is not None else profile.stop_after_n_applications
    applied = 0
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=profile.browser_mode == "headless")
        context = browser.new_context(storage_state=SESSION_FILE)
        page = context.new_page()
        
        for role in roles:
            if applied >= target_limit:
                break
            slug = role.lower().replace(".", "").replace(" ", "-")
            
            for page_no in range(1, 4):
                if applied >= target_limit:
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
                                from common.external_tracker import log_external_job
                                log_external_job("hirist", title, company, job_url, job_page.url, "", exp_text, posted_text)
                            else:
                                print("  Found Apply button! Clicking...")
                                apply_btn.click()
                                
                                # Wait for redirect to screening or applied page
                                try:
                                    job_page.wait_for_url("**/screening**", timeout=5000)
                                except PWTimeout:
                                    pass
                                    
                                if "screening" in job_page.url:
                                    print("  Detected screening questions! Auto-filling...")
                                    from common.human_input import ask_user
                                    import common.llm as llm
                                    import common.learned_answers as learned
                                    
                                    # Wait for form to load
                                    time.sleep(2)
                                    
                                    # Find all question texts
                                    questions = job_page.locator(".question-text")
                                    for i in range(questions.count()):
                                        q_node = questions.nth(i)
                                        if not q_node.is_visible():
                                            continue
                                            
                                        question_text = q_node.inner_text().strip()
                                        container = q_node.locator("xpath=..")
                                        
                                        # Handle Text inputs / Textareas
                                        from common.answers import get_screening_answer
                                        inputs = container.locator("textarea, input[type='text'], input:not([type='radio']):not([type='checkbox']):not([type='submit']):not([type='hidden'])")
                                        if inputs.count() > 0 and inputs.first.is_visible():
                                            try:
                                                ans = get_screening_answer(question_text, profile, f"{title} at {company}")
                                                if ans:
                                                    print(f"  Auto-filled text: '{ans[:30]}...' for '{question_text[:40]}'")
                                                    inputs.first.fill(ans)
                                            except Exception as e:
                                                print(f"  Failed to answer text question: {e}")
                                                
                                        # Handle Radio Buttons
                                        radios = container.locator("input[type='radio']")
                                        if radios.count() > 0:
                                            try:
                                                ans = get_screening_answer(f"Question: {question_text}. Options: Yes, No. Reply with EXACTLY 'Yes' or 'No'.", profile, f"{title} at {company}")
                                                if ans and "yes" in ans.lower():
                                                    yes_radio = container.locator("input[type='radio']").locator("xpath=..").filter(has_text=re.compile(r"^Yes$", re.I))
                                                    if yes_radio.count() > 0:
                                                        yes_radio.first.click()
                                                        print(f"  Auto-selected 'Yes' for '{question_text[:40]}'")
                                                elif ans and "no" in ans.lower():
                                                    no_radio = container.locator("input[type='radio']").locator("xpath=..").filter(has_text=re.compile(r"^No$", re.I))
                                                    if no_radio.count() > 0:
                                                        no_radio.first.click()
                                                        print(f"  Auto-selected 'No' for '{question_text[:40]}'")
                                                else:
                                                    # Fallback
                                                    radios.first.click()
                                            except:
                                                pass
                                    
                                    # Submit button
                                    submit_btn = job_page.locator("button:has-text('Submit'), button:has-text('Apply')")
                                    if submit_btn.count() > 0:
                                        submit_btn.last.click()
                                        time.sleep(3)
                                
                                # Check if successfully applied
                                if "/job/applied" in job_page.url or job_page.locator("text='Applied'").count() > 0 or "screening" in job_page.url:
                                    applied += 1
                                    applied_keys.add(_job_key(title, company))
                                    print(f"  ✅ Successfully applied: {title} @ {company} ({applied} total this run)")
                                    log_row([datetime.now().isoformat(), "hirist", title, company, "applied", "success"])
                                    time.sleep(profile.min_delay_seconds_between_applications)
                                else:
                                    print("  ❓ Clicked apply, but couldn't verify success. Might have a modal we missed.")
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
    run()
