"""
Glassdoor & Indeed Apply Script — High-Accuracy Automated Engine.
Requires session_glassdoor.json from login_capture.py.

Workflow:
1. Loads applicant profile from profile.yaml.
2. Navigates to Glassdoor job search filtered by 'Easy Apply only', target roles, and freshness.
3. Evaluates job listings per target role and paginates through search pages.
4. Performs strict deduplication against applications_log.csv.
5. Evaluates experience range, company exclusions, and remote/location eligibility.
6. Opens job detail and launches the 'Easy Apply' flow (which seamlessly routes through Indeed SmartApply).
7. Automatically answers employer screening questions (CTC, notice period, location, skills experience, yes/no).
8. Submits the application, confirms submission, and logs to applications_log.csv.

Usage:
    python glassdoor_apply.py
    python glassdoor_apply.py --role "Full Stack Developer" --limit 5
    python glassdoor_apply.py --dry-run
"""
import argparse
import csv
import random
import re
import sys
import time
import urllib.parse
from datetime import datetime, date
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from common.profile import Profile
from common.answers import get_screening_answer
from common.external_tracker import log_external_job
from common import stats_tracker
from common import llm

SESSION_FILE = "session_glassdoor.json"
LOG_FILE = "applications_log.csv"

STOP_PHRASES = [
    "verify you are human", "security check", "turnstile",
    "captcha", "unusual traffic", "blocked", "access denied",
    "we have detected unusual activity", "challenge-running"
]


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
            w.writerow([str(x) for x in row])
            f.flush()
        finally:
            try:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass


def _job_key(title: str | None, company: str | None) -> tuple[str, str]:
    """Generates a normalized tuple for deduplication."""
    return (
        " ".join((title or "").lower().split()),
        " ".join((company or "").lower().split()),
    )


def load_applied_job_keys(path: str = LOG_FILE) -> set[tuple[str, str]]:
    """Loads all previously applied jobs from applications_log.csv."""
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


def count_applications_today(path: str = LOG_FILE, today: date | None = None) -> int:
    """Counts how many successful applications were submitted to Glassdoor/Indeed today."""
    log_path = Path(path)
    if not log_path.exists():
        return 0
    if today is None:
        today = datetime.now().date()
    count = 0
    try:
        with log_path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                src = (row.get("source") or row.get("platform") or "").lower()
                if src in ("glassdoor", "indeed") and row.get("status") == "applied":
                    ts_str = row.get("timestamp") or ""
                    try:
                        row_date = datetime.fromisoformat(ts_str.replace(" ", "T")).date()
                    except Exception:
                        row_date = today
                    if row_date == today:
                        count += 1
    except (OSError, csv.Error):
        pass
    return count


def extract_experience_years(text: str | None) -> tuple[int | None, int | None]:
    """
    Extracts min and max required experience years from text.
    Handles 'Fresher', '0-2 Yrs', '3-5 years', '5+ Years', etc.
    """
    if not text:
        return None, None
    text_clean = text.strip()
    if "fresher" in text_clean.lower() or "entry level" in text_clean.lower() or "intern" in text_clean.lower():
        return 0, 0

    range_match = re.search(r"(\d+)\s*[-–to]+\s*(\d+)\s*(?:years?|yrs|yr)", text_clean, re.IGNORECASE)
    if range_match:
        return int(range_match.group(1)), int(range_match.group(2))

    plus_match = re.search(r"(\d+)\+\s*(?:years?|yrs|yr)", text_clean, re.IGNORECASE)
    if plus_match:
        return int(plus_match.group(1)), 99

    single_match = re.search(r"(\d+)\s*(?:years?|yrs|yr)", text_clean, re.IGNORECASE)
    if single_match:
        val = int(single_match.group(1))
        return val, val

    return None, None


