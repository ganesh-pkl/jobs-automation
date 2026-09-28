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

SESSION_FILE = "session_hirist.json"
LOG_FILE = "applications_log.csv"

def log_row(row: list):
    new_file = not Path(LOG_FILE).exists()
    if new_file:
        Path(LOG_FILE).touch(mode=0o600)
    with open(LOG_FILE, "a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["timestamp", "source", "title", "company", "status", "reason"])
        w.writerow(row)

def run():
    profile = Profile.load()
    roles = profile.target_roles
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=profile.browser_mode == "headless")
        context = browser.new_context(storage_state=SESSION_FILE)
        page = context.new_page()
        
        for role in roles:
            slug = role.lower().replace(".", "").replace(" ", "-")
            url = f"https://www.hirist.tech/search/{slug}-jobs.html"
            
            print(f"\n--- Searching Hirist: {role} ---")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
            except PWTimeout:
                print("  (Navigation timeout, continuing...)")
            
            time.sleep(3)
            
            # Hirist wraps job cards in anchor tags starting with /j/
            card_locators = page.locator("a[href^='/j/']").all()
            print(f"Found {len(card_locators)} jobs on the page.")
            
            for card in card_locators:
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
                
                # Check experience limits
                if exp_text:
                    digits = [int(s) for s in exp_text.lower().replace("yrs", "").replace("yr", "").replace("-", " ").split() if s.isdigit()]
                    if len(digits) >= 2:
                        lo, hi = digits[0], digits[-1]
                        if hi < profile.seniority_floor_years or lo > profile.seniority_ceiling_years:
                            print(f"Skipped: {title} @ {company} (Experience range mismatch: {exp_text})")
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
                job_page = context.new_page()
                try:
                    job_page.goto(f"https://www.hirist.tech{href}", wait_until="domcontentloaded", timeout=15000)
                    time.sleep(2)
                    
                    apply_btn = job_page.locator("button:has-text('Apply'), a:has-text('Apply')").first
                    if apply_btn.count() > 0:
                        btn_text = apply_btn.inner_text().strip().lower()
                        if "company website" in btn_text or "external" in btn_text:
                            print("  External application. Logging to CSV.")
                            log_row([datetime.now().isoformat(), "hirist", title, company, "skipped", "external link"])
                            from common.external_tracker import log_external_job
                            log_external_job("hirist", title, company, f"https://www.hirist.tech{href}", job_page.url, "", exp_text, posted_text)
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
                                print("  ✅ Successfully applied! (Or submitted screening)")
                                log_row([datetime.now().isoformat(), "hirist", title, company, "applied", "success"])
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
                        
        print("\n[v1.0] Done exploring Hirist for this run!")

        browser.close()

if __name__ == "__main__":
    run()
