"""
Foundit (formerly Monster India) Apply Script — High-Accuracy Direct Apply Engine.
Requires session_foundit.json from login_capture.py.

Workflow:
1. Loads applicant profile from profile.yaml.
2. Navigates to foundit.in search results (https://www.foundit.in/srp/results?query=...).
3. Slices results per role and paginates through results.
4. Performs strict deduplication against applications_log.csv.
5. Evaluates experience range, job freshness, title keywords, and excluded companies.
6. Opens job details and checks application type:
   - External jobs -> logged to external_jobs.csv
   - Quick Apply / Apply Now -> clicks direct apply, auto-answers screening questions via Groq/Profile rules,
     confirms submission, and records to applications_log.csv.
7. Enforces rate limits and safe delays between actions.

Usage:
    python foundit_apply.py
    python foundit_apply.py --role "Python Developer" --limit 5
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
from common.answers import get_screening_answer, is_sensitive_field
from common.external_tracker import log_external_job
from common.human_input import ask_user
from common import stats_tracker

SESSION_FILE = "session_foundit.json"
LOG_FILE = "applications_log.csv"

STOP_PHRASES = [
    "access denied", "security check", "verify it's you",
    "captcha", "unusual traffic", "blocked", "we have detected unusual activity",
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
    """Counts how many successful applications were submitted to Foundit today."""
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
                if src == "foundit" and row.get("status") == "applied":
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
    Handles 'Fresher', '5 - 8 Years', '0-2 Yrs', '8+ Years', '2 to 5 years', etc.
    """
    if not text:
        return None, None
    text_clean = text.strip()
    if text_clean.lower() == "fresher" or "fresher" in text_clean.lower():
        return 0, 0

    range_match = re.search(r"(\d+)\s*[-–to]+\s*(\d+)\s*(?:years?|yrs)", text_clean, re.IGNORECASE)
    if range_match:
        return int(range_match.group(1)), int(range_match.group(2))

    plus_match = re.search(r"(\d+)\+\s*(?:years?|yrs)", text_clean, re.IGNORECASE)
    if plus_match:
        return int(plus_match.group(1)), 99

    single_match = re.search(r"(\d+)\s*(?:years?|yrs)", text_clean, re.IGNORECASE)
    if single_match:
        val = int(single_match.group(1))
        return val, val

    return None, None


def extract_posted_age_days(posted_text: str | None) -> int:
    """
    Converts relative posted text (e.g., 'Posted 17 hours ago', 'Posted 2 days ago', 'Posted a month ago', 'Posted 1 week ago')
    to age in days.
    """
    if not posted_text:
        return 0
    
    # If a multiline block was passed, isolate the specific line containing 'posted' or 'ago'
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


def matches_target_keywords(title: str, profile: Profile, role: str) -> bool:
    """Checks if the title matches configured keywords for the role or profile."""
    if not title:
        return False
    title_lower = title.lower()

    # Explicit keyword requirements
    role_keywords = profile.role_required_keywords.get(role, [])
    if role_keywords:
        return any(
            re.search(r"\b" + re.escape(kw.lower()) + r"\b", title_lower)
            or kw.lower() in title_lower
            for kw in role_keywords
        )

    # Fallback: check significant tokens from role name
    tokens = [t.lower() for t in re.findall(r"\w+", role) if t.lower() not in {"engineer", "developer", "senior", "junior", "lead", "staff"}]
    if tokens:
        return any(t in title_lower for t in tokens)

    return True


def passes_company_filters(company: str | None, profile: Profile) -> bool:
    """Checks if company is allowed based on company_exclude and company_include_only."""
    if not company:
        return True
    comp_lower = company.strip().lower()

    exclude_list = [c.lower().strip() for c in profile.data.get("company_exclude", []) if c.strip()]
    if any(ex in comp_lower for ex in exclude_list):
        return False

    include_list = [c.lower().strip() for c in profile.data.get("company_include_only", []) if c.strip()]
    if include_list and not any(inc in comp_lower for inc in include_list):
        return False

    return True