def extract_posted_age_days(posted_text: str | None) -> int:
    """
    Converts relative posted text (e.g., '24h', '3d', 'Just now', '1 day ago', '3 days ago') to age in days.
    """
    if not posted_text:
        return 0
    clean = posted_text.strip().lower()
    
    # Hours / Minutes / Today
    if any(k in clean for k in ["hour", "minute", "sec", "just now", "today"]) or re.search(r"\b\d+\s*(?:h|hr|hrs|m|min|mins)\b", clean) or clean.endswith("h"):
        return 0
    
    # Check formats like '3d', '5d', '1w', '2w'
    short_d = re.search(r"^(\d+)\s*d", clean)
    if short_d:
        return int(short_d.group(1))
    short_w = re.search(r"^(\d+)\s*w", clean)
    if short_w:
        return int(short_w.group(1)) * 7

    # Check words
    digits = [int(s) for s in re.findall(r"\d+", clean)]
    num = digits[0] if digits else 1

    if "month" in clean or "mo" in clean:
        return num * 30
    if "week" in clean:
        return num * 7
    if "day" in clean:
        return num
    if "year" in clean:
        return num * 365
    return 0


def matches_target_keywords(title: str, profile: Profile, role: str | None = None) -> bool:
    """Checks if the title matches configured keywords for the role or profile."""
    if not title:
        return False
    t_lower = title.lower()

    # Disqualify non-relevant roles
    anti_patterns = [
        "sales", "recruiter", "talent acquisition", "graphic designer",
        "content writer", "marketing", "accountant", "legal", "customer support",
        "telecaller", "executive assistant", "hr manager", "devops manager",
        "director", "vp", "vice president", "principal architect", "head of"
    ]
    if any(re.search(r"\b" + re.escape(p) + r"\b", t_lower) for p in anti_patterns):
        return False

    # Check explicit role_required_keywords if defined
    role_reqs = getattr(profile, "role_required_keywords", {}) or {}
    if role and role in role_reqs:
        return any(re.search(r"\b" + re.escape(kw.lower()) + r"\b", t_lower) or kw.lower() in t_lower for kw in role_reqs[role])

    # Check significant tokens from role name
    if role:
        tokens = [t.lower() for t in re.findall(r"\w+", role) if t.lower() not in {"jobs", "job", "developer", "engineer", "senior", "junior", "lead", "staff"}]
        if tokens and any(t in t_lower for t in tokens):
            return True

    # General tech engineering title matching (Software Engineer, Developer, etc.)
    tech_core = ["software", "developer", "engineer", "full stack", "fullstack", "backend", "frontend", "web", "python", "react", "node", "javascript", "typescript", "sde"]
    if any(tc in t_lower for tc in tech_core):
        return True

    # Check against profile skills / keywords
    target_keywords = set()
    for tr in profile.target_roles:
        target_keywords.update([k.lower() for k in re.findall(r"\w+", tr) if k])
    for s in getattr(profile, "skills_primary", []):
        target_keywords.add(s.lower())

    matches = sum(1 for kw in target_keywords if kw in t_lower)
    return matches >= 1


def passes_company_filters(company: str | None, profile: Profile) -> bool:
    """Checks company exclusions/inclusions."""
    if not company:
        return True
    c_lower = company.lower().strip()
    exclude_list = [c.lower().strip() for c in profile.data.get("company_exclude", []) if c.strip()]
    if any(ex in c_lower for ex in exclude_list):
        return False
    include_list = [c.lower().strip() for c in profile.data.get("company_include_only", []) if c.strip()]
    if include_list and not any(inc in c_lower for inc in include_list):
        return False
    return True


