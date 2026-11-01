"""
Naukri auto-apply. Requires session_naukri.json from login_capture.py.

Usage:
    python naukri_apply.py

Stops the ENTIRE run immediately and reports if it hits a CAPTCHA, a login
prompt, or a rate-limit warning. Never attempts to solve any of those --
that's the point. Everything else (a bad selector, a question it can't
answer, a stray page error) is handled per-listing: that one listing is
skipped and logged, and the run continues.
"""
import csv
import random
import re
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from common.profile import Profile
from common import llm
from common import learned_answers
from common.human_input import ask_user
from common import stats_tracker
from common.answers import direct_profile_answer, get_screening_answer

SESSION_FILE = "session_naukri.json"
LOG_FILE = "applications_log.csv"

STOP_PHRASES = [
    "too many requests", "unusual activity", "verify you are human",
    "captcha", "temporarily blocked", "please try again later and reduce",
    "there was an error while processing your request", "access denied",
    "oops! something went wrong", "there was an error loading the page",
]

# Questions matching any of these should NEVER be answered by the LLM --
# these are exact personal facts an LLM could easily hallucinate wrong.
# Always routed straight to you and never stored for reuse.
SENSITIVE_FIELD_HINTS = [
    "date of birth", "dob", "pan number", "pan card", "aadhar", "aadhaar",
    "passport", "bank account", "ifsc", "father's name", "father name",
    "mother's name", "mother name", "marital status", "blood group",
    "emergency contact", "voter id", "driving licence", "driving license",
]


# Specific phrases per answer-library key, checked as whole phrases (not
# single split words) so "current ctc" and "current city" can never collide
# just because they both start with "current". This is what the old
# key.split("_")[0] logic got wrong -- it reduced both to "current" and
# whichever key came first in the dict won, regardless of what was asked.
TRIGGER_PHRASES = {
    "first_name": ["first name", "given name", "forename"],
    "last_name": ["last name", "family name", "surname"],
    "full_name": ["full name", "your name", "candidate name", "applicant name"],
    "dob": ["date of birth", "dob", "birth date", "birthdate", "d.o.b"],
    "email": ["email address", "email id", "e-mail", "email"],
    "phone": ["mobile number", "phone number", "contact number", "mobile", "phone"],
    "graduation_year": [
        "graduation year", "year of graduation", "passing year", "year of passing",
        "passout year", "pass out year", "completion year", "year of completion",
        "year graduated", "graduated in", "passing out year", "year of pass", "end year",
        "graduation date", "completion date",
    ],
    "education_start_year": [
        "start year", "starting year", "year of joining", "commencement year", "start date",
    ],
    "school_name": [
        "school name", "college name", "university name", "institute name", "institution name",
        "school or university", "name of school", "name of college", "name of university",
        "name of institute", "alma mater", "school", "university", "college", "institute", "institution",
    ],
    "degree_name": [
        "degree name", "qualification held", "highest qualification held", "highest qualification",
        "education level", "highest degree", "highest level of education", "degree",
    ],
    "field_of_study": [
        "field of study", "major", "specialization", "branch", "stream", "discipline", "department", "course",
    ],
    "postal_code": [
        "zip/postal code", "postal code", "zip code", "pin code", "pincode", "zip",
    ],
    "state_province": [
        "state/province", "state", "province",
    ],
    "country_name": [
        "country of residence", "nationality", "country",
    ],
    "current_job_title": [
        "current job title", "current title", "current designation", "present title",
        "present designation", "official title", "job title", "designation",
    ],
    "current_employer": [
        "current employer", "current company", "present employer", "present company",
    ],
    "skills_csv": [
        "skill set", "technical skills", "primary skills", "key skills",
    ],
    "gender": [
        "gender", "sex",
    ],
    "notice_period": ["notice period", "notice", "joining time", "how soon"],
    "current_ctc": ["current ctc", "current salary", "current compensation", "present ctc", "present salary", "current pay"],
    "expected_ctc": ["expected ctc", "expected salary", "expected compensation", "desired ctc", "expectation"],
    "current_city": ["current city", "current location", "which city", "current place", "located in", "base location", "city"],
    "relocate": ["relocate", "relocation", "willing to move"],
    "night_shift": ["night shift"],
    "weekend_work": ["weekend"],
}

TOTAL_EXPERIENCE_PHRASES = (
    "total experience",
    "overall experience",
    "total years of experience",
    "overall years of experience",
    "professional experience",
    "years of experience",
    "experience in years",
    "work experience",
    "how many years of experience",
)


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
    """Load confirmed applications so overlapping searches cannot reapply."""
    log_path = Path(path)
    if not log_path.exists():
        return set()
    try:
        with log_path.open(newline="") as f:
            return {
                _job_key(row.get("title"), row.get("company"))
                for row in csv.DictReader(f)
                if row.get("status") == "applied"
            }
    except (OSError, csv.Error):
        return set()


def count_applications_today(path: str = LOG_FILE, today=None) -> int:
    """Count confirmed applications on the local calendar day."""
    target_date = today or datetime.now().date()
    log_path = Path(path)
    if not log_path.exists():
        return 0
    count = 0
    try:
        with log_path.open(newline="") as f:
            for row in csv.DictReader(f):
                if row.get("status") != "applied":
                    continue
                try:
                    if datetime.fromisoformat(row["timestamp"]).date() == target_date:
                        count += 1
                except (KeyError, TypeError, ValueError):
                    continue
    except (OSError, csv.Error):
        return 0
    return count


def wait_before_next_application(profile: Profile) -> float:
    """Pause between application attempts using bounded random jitter."""
    legacy_delay = float(profile.data.get("pace_seconds_between_actions", 60))
    minimum = float(
        profile.data.get("min_delay_seconds_between_applications", legacy_delay)
    )
    maximum = float(
        profile.data.get("max_delay_seconds_between_applications", minimum)
    )
    if minimum < 0 or maximum < minimum:
        raise ValueError(
            "application delay must satisfy 0 <= minimum <= maximum"
        )
    delay = random.uniform(minimum, maximum)
    print(f"  Waiting {delay:.0f}s before the next application attempt...")
    time.sleep(delay)
    return delay


def _title_has_keyword(title: str, keyword: str) -> bool:
    """Match a role keyword on text boundaries so `java` won't match `javascript`."""
    return bool(
        re.search(rf"(?<!\w){re.escape(keyword.lower())}(?!\w)", title.lower())
    )


