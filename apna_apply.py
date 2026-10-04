"""
Apna Apply Script — High-Accuracy Direct Apply Engine with UI Sidebar Filters.
Requires session_apna.json from login_capture.py.

Workflow:
1. Loads applicant profile from profile.yaml.
2. Navigates to Apna job search (https://apna.co/jobs?text={role}).
3. Applies UI sidebar filters (Date posted, Work mode / Remote, Work type / Full-time, Department).
4. Reads job cards, extracts titles, companies, required experience, location, and salary.
5. Performs strict deduplication against applications_log.csv.
6. Evaluates experience range, company exclusions, title keywords, and remote/location eligibility.
7. Clicks on matching job cards and presses 'Apply for job' / 'Apply now'.
8. Auto-answers employer screening questions (Notice period, CTC, experience, English fluency, shift availability).
9. Submits the application, verifies submission confirmation, and logs to applications_log.csv.

Usage:
    python apna_apply.py
    python apna_apply.py --role "Full Stack Developer" --limit 5
    python apna_apply.py --dry-run
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
from common.human_input import ask_user
from common.external_tracker import log_external_job
from common import stats_tracker
from common import llm

SESSION_FILE = "session_apna.json"
LOG_FILE = "applications_log.csv"


def is_security_checkpoint(page) -> bool:
    """Accurately checks for bot challenges or access restrictions."""
    try:
        title = page.title().lower()
        if any(t in title for t in ["security check", "robot or human", "access denied", "attention required", "cloudflare"]):
            return True
        challenge_selectors = [
            '#challenge-running', '#cf-challenge-running', '#turnstile-wrapper',
            'iframe[src*="cloudflare"]', 'iframe[src*="recaptcha"]',
            'div[class*="captcha-container"]', 'div[id*="captcha"]'
        ]
        for sel in challenge_selectors:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=300):
                return True
        body = page.locator("body").inner_text(timeout=500).lower()
        if "verify you are human" in body or "please enable cookies and reload" in body:
            return True
    except Exception:
        pass
    return False


def dismiss_apna_overlays(page):
    """Closes promo modals, app install popups, notification prompts, or banners in Apna."""
    try:
        page.keyboard.press("Escape")
        time.sleep(0.3)
    except Exception:
        pass

    close_selectors = [
        'button[aria-label="Close"]',
        'button[aria-label="close"]',
        'button.close-modal',
        'div[role="dialog"] button:has-text("✕")',
        'button:has-text("✕")',
        'button:has-text("Later")',
        'button:has-text("Skip")',
        'button:has-text("Got it")',
        'button:has-text("Not now")',
        'button:has-text("Remind me later")',
        'div[class*="close" i]',
        'svg[class*="close" i]'
    ]
    for sel in close_selectors:
        try:
            btns = page.locator(sel)
            count = btns.count()
            for i in range(min(count, 3)):
                btn = btns.nth(i)
                if btn.is_visible(timeout=150):
                    btn.click(timeout=1000, force=True)
                    time.sleep(0.3)
        except Exception:
            pass


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
                if row.get("status") in ("applied", "dry_run_success")
            }
    except (OSError, csv.Error):
        return set()


def count_applications_today(path: str = LOG_FILE, today: date | None = None) -> int:
    """Counts how many successful applications were submitted to Apna today."""
    log_path = Path(path)
    if not log_path.exists():
        return 0
    today = today or date.today()
    count = 0
    try:
        with log_path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("status") in ("applied", "dry_run_success"):
                    src = (row.get("source") or "").lower()
                    if src == "apna":
                        ts_str = row.get("timestamp") or ""
                        try:
                            row_date = datetime.fromisoformat(ts_str.replace(" ", "T")).date()
                        except Exception:
                            row_date = today
                        if row_date == today:
                            count += 1
    except (OSError, csv.Error):
        return 0
    return count


def parse_experience_years(text: str) -> tuple[int | None, int | None]:
    """
    Extracts min and max required experience years from text.
    Handles 'Min. 2 years', 'Min. 1 year', '0 - 3 yrs', '2 - 5 years', '3+ yrs', 'Any experience', 'Fresher'.
    """
    if not text:
        return None, None
    t_lower = text.lower()
    if "any experience" in t_lower or "fresher" in t_lower or "no experience" in t_lower:
        return 0, 0
    m_min = re.search(r'min\.?\s*(\d+)\s*(?:years?|yrs?)', t_lower)
    if m_min:
        return int(m_min.group(1)), int(m_min.group(1))
    m = re.search(r'(\d+)\s*[-–to]+\s*(\d+)\s*(?:years?|yrs?)', t_lower)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r'(\d+)\+\s*(?:years?|yrs?)', t_lower)
    if m:
        return int(m.group(1)), 99
    m = re.search(r'(\d+)\s*(?:years?|yrs?)', t_lower)
    if m:
        return int(m.group(1)), int(m.group(1))
    return None, None


def matches_target_keywords(title: str, profile: Profile, target_role: str) -> bool:
    """Evaluates if the job title matches candidate skills or configured keywords."""
    if not title:
        return False
    title_lower = title.lower()

    # Exclude non-tech / mismatched functions
    anti_patterns = [
        "sales", "recruiter", "talent acquisition", "hr manager", "human resources",
        "accountant", "telecaller", "bpo", "content writer", "marketing", "graphic designer",
        "video editor", "delivery partner", "typist", "mis coordinator", "architect",
        "cashier", "cook", "driver", "field executive", "receptionist", "security guard",
        "technician", "electrician", "nurse", "pharmacist", "counselor", "data entry"
    ]
    is_tech = any(r in title_lower for r in ["engineer", "developer", "programmer", "software", "frontend", "backend", "full stack", "fullstack", "web", "react", "python", "node", "java"])
    if any(p in title_lower for p in anti_patterns) and not is_tech:
        return False

    req_keywords = profile.role_required_keywords.get(target_role, []) if hasattr(profile, "role_required_keywords") else []
    if req_keywords:
        return any(kw.lower() in title_lower for kw in req_keywords)

    role_tokens = [tok.lower() for tok in re.split(r'\W+', target_role) if len(tok) > 2]
    return any(tok in title_lower for tok in role_tokens) or is_tech


def passes_company_filters(company: str, profile: Profile) -> bool:
    """Verifies that the employer does not match company exclusion rules."""
    if not company:
        return True
    c_lower = company.lower()
    for exc in getattr(profile, "company_exclude", []):
        if exc.lower() in c_lower:
            return False
    if getattr(profile, "company_include_only", []):
        return any(inc.lower() in c_lower for inc in profile.company_include_only)
    return True


def passes_location_filters(loc_text: str, profile: Profile) -> bool:
    """
    Verifies that job location matches candidate's strict Apna preferences:
    - Remote / Work from home
    - Hyderabad / Secunderabad
    All other onsite cities (e.g. Coimbatore, Bengaluru, Pune, Delhi) are rejected.
    """
    if not loc_text:
        return False
    l_lower = loc_text.lower()
    
    # 1. Any remote / WFH indicator
    if any(k in l_lower for k in ["remote", "work from home", "wfh", "anywhere", "worldwide"]):
        return True

    # 2. Candidate's home city: Hyderabad / Secunderabad
    if "hyderabad" in l_lower or "secunderabad" in l_lower:
        return True

    return False


def is_job_eligible(job: dict, profile: Profile, target_role: str) -> tuple[bool, str]:
    """Evaluates candidate eligibility against job listing attributes."""
    title = job.get("title", "")
    company = job.get("company", "")
    loc = job.get("location", "")
    exp_text = job.get("experience_text", "")

    if not passes_company_filters(company, profile):
        return False, f"Company '{company}' matches exclusion list"

    if not matches_target_keywords(title, profile, target_role):
        return False, f"Title '{title}' does not match target role '{target_role}'"

    if not passes_location_filters(loc, profile):
        return False, f"Location '{loc}' is not Hyderabad or Remote"

    min_exp, max_exp = parse_experience_years(exp_text)
    candidate_exp = getattr(profile, "total_experience_years", 3)
    if min_exp is not None and min_exp > (candidate_exp + 1):
        return False, f"Required experience ({min_exp} yrs) exceeds candidate profile ({candidate_exp} yrs)"

    return True, "Eligible"


def apply_apna_sidebar_filters(page, profile: Profile):
    """
    Applies UI sidebar filters on Apna's job search page:
    1. Date posted (Last 24 hours / Last 3 days / Last 7 days)
    2. Work mode (Work from home if remote_first / remote_only)
    3. Work type (Full time)
    """
    time.sleep(1.5)
    dismiss_apna_overlays(page)

    def _click_filter_option(loc):
        try:
            loc.scroll_into_view_if_needed(timeout=1500)
            time.sleep(0.2)
            loc.click(timeout=1500, force=True)
            return True
        except Exception:
            try:
                loc.evaluate("el => el.click()")
                return True
            except Exception:
                return False

    # 1. Date posted filter
    freshness = getattr(profile, "job_freshness_days", 7)
    target_date_label = "Last 24 hours" if freshness <= 1 else ("Last 3 days" if freshness <= 3 else "Last 7 days")
    try:
        date_opt = page.locator(f'label:has-text("{target_date_label}"), span:has-text("{target_date_label}")').first
        if date_opt.is_visible(timeout=1500):
            print(f"  [Filter] Applying Date posted -> '{target_date_label}'...")
            _click_filter_option(date_opt)
            time.sleep(1)
        else:
            date_accordion = page.locator('div:has-text("Date posted"), button:has-text("Date posted"), p:has-text("Date posted")').first
            if date_accordion.is_visible(timeout=1000):
                _click_filter_option(date_accordion)
                time.sleep(0.5)
                date_opt = page.locator(f'label:has-text("{target_date_label}"), span:has-text("{target_date_label}")').first
                if date_opt.is_visible(timeout=1000):
                    _click_filter_option(date_opt)
                    time.sleep(1)
    except Exception as e:
        print(f"  [Filter] Note on Date posted filter: {e}")

    # 2. Work mode filter (Work from home)
    work_mode = getattr(profile, "work_mode", "flexible")
    if work_mode in ("remote_only", "remote_first"):
        try:
            wfh_opt = page.locator('label:has-text("Work from home"), span:has-text("Work from home")').first
            if wfh_opt.is_visible(timeout=1500):
                chk = wfh_opt.locator('input[type="checkbox"]').first
                if chk.count() == 0 or not chk.is_checked():
                    print("  [Filter] Applying Work mode -> 'Work from home'...")
                    _click_filter_option(wfh_opt)
                    time.sleep(1)
            else:
                wfh_accordion = page.locator('div:has-text("Work mode"), button:has-text("Work mode")').first
                if wfh_accordion.is_visible(timeout=1000):
                    _click_filter_option(wfh_accordion)
                    time.sleep(0.5)
                    wfh_opt = page.locator('label:has-text("Work from home"), span:has-text("Work from home")').first
                    if wfh_opt.is_visible(timeout=1000):
                        _click_filter_option(wfh_opt)
                        time.sleep(1)
        except Exception as e:
            print(f"  [Filter] Note on Work mode filter: {e}")

    # 3. Work type filter (Full time)
    try:
        ft_opt = page.locator('label:has-text("Full time"), span:has-text("Full time")').first
        if ft_opt.is_visible(timeout=1500):
            chk = ft_opt.locator('input[type="checkbox"]').first
            if chk.count() == 0 or not chk.is_checked():
                print("  [Filter] Applying Work type -> 'Full time'...")
                _click_filter_option(ft_opt)
                time.sleep(1)
    except Exception as e:
        print(f"  [Filter] Note on Work type filter: {e}")

    dismiss_apna_overlays(page)
    time.sleep(1.5)


def handle_apna_screening_modal(page, job_info: dict, profile: Profile, dry_run: bool = False) -> tuple[str, str]:
    """
    Handles Apna's application flow and questionnaire dialogs:
    1. Reads questions / input fields strictly within modal/dialog
    2. Answers radio buttons (experience, notice period, English fluency, shift, relocation)
    3. Fills text/numeric inputs (CTC, experience, city)
    4. Clicks Continue / Next / Submit Application
    5. Confirms completion
    """
    time.sleep(1.5)
    dismiss_apna_overlays(page)

    max_steps = 5
    for step in range(max_steps):
        # Check if already submitted / success banner
        page_text = page.locator("body").inner_text(timeout=500).lower()
        if any(w in page_text for w in ["applied successfully", "application submitted", "application sent", "hr will contact you", "contact hr"]):
            return "applied", "Successfully submitted Apna application"

        # STRICT: Locate modal or drawer dialog container only
        modal = page.locator('div[role="dialog"], div[class*="modal" i], div[class*="drawer" i], div[class*="popup" i], div[class*="bottomSheet" i]').first
        if not modal.is_visible(timeout=1000):
            # If no modal opened, check if 1-click apply succeeded
            if any(w in page_text for w in ["applied", "submitted", "application sent"]):
                return "applied", "1-click application registered on Apna"
            break

        # Check for "You may not be eligible" location modal
        try:
            container_text = container.inner_text().lower()
            if "you may not be eligible" in container_text or "this job requires" in container_text:
                if not any(k in container_text for k in ["hyderabad", "secunderabad", "work from home", "remote", "anywhere"]):
                    print("  [Ineligible Modal] Job requires strict non-Hyderabad on-site location. Skipping.")
                    close_btn = container.locator('button[aria-label="Close"], button:has-text("See Other Jobs"), button:has-text("✕")').first
                    if close_btn.is_visible(timeout=500):
                        close_btn.click(force=True)
                    return "ineligible", "Job requires non-Hyderabad location"
        except Exception:
            pass

        # 1. Handle Radio buttons / option choices inside modal
        try:
            # Check for English fluency question
            if "english" in container.inner_text().lower():
                good_eng = container.locator('label:has-text("Good"), label:has-text("Fluent"), label:has-text("Intermediate"), span:has-text("Good"), span:has-text("Fluent")').first
                if good_eng.is_visible(timeout=500):
                    good_eng.click(force=True)
                    time.sleep(0.3)

            # Check for Yes/No questions (e.g. Do you have experience, willing to work, comfortable with shift)
            yes_opts = container.locator('label:has-text("Yes"), button:has-text("Yes"), span:has-text("Yes")')
            for i in range(yes_opts.count()):
                y = yes_opts.nth(i)
                if y.is_visible(timeout=300):
                    y.click(force=True)
                    time.sleep(0.2)

            # Check for Notice Period / Availability radio options
            if any(w in container.inner_text().lower() for w in ["notice", "joining", "how soon"]):
                imm_opt = container.locator('label:has-text("Immediately"), label:has-text("15 days"), label:has-text("0 days"), span:has-text("Immediately")').first
                if imm_opt.is_visible(timeout=500):
                    imm_opt.click(force=True)
                    time.sleep(0.3)
        except Exception:
            pass

        # 2. Handle Text / Number Inputs strictly inside the modal container
        try:
            inputs = container.locator('input[type="text"]:not([role="combobox"]), input[type="number"], textarea').all()
            for inp in inputs:
                if not inp.is_visible() or inp.is_disabled():
                    continue
                curr = inp.input_value()
                if curr and len(curr.strip()) > 0:
                    continue

                label = ""
                try:
                    label_el = inp.locator('xpath=ancestor::*[label or contains(@class, "field") or contains(@class, "form")][1]').first
                    if label_el.count() > 0:
                        label = label_el.inner_text().strip()
                except Exception:
                    pass

                if not label:
                    label = inp.get_attribute("placeholder") or inp.get_attribute("aria-label") or inp.get_attribute("name") or "Question"

                if is_sensitive_field(label):
                    ans = ask_user(f"Apna screening asks sensitive field:\n{label}")
                else:
                    ans = get_screening_answer(label, profile, f"{job_info['title']} at {job_info['company']}")

                if ans is not None:
                    inp.fill(str(ans))
                    time.sleep(0.3)
        except Exception:
            pass

        # 3. Handle Select Dropdowns inside modal
        try:
            selects = container.locator('select').all()
            for sel in selects:
                if sel.is_visible():
                    opts = sel.locator('option').all()
                    if len(opts) > 1:
                        sel.select_option(index=1)
                        time.sleep(0.3)
        except Exception:
            pass

        if dry_run:
            return "dry_run_success", "Dry run completed at Apna apply step"

        # 4. Click Submit / Continue / Next / Apply button in modal
        action_btn = container.locator(
            'button:has-text("Submit"), button:has-text("Apply for job"), button:has-text("Apply now"), '
            'button:has-text("Continue"), button:has-text("Next"), button:has-text("Confirm"), button:has-text("Send")'
        ).first

        if action_btn.is_visible(timeout=1500) and not action_btn.is_disabled():
            action_btn.click(timeout=3000, force=True)
            time.sleep(2.5)

            # Check if modal closed or success text appeared
            page_text_after = page.locator("body").inner_text(timeout=500).lower()
            if any(w in page_text_after for w in ["applied successfully", "application submitted", "application sent", "hr will contact you", "contact hr"]):
                return "applied", "Successfully submitted Apna application"
        else:
            break

    # Final check for completion
    body_text = page.locator("body").inner_text(timeout=500).lower()
    if any(w in body_text for w in ["applied", "submitted", "application sent", "contact hr"]):
        return "applied", "Application completed on Apna"

    return "uncertain", "Could not verify final submission confirmation on Apna"


# =========================================================================
# MAIN APNA EXECUTION ENGINE
# =========================================================================

def run(limit: int | None = None, specific_role: str | None = None, dry_run: bool = False):
    """Main Apna application execution loop."""
    profile = Profile.load()
    roles = [specific_role] if specific_role else profile.target_roles
    applied_keys = load_applied_job_keys()
    today_applied = count_applications_today()
    platform_threshold = int(profile.data.get("apna_limit", profile.data.get("apna_run_limit", profile.data.get("apna_daily_limit", 25))))
    target_limit = limit if limit is not None else platform_threshold
    run_limit = min(target_limit, int(profile.stop_after_n_applications or 100))

    print(f"=== Apna Apply Automation ===")
    print(f"Date: {date.today()} | Applied today: {today_applied} | Platform Threshold: {platform_threshold} | Target This Run: {run_limit}")
    if dry_run:
        print(">>> RUNNING IN DRY-RUN MODE: No real applications will be submitted. <<<")

    session_path = Path(SESSION_FILE)
    if not session_path.exists():
        print(f"Error: {SESSION_FILE} not found! Run `python login_capture.py apna` first to save your logged-in session.")
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
            viewport={"width": 1366, "height": 900}
        )
        page = context.new_page()

        for role_idx, target_role in enumerate(roles, 1):
            if applied_count >= run_limit:
                break

            encoded_role = urllib.parse.quote_plus(target_role)
            search_url = f"https://apna.co/jobs?location=Hyderabad&search=true&text={encoded_role}"

            print(f"\n{'='*55}")
            print(f"[{role_idx}/{len(roles)}] Searching Apna for: {target_role} (Hyderabad & Remote)")
            print(f"URL: {search_url}")
            print(f"{'='*55}")

            try:
                page.goto(search_url, wait_until="domcontentloaded", timeout=35000)
                time.sleep(3)
            except Exception as e:
                print(f"[Error] Failed to load Apna search URL: {e}")
                continue

            if is_security_checkpoint(page):
                print("🚨 Bot challenge detected on Apna. Halting safely.")
                break

            dismiss_apna_overlays(page)

            # Apply UI filters (Date posted, Work mode, Work type)
            apply_apna_sidebar_filters(page, profile)

            # Scroll down smoothly to load cards
            for _ in range(3):
                page.mouse.wheel(0, 600)
                time.sleep(0.8)

            # Extract job cards from Apna search results
            job_cards = page.evaluate("""() => {
                const cards = [];
                const cardSelectors = [
                    'div[class*="jobCard" i]',
                    'div[class*="JobCard" i]',
                    'div[class*="job-card" i]',
                    'a[href*="/job/"]',
                    'div:has(h2):has(p)',
                    'div[class*="card" i]'
                ];

                let rawCards = [];
                for (const sel of cardSelectors) {
                    const found = document.querySelectorAll(sel);
                    if (found.length > 0) {
                        rawCards = Array.from(found);
                        break;
                    }
                }

                if (rawCards.length === 0) {
                    const headings = document.querySelectorAll('h2, h3, p[class*="title" i]');
                    headings.forEach(h => {
                        const c = h.closest('div[style*="border"], div[class*="item" i], div[class*="card" i]') || h.parentElement;
                        if (c && !rawCards.includes(c)) {
                            rawCards.push(c);
                        }
                    });
                }

                rawCards.forEach((card, idx) => {
                    const textContent = card.innerText || '';
                    const titleEl = card.querySelector('h2, h3, p[class*="title" i], [class*="heading" i]');
                    const compEl = card.querySelector('p[class*="company" i], div[class*="company" i], span[class*="company" i]');
                    const linkEl = card.querySelector('a[href*="/job/"]') || (card.tagName === 'A' ? card : null);

                    let title = titleEl ? titleEl.innerText.trim() : '';
                    let company = compEl ? compEl.innerText.trim() : '';

                    if (!title || !company) {
                        const lines = textContent.split('\\n').map(l => l.trim()).filter(Boolean);
                        if (lines.length >= 2) {
                            title = title || lines[0];
                            company = company || lines[1];
                        }
                    }

                    // Extract Experience text
                    const expMatch = textContent.match(/(?:Min\\.?\\s*\\d+\\s*(?:years?|yrs?)|\\d+\\s*[-–to]+\\s*\\d+\\s*(?:years?|yrs?)|\\d+\\+\\s*(?:years?|yrs?)|Any experience|Fresher)/i);
                    const expText = expMatch ? expMatch[0] : '';

                    // Extract Location text & href
                    const href = linkEl ? linkEl.getAttribute('href') || '' : '';
                    let locText = '';
                    if (textContent.includes('Work from home') || textContent.includes('Remote') || href.includes('work-from-home') || href.includes('remote')) {
                        locText = 'Work from home';
                    } else if (href.includes('hyderabad') || href.includes('secunderabad') || /hyderabad|secunderabad/i.test(textContent)) {
                        locText = 'Hyderabad';
                    } else {
                        const hrefParts = href.split('/').filter(Boolean);
                        let slugCity = '';
                        if (hrefParts.length >= 2 && hrefParts[0] === 'job') {
                            slugCity = hrefParts[1].replace(/-region$/, '').replace(/-/g, ' ');
                        }
                        const locMatch = (textContent + ' ' + href).match(/(?:Kalapatti|Coimbatore|Bengaluru|Bangalore|Pune|Mumbai|Delhi|Noida|Gurgaon|Gurugram|Chennai|Kolkata|Ahmedabad|Jaipur|Chandigarh|Kochi|Indore|Lucknow|Bhopal)/i);
                        locText = locMatch ? locMatch[0] : (slugCity || 'Other City');
                    }

                    if (title && company) {
                        cards.push({
                            index: idx,
                            title: title,
                            company: company,
                            location: locText,
                            experience_text: expText,
                            href: href
                        });
                    }
                });

                return cards;
            }""")

            if not job_cards:
                print(f"No job cards found for '{target_role}' on Apna.")
                continue

            print(f"Found {len(job_cards)} job cards on Apna for '{target_role}'.")

            for card_info in job_cards:
                if applied_count >= run_limit:
                    break

                title = card_info["title"]
                company = card_info["company"]
                job_key = _job_key(title, company)
                href = card_info.get("href", "")

                print(f"\n👉 Processing: {title} @ {company} ({card_info['location']} | {card_info['experience_text'] or 'Any exp'})")

                # Deduplication
                if job_key in applied_keys:
                    print(f"  [Skip] Already applied previously to {title} @ {company}.")
                    stats_tracker.record_previously_applied_skipped(1)
                    continue

                # Eligibility check
                eligible, reason = is_job_eligible(card_info, profile, target_role)
                if not eligible:
                    print(f"  [Skip] Ineligible: {reason}")
                    continue

                # Open job details page directly
                attempt_count += 1
                print(f"  [Apply] Opening details for {title} @ {company}...")

                try:
                    if href and href.startswith("/job/"):
                        job_url = f"https://apna.co{href}"
                        page.goto(job_url, wait_until="domcontentloaded", timeout=25000)
                        time.sleep(2)
                    elif href and href.startswith("http"):
                        page.goto(href, wait_until="domcontentloaded", timeout=25000)
                        time.sleep(2)
                    else:
                        card_locators = page.locator('div[class*="jobCard" i], div[class*="JobCard" i], div[class*="job-card" i], a[href*="/job/"]')
                        if card_locators.count() > card_info["index"]:
                            card_locators.nth(card_info["index"]).click(timeout=3000, force=True)
                        else:
                            page.locator(f'text="{title}"').first.click(timeout=3000, force=True)
                        time.sleep(2)
                except Exception as e:
                    print(f"  [Warning] Could not open job details: {e}")
                    continue

                dismiss_apna_overlays(page)

                # Check if already applied on page
                body_text = page.locator("body").inner_text(timeout=1000).lower()
                if "already applied" in body_text:
                    print(f"  [Info] Already marked as applied on Apna.")
                    applied_keys.add(job_key)
                    continue

                # Look for the prominent green "Apply for job" / "Apply now" button
                try:
                    apply_btns = page.locator(
                        'button:has-text("Apply for job"), button:has-text("Apply now"), button:has-text("Apply"), '
                        'a:has-text("Apply for job"), a:has-text("Apply now")'
                    ).all()

                    apply_btn = None
                    for b in apply_btns:
                        if b.is_visible():
                            box = b.bounding_box()
                            # Prefer main card button below header (y > 80)
                            if box and box["y"] > 80:
                                apply_btn = b
                                break
                    if not apply_btn and len(apply_btns) > 0:
                        apply_btn = apply_btns[0]

                    if not apply_btn or not apply_btn.is_visible(timeout=2000):
                        print(f"  [Warning] Apply button not found on job page.")
                        continue

                    btn_text = apply_btn.inner_text().strip()
                    btn_href = apply_btn.get_attribute("href") or ""

                    # Check if button directs externally
                    if any(w in btn_text.lower() for w in ["company website", "company site", "external"]) or (btn_href.startswith("http") and "apna.co" not in btn_href):
                        ext_url = btn_href or page.url
                        print(f"  [External Link] Job directs to external careers portal ({ext_url}).")
                        log_external_job("apna", title, company, page.url, ext_url, card_info.get("location", ""), card_info.get("experience_text", ""), profile=profile)
                        print(f"  -> Saved to external_jobs.csv (Skipping to find direct in-app job).")
                        timestamp = datetime.now().isoformat(timespec="seconds")
                        log_row([timestamp, "apna", title, company, "external_saved", f"External careers link: {ext_url}"])
                        continue

                    # If dry run mode, stop before clicking real submit
                    if dry_run:
                        print(f"  [Dry Run] Ready to apply to {title} @ {company} via Apna Direct Apply button.")
                        applied_count += 1
                        applied_keys.add(job_key)
                        stats_tracker.record_discovered(1)
                        timestamp = datetime.now().isoformat(timespec="seconds")
                        log_row([timestamp, "apna", title, company, "dry_run_success", "Dry run verified direct apply button"])
                        continue

                    # Click apply button and capture any popup tab
                    opened_popup = None
                    try:
                        with context.expect_page(timeout=2500) as popup_info:
                            apply_btn.scroll_into_view_if_needed()
                            time.sleep(0.2)
                            apply_btn.click(timeout=3000, force=True)
                        opened_popup = popup_info.value
                    except Exception:
                        pass

                    # If click opened an external tab
                    if opened_popup:
                        time.sleep(1)
                        ext_url = opened_popup.url
                        if "apna.co" not in ext_url:
                            print(f"  [External Redirect] Job opened external career site: {ext_url}")
                            log_external_job("apna", title, company, page.url, ext_url, card_info.get("location", ""), card_info.get("experience_text", ""), profile=profile)
                            print(f"  -> Saved to external_jobs.csv (Skipping to find direct in-app job).")
                            try:
                                opened_popup.close()
                            except Exception:
                                pass
                            timestamp = datetime.now().isoformat(timespec="seconds")
                            log_row([timestamp, "apna", title, company, "external_saved", f"External redirect: {ext_url}"])
                            continue
                        else:
                            page = opened_popup

                    # Check if main page navigated outside apna.co
                    if "apna.co" not in page.url:
                        ext_url = page.url
                        print(f"  [External Redirect] Navigated to external site: {ext_url}")
                        log_external_job("apna", title, company, page.url, ext_url, card_info.get("location", ""), card_info.get("experience_text", ""), profile=profile)
                        print(f"  -> Saved to external_jobs.csv (Skipping to find direct in-app job).")
                        timestamp = datetime.now().isoformat(timespec="seconds")
                        log_row([timestamp, "apna", title, company, "external_saved", f"External redirect: {ext_url}"])
                        continue

                except Exception as e:
                    print(f"  [Warning] Apply button error: {e}")
                    continue

                # Handle Direct In-App Questionnaire / Screening Dialog on Apna
                status, reason = handle_apna_screening_modal(page, card_info, profile, dry_run=dry_run)
                print(f"  -> Result: {status.upper()} ({reason})")

                # Log row to applications_log.csv
                timestamp = datetime.now().isoformat(timespec="seconds")
                log_row([timestamp, "apna", title, company, status, reason])

                if status in ("applied", "dry_run_success"):
                    applied_count += 1
                    applied_keys.add(job_key)
                    stats_tracker.record_discovered(1)

                    if not dry_run and applied_count < run_limit:
                        delay = random.randint(
                            int(profile.min_delay_seconds_between_applications),
                            int(profile.max_delay_seconds_between_applications)
                        )
                        print(f"  ⏳ Waiting {delay}s before next application to maintain natural cadence...")
                        time.sleep(delay)

        browser.close()

    print(f"\n==========================================")
    print(f"Apna Run Summary: Direct Applied: {applied_count} | Attempts: {attempt_count}")
    print(f"==========================================")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Apna Job Application Automation")
    parser.add_argument("--role", type=str, default=None, help="Target specific role override")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of applications to submit")
    parser.add_argument("--dry-run", action="store_true", help="Preview mode: runs all steps up to the final submit")
    args = parser.parse_args()

    run(limit=args.limit, specific_role=args.role, dry_run=args.dry_run)