def fill_indeed_questions_step(page, profile: Profile, job_title: str) -> bool:
    """
    Scans the questions module on smartapply.indeed.com, extracts question prompts,
    and fills textareas, inputs, radios, dropdowns, and checkboxes using profile facts + AI.
    """
    # Extract questions info via JS
    elements = page.evaluate("""() => {
        const results = [];
        
        // Find all question fieldsets / form groups / containers
        const items = document.querySelectorAll(
            'fieldset, div[class*="Questions-item"], div[class*="form-group"], div[data-testid*="question"], div[class*="ia-Question"]'
        );
        
        // Also check standalone textareas/inputs if no containers
        const inputs = document.querySelectorAll('input:not([type="hidden"]), textarea, select');
        
        inputs.forEach((el, idx) => {
            if (!el.id) el.id = 'ia_field_' + idx + '_' + Math.floor(Math.random() * 1000);
            
            // Find label or legend
            let label = '';
            if (el.labels && el.labels.length > 0) {
                label = el.labels[0].innerText.trim();
            }
            if (!label) {
                const parentFieldset = el.closest('fieldset');
                if (parentFieldset) {
                    const legend = parentFieldset.querySelector('legend');
                    if (legend) label = legend.innerText.trim();
                }
            }
            if (!label) {
                const parentItem = el.closest('div[class*="Questions"], div[class*="form-group"], div[class*="item"]');
                if (parentItem) {
                    const header = parentItem.querySelector('label, p, span, h3, h4');
                    if (header) label = header.innerText.trim();
                }
            }
            if (!label) {
                label = el.getAttribute('aria-label') || el.getAttribute('placeholder') || '';
            }
            
            results.push({
                id: el.id,
                tag: el.tagName.toUpperCase(),
                type: (el.getAttribute('type') || '').toLowerCase(),
                name: el.getAttribute('name') || '',
                value: el.value || '',
                label: label,
                required: el.required || el.getAttribute('aria-required') === 'true',
                checked: el.checked || false
            });
        });
        
        return results;
    }""")

    if not elements:
        return True

    processed_radios = set()

    for item in elements:
        tag = item.get("tag")
        itype = item.get("type")
        field_id = item.get("id")
        label = item.get("label", "").strip()
        val = item.get("value", "").strip()
        name = item.get("name", "")

        if not label and not field_id:
            continue

        # 1. Radio Buttons
        if itype == "radio":
            if name in processed_radios:
                continue
            processed_radios.add(name)
            ans = get_screening_answer(label or name, profile, job_title) or "Yes"
            ans_clean = ans.lower().strip()
            
            # Select appropriate radio button in this group
            page.evaluate(f"""([groupName, answer]) => {{
                const radios = Array.from(document.querySelectorAll(`input[type="radio"][name="${{groupName}}"]`));
                if (radios.length === 0) return;
                
                let target = null;
                if (answer.includes('yes') || answer === '1' || answer === 'true') {{
                    target = radios.find(r => {{
                        const lbl = (r.labels && r.labels[0] ? r.labels[0].innerText : '') || r.value || '';
                        return /yes|true/i.test(lbl);
                    }});
                }} else if (answer.includes('no') || answer === '0' || answer === 'false') {{
                    target = radios.find(r => {{
                        const lbl = (r.labels && r.labels[0] ? r.labels[0].innerText : '') || r.value || '';
                        return /no|false/i.test(lbl);
                    }});
                }}
                if (!target) target = radios[0]; // fallback to first option
                if (target) {{
                    target.click();
                    target.checked = true;
                    target.dispatchEvent(new Event('change', {{bubbles: true}}));
                }}
            }}""", [name, ans_clean])
            continue

        # 2. Checkboxes
        if itype == "checkbox":
            # Auto-check mandatory/compliance agreements
            if not item.get("checked"):
                page.evaluate(f"""(id) => {{
                    const el = document.getElementById(id);
                    if (el) {{
                        el.click();
                        el.checked = true;
                        el.dispatchEvent(new Event('change', {{bubbles: true}}));
                    }}
                }}""", field_id)
            continue

        # 3. Select Dropdowns
        if tag == "SELECT":
            ans = get_screening_answer(label, profile, job_title) or "Yes"
            page.evaluate(f"""([id, answer]) => {{
                const el = document.getElementById(id);
                if (!el || el.options.length <= 1) return;
                let matchedIdx = -1;
                for (let i = 0; i < el.options.length; i++) {{
                    const optText = el.options[i].text.toLowerCase();
                    if (answer.toLowerCase().includes('yes') && optText.includes('yes')) {{
                        matchedIdx = i; break;
                    }}
                    if (optText.includes(answer.toLowerCase())) {{
                        matchedIdx = i; break;
                    }}
                }}
                if (matchedIdx === -1) matchedIdx = 1; // default to first non-empty option
                el.selectedIndex = matchedIdx;
                el.dispatchEvent(new Event('change', {{bubbles: true}}));
            }}""", [field_id, ans])
            continue

        # 4. Textarea and Text/Number Inputs
        if tag == "TEXTAREA" or itype in ("text", "number", "tel", "email", ""):
            if val:  # already filled
                continue
            
            ans = get_screening_answer(label, profile, job_title)
            if not ans:
                # smart fallback
                lbl_l = label.lower()
                if "ctc" in lbl_l or "salary" in lbl_l:
                    ans = f"{profile.expected_ctc_lpa} LPA"
                elif "notice" in lbl_l:
                    ans = f"{profile.notice_period_days} days"
                elif "experience" in lbl_l or "years" in lbl_l:
                    ans = str(profile.total_experience_years)
                elif "location" in lbl_l or "city" in lbl_l:
                    ans = profile.current_city
                else:
                    ans = "Yes"

            # If the input specifically demands a pure numeric value
            if itype == "number" or "years" in label.lower() or "how many" in label.lower():
                num_match = re.search(r"\b(\d+(?:\.\d+)?)\b", str(ans))
                if num_match:
                    ans = num_match.group(1)

            # Set value and trigger input & change events
            page.evaluate(f"""([id, text]) => {{
                const el = document.getElementById(id);
                if (!el) return;
                el.value = text;
                el.dispatchEvent(new Event('input', {{bubbles: true}}));
                el.dispatchEvent(new Event('change', {{bubbles: true}}));
            }}""", [field_id, str(ans)])

    return True


