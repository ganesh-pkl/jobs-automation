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
    "notice_period": ["notice period"],
    "current_ctc": ["current ctc", "current salary", "current compensation", "present ctc", "present salary"],
    "expected_ctc": ["expected ctc", "expected salary", "expected compensation"],
    "current_city": ["current city", "current location", "which city", "current place"],
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
)


def log_row(row: list):
    new_file = not Path(LOG_FILE).exists()
    if new_file:
        Path(LOG_FILE).touch(mode=0o600)
    with open(LOG_FILE, "a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["timestamp", "source", "title", "company", "status", "reason"])
        w.writerow(row)


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
    remote_markers = ("remote", "work from home", "wfh")
    is_remote = any(marker in location_lower for marker in remote_markers)
    is_hybrid = "hybrid" in location_lower

    if profile.work_mode == "remote_only" and not is_remote:
        return False, f"not a remote job ({card.get('location')})"

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
          }))
          .filter(c => c.title && c.href)
    """, default=[]) or []


def check_apply_button(page) -> str:
    """Returns 'external', 'native', or 'none' based on the detail page's apply button."""
    if page.query_selector('#company-site-button'):
        return "external"
    if page.query_selector('#apply-button'):
        return "native"
    return "none"


def _wait_send_enabled(page, timeout_ms: int = 4000) -> bool:
    """Polls until the Send control's wrapper no longer has Naukri's
    'disabled' class. Confirmed real markup (from a debug HTML dump you
    sent): <div id="sendMsg__..." class="send disabled"> wraps the
    clickable <div class="sendMsg">Save</div>. It starts disabled and only
    becomes clickable after Naukri's frontend registers your selection and
    re-renders -- clicking Send before that happens is a silent no-op,
    which is what was causing radio/checkbox answers to never actually save."""
    waited = 0
    step = 300
    while waited <= timeout_ms:
        enabled = safe_evaluate(page, """
            () => {
                const wrapper = document.querySelector('[id^="sendMsg__"]');
                if (!wrapper) return true;  // no wrapper found -- don't block forever on a guess
                return !wrapper.className.includes('disabled');
            }
        """, default=True)
        if enabled:
            return True
        time.sleep(step / 1000)
        waited += step
    return False


def _js_click_send(page) -> bool:
    """Clicks Naukri's screening-chat Send/Save control -- a <div class="sendMsg">,
    not a <button>. A JS click bypasses the chatbot_Overlay div that sits
    visually on top of it and blocks Playwright's normal .click()."""
    return safe_evaluate(page, """
        () => {
            const el = document.querySelector('.sendMsg');
            if (!el) return false;
            el.click();
            return true;
        }
    """, default=False)