def passes_filters(card: dict, profile: Profile, role: str) -> tuple[bool, str]:
    title = (card.get("title") or "").lower()
    company = (card.get("company") or "").lower()
    location = (card.get("location") or "").lower()

    required_keywords = profile.data.get("role_required_keywords", {}).get(role)
    if not required_keywords:
        generic = {"engineer", "developer", "administrator", "analyst", "senior", "junior", "lead"}
        required_keywords = [w for w in role.lower().split() if w not in generic]
    if required_keywords and not any(
        _title_has_keyword(title, kw) for kw in required_keywords
    ):
        return False, f"title doesn't match role keywords ({role})"

    for excl in profile.company_exclude:
        if excl.lower() in company:
            return False, f"company excluded ({excl})"

    if profile.company_include_only:
        if not any(inc.lower() in company for inc in profile.company_include_only):
            return False, "not in include-only list"

    location_lower = location.lower() if location else ""
    remote_markers = (
        "remote", "work from home", "wfh", "anywhere", "telecommute",
        "virtual", "home-based", "worldwide", "work from anywhere", "pan india"
    )
    is_remote = any(marker in location_lower for marker in remote_markers)
    is_hybrid = "hybrid" in location_lower

    if profile.work_mode == "remote_only":
        if not is_remote:
            return False, f"not a remote job ({card.get('location')})"
        if is_hybrid and not any(m in location_lower for m in ("permanent remote", "100% remote", "fully remote")):
            if any(w in location_lower for w in ("days in office", "days a week", "days/week", "onsite", "on-site")):
                return False, f"hybrid job requires office presence ({card.get('location')})"

    if not is_remote:
        # Must match an allowed city
        allowed_locations = [profile.current_city, *profile.relocate_cities]
        city_match = None
        for place in allowed_locations:
            if place and place.lower() in location_lower:
                city_match = place.lower()
                break
                
        if not city_match:
            return False, f"location not allowed ({card.get('location')})"
            

    exp_text = card.get("exp") or ""
    if profile.data.get("require_zero_experience"):
        exp_numbers = re.findall(r"\d+(?:\.\d+)?", exp_text)
        explicitly_fresher = bool(re.search(r"\bfreshers?\b", exp_text, re.I))
        if not explicitly_fresher and (
            not exp_numbers or float(exp_numbers[0]) != 0
        ):
            return False, f"zero-experience eligibility not confirmed ({exp_text})"
    digits = [int(s) for s in exp_text.replace("Yrs", "").replace("yrs", "")
              .replace("-", " ").split() if s.isdigit()]
    if len(digits) >= 2:
        lo, hi = digits[0], digits[-1]
        if hi < profile.seniority_floor_years or lo > profile.seniority_ceiling_years:
            return False, f"experience range mismatch ({exp_text})"

    salary_floor = profile.data.get("salary_floor_lpa")
    if salary_floor is not None:
        salary_text = card.get("salary") or ""
        salary_numbers = [
            float(n.replace(",", ""))
            for n in re.findall(r"\d+(?:\.\d+)?", salary_text)
        ]
        if not salary_numbers:
            if profile.data.get("require_stated_salary"):
                return False, "salary not stated on listing"
        else:
            if profile.data.get("require_salary_above_floor"):
                if not re.search(r"(?:lpa|lacs?\s*p\.?\s*a\.?|lakhs?\s*p\.?\s*a\.?)", salary_text, re.I):
                    return False, f"annual salary units not confirmed ({salary_text})"
                if min(salary_numbers) <= float(salary_floor):
                    return False, f"salary range is not strictly above floor ({salary_text})"
            if max(salary_numbers) < float(salary_floor):
                return False, f"salary below floor ({salary_text})"

    posted_text = (card.get("posted") or "").lower()
    if posted_text and profile.job_freshness_days:
        age_days = 0
        nums = [int(s) for s in posted_text.split() if s.isdigit()]
        num = nums[0] if nums else 0
        if "month" in posted_text:
            age_days = num * 30 if num else 30
        elif "week" in posted_text:
            age_days = num * 7 if num else 7
        elif "day" in posted_text:
            age_days = num if num else 1

        if age_days > profile.job_freshness_days:
            return False, f"job posting is too old ({card.get('posted')})"

    max_applicants = profile.data.get("max_applicants", 10 if profile.data.get("under_10_applicants_only") else None)
    if max_applicants is not None:
        app_text = (card.get("applicants") or "").lower()
        if app_text:
            nums = [int(s) for s in re.findall(r"\d+", app_text)]
            if nums and nums[0] > max_applicants:
                return False, f"too many applicants ({nums[0]} > {max_applicants})"

    return True, ""


def safe_evaluate(page, script, arg=None, default=None):
    """Runs page.evaluate but never lets a destroyed-context or stray JS error
    crash the whole run -- returns `default` instead. Naukri's own chat widget
    and SPA navigation can tear down the page mid-script, which raises
    Playwright's 'Execution context was destroyed' error; that's expected
    occasionally, not something to crash over."""
    try:
        return page.evaluate(script, arg) if arg is not None else page.evaluate(script)
    except Exception as e:
        print(f"  (page.evaluate failed, continuing anyway: {e})")
        return default


def enumerate_cards(page):
    """Reads real job cards off the search results page. Card wrapper is
    div.srp-jobtuple-wrapper with a data-job-id attribute -- that attribute
    is the reliable marker; filter/promo widgets on the same page don't
    have it."""
    return safe_evaluate(page, """
        () => Array.from(document.querySelectorAll('.srp-jobtuple-wrapper[data-job-id]'))
          .map((c, i) => ({
            i,
            jobId: c.getAttribute('data-job-id'),
            title: c.querySelector('a.title')?.innerText.trim(),
            href: c.querySelector('a.title')?.href,
            company: c.querySelector('a.comp-name, .comp-dtls-wrap a')?.innerText.trim(),
            exp: c.querySelector('.expwdth')?.innerText,
            location: c.querySelector('.locWdth')?.innerText,
            salary: c.querySelector('.sal, .salary, .salaryWdth, [class*="salary"]')?.innerText,
            posted: c.querySelector('.job-post-day, [class*="post-day"], .type, .sub-type')?.innerText,
            applicants: c.querySelector('.apply-count, [class*="applicant"], [class*="applied"], [class*="apply-status"]')?.innerText,
          }))
          .filter(c => c.title && c.href)
    """, default=[]) or []


def check_apply_button(page) -> str:
    """Returns 'external', 'native', or 'none' based on the detail page's apply button."""
    if page.query_selector('#company-site-button, a[class*="company-site-button"]'):
        return "external"
    if page.query_selector('#apply-button, .styles_apply-button__uJI3A, .apply-button, button:has-text("Apply")'):
        return "native"
    return "none"


def _wait_send_enabled(page, timeout_ms: int = 4000) -> bool:
    """Waits for Naukri's screening-chat Save/Send button to become enabled."""
    waited = 0
    step = 250
    while waited <= timeout_ms:
        enabled = safe_evaluate(page, """
            () => {
                const root = document.querySelector('.chatbot_Drawer, ._chatBotContainer, [class*="chatbot_Drawer"]') || document;
                const sendParent = root.querySelector('.send, .sendMsgbtn_container, [id^="sendMsgbtn_container"]');
                if (sendParent) {
                    const cls = (sendParent.className || '').toLowerCase();
                    if (cls.includes('disabled')) return false;
                }
                const btn = root.querySelector('.sendMsg, button, [role="button"]');
                if (btn) {
                    const cls = (btn.className || '').toLowerCase();
                    const disabledAttr = btn.getAttribute('disabled');
                    if (disabledAttr !== null && disabledAttr !== 'false') return false;
                    return !cls.includes('disabled');
                }
                return true;
            }
        """, default=True)
        if enabled:
            return True
        time.sleep(step / 1000)
        waited += step
    return True


def _js_click_send(page) -> bool:
    """Clicks Naukri's screening-chat Save/Send control."""
    # 1. First attempt: Playwright locator with real user pointer events
    try:
        btn = page.locator(
            '.chatbot_Drawer .send:not(.disabled) .sendMsg, .chatbot_Drawer .sendMsg:visible, [id^="sendMsg"]:visible, button:has-text("Save"):visible, button:has-text("Send"):visible'
        ).last
        if btn.count() > 0 and btn.is_visible():
            btn.scroll_into_view_if_needed()
            btn.click(force=True)
            time.sleep(0.6)
            return True
    except Exception:
        pass

    # 2. Second attempt: JavaScript synthetic event dispatch
    return safe_evaluate(page, """
        () => {
            const root = document.querySelector('.chatbot_Drawer, ._chatBotContainer, [class*="chatbot_Drawer"]') || document;
            const btns = Array.from(root.querySelectorAll('.sendMsg, [class*="sendMsg"], [class*="saveMsg"], .chatbot_Send, button'));
            const sendBtn = btns.find(b => {
                const t = (b.innerText || b.getAttribute('aria-label') || '').trim().toLowerCase();
                const cls = (b.className || '').toLowerCase();
                return t === 'save' || t === 'send' || t === 'submit' || t === 'next' ||
                       cls.includes('send') || cls.includes('save') || cls.includes('submit');
            });
            if (sendBtn) {
                sendBtn.scrollIntoView({behavior: 'instant', block: 'center'});
                sendBtn.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, cancelable: true, view: window}));
                sendBtn.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, cancelable: true, view: window}));
                sendBtn.click();
                return true;
            }
            return false;
        }
    """, default=False)