def handle_indeed_smartapply_flow(apply_page, profile: Profile, job_title: str, company: str, dry_run: bool = False) -> tuple[str, str]:
    """
    Navigates and completes the smartapply.indeed.com / Glassdoor Easy Apply form flow:
    - Resume selection step
    - Screening questions module
    - Review step
    - Final submission
    """
    max_steps = 15
    for step in range(max_steps):
        time.sleep(1.5)
        curr_url = apply_page.url.lower()
        page_content = apply_page.content().lower()

        # Check for CAPTCHA / bot detection
        if any(sp in page_content for sp in STOP_PHRASES):
            return "security_checkpoint", "Hit bot/security checkpoint on application page"

        # 1. Post-Apply Confirmation
        if "post-apply" in curr_url or "submitted" in page_content or "application submitted" in page_content:
            return "applied", "Successfully submitted via Indeed SmartApply"

        # 2. Review Module (Final step before submit)
        if "review-module" in curr_url or "review your application" in page_content:
            if dry_run:
                return "dry_run_success", "Dry run completed at review step"

            # Check any required checkboxes on review page
            apply_page.evaluate("""() => {
                document.querySelectorAll('input[type="checkbox"]:not(:checked)').forEach(cb => {
                    cb.click();
                    cb.checked = true;
                    cb.dispatchEvent(new Event('change', {bubbles: true}));
                });
            }""")

            # Click "Submit your application" button
            submit_btn = apply_page.locator('button:has-text("Submit your application"), button:has-text("Submit Application"), button:has-text("Submit")').first
            if submit_btn.is_visible():
                submit_btn.click()
                time.sleep(3)
                # Wait for post-apply navigation
                try:
                    apply_page.wait_for_url(lambda u: "post-apply" in u or "submitted" in u, timeout=12000)
                    return "applied", "Successfully submitted application"
                except Exception:
                    if "submitted" in apply_page.content().lower() or "application submitted" in apply_page.content().lower():
                        return "applied", "Successfully submitted application"
                    return "applied", "Submitted (assumed success)"
            else:
                return "failed", "Submit button not found on review page"

        # 3. Resume Selection Module
        if "resume-selection-module" in curr_url or "add a resume" in page_content or "select a resume" in page_content:
            # If resume already uploaded and selected, just click Continue
            continue_btn = apply_page.locator('button:has-text("Continue"), button:has-text("Next")').first
            if continue_btn.is_visible():
                continue_btn.click()
                continue
            else:
                # Check for file upload input
                file_input = apply_page.locator('input[type="file"]').first
                if file_input.is_visible() and Path(profile.resume_file_name).exists():
                    file_input.set_input_files(str(Path(profile.resume_file_name).resolve()))
                    time.sleep(2)
                    continue_btn = apply_page.locator('button:has-text("Continue"), button:has-text("Next")').first
                    if continue_btn.is_visible():
                        continue_btn.click()
                        continue

        # 4. Questions Module
        if "questions-module" in curr_url or "answer these questions" in page_content or "questions from the employer" in page_content:
            fill_indeed_questions_step(apply_page, profile, job_title)
            time.sleep(1)
            continue_btn = apply_page.locator('button:has-text("Continue"), button:has-text("Next")').first
            if continue_btn.is_visible():
                continue_btn.click()
                continue

        # 5. Generic / Contact Info / Experience Modules
        continue_btn = apply_page.locator('button:has-text("Continue"), button:has-text("Next"), button:has-text("Review your application")').first
        if continue_btn.is_visible():
            fill_indeed_questions_step(apply_page, profile, job_title)
            continue_btn.click()
            continue

        # If no continue button found and not on known module, give it a moment
        time.sleep(2)

    return "timed_out", f"Reached maximum step limit ({max_steps}) without final confirmation"