def click_native_apply(page) -> bool:
    """Clicks the Apply button and returns whether the click actually
    registered, so the caller can tell 'click failed' apart from 'clicked
    fine, just couldn't confirm success afterward' -- those need different
    handling."""
    return bool(safe_evaluate(page, """
        () => {
            const btn = document.getElementById('apply-button');
            if (btn) { btn.click(); return true; }
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
    if label in {"applied", "already applied"}:
        return True
    success_patterns = (
        r'\bsuccessfully applied\b',
        r'\bapplication (?:has been )?sent\b',
        r'\byou have applied\b',
        r'\bapplication submitted\b',
        r'\bapplied to\s+["“][^"”]+["”]',
        r'\bthank you for your responses?\b',
    )
    return any(re.search(pattern, text) for pattern in success_patterns)


def verify_applied(page) -> bool:
    """Best-effort check that the application actually went through. Confirmed
    real pattern: Naukri shows a green checkmark panel reading
    'Applied to "<job title>"' a few seconds after submit -- checking for
    that prefix specifically, plus a short wait since it isn't instant."""
    try:
        page.wait_for_timeout(3000)  # the confirmation panel takes a moment to appear
    except Exception:
        pass
    try:
        text = page.inner_text("body").lower()
    except Exception:
        return False
    label = ""
    btn = page.query_selector('#apply-button')
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
    """Reads radio/checkbox options in the chat drawer, with their visible labels."""
    return safe_evaluate(page, """
        () => {
            const root = document.querySelector('[class*="chatbot_Drawer"]') || document;
            const inputs = Array.from(root.querySelectorAll('input[type=radio], input[type=checkbox]'));
            return inputs.map((el, idx) => {
                let label = '';
                if (el.id) {
                    const lbl = root.querySelector(`label[for="${el.id}"]`);
                    if (lbl) label = lbl.innerText.trim();
                }
                if (!label) {
                    const parentLabel = el.closest('label');
                    if (parentLabel) label = parentLabel.innerText.trim();
                }
                if (!label && el.nextElementSibling) {
                    label = (el.nextElementSibling.innerText || '').trim();
                }
                return {idx, label, type: el.type};
            });
        }
    """, default=[]) or []


def _click_option(page, idx: int) -> bool:
    return safe_evaluate(page, """
        (idx) => {
            const root = document.querySelector('[class*="chatbot_Drawer"]') || document;
            const inputs = Array.from(root.querySelectorAll('input[type=radio], input[type=checkbox]'));
            if (inputs[idx]) { inputs[idx].click(); return true; }
            return false;
        }
    """, arg=idx, default=False)


def _click_skip_question(page) -> bool:
    """Click Naukri's explicit skip chip for an inapplicable question."""
    return bool(safe_evaluate(page, """
        () => {
            const root = document.querySelector('[class*="chatbot_Drawer"]') || document;
            const candidates = Array.from(root.querySelectorAll(
                'button, [role="button"], .chatbot_Chip, .chipItem'
            ));
            const skip = candidates.find(
                el => (el.innerText || '').trim().toLowerCase() === 'skip this question'
            );
            if (!skip) return false;
            skip.click();
            return true;
        }
    """, default=False))


def _resume_path(profile: Profile) -> Path | None:
    """Resolve the private resume configured for profile-update prompts."""
    configured = str(profile.data.get("resume_file_name", "")).strip()
    if not configured:
        return None
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path if path.is_file() else None


def _upload_resume(page, profile: Profile, question: str) -> bool:
    """Upload the configured resume when Naukri Campus requires it."""
    lower_q = question.lower()
    if "upload" not in lower_q or "resume" not in lower_q:
        return False

    resume_path = _resume_path(profile)
    if resume_path is None:
        raise SkipJob(
            "Naukri requested a resume upload, but resume_file_name does not point "
            "to a readable file"
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
    print(f"  (uploaded configured resume: {resume_path.name})")
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
            r"\b(?:experience|years?)\b[^?]{0,80}\b(?:in|with|using|on)\b",
            lower_q,
        )
        or re.search(r"\byears?\s+of\s+[^?]+\s+experience\b", lower_q)
    )
    if asks_skill_specific_experience:
        try:
            if float(answers.get("years_experience", 0)) <= 0:
                return "0"
        except (TypeError, ValueError):
            pass
    if (
        "years_experience" in answers
        and not asks_skill_specific_experience
        and any(phrase in lower_q for phrase in TOTAL_EXPERIENCE_PHRASES)
    ):
        return str(answers["years_experience"])

    for key, phrases in TRIGGER_PHRASES.items():
        if key in answers and any(phrase in lower_q for phrase in phrases):
            return str(answers[key])
    return None


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
    """Tries to pick the correct option using known profile facts, for
    common Yes/No-style questions. Returns None (never guesses) if nothing
    in the profile clearly answers this specific question -- caller falls
    back to asking you directly rather than risk a wrong click."""
    lower_q = question.lower()
    by_label = {o["idx"]: o["label"].strip().lower() for o in options}

    def find_by_text(text: str):
        for idx, label in by_label.items():
            if label == text.lower():
                return next(o for o in options if o["idx"] == idx)
        return None

    rules = [
        (["relocate", "relocation", "willing to move"],
         "Yes" if profile.data.get("relocate_cities") else "No"),
        (["night shift"], "Yes" if profile.data.get("night_shift_ok") else "No"),
        (["weekend"], "Yes" if profile.data.get("weekend_ok") else "No"),
        (["immediately available", "immediate joiner"],
         "Yes" if profile.data.get("immediately_available") else "No"),
        (["currently employed", "currently working"],
         "Yes" if profile.data.get("current_employer") else "No"),
    ]
    for triggers, desired in rules:
        if any(t in lower_q for t in triggers):
            match = find_by_text(desired)
            if match:
                return match
    return None


def _handle_options_question(page, question: str, profile: Profile, timeout_s: int) -> bool:
    """Returns True if handled (clicked something), raises SkipJob otherwise."""
    options = _get_options(page)
    if not options:
        return False

    sensitive = _is_sensitive_field(question)
    if not sensitive:
        stored = learned_answers.get_answer(question)
        if stored:
            for opt in options:
                if opt["label"].strip().lower() == stored.strip().lower():
                    _click_option(page, opt["idx"])
                    print(f"  (used a remembered answer for: {question[:80]})")
                    return True

        auto = _auto_decide_option(question, options, profile)
        if auto:
            _click_option(page, auto["idx"])
            learned_answers.save_answer(question, auto["label"])
            print(f"  (auto-picked '{auto['label']}' from your profile for: {question[:80]})")
            return True

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
        raise SkipJob(f"couldn't match your response '{response}' to an option: {question[:120]}")

    for match in matches:
        _click_option(page, match["idx"])
    if not sensitive:
        learned_answers.save_answer(
            question, matches[0]["label"] if len(matches) == 1 else response
        )
    return True


# If the chat's last message is one of these, it's wrapping up rather than
# asking something new -- stop cleanly instead of trying to draft an answer
# into a box that may no longer exist.
COMPLETION_PHRASES = [
    "thank you for applying", "thanks for your response", "thanks for your time",
    "thank you for your response", "thank you for your responses",
    "we will get back", "responses have been recorded", "no further questions",
    "application submitted", "application was submitted", "that's all",
    "all the information we need",
]


def _is_completion_message(text: str) -> bool:
    lower = text.lower()
    return any(p in lower for p in COMPLETION_PHRASES)


def _has_answerable_input(page, timeout_ms: int = 3000) -> bool:
    """Checks for a text box or radio/checkbox to answer. Polls for up to
    timeout_ms instead of checking once -- the previous single-check version
    could wrongly conclude "nothing to answer" if the next question's input
    just hadn't rendered yet after the previous Save click, causing it to
    skip clicking Save on what was actually still a real question."""
    waited = 0
    step = 300
    while waited <= timeout_ms:
        found = safe_evaluate(page, """
            () => !!(document.querySelector('[id^="userInput"], [contenteditable="true"]') ||
                     document.querySelector(
                         'input[type=radio], input[type=checkbox], input[type=file]'
                     ))
        """, default=False)
        if found:
            return True
        time.sleep(step / 1000)
        waited += step
    return False


def _read_filled_text(page) -> str:
    return safe_evaluate(page, """
        () => {
            const ed = document.querySelector('[id^="userInput"], [contenteditable="true"]');
            return ed ? (ed.innerText || ed.textContent || '').trim() : '';
        }
    """, default="") or ""


def _fill_and_send(page, text: str, question: str):
    """Fills the answer box, VERIFIES the text actually landed before
    clicking Send, then sends. This is the fix for answers going through
    blank: previously Send could fire even if the fill silently failed
    (a timing hiccup, or the box wasn't there), which is what produces
    Naukri's "incomplete information" rejection on the final application."""
    _fill_freetext(page, text)
    time.sleep(0.4)
    filled = _read_filled_text(page)
    if not filled:
        _fill_freetext(page, text)  # one retry -- could be a focus/timing hiccup
        time.sleep(0.6)
        filled = _read_filled_text(page)
    if not filled:
        raise SkipJob(f"couldn't confirm the answer registered before sending: {question[:120]}")
    _wait_send_enabled(page)  # same disabled-until-registered pattern as the options branch
    _js_click_send(page)
    time.sleep(2.0)  # give Naukri's backend a moment to actually save it


def answer_screening_chat(page, profile: Profile, job_context: str, timeout_s: int):
    """Handles Naukri's post-apply screening chat drawer, if it appears."""
    answers = profile.answer_library()
    try:
        # Broadened on purpose: a pure radio/checkbox question has no text
        # box at all, so waiting only for contenteditable was causing the
        # function to give up immediately on those questions, thinking
        # there was no screening chat when there actually was one.
        page.wait_for_selector(
            '[contenteditable="true"], [contenteditable=""], '
            'input[type=radio], input[type=checkbox], input[type=file]',
            timeout=30000,
        )
    except PWTimeout:
        return  # no screening chat for this listing

    for _ in range(15):
        stop = page_has_stop_signal(page)
        if stop:
            raise StopRun(f"stop signal during screening chat: {stop}")

        bubbles = page.query_selector_all('.botMsg')
        if not bubbles:
            break
        try:
            question = bubbles[-1].inner_text().strip()
        except Exception:
            break  # page likely navigated away mid-read; treat as chat finished
        if not question:
            break

        if "input seems invalid" in question.lower():
            raise SkipJob(f"Naukri rejected the previous answer: {question[:120]}")

        if _is_completion_message(question):
            time.sleep(1.5)  # let the "Applied" confirmation redirect begin before we check for it
            break  # chat is wrapping up, not asking a new question

        if not _has_answerable_input(page):
            break  # nothing to fill or click here — informational message only

        if _upload_resume(page, profile, question):
            continue

        options = _get_options(page)
        if options:
            _handle_options_question(page, question, profile, timeout_s)
            if not _wait_send_enabled(page):
                raise SkipJob(f"Send button stayed disabled after selecting an option "
                               f"(selection may not have registered): {question[:120]}")
            _js_click_send(page)
            time.sleep(2.0)
            continue


        if _should_skip_question(question, profile):
            if not _click_skip_question(page):
                raise SkipJob(
                    f"current CTC does not apply, but no skip control was found: {question[:120]}"
                )
            print("  (skipped current CTC because the profile is marked as fresher / 0 LPA)")
            time.sleep(2.0)
            continue

        if _is_sensitive_field(question):
            response = ask_user(
                f"Screening question (personal detail):\n{question}",
                timeout_seconds=timeout_s,
            )
            if response is None:
                raise SkipJob(f"no response for sensitive field: {question[:120]}")
            _fill_and_send(page, response, question)
            continue

        stored = learned_answers.get_answer(question)
        if stored:
            _fill_and_send(page, stored, question)
            print(f"  (used a remembered answer for: {question[:80]})")
            continue

        direct_answer = _direct_profile_answer(question, answers)
        if direct_answer is not None:
            _fill_and_send(page, direct_answer, question)
            continue

        try:
            draft = llm.draft_answer(question, profile.llm_context(), job_context)
        except Exception:
            print("  AI drafting unavailable; requesting a manual answer.")
            draft = "[NEEDS_HUMAN_INPUT: provider unavailable]"
        if draft.startswith("[NEEDS_HUMAN_INPUT"):
            response = ask_user(f"Screening question (AI couldn't answer from your profile):\n{question}",
                                 timeout_seconds=timeout_s)
            if response is None:
                raise SkipJob(f"no response for: {question[:120]}")
            _fill_and_send(page, response, question)
            learned_answers.save_answer(question, response)
        else:
            _fill_and_send(page, draft, question)


def _fill_freetext(page, text: str):
    safe_evaluate(page, """
        (text) => {
            const ed = document.querySelector('[id^="userInput"], [contenteditable="true"]');
            if (!ed) return;
            ed.focus();
            document.execCommand('insertText', false, text);
        }
    """, arg=text)


def run(preview: bool = False):
    profile = Profile.load()
    if not Path(SESSION_FILE).exists():
        raise SystemExit(f"{SESSION_FILE} not found. Run: python login_capture.py naukri")

    human_timeout = profile.data.get("human_input_timeout_seconds", 120)
    max_pages = profile.data.get("max_pages_per_role", 5)
    max_attempts = profile.data.get(
        "stop_after_n_attempts", profile.stop_after_n_applications
    )
    daily_limit = int(profile.data.get("daily_application_limit", 10))
    applied_today = count_applications_today()
    remaining_today = max(0, daily_limit - applied_today)
    run_success_limit = min(profile.stop_after_n_applications, remaining_today)
    if preview:
        run_success_limit = profile.stop_after_n_applications
    if run_success_limit <= 0:
        print(
            f"Daily application limit reached ({applied_today}/{daily_limit}). "
            "No applications were attempted."
        )
        return

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

            slug = role.lower().replace(" ", "-")
            exp_param = int(profile.total_experience_years)
            seen_job_ids = set()
            stopped_entirely = False

            for page_no in range(1, max_pages + 1):
                if applied >= run_success_limit or attempted >= max_attempts:
                    break

                # Build location slug
                # Build location slug prioritizing remote, then current city, then relocate cities
                allowed_locations = []
                if profile.work_mode in ("remote_only", "hybrid", "any", "flexible"):
                    allowed_locations.append("remote")
                if profile.current_city:
                    allowed_locations.append(profile.current_city)
                allowed_locations.extend(profile.relocate_cities)
                
                # Deduplicate and format
                seen = set()
                deduped_locations = [loc.lower() for loc in allowed_locations if loc and loc.lower() not in seen and not seen.add(loc.lower())]
                loc_slug = "-".join(deduped_locations).replace(" ", "-")
                
                base_path = f"{slug}-jobs-in-{loc_slug}" if loc_slug else f"{slug}-jobs"
                page_suffix = "" if page_no == 1 else f"-{page_no}"
                
                url = (
                    f"https://www.naukri.com/{base_path}{page_suffix}"
                    f"?experience={exp_param}"
                    f"&sort=f"
                )
                if profile.job_freshness_days:
                    url += f"&jobAge={profile.job_freshness_days}"
                print(f"\n--- Searching: {role}, page {page_no} ({url}) ---")
                try:
                    page.goto(url)
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

                for card in new_cards:
                    seen_job_ids.add(card.get("jobId"))
                    if applied >= run_success_limit or attempted >= max_attempts:
                        break

                    key = _job_key(card.get("title"), card.get("company"))
                    if key in applied_job_keys:
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
                        page.goto(card["href"])
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

                        if attempted:
                            wait_before_next_application(profile)
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
    run()