def click_native_apply(page) -> bool:
    """Clicks the visible Apply button using Playwright locator and JS fallback."""
    time.sleep(random.uniform(0.4, 0.8))
    
    # 1. First attempt: Playwright locator with real user pointer events
    try:
        loc = page.locator('button#apply-button:visible, button.styles_apply-button__uJI3A:visible, button.apply-button:visible, #apply-button:visible, button:has-text("Apply"):visible').first
        if loc.count() > 0 and loc.is_visible(timeout=1000):
            loc.scroll_into_view_if_needed()
            time.sleep(random.uniform(0.3, 0.5))
            loc.click(timeout=3000)
            return True
    except Exception:
        pass

    # 2. Second attempt: JavaScript synthetic event dispatch on the visible button
    return bool(safe_evaluate(page, """
        () => {
            const btns = Array.from(document.querySelectorAll('#apply-button, .apply-button, button.styles_apply-button__uJI3A, button'));
            const applyBtn = btns.find(b => {
                const t = (b.innerText || '').trim().toLowerCase();
                const id = (b.id || '').toLowerCase();
                const cls = (b.className || '').toLowerCase();
                const isApplyText = t === 'apply' || t.startsWith('apply on') || t === 'apply now';
                const isVisible = b.offsetParent !== null;
                return (id === 'apply-button' || cls.includes('apply-button') || isApplyText) && isVisible;
            }) || document.getElementById('apply-button');

            if (applyBtn) {
                applyBtn.scrollIntoView({ behavior: 'instant', block: 'center' });
                applyBtn.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, cancelable: true, view: window}));
                applyBtn.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, cancelable: true, view: window}));
                applyBtn.click();
                return true;
            }
            return false;
        }
    """, default=False))


def page_has_stop_signal(page) -> str | None:
    try:
        text = page.inner_text("body").lower()
    except Exception:
        return None  # page mid-navigation -- checked again on the next loop iteration
    for phrase in STOP_PHRASES:
        if phrase in text:
            return phrase
    if "login" in page.url and "naukri.com/nlogin" in page.url:
        return "session expired / login prompt"
    return None


def _save_debug_screenshot(page, job_title: str) -> str:
    """Saves a screenshot when the outcome is uncertain, so it can be looked
    at afterward instead of needing to catch it live. Also saves the page's
    HTML for the same reason — a screenshot shows what it looked like, the
    HTML shows exactly why the confirmation-text check missed it."""
    debug_dir = Path("debug_screenshots")
    debug_dir.mkdir(mode=0o700, exist_ok=True)
    safe_name = "".join(c if c.isalnum() else "_" for c in (job_title or "unknown"))[:60]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    png_path = debug_dir / f"{safe_name}_{timestamp}.png"
    html_path = debug_dir / f"{safe_name}_{timestamp}.html"
    try:
        page.screenshot(path=str(png_path))
        png_path.chmod(0o600)
    except Exception as e:
        print(f"  (couldn't save debug screenshot: {e})")
    try:
        html_path.write_text(page.content())
        html_path.chmod(0o600)
    except Exception as e:
        print(f"  (couldn't save debug HTML: {e})")
    return str(png_path)


def _is_application_confirmation(body_text: str, apply_label: str = "") -> bool:
    """Return True only for explicit application-success states."""
    text = " ".join((body_text or "").lower().split())
    label = " ".join((apply_label or "").lower().split())
    if label in {"applied", "already applied"} or "applied" in label:
        return True
    success_patterns = (
        r'\bsuccessfully applied\b',
        r'\bapplication (?:has been )?sent\b',
        r'\byou have (?:already )?applied\b',
        r'\bapplication (?:has been )?submitted\b',
        r'\bapplied to\b',
        r'\bthank you for applying\b',
        r'\bthank you for your responses?\b',
        r'\bresponses? (?:have|has) been recorded\b',
    )
    return any(re.search(pattern, text) for pattern in success_patterns)


def verify_applied(page) -> bool:
    """Best-effort check that the application actually went through."""
    for _ in range(5):  # Poll over ~3-4 seconds for UI confirmation
        time.sleep(0.6)
        rejected = safe_evaluate(page, """
            () => {
                const bodyText = (document.body.innerText || '').toLowerCase();
                return bodyText.includes('application was not accepted') ||
                       bodyText.includes('incomplete information') ||
                       bodyText.includes('mandatory questions when reapplying');
            }
        """, default=False)
        if rejected:
            return False

        is_done = safe_evaluate(page, """
            () => {
                const bodyText = (document.body.innerText || '').toLowerCase();
                const hasConfirmText = bodyText.includes('successfully applied') ||
                                       bodyText.includes('application sent') ||
                                       bodyText.includes('application has been sent') ||
                                       bodyText.includes('you have applied') ||
                                       bodyText.includes('application submitted') ||
                                       bodyText.includes('applied to') ||
                                       bodyText.includes('thank you for applying') ||
                                       bodyText.includes('thank you for your response');

                const btns = Array.from(document.querySelectorAll('#apply-button, .apply-button, button.styles_apply-button__uJI3A, button'));
                const hasAppliedBtn = btns.some(b => {
                    const t = (b.innerText || '').trim().toLowerCase();
                    return t === 'applied' || t === 'already applied';
                });

                const hasBadge = !!document.querySelector('.applied-badge, [class*="applied-badge"], [class*="apply-success"]');

                return hasConfirmText || hasAppliedBtn || hasBadge;
            }
        """, default=False)
        if is_done:
            return True

    text = ""
    try:
        text = page.inner_text("body").lower()
    except Exception:
        pass
    label = ""
    btn = page.query_selector('#apply-button, .styles_apply-button__uJI3A, .apply-button')
    if btn:
        try:
            label = btn.inner_text()
        except Exception:
            pass
    return _is_application_confirmation(text, label)


class StopRun(Exception):
    """Something genuinely dangerous or account-risking happened -- CAPTCHA,
    login expired, rate-limit warning. Halts the entire run immediately."""


class SkipJob(Exception):
    """This one listing can't be completed -- logs it and moves to the next
    listing. Does NOT stop the run."""


def _get_options(page) -> list[dict]:
    """Reads radio/checkbox options and chip pills in the chat drawer, with their visible labels."""
    return safe_evaluate(page, """
        () => {
            const root = document.querySelector('.chatbot_Drawer, ._chatBotContainer, [class*="chatbot_Drawer"]') || document;
            
            // 1. Radio / Checkbox inputs
            const inputs = Array.from(root.querySelectorAll('input[type=radio], input[type=checkbox]'));
            if (inputs.length > 0) {
                const allLabels = Array.from(root.querySelectorAll('label'));
                return inputs.map((el, idx) => {
                    let label = '';
                    if (el.labels && el.labels.length > 0) {
                        label = (el.labels[0].innerText || el.labels[0].textContent || '').trim();
                    }
                    if (!label && el.id) {
                        const lbl = allLabels.find(l => l.htmlFor === el.id || l.getAttribute('for') === el.id);
                        if (lbl) label = (lbl.innerText || lbl.textContent || '').trim();
                    }
                    if (!label) {
                        const parentLabel = el.closest('label, .ssrc__radio-btn-container, [class*="radio-btn"], div');
                        if (parentLabel) label = (parentLabel.innerText || parentLabel.textContent || '').trim();
                    }
                    if (!label && el.nextElementSibling) {
                        label = (el.nextElementSibling.innerText || el.nextElementSibling.textContent || '').trim();
                    }
                    if (!label && el.value) {
                        label = el.value.trim();
                    }
                    return {idx, label: label || `Option ${idx + 1}`, type: el.type, kind: 'input'};
                }).filter(o => o.label && o.label !== 'Save' && o.label !== 'Send');
            }

            // 2. Chip buttons or option pills
            const chips = Array.from(root.querySelectorAll('.chatbot_Chip, .chipItem, .chatbot_ChipWrapper button, [class*="chipItem"], [class*="chip_"], [role="radio"]'));
            if (chips.length > 0) {
                return chips.map((el, idx) => ({
                    idx,
                    label: (el.innerText || el.textContent || '').trim(),
                    type: 'chip',
                    kind: 'chip'
                })).filter(o => o.label && !o.label.toLowerCase().includes('skip this question'));
            }

            return [];
        }
    """, default=[]) or []


