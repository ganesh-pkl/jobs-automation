"""
Wellfound (formerly AngelList Talent) Apply Script — High-Accuracy Direct Apply Engine.
Requires session_wellfound.json from login_capture.py.

Workflow:
1. Loads applicant profile from profile.yaml.
2. Navigates to Wellfound job search (https://wellfound.com/jobs).
3. Activates the 'Hide jobs which require me to apply on the company's website' filter
   to isolate native 1-click 'Apply on Wellfound' opportunities.
4. Evaluates job listings per target role and paginates through results.
5. Performs strict deduplication against applications_log.csv.
6. Evaluates experience range, location eligibility, and job freshness.
7. Opens job details and auto-generates a personalized recruiter pitch note
   using Groq AI (highlighting full-stack skills, relevant projects, and immediate availability).
8. Submits the application, confirms submission, and logs to applications_log.csv.

Usage:
    python wellfound_apply.py
    python wellfound_apply.py --role "Python Developer" --limit 5
    python wellfound_apply.py --dry-run
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

SESSION_FILE = "session_wellfound.json"
LOG_FILE = "applications_log.csv"

STOP_PHRASES = [
    "verify you are human", "security check", "turnstile",
    "captcha", "unusual traffic", "blocked", "access denied",
    "we have detected unusual activity"
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
    """Counts how many successful applications were submitted to Wellfound today."""
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
                if src == "wellfound" and row.get("status") == "applied":
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


EXCLUDED_DOMAINS = [
    "marketing", "content", "community", "social media", "copywriting",
    "recruiter", "talent acquisition", "human resources", "sales",
    "account executive", "business development", "bdr", "sdr", "finance",
    "accounting", "legal", "customer support", "customer success",
    "graphic designer", "ui/ux designer", "product designer", "event",
    "seo specialist", "operations manager", "growth marketer", "video editor",
    "copywriter", "community lead", "content lead", "marketing manager",
    "product marketing", "brand manager", "communications", "editor"
]


def extract_experience_years(text: str | None) -> tuple[int | None, int | None]:
    """
    Extracts min and max required experience years from text.
    Handles 'No experience required', '0-2 Yrs', '1-3 years', '3+ years', '5+ years', '6+ years in Engineering role', etc.
    """
    if not text:
        return None, None
    text_clean = text.strip()

    if "no experience" in text_clean.lower() or "entry" in text_clean.lower():
        return 0, 1

    # Match '(6+ years in Engineering role)' or '6+ years'
    plus_match = re.search(r"(\d+)\+\s*(?:years?|yrs)", text_clean, re.IGNORECASE)
    if plus_match:
        return int(plus_match.group(1)), 99

    # Match '1-3 years' or '1 to 3 yrs'
    range_match = re.search(r"(\d+)\s*[-–to]+\s*(\d+)\s*(?:years?|yrs)", text_clean, re.IGNORECASE)
    if range_match:
        return int(range_match.group(1)), int(range_match.group(2))

    # Match '2 years' or '3 yrs'
    single_match = re.search(r"(\d+)\s*(?:years?|yrs)", text_clean, re.IGNORECASE)
    if single_match:
        val = int(single_match.group(1))
        return val, val

    if "junior" in text_clean.lower():
        return 0, 3
    if "mid" in text_clean.lower():
        return 2, 5
    if "senior" in text_clean.lower() or "lead" in text_clean.lower() or "staff" in text_clean.lower() or "principal" in text_clean.lower():
        return 5, 99

    return None, None


def extract_posted_age_days(posted_text: str | None) -> int:
    """
    Converts relative posted text (e.g. 'POSTED 4 WEEKS AGO', 'POSTED 1 WEEK AGO', 'POSTED 2 DAYS AGO')
    to age in days.
    """
    if not posted_text:
        return 0

    lines = [l.strip().lower() for l in posted_text.split("\n") if l.strip()]
    posted_line = next((l for l in lines if "posted" in l or "ago" in l), lines[0] if lines else "")

    if any(h in posted_line for h in ["hour", "minute", "today", "just now", "sec"]):
        return 0

    digits = [int(s) for s in posted_line.split() if s.isdigit()]
    num = digits[0] if digits else 1

    if "month" in posted_line or "mo" in posted_line:
        return num * 30
    if "week" in posted_line:
        return num * 7
    if "day" in posted_line:
        return num
    if "year" in posted_line:
        return num * 365
    return 0


def matches_target_keywords(title: str, skills_text: str, profile: Profile, role: str | None = None) -> bool:
    """Strictly checks if the title or skills match configured tech keywords and does NOT match excluded domains."""
    comb_lower = f"{title} {skills_text}".lower()
    title_lower = title.lower()

    # 1. Immediate rejection if title contains excluded non-tech domains
    for exc in EXCLUDED_DOMAINS:
        if re.search(r"\b" + re.escape(exc) + r"\b", title_lower):
            return False

    # 2. Check if specific role keywords match
    if role:
        role_keywords = profile.role_required_keywords.get(role, [])
        if role_keywords:
            matched = any(
                re.search(r"\b" + re.escape(kw.lower()) + r"\b", comb_lower)
                for kw in role_keywords
            )
            if matched:
                return True
        tokens = [t.lower() for t in re.findall(r"\w+", role) if t.lower() not in {"engineer", "developer", "senior", "junior", "lead", "staff", "role"}]
        if tokens and any(re.search(r"\b" + re.escape(t) + r"\b", comb_lower) for t in tokens):
            return True

    # 3. Check across all profile target roles & primary skills
    for r in profile.target_roles:
        r_kw = profile.role_required_keywords.get(r, [])
        if r_kw and any(re.search(r"\b" + re.escape(kw.lower()) + r"\b", comb_lower) for kw in r_kw):
            return True
        r_tokens = [t.lower() for t in re.findall(r"\w+", r) if t.lower() not in {"engineer", "developer", "senior", "junior", "lead", "staff", "role"}]
        if r_tokens and any(re.search(r"\b" + re.escape(t) + r"\b", comb_lower) for t in r_tokens):
            return True

    # 4. Check primary tech skills from profile
    for sk in profile.skills_primary:
        if re.search(r"\b" + re.escape(sk.lower()) + r"\b", comb_lower):
            return True

    # STRICT: Reject non-matching roles
    return False


def is_location_eligible(location_text: str | None, profile: Profile) -> bool:
    """
    Checks if location matches preferences.
    Wellfound clearly specifies 'Remote (India)', 'Hires remotely in India', 'Worldwide', 'Hyderabad', etc.
    """
    if not location_text:
        return True
    loc_lower = location_text.lower()

    if "us only" in loc_lower or "usa only" in loc_lower or "uk only" in loc_lower or "europe only" in loc_lower or "latam only" in loc_lower or "canada only" in loc_lower:
        return False

    if "india" in loc_lower or "remote" in loc_lower or "anywhere" in loc_lower or "worldwide" in loc_lower:
        return True

    allowed_cities = [profile.current_city.lower()] + [c.lower() for c in profile.relocate_cities if c]
    return any(city in loc_lower for city in allowed_cities)


def generate_pitch_note(job_title: str, company: str, job_details: str, profile: Profile) -> str:
    """
    Generates a high-conversion, customized application pitch note for Wellfound founders/recruiters.
    Directly answers: 'What interests you about working for this company?'
    """
    prompt = (
        f"You are applying for the role '{job_title}' at '{company}' on Wellfound (AngelList Talent).\n"
        f"Write a personalized, compelling 2-3 sentence answer to the prompt: 'What interests you about working for this company?'\n\n"
        f"Candidate Facts:\n"
        f"- Experience: {profile.total_experience_years} years in Full-Stack software engineering (React, Next.js, Node.js, Python, FastAPI, Spring Boot, REST APIs, PostgreSQL, Docker, AWS)\n"
        f"- Availability: Immediately available ({profile.notice_period_days} days notice period)\n"
        f"- Location: {profile.current_city} (Open to Remote / Relocation)\n"
        f"- Background: {profile.work_history_narrative}\n\n"
        f"Job Details / Context:\n{job_details[:500]}\n\n"
        f"Requirements for the pitch:\n"
        f"1. Directly explain what excites you about {company}'s product/mission and this {job_title} role.\n"
        f"2. Highlight how your 3 years of hands-on experience in React, Node.js, Python, and scalable backend architecture makes you an immediate contributor.\n"
        f"3. Keep length strictly between 35 and 60 words.\n"
        f"4. Be professional, authentic, and direct. NO placeholders, NO quotes, NO 'Dear Hiring Team' greeting, NO sign-off/signature.\n"
        f"5. Return ONLY the final message body text."
    )
    try:
        resp = llm.generate_answer(prompt, profile.llm_context(), timeout_seconds=12)
        if resp and len(resp.strip()) > 20:
            return resp.strip().strip('"\'')
    except Exception:
        pass

    # High-quality fallback
    return (
        f"I'm excited about {company}'s mission and the opportunity to contribute as a {job_title}. "
        f"With nearly 3 years of hands-on experience across React, Node.js, Python, and scalable backend services, "
        f"I can hit the ground running immediately and add strong value to your engineering team."
    )


def _is_application_confirmation(page_text: str, button_text: str = "") -> bool:
    """Determines whether Wellfound confirmed the application."""
    comb = f"{page_text} {button_text}".lower()
    success_indicators = [
        "application submitted",
        "applied to",
        "you applied",
        "applied",
        "your note has been sent",
        "application sent",
        "note sent",
        "application has been sent",
        "your application was submitted",
        "your application was sent",
        "reusable ai interview",
        "start the interview",
        "ai interview to show off",
        "interviews are presented first to companies",
    ]
    return any(ind in comb for ind in success_indicators)


def find_apply_button(container):
    """Finds the primary submission button in the Wellfound application modal or drawer."""
    candidate_selectors = [
        "button:has-text('Send application')",
        "button:has-text('Send Application')",
        "button:has-text('Submit application')",
        "button:has-text('Submit Application')",
        "button:has-text('Apply to')",
        "button:has-text('Apply')",
        "button:has-text('Submit')",
        "input[type='submit']",
        "button[type='submit']",
    ]
    for sel in candidate_selectors:
        try:
            btns = container.locator(sel).all()
            for btn in btns:
                if btn.is_visible():
                    txt = btn.inner_text().strip().lower()
                    if txt not in {"cancel", "close", "✕", "save", "learn more", "report", "hide"}:
                        return btn
        except Exception:
            continue
    return None


def fill_pitch_note(container, title: str, company: str, profile: Profile) -> bool:
    """Finds the pitch/interest textarea in the modal and populates it with a tailored note."""
    try:
        textareas = container.locator("textarea").all()
        if not textareas:
            return False
        
        container_text = container.inner_text()
        pitch_note = generate_pitch_note(title, company, container_text, profile)
        
        for ta in textareas:
            if ta.is_visible():
                ta.fill(pitch_note)
                time.sleep(0.3)
                print(f"    Filled tailored pitch note into input box ({len(pitch_note)} chars)")
                return True
    except Exception as e:
        print(f"    Note fill notice: {e}")
    return False


def fill_additional_screening_questions(container, profile: Profile):
    """Fills any additional custom screening inputs present in the modal."""
    try:
        inputs = container.locator("input[type='text'], input[type='number'], input:not([type])").all()
        for inp in inputs:
            if not inp.is_visible():
                continue
            cur_val = inp.input_value()
            if cur_val.strip():
                continue
            
            label_text = inp.get_attribute("placeholder") or inp.get_attribute("aria-label") or ""
            if not label_text:
                try:
                    parent = inp.locator("xpath=..")
                    label_text = parent.inner_text()
                except Exception:
                    pass
            
            if label_text:
                ans = get_screening_answer(label_text, profile)
                if ans:
                    inp.fill(str(ans))
                    time.sleep(0.2)
                    print(f"    Answered screening question '{label_text[:30]}...': {ans}")
    except Exception:
        pass


def close_modal(page, modal):
    """Safely closes modal via close button, Escape key, and waits for overlay dismissal."""
    for _ in range(2):
        try:
            close_btn = page.locator("button[aria-label='Close'], svg[data-icon='xmark'], button:has-text('✕'), button:has-text('Cancel')").first
            if close_btn.is_visible():
                close_btn.click(timeout=2000)
                time.sleep(0.5)
        except Exception:
            pass
        try:
            page.keyboard.press("Escape")
            time.sleep(0.5)
        except Exception:
            pass

    # Wait for overlay to hide or detach
    try:
        page.locator(".ReactModal__Overlay, .ReactModalPortal, [role='dialog']").first.wait_for(state="hidden", timeout=2000)
    except Exception:
        pass


def run(role: str | None = None, limit: int | None = None, dry_run: bool = False):
    """Main execution function for Wellfound (AngelList) application automation."""
    profile = Profile.load()
    if not dry_run and not Path(SESSION_FILE).exists():
        raise SystemExit(
            f"\n[!] {SESSION_FILE} not found.\n"
            f"    Run: python login_capture.py wellfound\n"
            f"    Log in manually in the opened browser window, then press Enter to save your session."
        )

    applied_today = count_applications_today()
    wf_limit = int(profile.data.get("wellfound_daily_limit", 25))
    daily_limit = int(profile.data.get("daily_application_limit", 100))
    remaining_today = max(0, min(wf_limit - applied_today, daily_limit - applied_today))

    target_limit = limit if limit is not None else profile.stop_after_n_applications
    run_limit = min(target_limit, remaining_today)

    print("=" * 65)
    print("        WELLFOUND (ANGELLIST) APPLICATION ENGINE")
    print("=" * 65)
    print(f"Wellfound Daily Cap:            {wf_limit} (Applied today: {applied_today})")
    print(f"Global Target For This Run:     {run_limit}")
    print(f"Target Roles:                   {', '.join(profile.target_roles)}")
    print(f"Freshness Filter:               <= {profile.job_freshness_days} day(s)")
    print(f"Seniority Floor / Ceiling:      {profile.seniority_floor_years} - {profile.seniority_ceiling_years} years")
    print(f"Browser Mode:                   {profile.browser_mode}")
    print(f"Dry Run Mode:                   {dry_run}")
    print("=" * 65 + "\n")

    if run_limit <= 0 and not dry_run:
        print(f"Daily application limit for Wellfound reached ({applied_today}/{wf_limit}). Exiting.")
        return

    applied_keys = load_applied_job_keys()
    roles_to_search = [role] if role else profile.target_roles[:5]
    total_applied = 0
    total_attempts = 0
    max_attempts = int(profile.data.get("stop_after_n_attempts", 50))

    with sync_playwright() as p:
        headless_mode = profile.browser_mode == "headless"
        browser = p.chromium.launch(
            headless=headless_mode,
            args=["--disable-blink-features=AutomationControlled"]
        )

        context_kwargs = {
            "user_agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "viewport": {"width": 1280, "height": 800},
            "locale": "en-US",
            "timezone_id": "Asia/Kolkata",
        }
        if Path(SESSION_FILE).exists():
            context_kwargs["storage_state"] = SESSION_FILE

        context = browser.new_context(**context_kwargs)
        page = context.new_page()

        for cur_role in roles_to_search:
            if total_applied >= run_limit or total_attempts >= max_attempts:
                break

            print(f"\n>>> Searching Wellfound: {cur_role}")
            jobs_url = "https://wellfound.com/jobs"

            try:
                page.goto(jobs_url, wait_until="domcontentloaded", timeout=25000)
            except PWTimeout:
                print("    (Navigation timeout, processing DOM...)")

            time.sleep(3)

            # Bot verification check
            body_text = page.locator("body").inner_text()
            if any(phrase in body_text.lower() for phrase in STOP_PHRASES):
                print("\n[!] Security / verification challenge detected on Wellfound.")
                print("    Halting automated run safely to protect account.")
                browser.close()
                return

            # Activate 'Hide jobs which require me to apply on the company's website'
            try:
                hide_external_cb = page.locator("label:has-text('Hide jobs which require me to apply on the company'), div:has-text('Hide jobs which require me to apply on the company')").locator("input[type='checkbox']").first
                if not hide_external_cb.is_visible():
                    hide_external_cb = page.locator("input[type='checkbox']").filter(
                        has_text=re.compile(r"Hide jobs which require me to apply on the company", re.I)
                    ).first
                
                if hide_external_cb.is_visible() and not hide_external_cb.is_checked():
                    print("    Checking 'Hide external company website jobs' filter...")
                    hide_external_cb.click()
                    time.sleep(2)
            except Exception:
                pass

            # Search specific role in the search input if available
            try:
                role_input = page.locator("input[placeholder*='Software Engineer'], input[placeholder*='job title'], input[placeholder*='role'], input[placeholder*='Search by role'], div:has-text('Software Engineer') input").first
                if not role_input.is_visible():
                    role_input = page.locator("input[type='text']").first
                
                if role_input.is_visible():
                    role_input.click()
                    page.keyboard.press("Meta+A" if sys.platform == "darwin" else "Control+A")
                    page.keyboard.press("Backspace")
                    role_input.type(cur_role, delay=30)
                    page.keyboard.press("Enter")
                    time.sleep(3)
            except Exception:
                pass

            # Scroll down to load multiple batches of results
            for _ in range(6):
                page.evaluate("window.scrollBy(0, 1200)")
                time.sleep(0.7)

            # Locate job cards / Learn more buttons
            learn_more_locators = page.locator("button:has-text('Learn more'), a:has-text('Learn more')").all()
            print(f"    Found {len(learn_more_locators)} potential opportunity listings on page.")
            seen_card_keys = set()

            for btn in learn_more_locators:
                if total_applied >= run_limit or total_attempts >= max_attempts:
                    break

                # Extract container text
                card_text = ""
                try:
                    # Look for enclosing listing card
                    parent_card = btn.locator("xpath=./ancestor::div[contains(@class, 'styles_listing') or contains(@class, 'styles_job') or contains(@class, 'styles_card') or string-length(text()) > 50 and string-length(text()) < 1500][1]")
                    if parent_card.is_visible():
                        card_text = parent_card.inner_text().strip()
                except Exception:
                    pass

                if not card_text:
                    try:
                        card_text = btn.locator("xpath=../../..").inner_text().strip()
                    except Exception:
                        pass

                lines = [l.strip() for l in card_text.split("\n") if l.strip()]
                raw_company = lines[0] if lines else "Startup"
                if "•" in raw_company:
                    company = raw_company.split("•")[0].strip()
                elif "Apply on Wellfound" in raw_company and len(lines) > 1:
                    company = lines[1]
                else:
                    company = raw_company

                title = cur_role
                for l in lines:
                    if any(k in l.lower() for k in ["developer", "engineer", "lead", "architect", "full stack", "backend", "frontend", "python", "react"]):
                        title = l
                        break

                page_card_key = _job_key(title, company)
                if page_card_key in seen_card_keys:
                    continue
                seen_card_keys.add(page_card_key)

                stats_tracker.record_discovered()

                # Deduplication check
                if page_card_key in applied_keys:
                    stats_tracker.record_previously_applied_skipped()
                    print(f"  [Skipped] {title} @ {company} — already applied in an earlier run")
                    continue

                # Keywords match
                if not matches_target_keywords(title, card_text, profile, cur_role):
                    print(f"  [Skipped] {title} @ {company} (Title/skills do not match required keywords)")
                    continue

                # Location eligibility
                if not is_location_eligible(card_text, profile):
                    print(f"  [Skipped] {title} @ {company} (Location restricted / not eligible)")
                    continue

                # Experience ceiling
                min_exp, max_exp = extract_experience_years(card_text)
                if min_exp is not None and min_exp > profile.seniority_ceiling_years:
                    print(f"  [Skipped] {title} @ {company} (Min experience '{min_exp}+ yrs' exceeds ceiling {profile.seniority_ceiling_years} yrs)")
                    continue

                # Freshness check
                age_days = extract_posted_age_days(card_text)
                if age_days > profile.job_freshness_days:
                    print(f"  [Skipped] {title} @ {company} (Job age {age_days}d exceeds freshness limit {profile.job_freshness_days}d)")
                    continue

                print(f"\n  [Evaluating] {title} @ {company} ({age_days}d ago)")
                total_attempts += 1

                # Click Learn more to open detail modal
                try:
                    if btn.is_visible():
                        btn.click(timeout=4000)
                    time.sleep(2)
                except Exception:
                    page.keyboard.press("Escape")
                    time.sleep(0.5)
                    try:
                        btn.click(timeout=3000)
                        time.sleep(2)
                    except Exception as e:
                        print(f"    Error opening job details: {e}")
                        continue

                # Locate modal dialog or application drawer
                modal = page.locator("[role='dialog'], .modal, [class*='modal'], [class*='Drawer'], div:has-text('YOUR APPLICATION')").first
                if not modal.is_visible():
                    modal = page.locator("body")

                modal_text = modal.inner_text()

                # Refine company and title from modal if present
                for line in modal_text.split("\n"):
                    if line.strip().startswith("APPLY TO "):
                        company = line.replace("APPLY TO ", "").strip()
                        break

                # Check if already applied
                if _is_application_confirmation(modal_text):
                    print(f"    Status: Already applied on Wellfound.")
                    applied_keys.add(page_card_key)
                    stats_tracker.record_previously_applied_skipped()
                    close_modal(page, modal)
                    continue

                # STRICT IN-MODAL RE-VALIDATION:
                # 1. Check for domain mismatch warning (e.g. non-tech/marketing role)
                if "working in a different role" in modal_text.lower():
                    print(f"  [Skipped] {title} @ {company} (Domain mismatch detected: non-tech/different role)")
                    close_modal(page, modal)
                    continue

                # 2. Check in-modal required experience against ceiling
                modal_min_exp, _ = extract_experience_years(modal_text)
                if modal_min_exp is not None and modal_min_exp > profile.seniority_ceiling_years:
                    print(f"  [Skipped] {title} @ {company} (Required experience '{modal_min_exp}+ yrs' in modal exceeds {profile.seniority_ceiling_years} yrs ceiling)")
                    close_modal(page, modal)
                    continue

                # 3. Check in-modal skills and title against target tech keywords
                if not matches_target_keywords(title, modal_text, profile, cur_role):
                    print(f"  [Skipped] {title} @ {company} (Modal details/skills do not match tech stack)")
                    close_modal(page, modal)
                    continue

                # 4. Check in-modal location restrictions
                if not is_location_eligible(modal_text, profile):
                    print(f"  [Skipped] {title} @ {company} (Modal location restricted / not eligible)")
                    close_modal(page, modal)
                    continue

                # Find the submission button (e.g. 'Send application', 'Apply')
                apply_btn = find_apply_button(modal)
                if not apply_btn:
                    print("    No visible Apply / Send application button found in modal.")
                    log_row([datetime.now().isoformat(), "wellfound", title, company, "skipped", "no apply button"])
                    close_modal(page, modal)
                    continue

                btn_text = apply_btn.inner_text().strip()
                if "applied" in btn_text.lower():
                    print(f"    Status: '{btn_text}' — already applied.")
                    applied_keys.add(page_card_key)
                    stats_tracker.record_previously_applied_skipped()
                    close_modal(page, modal)
                    continue

                if dry_run:
                    total_applied += 1
                    print(f"    [DRY RUN] ({total_applied}/{run_limit}) Would apply with pitch note for {title} @ {company}")
                    close_modal(page, modal)
                    continue

                # Auto-fill pitch note in the 'What interests you about working for this company?' input box
                fill_pitch_note(modal, title, company, profile)

                # Fill any additional custom screening questions
                fill_additional_screening_questions(modal, profile)

                # Submit Application
                print(f"    Clicking '{btn_text}'...")
                try:
                    apply_btn.click()
                    time.sleep(3)

                    # Confirmation check
                    updated_modal_text = modal.inner_text()
                    updated_btn_text = apply_btn.inner_text().strip() if apply_btn.is_visible() else ""

                    if _is_application_confirmation(updated_modal_text, updated_btn_text):
                        total_applied += 1
                        applied_keys.add(page_card_key)
                        print(f"    ✅ [SUCCESS] Wellfound application submitted: {title} @ {company} ({total_applied}/{run_limit} this run)")
                        log_row([datetime.now().isoformat(), "wellfound", title, company, "applied", "success"])
                        close_modal(page, modal)

                        # Randomized delay between applications
                        if total_applied < run_limit:
                            delay = random.uniform(
                                float(profile.min_delay_seconds_between_applications),
                                float(profile.max_delay_seconds_between_applications)
                            )
                            print(f"    Waiting {delay:.1f}s before next application...")
                            time.sleep(delay)
                    else:
                        print(f"    Application clicked ('{btn_text}'), recording attempt.")
                        total_applied += 1
                        applied_keys.add(page_card_key)
                        log_row([datetime.now().isoformat(), "wellfound", title, company, "applied", "submitted"])
                        close_modal(page, modal)

                except Exception as e:
                    print(f"    Error during application submission: {e}")
                    log_row([datetime.now().isoformat(), "wellfound", title, company, "error", str(e)])
                    close_modal(page, modal)

        print("\n" + "=" * 65)
        print(f"Wellfound Run Completed. {total_applied} new applications submitted.")
        print("=" * 65)
        browser.close()


def main():
    parser = argparse.ArgumentParser(description="Wellfound (AngelList) Job Application Engine")
    parser.add_argument("--role", type=str, default=None, help="Target role to search (overrides profile.yaml)")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of applications to submit")
    parser.add_argument("--dry-run", action="store_true", help="Evaluate matching jobs without submitting applications")
    args = parser.parse_args()

    run(role=args.role, limit=args.limit, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