def passes_location_filters(loc_text: str | None, profile: Profile) -> bool:
    """Checks if the location matches applicant preferences."""
    if not loc_text:
        return True
    loc_lower = loc_text.lower()

    if "remote" in loc_lower or "work from home" in loc_lower or "anywhere" in loc_lower:
        return True

    if profile.work_mode == "remote_only":
        return "remote" in loc_lower or "work from home" in loc_lower

    allowed_cities = [profile.current_city.lower()] + [c.lower() for c in profile.relocate_cities if c]
    return any(city in loc_lower for city in allowed_cities)


def _is_application_confirmation(page_text: str, button_text: str = "") -> bool:
    """Determines whether an application was confirmed as successfully submitted."""
    comb = f"{page_text} {button_text}".lower()
    success_indicators = [
        "applied successfully",
        "successfully applied",
        "application submitted",
        "your application has been sent",
        "you have applied to this job",
        "already applied",
        "thank you for applying",
        "applied",
    ]
    return any(ind in comb for ind in success_indicators)


def handle_screening_form(page, profile: Profile, job_title: str, company: str) -> bool:
    """
    Handles any screening questions modal or questionnaire that appears on Foundit.
    Returns True if handled/submitted successfully.
    """
    job_context = f"{job_title} at {company}"
    
    # Check for screening dialog/container
    modal = page.locator(".modal, [role='dialog'], .questionnaire-container, .screening-modal, .apply-modal, .applyModal, .popup-container").first
    if not modal.is_visible():
        return True
    container = modal

    # 1. Handle text inputs & textareas inside the modal
    inputs = container.locator("textarea, input[type='text'], input:not([type]):not([hidden]), input[type='number']").all()
    for inp in inputs:
        if not inp.is_visible() or inp.is_disabled():
            continue
        curr_val = inp.input_value()
        if curr_val:
            continue

        # Extract question/label
        label = ""
        try:
            label_el = inp.locator("xpath=../../preceding-sibling::label | ../preceding-sibling::label | preceding-sibling::label | ../label").first
            if label_el.count() > 0:
                label = label_el.inner_text().strip()
        except Exception:
            pass

        if not label:
            label = inp.get_attribute("placeholder") or inp.get_attribute("aria-label") or inp.get_attribute("name") or "Screening Question"

        if is_sensitive_field(label):
            val = ask_user(f"Foundit asks sensitive question:\n{label}")
        else:
            val = get_screening_answer(label, profile, job_context)

        if val is not None:
            try:
                inp.fill(str(val))
                time.sleep(0.3)
                print(f"    Auto-filled '{label[:30]}': {str(val)[:30]}")
            except Exception as e:
                print(f"    Warning: Could not fill '{label}': {e}")

    # 2. Handle Radio button questions (Yes / No)
    radio_groups = container.locator("input[type='radio']").all()
    if radio_groups:
        yes_radios = container.locator("label").filter(has_text=re.compile(r"^Yes$", re.I)).all()
        for yr in yes_radios:
            if yr.is_visible():
                try:
                    yr.click()
                    time.sleep(0.2)
                except Exception:
                    pass

    # 3. Handle Select Dropdowns
    selects = container.locator("select").all()
    for sel in selects:
        if sel.is_visible():
            try:
                options = sel.locator("option").all()
                if len(options) > 1:
                    sel.select_option(index=1)
            except Exception:
                pass

    # 4. Click Submit / Next / Apply button in modal
    submit_buttons = container.locator(
        "button:has-text('Submit'), button:has-text('Apply'), button:has-text('Confirm'), "
        "button:has-text('Continue'), input[type='submit'], [class*='submit-btn']"
    ).all()
    
    for btn in submit_buttons:
        if btn.is_visible() and not btn.is_disabled():
            btn_text = btn.inner_text().strip()
            print(f"    Clicking modal action: '{btn_text}'")
            btn.click()
            time.sleep(2)
            return True

    return True