def _click_option(page, idx: int) -> bool:
    """Clicks the option radio/checkbox/chip at idx and ensures the selection takes effect."""
    return safe_evaluate(page, """
        (idx) => {
            const root = document.querySelector('.chatbot_Drawer, ._chatBotContainer, [class*="chatbot_Drawer"]') || document;
            
            // 1. Radio / Checkbox inputs
            const inputs = Array.from(root.querySelectorAll('input[type=radio], input[type=checkbox]'));
            if (inputs[idx]) {
                const input = inputs[idx];
                const allLabels = Array.from(root.querySelectorAll('label'));
                let label = input.labels?.[0] || (input.id ? allLabels.find(l => l.htmlFor === input.id || l.getAttribute('for') === input.id) : null);
                if (!label) {
                    label = input.closest('label') || input.parentElement?.querySelector('label');
                }
                
                // Click the label if present, else input
                if (label) {
                    label.scrollIntoView({behavior: 'instant', block: 'center'});
                    label.click();
                } else {
                    input.scrollIntoView({behavior: 'instant', block: 'center'});
                    input.click();
                }
                
                input.checked = true;
                input.dispatchEvent(new Event('input', {bubbles: true}));
                input.dispatchEvent(new Event('change', {bubbles: true}));
                
                // Immediately un-disable send/save buttons in the drawer
                const sendBtn = root.querySelector('[id^="sendMsg__"], .send, .sendMsg');
                if (sendBtn) {
                    sendBtn.classList.remove('disabled');
                    sendBtn.removeAttribute('disabled');
                    sendBtn.style.pointerEvents = 'auto';
                }
                const sendContainer = root.querySelector('[id^="sendMsgbtn_container"], .sendMsgbtn_container');
                if (sendContainer) {
                    sendContainer.classList.remove('disabled');
                    sendContainer.style.pointerEvents = 'auto';
                }
                return true;
            }
            
            // 2. Chip buttons or option pills
            const chips = Array.from(root.querySelectorAll('.chatbot_Chip, .chipItem, .chatbot_ChipWrapper button, [class*="chipItem"], [class*="chip_"], [role="radio"]'));
            if (chips[idx]) {
                chips[idx].scrollIntoView({behavior: 'instant', block: 'center'});
                chips[idx].click();
                return true;
            }
            return false;
        }
    """, arg=idx, default=False)


def _click_skip_question(page) -> bool:
    """Click Naukri's explicit skip chip for an inapplicable question."""
    return bool(safe_evaluate(page, """
        () => {
            const root = document.querySelector('.chatbot_Drawer, ._chatBotContainer, [class*="chatbot_Drawer"]') || document;
            const candidates = Array.from(root.querySelectorAll(
                'button, [role="button"], .chatbot_Chip, .chipItem, span, div'
            ));
            const skip = candidates.find(
                el => (el.innerText || '').trim().toLowerCase() === 'skip this question'
            );
            if (!skip) return false;
            skip.click();
            return true;
        }
    """, default=False))


def _resume_path(profile: Profile, job_context: str = "") -> Path | None:
    """Resolve the private resume configured or targeted for the job role."""
    from common.resume_selector import get_resume_path
    path = get_resume_path(profile, job_context=job_context)
    if path and path.is_file():
        return path
    configured = str(profile.data.get("resume_file_name", "")).strip()
    if not configured:
        return None
    p = Path(configured).expanduser()
    if not p.is_absolute():
        p = Path.cwd() / p
    return p if p.is_file() else None


def _upload_resume(page, profile: Profile, question: str, job_context: str = "") -> bool:
    """Upload the configured or role-targeted resume when Naukri Campus requires it."""
    lower_q = question.lower()
    if "upload" not in lower_q or "resume" not in lower_q:
        return False

    resume_path = _resume_path(profile, job_context=job_context)
    if resume_path is None:
        raise SkipJob(
            "Naukri requested a resume upload, but no matching resume could be resolved."
        )

    uploader = page.locator('input.chatbot_Uploader[type="file"], input[type="file"]').last
    try:
        uploader.set_input_files(str(resume_path), timeout=5000)
    except Exception as e:
        raise SkipJob(f"couldn't upload the configured resume: {e}") from e

    if not _wait_send_enabled(page, timeout_ms=10000):
        raise SkipJob("resume uploaded, but Naukri's Save button stayed disabled")
    if not _js_click_send(page):
        raise SkipJob("resume uploaded, but Naukri's Save control could not be clicked")
    print(f"  (uploaded role-targeted resume: {resume_path.name})")
    time.sleep(3.0)
    return True


def _is_sensitive_field(question: str) -> bool:
    lower_q = question.lower()
    return any(hint in lower_q for hint in SENSITIVE_FIELD_HINTS)


def _direct_profile_answer(question: str, answers: dict) -> str | None:
    """Answer only questions that unambiguously map to a profile fact."""
    lower_q = " ".join(question.lower().split())
    asks_skill_specific_experience = bool(
        re.search(
            r"\b(?:experience|years?)\b[^?]{0,80}\b(?:in|with|using|on)\s+(?!years?\b|yrs?\b|months?\b)[a-z0-9#+.]+",
            lower_q,
        )
        or re.search(r"\byears?\s+of\s+(?!experience\b)[a-z0-9#+.]+\s+experience\b", lower_q)
    )
    if asks_skill_specific_experience:
        try:
            if float(answers.get("years_experience", 0)) <= 0:
                return "0"
        except (TypeError, ValueError):
            pass
        return None

    return direct_profile_answer(question, answers)


def _should_skip_question(question: str, profile: Profile) -> bool:
    """Skip fields that do not apply instead of submitting invalid fake values."""
    lower_q = question.lower()
    asks_current_ctc = any(
        phrase in lower_q
        for phrase in ("current ctc", "current salary", "present ctc", "present salary")
    )
    try:
        has_no_current_ctc = float(profile.data.get("current_ctc_lpa", 0)) <= 0
    except (TypeError, ValueError):
        has_no_current_ctc = False
    return asks_current_ctc and has_no_current_ctc