def run(limit: int | None = None, specific_role: str | None = None, dry_run: bool = False):
    """Main Glassdoor application execution loop."""
    profile = Profile.load()
    roles = [specific_role] if specific_role else profile.target_roles
    applied_keys = load_applied_job_keys()
    today_applied = count_applications_today()

    max_limit = limit if limit is not None else getattr(profile, "glassdoor_daily_limit", 25)
    remaining_budget = max(0, max_limit - today_applied)

    print(f"=== Glassdoor / Indeed Apply Automation ===")
    print(f"Date: {date.today()} | Applied today: {today_applied} | Daily Limit: {max_limit} | Remaining: {remaining_budget}")
    if dry_run:
        print(">>> RUNNING IN DRY-RUN MODE: No real applications will be submitted. <<<")

    if remaining_budget <= 0:
        print("Daily application budget reached for Glassdoor. Exiting cleanly.")
        return

    session_path = Path(SESSION_FILE)
    if not session_path.exists():
        print(f"Error: {SESSION_FILE} not found! Run `python login_capture.py glassdoor` first to save your logged-in session.")
        sys.exit(1)

    applied_count = 0
    attempt_count = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=profile.browser_mode == "headless",
            args=["--disable-blink-features=AutomationControlled"]
        )
        context = browser.new_context(
            storage_state=SESSION_FILE,
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 850}
        )
        page = context.new_page()

        for role in roles:
            if applied_count >= remaining_budget:
                break

            print(f"\n==========================================")
            print(f"Searching Glassdoor for: {role}")
            print(f"==========================================")

            # Build search URL
            role_slug = urllib.parse.quote(role)
            freshness = getattr(profile, "job_freshness_days", 3)
            remote_param = "&remoteWorkType=1" if profile.work_mode in ("remote_only", "remote_first") else ""
            
            for page_no in range(1, profile.max_pages_per_role + 1):
                if applied_count >= remaining_budget:
                    break

                search_url = f"https://www.glassdoor.co.in/Job/jobs.htm?sc.keyword={role_slug}&fromAge={freshness}&applicationType=1{remote_param}&p={page_no}"
                print(f"\n--- Page {page_no}: {search_url} ---")

                try:
                    page.goto(search_url, wait_until="domcontentloaded", timeout=25000)
                except PWTimeout:
                    print("  (Page load timed out, continuing...)")

                time.sleep(3)

                # Check security check
                content_lower = page.content().lower()
                if any(sp in content_lower for sp in STOP_PHRASES):
                    print("  [ALERT] Security / Bot check detected on Glassdoor search page. Pausing automation.")
                    break

                # Extract job listing items
                job_cards = page.evaluate("""() => {
                    const cards = [];
                    const items = document.querySelectorAll('li[data-test="jobListing"], div[data-test="jobListing"], li[class*="jobListing"]');
                    items.forEach((el, idx) => {
                        const titleEl = el.querySelector('[data-test="job-title"], a.JobCard_jobTitle___, a[class*="jobTitle"]');
                        const companyEl = el.querySelector('[data-test="employer-name"], div[class*="employerName"], span[class*="employerName"]');
                        const locEl = el.querySelector('[data-test="emp-location"], div[class*="location"], span[class*="location"]');
                        const ageEl = el.querySelector('[data-test="job-age"], div[class*="listingAge"]');
                        const easyApplyEl = el.querySelector('[data-test="job-easy-apply"], span:has-text("Easy Apply")');
                        
                        if (titleEl) {
                            cards.push({
                                index: idx,
                                title: titleEl.innerText.trim(),
                                company: companyEl ? companyEl.innerText.trim().replace(/[\\d\\.\\★]+$/, '').trim() : '',
                                location: locEl ? locEl.innerText.trim() : '',
                                age: ageEl ? ageEl.innerText.trim() : '',
                                isEasyApply: true // Filter was applied in URL
                            });
                        }
                    });
                    return cards;
                }""")

                print(f"Found {len(job_cards)} job cards on page {page_no}.")

                if not job_cards:
                    print("No more job listings found for this role.")
                    break

                for card_info in job_cards:
                    if applied_count >= remaining_budget:
                        break

                    title = card_info.get("title", "")
                    company = card_info.get("company", "")
                    location = card_info.get("location", "")
                    age_text = card_info.get("age", "")

                    if not title or not company:
                        continue

                    job_key = _job_key(title, company)
                    if job_key in applied_keys:
                        print(f"  [SKIP] Already applied: {title} @ {company}")
                        continue

                    if not matches_target_keywords(title, profile, role):
                        print(f"  [SKIP] Keyword mismatch: '{title}'")
                        continue

                    if not passes_company_filters(company, profile):
                        print(f"  [SKIP] Company excluded: '{company}'")
                        continue

                    age_days = extract_posted_age_days(age_text)
                    if age_days > profile.job_freshness_days:
                        print(f"  [SKIP] Job too old ({age_days}d > {profile.job_freshness_days}d): {title} @ {company}")
                        continue

                    print(f"\n👉 Processing: {title} @ {company} ({location} | {age_text})")
                    attempt_count += 1

                    # Click the job card on the left list to load the detail pane
                    card_loc = page.locator(f'li[data-test="jobListing"], div[data-test="jobListing"]').nth(card_info["index"])
                    try:
                        card_loc.scroll_into_view_if_needed(timeout=3000)
                        card_loc.click(timeout=3000)
                        time.sleep(2)
                    except Exception as e:
                        print(f"  (Failed to select job card: {e})")
                        continue

                    # Look for the Easy Apply button in the detail pane
                    easy_apply_btn = page.locator('button[data-test="easy-apply-button"], button:has-text("Easy Apply"), a:has-text("Easy Apply")').first
                    if not easy_apply_btn.is_visible():
                        print(f"  [SKIP] Easy Apply button not visible for {title} @ {company}")
                        log_external_job(
                            title=title,
                            company=company,
                            location=location,
                            url=page.url,
                            source="glassdoor",
                            match_reason="Glassdoor external redirection"
                        )
                        continue

                    # Click Easy Apply and capture the new tab/page or current page flow
                    with context.expect_page() as new_page_info:
                        try:
                            easy_apply_btn.click()
                        except Exception as e:
                            print(f"  (Click error: {e})")
                            continue

                    try:
                        apply_page = new_page_info.value
                        apply_page.wait_for_load_state("domcontentloaded", timeout=15000)
                    except Exception:
                        apply_page = page

                    print(f"  -> Opened application portal: {apply_page.url}")

                    # Run Indeed SmartApply Form Engine
                    status, reason = handle_indeed_smartapply_flow(
                        apply_page=apply_page,
                        profile=profile,
                        job_title=title,
                        company=company,
                        dry_run=dry_run
                    )

                    print(f"  -> Result: {status.upper()} ({reason})")

                    # If a separate tab was opened, close it to keep browser lightweight
                    if apply_page != page:
                        try:
                            apply_page.close()
                        except Exception:
                            pass

                    # Record application log
                    timestamp = datetime.now().isoformat(timespec="seconds")
                    log_row([timestamp, "glassdoor", title, company, status, reason])

                    if status in ("applied", "dry_run_success"):
                        applied_count += 1
                        applied_keys.add(job_key)
                        stats_tracker.record_applied("glassdoor")

                        # Respect human-like delays
                        if not dry_run and applied_count < remaining_budget:
                            delay = random.randint(
                                int(profile.min_delay_seconds_between_applications),
                                int(profile.max_delay_seconds_between_applications)
                            )
                            print(f"  ⏳ Waiting {delay}s before next application to maintain natural cadence...")
                            time.sleep(delay)

        browser.close()

    print(f"\n==========================================")
    print(f"Glassdoor Run Summary: Applied: {applied_count} | Attempts: {attempt_count}")
    print(f"==========================================")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Glassdoor & Indeed SmartApply Job Automation")
    parser.add_argument("--role", type=str, default=None, help="Target specific role override")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of applications to submit")
    parser.add_argument("--dry-run", action="store_true", help="Preview mode: runs all steps up to the final submit")
    args = parser.parse_args()

    run(limit=args.limit, specific_role=args.role, dry_run=args.dry_run)