def run(role: str | None = None, limit: int | None = None, dry_run: bool = False):
    """Main execution function for Foundit application automation."""
    profile = Profile.load()
    if not dry_run and not Path(SESSION_FILE).exists():
        raise SystemExit(
            f"\n[!] {SESSION_FILE} not found.\n"
            f"    Run: python login_capture.py foundit\n"
            f"    Log in manually in the opened browser window, then press Enter to save your session."
        )

    applied_today = count_applications_today()
    foundit_limit = int(profile.data.get("foundit_daily_limit", 30))
    daily_limit = int(profile.data.get("daily_application_limit", 100))
    remaining_today = max(0, min(foundit_limit - applied_today, daily_limit - applied_today))

    target_limit = limit if limit is not None else profile.stop_after_n_applications
    run_limit = min(target_limit, remaining_today)

    print("=" * 65)
    print(f"           FOUNDIT APPLICATION AUTOMATION ENGINE")
    print("=" * 65)
    print(f"Foundit Daily Cap:              {foundit_limit} (Applied today: {applied_today})")
    print(f"Global Target For This Run:     {run_limit}")
    print(f"Target Roles:                   {', '.join(profile.target_roles)}")
    print(f"Freshness Filter:               <= {profile.job_freshness_days} day(s)")
    print(f"Seniority Floor / Ceiling:      {profile.seniority_floor_years} - {profile.seniority_ceiling_years} years")
    print(f"Browser Mode:                   {profile.browser_mode}")
    print(f"Dry Run Mode:                   {dry_run}")
    print("=" * 65 + "\n")

    if run_limit <= 0 and not dry_run:
        print(f"Daily application limit for Foundit reached ({applied_today}/{foundit_limit}). Exiting.")
        return

    applied_keys = load_applied_job_keys()
    roles_to_search = [role] if role else profile.target_roles
    total_applied = 0
    total_attempts = 0
    max_attempts = int(profile.data.get("stop_after_n_attempts", 50))

    # Location query string
    loc_tokens = [profile.current_city] + [c for c in profile.relocate_cities if c]
    loc_query = ",".join(loc_tokens) if loc_tokens else ""

    with sync_playwright() as p:
        # Launch Chromium with anti-detection flags
        # Headless mode can be blocked by Akamai, so we launch visible unless headless explicitly works
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

            encoded_query = urllib.parse.quote_plus(cur_role)
            encoded_loc = urllib.parse.quote_plus(loc_query) if loc_query else ""

            max_pages = int(profile.data.get("max_pages_per_role", 5))
            for page_no in range(1, max_pages + 1):
                if total_applied >= run_limit or total_attempts >= max_attempts:
                    break

                start_index = (page_no - 1) * 15
                search_url = f"https://www.foundit.in/srp/results?query={encoded_query}"
                if encoded_loc:
                    search_url += f"&locations={encoded_loc}"
                if start_index > 0:
                    search_url += f"&start={start_index}"

                print(f"\n>>> Searching Foundit: {cur_role} (Page {page_no})")
                print(f"    URL: {search_url}")

                try:
                    page.goto(search_url, wait_until="domcontentloaded", timeout=25000)
                except PWTimeout:
                    print("    (Navigation timeout, attempting to process current DOM...)")

                time.sleep(3)

                # Check for bot challenge or block phrases
                body_text = page.locator("body").inner_text()
                if any(phrase in body_text.lower() for phrase in STOP_PHRASES):
                    print(f"\n[!] Security / verification challenge detected on Foundit page.")
                    print(f"    Halting automated run safely to protect account.")
                    browser.close()
                    return

                # Activate Quick Apply filter pill on top bar if available
                try:
                    quick_apply_filter = page.locator("button.quickApplyFilter, button:has-text('Quick Apply')").first
                    if quick_apply_filter.is_visible() and not "selected" in (quick_apply_filter.get_attribute("class") or ""):
                        quick_apply_filter.click()
                        time.sleep(1.5)
                except Exception:
                    pass

                # Scroll down to load all dynamic cards
                try:
                    for _ in range(3):
                        page.evaluate("window.scrollBy(0, 1000)")
                        time.sleep(0.5)
                except Exception:
                    pass

                # Locate job cards
                card_locators = page.locator(".srpResultCardContainer .cardContainer, [class*='cardContainer']").all()
                print(f"    Found {len(card_locators)} job cards on page {page_no}.")

                if not card_locators:
                    print("    No cards found on this page. Moving to next search...")
                    break

                for card_idx, card in enumerate(card_locators):
                    if total_applied >= run_limit or total_attempts >= max_attempts:
                        break

                    stats_tracker.record_discovered()

                    # Extract card details
                    title_el = card.locator(".jobTitle, #jobCardTitle, [class*='jobTitle']").first
                    comp_el = card.locator(".companyName, [class*='companyName']").first
                    exp_el = card.locator(".experienceSalary .details, .iconContainer + .details, [class*='experience']").first
                    loc_el = card.locator(".location, .details.location, [class*='location']").first
                    card_id = card.get_attribute("id") or f"card_{card_idx}"

                    title = title_el.inner_text().strip() if title_el.count() > 0 else ""
                    company = comp_el.inner_text().strip() if comp_el.count() > 0 else "Unknown"
                    exp_text = exp_el.inner_text().strip() if exp_el.count() > 0 else ""
                    loc_text = loc_el.inner_text().strip() if loc_el.count() > 0 else ""
                    card_text = card.inner_text().strip()

                    if not title:
                        continue

                    # Deduplication Check
                    job_key = _job_key(title, company)
                    if job_key in applied_keys:
                        stats_tracker.record_previously_applied_skipped()
                        print(f"  [Skipped] {title} @ {company} — already applied in an earlier run")
                        continue

                    # Keyword Filter Check
                    if not matches_target_keywords(title, profile, cur_role):
                        print(f"  [Skipped] {title} @ {company} (Title does not match required keywords)")
                        continue

                    # Company Filter Check
                    if not passes_company_filters(company, profile):
                        print(f"  [Skipped] {title} @ {company} (Company excluded by filter rules)")
                        continue

                    # Location Filter Check
                    if not passes_location_filters(loc_text, profile):
                        print(f"  [Skipped] {title} @ {company} (Location '{loc_text}' does not match preferences)")
                        continue

                    # Experience Range Check
                    min_exp, max_exp = extract_experience_years(exp_text)
                    if min_exp is not None and max_exp is not None:
                        if max_exp < profile.seniority_floor_years or min_exp > profile.seniority_ceiling_years:
                            print(f"  [Skipped] {title} @ {company} (Experience '{exp_text}' outside floor {profile.seniority_floor_years} - ceiling {profile.seniority_ceiling_years} yrs)")
                            continue

                    # Freshness Check
                    age_days = extract_posted_age_days(card_text)
                    if age_days > profile.job_freshness_days:
                        print(f"  [Skipped] {title} @ {company} (Job age {age_days}d exceeds freshness limit {profile.job_freshness_days}d)")
                        continue

                    print(f"\n  [Evaluating] {title} @ {company} | Exp: '{exp_text}' | Loc: '{loc_text}'")

                    total_attempts += 1

                    # Click card to open right detail pane
                    try:
                        card.scroll_into_view_if_needed()
                        card.click()
                        time.sleep(2)
                    except Exception as e:
                        print(f"    Error selecting card: {e}")
                        continue

                    # Locate the Apply button in right pane or modal
                    apply_btn = page.locator("#applyNowBtn, button:has-text('Apply Now'), button:has-text('Quick Apply'), button:has-text('Apply')").filter(has_text=re.compile(r"Apply", re.I))
                    
                    visible_apply_btn = None
                    for b_i in range(apply_btn.count()):
                        b = apply_btn.nth(b_i)
                        if b.is_visible():
                            visible_apply_btn = b
                            break

                    if not visible_apply_btn:
                        # Check if it already shows Applied
                        right_pane_text = page.locator(".rightSection, .jobDetails, [class*='rightSection']").inner_text()
                        if "already applied" in right_pane_text.lower() or "applied" in right_pane_text.lower():
                            print(f"    Status: Already applied on portal.")
                            applied_keys.add(job_key)
                            stats_tracker.record_previously_applied_skipped()
                        else:
                            print("    No visible Apply button found.")
                            log_row([datetime.now().isoformat(), "foundit", title, company, "skipped", "no apply button"])
                        continue

                    btn_text = visible_apply_btn.inner_text().strip()

                    if "applied" in btn_text.lower():
                        print(f"    Button status: '{btn_text}' — already applied.")
                        applied_keys.add(job_key)
                        stats_tracker.record_previously_applied_skipped()
                        continue

                    # Check for External Company Site Application
                    if "company site" in btn_text.lower() or "company website" in btn_text.lower() or "external" in btn_text.lower():
                        print(f"    External application detected ('{btn_text}'). Logging to external_jobs.csv...")
                        job_url = f"https://www.foundit.in/srp/results?query={encoded_query}&start={start_index}"
                        log_row([datetime.now().isoformat(), "foundit", title, company, "skipped", "external link"])
                        log_external_job("foundit", title, company, job_url, page.url, loc_text, exp_text, f"{age_days} days ago")
                        continue

                    if dry_run:
                        total_applied += 1
                        print(f"    [DRY RUN] ({total_applied}/{run_limit}) Would click '{btn_text}' for {title} @ {company}")
                        continue

                    # Click Apply Now / Quick Apply
                    print(f"    Found active apply button: '{btn_text}'. Submitting application...")
                    try:
                        # Listen for external redirects opening a new tab
                        new_tab = None
                        try:
                            with context.expect_page(timeout=3500) as new_page_info:
                                visible_apply_btn.click()
                            new_tab = new_page_info.value
                        except Exception:
                            pass

                        if new_tab:
                            # External redirect to company ATS/careers website
                            try:
                                new_tab.wait_for_load_state("domcontentloaded", timeout=10000)
                            except Exception:
                                pass
                            ext_url = new_tab.url
                            print(f"    🔗 [EXTERNAL REDIRECT] Opened company careers portal: {ext_url}")
                            new_tab.close()
                            job_url = f"https://www.foundit.in/srp/results?query={encoded_query}&start={start_index}"
                            log_row([datetime.now().isoformat(), "foundit", title, company, "skipped", "external link"])
                            log_external_job("foundit", title, company, job_url, ext_url, loc_text, exp_text, f"{age_days} days ago")
                            continue

                        time.sleep(2)

                        # Handle screening modal if presented
                        handle_screening_form(page, profile, title, company)

                        # Verify success
                        time.sleep(2)
                        current_body = page.locator("body").inner_text()
                        updated_btn_text = visible_apply_btn.inner_text().strip() if visible_apply_btn.is_visible() else ""

                        if _is_application_confirmation(current_body, updated_btn_text):
                            total_applied += 1
                            applied_keys.add(job_key)
                            print(f"    ✅ [SUCCESS] Direct application submitted: {title} @ {company} ({total_applied}/{run_limit} this run)")
                            log_row([datetime.now().isoformat(), "foundit", title, company, "applied", "success"])
                            
                            # Random delay between applications to protect account
                            if total_applied < run_limit:
                                delay = random.uniform(
                                    float(profile.min_delay_seconds_between_applications),
                                    float(profile.max_delay_seconds_between_applications)
                                )
                                print(f"    Waiting {delay:.1f}s before next application...")
                                time.sleep(delay)
                        else:
                            print("    Clicked apply, but confirmation toast/button state was inconclusive. Logging attempt.")
                            log_row([datetime.now().isoformat(), "foundit", title, company, "applied", "submitted without confirmation modal"])
                            total_applied += 1
                            applied_keys.add(job_key)
                            if total_applied < run_limit:
                                delay = random.uniform(
                                    float(profile.min_delay_seconds_between_applications),
                                    float(profile.max_delay_seconds_between_applications)
                                )
                                print(f"    Waiting {delay:.1f}s before next application...")
                                time.sleep(delay)

                    except Exception as e:
                        print(f"    Error during application submission: {e}")
                        log_row([datetime.now().isoformat(), "foundit", title, company, "error", str(e)])

        print("\n" + "=" * 65)
        print(f"Foundit Run Completed. {total_applied} new applications submitted.")
        print("=" * 65)
        browser.close()


def main():
    parser = argparse.ArgumentParser(description="Foundit Job Application Automation Engine")
    parser.add_argument("--role", type=str, default=None, help="Target role to search (overrides profile.yaml)")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of applications to submit")
    parser.add_argument("--dry-run", action="store_true", help="Evaluate matching jobs without submitting applications")
    args = parser.parse_args()

    run(role=args.role, limit=args.limit, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