def _auto_decide_option(question: str, options: list[dict], profile: Profile) -> dict | None:
    """Picks the correct option using known profile facts or smart defaults."""
    lower_q = question.lower()
    by_label = {o["idx"]: o["label"].strip().lower() for o in options}

    def find_by_text(text: str):
        for idx, label in by_label.items():
            if label == text.lower() or label == text.lower() + "." or text.lower() in label:
                return next(o for o in options if o["idx"] == idx)
        return None

    rules = [
        (["military", "armed forces", "served in the military", "veteran", "ex-serviceman", "armed force"],
         "Never served" if find_by_text("Never served") else "No"),
        (["laid off", "layoff", "lay off", "got laid off", "reason for job change", "reason for leaving"],
         "No" if find_by_text("No") else "Career Growth"),
        (["interview", "virtual interview", "video interview", "video call", "telephonic", "f2f", "face to face", "online test", "assessment", "discussion", "available for interview", "available for virtual", "saturday", "weekend interview", "virtual round"],
         "Yes"),
        (["fill the form", "google form", "filled the form", "form link", "survey", "form:"],
         "Yes - Filled the form"),
        (["laptop", "wifi", "internet", "broadband", "work from home setup", "wfh setup", "permanent remote", "remote setup", "hybrid", "remote work"],
         "Yes"),
        (["background check", "background verification", "bgv", "drug test", "reference check"],
         "Yes"),
        (["work authorization", "authorized to work", "valid passport", "eligible to work", "citizen"],
         "Yes"),
        (["visa sponsorship", "require sponsorship", "need visa"],
         "No"),
        (["career break", "gap in career", "employment gap", "criminal", "court case", "pending legal", "disciplinary", "backlog", "bond"],
         "No"),
        (["ex-infosys", "ex infy", "worked in infosys", "previous employer", "ex-employee", "ex infy id"],
         "No"),
        (["gender", "sex"],
         "Male"),
        (["marital status"],
         "Single" if find_by_text("Single") else "Unmarried"),
        (["relocate", "relocation", "willing to move", "willing to relocate", "residing in bengaluru or willing", "relocate to bengaluru", "preferred location"],
         "Can Relocate All Over India" if find_by_text("Can Relocate All Over India") else ("Yes" if profile.data.get("relocate_cities") else "No")),
        (["night shift"], "Yes" if profile.data.get("night_shift_ok") else "No"),
        (["weekend"], "Yes" if profile.data.get("weekend_ok") else "No"),
        (["immediately available", "immediate joiner", "ready to join", "notice period"],
         "Immediate" if find_by_text("Immediate") else ("Immediate (0-15 Days)" if find_by_text("Immediate (0-15 Days)") else ("0-15 Days" if find_by_text("0-15 Days") else ("Yes" if profile.data.get("immediately_available") else "No")))),
        (["currently employed", "currently working"],
         "Yes" if profile.data.get("current_employer") else "No"),
    ]
    for triggers, desired in rules:
        if any(t in lower_q for t in triggers):
            match = find_by_text(desired)
            if match:
                return match

    # Auto-pick Yes for technical capability, experience, willingness, comfort, and interview availability
    if any(t in lower_q for t in ("experience", "comfortable", "willing", "proficient", "hands on", "worked on", "knowledge", "available", "ready", "agree", "open to", "interview")):
        yes_opt = find_by_text("Yes")
        if yes_opt:
            return yes_opt

    # Match candidate primary and adjacent skills (e.g. "Django", "FastAPI", "React", "Python", etc.)
    all_skills = [s.lower() for s in (profile.data.get("skills_primary", []) + profile.data.get("skills_adjacent", []))]
    for opt in options:
        opt_text = opt["label"].lower().strip()
        if any(s == opt_text or f" {s} " in f" {opt_text} " for s in all_skills):
            return opt

    # Match role selection options (e.g. "Which technical requirement are you applying for?")
    for role_kw in ("java", "full stack", "react", "spring boot", "backend", "software developer", "frontend", "python"):
        matched_role_opt = find_by_text(role_kw)
        if matched_role_opt:
            return matched_role_opt

    # Auto-match experience range options (e.g. "1-3 years", "3-5 years", "3 years")
    exp_years = float(profile.total_experience_years or 3)
    for opt in options:
        lbl = opt["label"].lower()
        digits = [float(s) for s in re.findall(r"\d+(?:\.\d+)?", lbl)]
        if len(digits) == 2:
            if digits[0] <= exp_years <= digits[1]:
                return opt
        elif len(digits) == 1:
            if "+" in lbl and exp_years >= digits[0]:
                return opt
            elif digits[0] == exp_years:
                return opt

    # Prefer Yes or positive option if present among choices
    for pref in ("Yes - Filled the form", "Yes", "Immediate", "Immediate (0-15 Days)", "0-15 Days", "Can Relocate All Over India", "Never served", "No"):
        opt = find_by_text(pref)
        if opt:
            return opt

    return options[0] if options else None


def _handle_options_question(page, question: str, profile: Profile, timeout_s: int) -> bool:
    """Returns True if handled (clicked something), raises SkipJob otherwise."""
    options = _get_options(page)
    if not options:
        return False

    answers = profile.answer_library()

    # 1. Check direct profile fact matching against option labels first
    direct_ans = direct_profile_answer(question, answers)
    if direct_ans:
        direct_norm = str(direct_ans).strip().lower()
        for opt in options:
            opt_lbl = opt["label"].strip().lower()
            if opt_lbl == direct_norm or direct_norm in opt_lbl or opt_lbl in direct_norm:
                _click_option(page, opt["idx"])
                learned_answers.save_answer(question, opt["label"])
                print(f"  (picked '{opt['label']}' from profile fact '{direct_ans}' for: {question[:80]})")
                return True

    # 2. Check remembered answer
    stored = learned_answers.get_answer(question)
    if stored:
        for opt in options:
            if opt["label"].strip().lower() == stored.strip().lower():
                _click_option(page, opt["idx"])
                print(f"  (used a remembered answer for: {question[:80]})")
                return True

    # 3. Auto-decide based on profile rules and known facts
    auto = _auto_decide_option(question, options, profile)
    if auto:
        _click_option(page, auto["idx"])
        learned_answers.save_answer(question, auto["label"])
        print(f"  (auto-picked '{auto['label']}' from your profile for: {question[:80]})")
        return True

    # 3b. Dynamic LLM screening answer matching if no direct rule matched
    llm_ans = get_screening_answer(question, profile, "")
    if llm_ans:
        llm_norm = str(llm_ans).strip().lower()
        for opt in options:
            opt_lbl = opt["label"].strip().lower()
            if opt_lbl == llm_norm or llm_norm in opt_lbl or opt_lbl in llm_norm:
                _click_option(page, opt["idx"])
                learned_answers.save_answer(question, opt["label"])
                print(f"  (LLM-picked '{opt['label']}' for: {question[:80]})")
                return True

    # 4. If sensitive and completely unresolved, ask user
    if _is_sensitive_field(question):
        options_text = "\n".join(f"  {o['idx']}: {o['label']}" for o in options)
        response = ask_user(
            f"Screening question needs a choice:\n{question}\n\nOptions:\n{options_text}\n"
            f"Type the number of your choice (or numbers separated by commas for multi-select):",
            timeout_seconds=timeout_s,
        )
        if response is None:
            raise SkipJob(f"no response for options question: {question[:120]}")

        chosen_raw = [r.strip() for r in response.split(",") if r.strip()]
        matches = []
        for r in chosen_raw:
            try:
                idx = int(r)
                match = next((o for o in options if o["idx"] == idx), None)
            except ValueError:
                match = next((o for o in options if o["label"].strip().lower() == r.lower()), None)
            if match:
                matches.append(match)

        if not matches:
            raise SkipJob(f"no valid option chosen for: {question[:120]}")

        for match in matches:
            _click_option(page, match["idx"])
            learned_answers.save_answer(question, match["label"])
        return True

    # 5. Default fallback: Pick the first available option
    if options:
        _click_option(page, options[0]["idx"])
        learned_answers.save_answer(question, options[0]["label"])
        print(f"  (fallback-picked first option '{options[0]['label']}' for: {question[:80]})")
        return True

    return False


# If the chat's last message is one of these, it's wrapping up rather than
# asking something new -- stop cleanly instead of trying to draft an answer
# into a box that may no longer exist.
COMPLETION_PHRASES = [
    "thank you for applying", "thanks for your response", "thanks for your time",
    "thank you for your response", "thank you for your responses",
    "we will get back", "responses have been recorded", "no further questions",
    "application submitted", "application was submitted", "that's all",
    "all the information we need", "application has been sent",
    "your application has been sent", "sent to the recruiter",
    "successfully applied", "received your application",
]


def _is_completion_message(text: str) -> bool:
    lower = text.lower()
    return any(p in lower for p in COMPLETION_PHRASES)


def _has_answerable_input(page, timeout_ms: int = 3000) -> bool:
    """Checks for a text box, textarea, or radio/checkbox/dob widget to answer."""
    waited = 0
    step = 300
    while waited <= timeout_ms:
        found = safe_evaluate(page, """
            () => !!(document.querySelector('[id^="userInput"], .textArea, [contenteditable="true"], input[type="text"], input[placeholder*="message" i], textarea, .chat-input, [class*="inputBox"]') ||
                     document.querySelector(
                         'input[type=radio], input[type=checkbox], input[type=file], .chatbot_Chip, .chipItem, [class*="radio-btn"], .dob__container, .dob__input-container, .dob__input, input.day, input.month, input.year, input[type="date"]'
                     ))
        """, default=False)
        if found:
            return True
        time.sleep(step / 1000)
        waited += step
    return False


def _read_filled_text(page) -> str:
    try:
        inp = page.locator(
            '.chatbot_Drawer .textArea, .chatbot_Drawer [contenteditable="true"], [id^="userInput"]:visible, .textArea:visible'
        ).last
        if inp.count() > 0 and inp.is_visible():
            try:
                val = inp.input_value()
                if val and val.strip():
                    return val.strip()
            except Exception:
                pass
            val = inp.inner_text() or ""
            if val.strip():
                return val.strip()
    except Exception:
        pass

    return safe_evaluate(page, """
        () => {
            const root = document.querySelector('.chatbot_Drawer, ._chatBotContainer, [class*="chatbot_Drawer"]') || document;
            const ed = root.querySelector('[id^="userInput"], .textArea, input[placeholder*="message" i], input[placeholder*="type" i], input[type="text"], textarea, .chat-input input, .chat-input, [class*="inputBox"] input, [class*="inputBox"], [contenteditable="true"]');
            if (!ed) return '';
            return (ed.value || ed.innerText || ed.textContent || '').trim();
        }
    """, default="") or ""


def _fill_freetext(page, text: str):
    """Fills answer in Naukri screening drawer (contenteditable div or input/textarea)."""
    text_str = str(text).strip()
    # 1. First attempt: Playwright native locator + native keyboard typing so React updates
    try:
        inp = page.locator(
            '.chatbot_Drawer .textArea, .chatbot_Drawer [contenteditable="true"], [id^="userInput"]:visible, .textArea:visible'
        ).last
        if inp.count() > 0 and inp.is_visible():
            inp.scroll_into_view_if_needed()
            inp.click(force=True)
            time.sleep(0.15)
            page.keyboard.press("Meta+A")
            page.keyboard.press("Backspace")
            page.keyboard.press("Control+A")
            page.keyboard.press("Backspace")
            time.sleep(0.1)
            page.keyboard.type(text_str, delay=15)
            time.sleep(0.3)
            return
    except Exception:
        pass

    # 2. JavaScript fallback and state synchronization
    safe_evaluate(page, """
        (text) => {
            const root = document.querySelector('.chatbot_Drawer, ._chatBotContainer, [class*="chatbot_Drawer"]') || document;
            const ed = root.querySelector('[id^="userInput"], .textArea[contenteditable="true"], .textArea, [contenteditable="true"], input[placeholder*="message" i], input[placeholder*="type" i], textarea, input[type="text"], .chat-input input, .chat-input, [class*="inputBox"] input, [class*="inputBox"]');
            if (ed) {
                ed.scrollIntoView({behavior: 'instant', block: 'center'});
                ed.focus();
                if (ed.tagName === 'INPUT' || ed.tagName === 'TEXTAREA') {
                    let proto = window.HTMLInputElement.prototype;
                    if (ed.tagName === 'TEXTAREA') proto = window.HTMLTextAreaElement.prototype;
                    const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
                    if (setter) setter.call(ed, text);
                    else ed.value = text;
                    ed.dispatchEvent(new Event('input', {bubbles: true}));
                    ed.dispatchEvent(new Event('change', {bubbles: true}));
                    ed.dispatchEvent(new Event('blur', {bubbles: true}));
                } else {
                    ed.innerText = text;
                    ed.dispatchEvent(new InputEvent('input', {bubbles: true, cancelable: true, inputType: 'insertText', data: text}));
                    ed.dispatchEvent(new Event('input', {bubbles: true}));
                    ed.dispatchEvent(new Event('change', {bubbles: true}));
                    ed.dispatchEvent(new Event('blur', {bubbles: true}));
                }
            }
        }
    """, arg=text_str)


def _fill_and_send(page, text: str, question: str):
    """Fills the answer box, verifies text presence, and sends."""
    _fill_freetext(page, text)
    time.sleep(0.4)
    filled = _read_filled_text(page)
    if not filled:
        _fill_freetext(page, text)  # one retry
        time.sleep(0.5)
        filled = _read_filled_text(page)

    _wait_send_enabled(page, timeout_ms=2500)
    _js_click_send(page)
    time.sleep(2.0)  # give Naukri's backend a moment to actually save it


def _parse_dob_parts(dob_raw: str) -> tuple[str, str, str]:
    """Parses a DOB string into (day, month, year) strings padded to 2, 2, 4 digits."""
    digits = re.findall(r"\d+", str(dob_raw or "").strip())
    if len(digits) >= 3:
        if len(digits[0]) == 4:  # YYYY-MM-DD
            year, month, day = digits[0], digits[1], digits[2]
        else:  # DD-MM-YYYY or DD/MM/YYYY
            day, month, year = digits[0], digits[1], digits[2]
        return day.zfill(2), month.zfill(2), year.zfill(4)
    return "10", "06", "2001"


def _handle_dob_question(page, question: str, profile: Profile) -> bool:
    """Answers Naukri screening chat Date of Birth widget or input."""
    lower_q = (question or "").lower()
    is_dob_q = any(p in lower_q for p in ("date of birth", "dob", "born on", "birth date", "birthdate", "d.o.b", "when were you born"))
    has_dob_widget = safe_evaluate(page, """
        () => {
            const root = document.querySelector('.chatbot_Drawer, ._chatBotContainer, [class*="chatbot_Drawer"]') || document;
            return !!root.querySelector('.dob__container, .dob__input-container, .dob__input, input.day, input.month, input.year, input[type="date"]');
        }
    """, default=False)

    if not (is_dob_q or has_dob_widget):
        return False

    dob_val = (
        profile.data.get("dob")
        or profile.data.get("date_of_birth")
        or profile.data.get("birth_date")
        or "10/06/2001"
    )
    day, month, year = _parse_dob_parts(str(dob_val))

    # 1. Fill 3-box widget (.dob__input or input.day/month/year)
    try:
        day_loc = page.locator('.chatbot_Drawer input.day, .chatbot_Drawer .dob__input.day, .dob__input.day').first
        month_loc = page.locator('.chatbot_Drawer input.month, .chatbot_Drawer .dob__input.month, .dob__input.month').first
        year_loc = page.locator('.chatbot_Drawer input.year, .chatbot_Drawer .dob__input.year, .dob__input.year').first
        if day_loc.count() > 0 and month_loc.count() > 0 and year_loc.count() > 0:
            day_loc.fill(day)
            month_loc.fill(month)
            year_loc.fill(year)
    except Exception:
        pass

    filled_boxes = safe_evaluate(page, """
        ({d, m, y}) => {
            const root = document.querySelector('.chatbot_Drawer, ._chatBotContainer, [class*="chatbot_Drawer"]') || document;
            const dayInput = root.querySelector('input.day, .dob__input.day, input[placeholder*="DD" i], input[name*="day" i]');
            const monthInput = root.querySelector('input.month, .dob__input.month, input[placeholder*="MM" i], input[name*="month" i]');
            const yearInput = root.querySelector('input.year, .dob__input.year, input[placeholder*="YYYY" i], input[name*="year" i]');

            if (dayInput && monthInput && yearInput) {
                const setVal = (el, val) => {
                    el.focus();
                    const proto = window.HTMLInputElement.prototype;
                    const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
                    if (setter) setter.call(el, val);
                    else el.value = val;
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    el.dispatchEvent(new Event('blur', {bubbles: true}));
                };
                setVal(dayInput, d);
                setVal(monthInput, m);
                setVal(yearInput, y);

                const sendWrap = root.querySelector('.send, [id^="sendMsg__"], .sendMsgbtn_container');
                if (sendWrap) {
                    sendWrap.classList.remove('disabled');
                    sendWrap.style.pointerEvents = 'auto';
                }
                return true;
            }
            return false;
        }
    """, arg={"d": day, "m": month, "y": year}, default=False)

    if filled_boxes:
        time.sleep(0.5)
        _wait_send_enabled(page, timeout_ms=3000)
        _js_click_send(page)
        print(f"  (filled 3-box date-of-birth: {day}/{month}/{year})")
        return True

    # 2. Fill native date input
    filled_date = safe_evaluate(page, """
        ({y, m, d}) => {
            const root = document.querySelector('.chatbot_Drawer, ._chatBotContainer, [class*="chatbot_Drawer"]') || document;
            const dateInp = root.querySelector('input[type="date"]');
            if (dateInp) {
                dateInp.focus();
                const isoVal = `${y}-${m}-${d}`;
                const proto = window.HTMLInputElement.prototype;
                const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
                if (setter) setter.call(dateInp, isoVal);
                else dateInp.value = isoVal;
                dateInp.dispatchEvent(new Event('input', {bubbles: true}));
                dateInp.dispatchEvent(new Event('change', {bubbles: true}));
                return true;
            }
            return false;
        }
    """, arg={"y": year, "m": month, "d": day}, default=False)

    if filled_date:
        time.sleep(0.5)
        _wait_send_enabled(page, timeout_ms=3000)
        _js_click_send(page)
        print(f"  (filled date input: {year}-{month}-{day})")
        return True

    # 3. Standard contenteditable textarea or text input
    dob_formatted = f"{day}-{month}-{year}" if any(k in lower_q for k in ("-", "hyphen", "dd-mm")) else f"{day}/{month}/{year}"
    _fill_and_send(page, dob_formatted, question)
    print(f"  (filled date-of-birth: {dob_formatted})")
    return True


def answer_screening_chat(page, profile: Profile, job_context: str, timeout_s: int):
    """Handles Naukri's post-apply screening chat drawer, if it appears."""
    answers = profile.answer_library()
    try:
        page.wait_for_selector(
            '.chatbot_Drawer, [class*="chatbot_Drawer"], [class*="botMsg"], [class*="chat-drawer"], ._chatBotContainer',
            timeout=9000,
        )
    except PWTimeout:
        # Check if drawer element is in DOM even if animation or connection was delayed
        drawer_present = safe_evaluate(page, """
            () => !!document.querySelector('.chatbot_Drawer, ._chatBotContainer, [class*="chatbot_Drawer"], [class*="botMsg"]')
        """, default=False)
        if not drawer_present:
            return  # no screening chat for this listing

    last_question = ""
    same_q_count = 0

    for step in range(20):
        time.sleep(1.0)
        stop = page_has_stop_signal(page)
        if stop:
            raise StopRun(f"stop signal during screening chat: {stop}")

        # Check if drawer closed
        drawer_exists = safe_evaluate(page, """
            () => {
                const drawer = document.querySelector('.chatbot_Drawer, [class*="chatbot_Drawer"], ._chatBotContainer, .chat-container');
                if (!drawer) return false;
                const style = window.getComputedStyle(drawer);
                return style.display !== 'none' && style.visibility !== 'hidden' && drawer.offsetParent !== null;
            }
        """, default=False)
        if not drawer_exists and step > 0:
            break

        bubbles = page.query_selector_all(
            '.chatbot_Drawer .botMsg, [class*="chatbot_Drawer"] .botMsg, .botMsg, [class*="botMsg"], [class*="botMessage"], [class*="msgContainer"], [class*="msgText"], .msg-text, [class*="chatbot"] p, [class*="messageBubble"], [class*="chatBubble"], [class*="bot-msg"], div[class*="bubble"]'
        )
        if not bubbles:
            break
        try:
            question = bubbles[-1].inner_text().strip()
        except Exception:
            break  # page likely navigated away mid-read; treat as chat finished
        if not question:
            break

        # If it's the exact same question we just answered, wait briefly for the next bubble
        if question == last_question:
            same_q_count += 1
            if same_q_count <= 3:
                time.sleep(1.5)
                continue
            else:
                break
        else:
            same_q_count = 0

        if "input seems invalid" in question.lower():
            raise SkipJob(f"Naukri rejected the previous answer: {question[:120]}")

        if _is_completion_message(question):
            time.sleep(1.5)  # let the "Applied" confirmation redirect begin before we check for it
            safe_evaluate(page, """
                () => {
                    const cross = document.querySelector('.crossIcon, .chatBot-ic-cross, [class*="cross"], [aria-label="Close"]');
                    if (cross) cross.click();
                }
            """)
            break

        if not _has_answerable_input(page):
            # Informational message; check if a new bubble arrives
            time.sleep(1.5)
            new_bubbles = page.query_selector_all('.botMsg, [class*="botMsg"], [class*="botMessage"], [class*="msgContainer"], [class*="msgText"]')
            if len(new_bubbles) > len(bubbles):
                continue
            break

        last_question = question

        if _upload_resume(page, profile, question, job_context=job_context):
            continue

        if _handle_dob_question(page, question, profile):
            time.sleep(2.0)
            continue

        options = _get_options(page)
        if options:
            _handle_options_question(page, question, profile, timeout_s)
            time.sleep(0.5)
            _wait_send_enabled(page, timeout_ms=1500)
            _js_click_send(page)
            time.sleep(2.0)
            continue

        if _should_skip_question(question, profile):
            if _click_skip_question(page):
                print("  (skipped current CTC because the profile is marked as fresher / 0 LPA)")
                time.sleep(2.0)
                continue

        # 1. Direct profile fact check (DOB, skill experience e.g. Python, total experience, CTC, location, etc.)
        direct_ans = direct_profile_answer(question, answers)
        if direct_ans is not None:
            print(f"  [Profile Fact] Question: {question[:80]} -> Answer: {direct_ans}")
            _fill_and_send(page, direct_ans, question)
            learned_answers.save_answer(question, direct_ans)
            time.sleep(2.0)
            continue

        if _is_sensitive_field(question):
            response = ask_user(
                f"Screening question (personal detail):\n{question}",
                timeout_seconds=timeout_s,
            )
            if response is None:
                raise SkipJob(f"no response for sensitive field: {question[:120]}")
            print(f"  [User Input] Question: {question[:80]} -> Answer: {response}")
            _fill_and_send(page, response, question)
            continue

        stored = learned_answers.get_answer(question)
        if stored:
            print(f"  [Remembered Answer] Question: {question[:80]} -> Answer: {stored}")
            _fill_and_send(page, stored, question)
            continue

        # 2. Comprehensive screening answer generator (Groq/Ollama LLM + smart typed profile fallbacks)
        ans = get_screening_answer(question, profile, job_context=job_context)
        if ans is not None:
            print(f"  [AI Screening Answer] Question: {question[:80]} -> Answer: {ans}")
            _fill_and_send(page, ans, question)
            time.sleep(2.0)
            continue





def build_naukri_search_url(role: str, page_no: int, profile: Profile) -> str:
    slug = role.lower().replace(" ", "-").replace("/", "-")
    exp_param = int(profile.total_experience_years)
    page_suffix = "" if page_no == 1 else f"-{page_no}"

    import urllib.parse
    params = []
    if profile.work_mode == "remote_only":
        base_path = f"{slug}-remote-jobs"
        params.extend([
            ("experience", str(exp_param)),
            ("sort", "f"),
            ("wfhType", "0"),
            ("wfhType", "2"),
        ])
    else:
        allowed_locations = []
        if profile.work_mode in ("hybrid", "any", "flexible"):
            allowed_locations.append("remote")
        if profile.current_city:
            allowed_locations.append(profile.current_city)
        allowed_locations.extend(profile.relocate_cities)

        seen = set()
        deduped_locations = [
            loc.lower()
            for loc in allowed_locations
            if loc and loc.lower() not in seen and not seen.add(loc.lower())
        ]
        loc_slug = "-".join(deduped_locations).replace(" ", "-")
        base_path = f"{slug}-jobs-in-{loc_slug}" if loc_slug else f"{slug}-jobs"
        params.extend([
            ("experience", str(exp_param)),
            ("sort", "f"),
        ])

    if getattr(profile, "job_freshness_days", None):
        params.append(("jobAge", str(profile.job_freshness_days)))

    query = urllib.parse.urlencode(params)
    return f"https://www.naukri.com/{base_path}{page_suffix}?{query}"


def run(preview: bool = False, limit: int | None = None):
    profile = Profile.load()
    if not Path(SESSION_FILE).exists():
        raise SystemExit(f"{SESSION_FILE} not found. Run: python login_capture.py naukri")

    human_timeout = profile.data.get("human_input_timeout_seconds", 120)
    max_pages = profile.data.get("max_pages_per_role", 8)
    applied_today = count_applications_today()
    platform_threshold = int(profile.data.get("naukri_limit", profile.data.get("naukri_run_limit", profile.data.get("naukri_daily_limit", 40))))
    target_limit = limit if limit is not None else platform_threshold
    run_success_limit = min(target_limit, int(profile.stop_after_n_applications or 100))
    if preview:
        run_success_limit = target_limit
    max_attempts = profile.data.get(
        "stop_after_n_attempts", max(50, run_success_limit * 3)
    )
    print(f"Naukri session starting (Platform threshold: {platform_threshold} | Applied today: {applied_today} | Target this run: {run_success_limit})")

    applied = 0
    attempted = 0
    applied_job_keys = load_applied_job_keys()
    with sync_playwright() as p:
        browser_mode = profile.data.get("browser_mode", "visible")
        if browser_mode == "headless":
            browser = p.chromium.launch(headless=True, slow_mo=150)
        elif browser_mode == "minimized":
            browser = p.chromium.launch(headless=False, slow_mo=150, args=["--start-minimized"])
        else:
            browser = p.chromium.launch(headless=False, slow_mo=150)
        context = browser.new_context(storage_state=SESSION_FILE)
        page = context.new_page()

        for role in profile.target_roles:
            if applied >= run_success_limit or attempted >= max_attempts:
                break

            seen_job_ids = set()
            stopped_entirely = False

            for page_no in range(1, max_pages + 1):
                if applied >= run_success_limit or attempted >= max_attempts:
                    break

                url = build_naukri_search_url(role, page_no, profile)
                print(f"\n--- Searching: {role}, page {page_no} ({url}) ---")
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_selector('.srp-jobtuple-wrapper[data-job-id]', timeout=10000)
                except PWTimeout:
                    pass
                except Exception as e:
                    print(f"  (navigation error, treating as end of results for this role: {e})")
                    break
                time.sleep(1)


                stop = page_has_stop_signal(page)
                if stop:
                    print(f"STOPPING: {stop}")
                    if not preview:
                        log_row([datetime.now(), "naukri", "-", "-", "stopped", stop])
                    stopped_entirely = True
                    break

                cards = enumerate_cards(page)
                new_cards = [c for c in cards if c.get("jobId") not in seen_job_ids]
                print(f"Found {len(cards)} job cards ({len(new_cards)} new) on page {page_no}.")

                if not new_cards:
                    print("No new listings on this page -- treating as the last page for this role.")
                    break

                for card in cards:
                    if card.get("jobId") in seen_job_ids:
                        stats_tracker.record_duplicate_skipped()
                
                for card in new_cards:
                    seen_job_ids.add(card.get("jobId"))
                    stats_tracker.record_discovered()
                    if applied >= run_success_limit or attempted >= max_attempts:
                        break

                    key = _job_key(card.get("title"), card.get("company"))
                    if key in applied_job_keys:
                        stats_tracker.record_previously_applied_skipped()
                        print(
                            f"Skipped: {card.get('title')} @ {card.get('company')} — "
                            "already applied in an earlier run"
                        )
                        continue

                    ok, reason = passes_filters(card, profile, role)
                    if preview:
                        print(f"{'MATCH' if ok else 'SKIP'}: {card.get('title')} @ {card.get('company')} — {reason or card.get('href')}")
                        continue
                    if not ok:
                        log_row([datetime.now(), "naukri", card.get("title"),
                                  card.get("company"), "skipped", reason])
                        print(f"Skipped: {card.get('title')} @ {card.get('company')} — {reason}")
                        continue

                    try:
                        page.goto(card["href"], wait_until="domcontentloaded", timeout=25000)
                        time.sleep(2)

                        stop = page_has_stop_signal(page)
                        if stop:
                            raise StopRun(f"stop signal: {stop}")

                        apply_state = check_apply_button(page)
                        if apply_state == "external":
                            log_row([datetime.now(), "naukri", card.get("title"),
                                      card.get("company"), "skipped", "external apply"])
                            from common.external_tracker import log_external_job
                            ext_url = safe_evaluate(page, "() => document.querySelector('#company-site-button')?.href || ''", default="")
                            log_external_job("naukri", card.get("title") or "", card.get("company") or "", card.get("href") or "", ext_url, card.get("location") or "", card.get("exp") or "", card.get("posted") or "")
                            print(f"Skipped (External Apply logged to CSV): {card.get('title')} @ {card.get('company')}")
                            continue
                        if apply_state == "none":
                            log_row([datetime.now(), "naukri", card.get("title"),
                                      card.get("company"), "skipped", "no apply button found"])
                            print(f"Skipped: {card.get('title')} @ {card.get('company')} — no apply button found")
                            continue

                        clicked = click_native_apply(page)
                        if not clicked:
                            log_row([datetime.now(), "naukri", card.get("title"),
                                      card.get("company"), "skipped", "apply button click didn't register"])
                            print(f"Skipped: {card.get('title')} @ {card.get('company')} — apply click didn't register")
                            continue
                        attempted += 1
                        time.sleep(2)

                        answer_screening_chat(page, profile, f"{card.get('title')} at {card.get('company')}",
                                               human_timeout)
                        time.sleep(1.5)

                        if verify_applied(page):
                            applied += 1
                            applied_job_keys.add(key)
                            log_row([datetime.now(), "naukri", card.get("title"),
                                      card.get("company"), "applied", ""])
                            print(f"Applied: {card.get('title')} @ {card.get('company')} ({applied} total)")
                        else:
                            shot_path = _save_debug_screenshot(page, card.get("title"))
                            log_row([datetime.now(), "naukri", card.get("title"),
                                      card.get("company"), "uncertain",
                                      f"couldn't confirm submission — screenshot saved to {shot_path}"])
                            print(f"UNCERTAIN: {card.get('title')} @ {card.get('company')} — "
                                  f"couldn't confirm the application actually went through. Check it manually.")

                        wait_before_next_application(profile)

                    except SkipJob as e:
                        log_row([datetime.now(), "naukri", card.get("title"),
                                  card.get("company"), "skipped", str(e)])
                        print(f"Skipped: {card.get('title')} @ {card.get('company')} — {e}")
                        continue

                    except StopRun as e:
                        print(f"STOPPING: {e}")
                        log_row([datetime.now(), "naukri", card.get("title"),
                                  card.get("company"), "stopped", str(e)])
                        browser.close()
                        return

                    except Exception as e:
                        # Anything unexpected (stray Playwright errors, torn-down
                        # page contexts, etc.) -- log it and move to the next
                        # listing instead of crashing the whole run.
                        log_row([datetime.now(), "naukri", card.get("title"),
                                  card.get("company"), "skipped", f"unexpected error: {e}"])
                        print(f"Skipped (unexpected error): {card.get('title')} @ {card.get('company')} — {e}")
                        continue

                if stopped_entirely:
                    break

            if stopped_entirely:
                break

        browser.close()

    if preview:
        print("Preview complete. No applications submitted and no application log written.")
        return
    print(
        f"\nDone. {applied} applications submitted from {attempted} attempts this run. "
        f"See {LOG_FILE} for the full log."
    )


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Naukri Apply Bot")
    parser.add_argument("--limit", type=int, default=None, help="Application limit for this run")
    parser.add_argument("--preview", action="store_true", help="Preview matches without applying")
    args, _ = parser.parse_known_args()
    run(preview=args.preview, limit=args.limit)
