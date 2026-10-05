"""
LinkedIn Easy Apply automation with Groq LLM dynamic drafting, anti-detection pacing, and robust form handling.
Requires session_linkedin.json from login_capture.py.
"""
import csv
import random
import re
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout, Error as PWError

from common.profile import Profile
from common import llm
from common import stats_tracker
from common.answers import get_screening_answer
from common.recruiter_connect import extract_hiring_manager, send_recruiter_connection_request

SESSION_FILE = "session_linkedin.json"
LOG_FILE = "applications_log.csv"

STOP_PHRASES = [
    "easy apply limit",
    "application limit",
    "maximum number of easy apply",
    "reached the limit for easy apply",
    "reached the easy apply limit",
    "try again tomorrow",
    "come back tomorrow",
    "unusual activity",
    "verify it's you",
    "captcha",
    "security verification",
    "we've restricted",
    "temporarily restricted",
]

EXCLUDED_SENIORITY_KEYWORDS = [
    "lead", "principal", "staff", "architect", "director", "head of",
    "manager", "vp", "vice president", "sr.", "sr ", "senior", "group lead",
]


def extract_experience_requirement(text: str) -> int | None:
    """Extracts minimum years of experience required from text. Returns min_years or None."""
    if not text:
        return None
    lower = text.lower()
    
    # Check "X+ years", "X-Y years", "X to Y years", "X yrs"
    matches = re.findall(r"(\d+)\s*(?:-|to|\+)\s*(?:(\d+)\s*)?(?:years?|yrs?)", lower)
    for m in matches:
        try:
            val = int(m[0])
            if 1 <= val <= 25:
                return val
        except (ValueError, IndexError):
            pass
            
    # Check "minimum X years", "at least X years", "min X years"
    min_match = re.search(r"(?:minimum|at least|min\.?)\s*(\d+)\s*(?:years?|yrs?)", lower)
    if min_match:
        try:
            val = int(min_match.group(1))
            if 1 <= val <= 25:
                return val
        except ValueError:
            pass
            
    return None


def _resume_path(profile: Profile) -> Path | None:
    """Resolve the private resume configured in profile.yaml."""
    configured = str(profile.data.get("resume_file_name", "")).strip()
    if not configured:
        return None
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path if path.is_file() else None


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


def count_applications_today(path: str = LOG_FILE, today=None) -> int:
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
                if src == "linkedin" and row.get("status") == "applied":
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


def wait_before_next_application(profile: Profile) -> float:
    """Sleeps a randomized human cooldown delay between successful applications."""
    min_delay = float(getattr(profile, "min_delay_seconds_between_applications", 75))
    max_delay = float(getattr(profile, "max_delay_seconds_between_applications", 135))
    if min_delay > max_delay:
        min_delay, max_delay = max_delay, min_delay
    delay = random.uniform(min_delay, max_delay)
    print(f"  ⏳ Human pacing cooldown: waiting {int(delay)}s before next application...")
    time.sleep(delay)
    return delay


def page_has_stop_signal(page) -> str | None:
    text = page.inner_text("body").lower()
    for phrase in STOP_PHRASES:
        if phrase in text:
            return phrase
    if "/checkpoint/" in page.url or "/login" in page.url:
        return "session expired / checkpoint challenge"
    return None


def describe_modal(page) -> dict:
    return page.evaluate("""
        () => {
            const modal = document.querySelector('.jobs-easy-apply-modal') ||
                          document.querySelector('dialog[open]') ||
                          document.querySelector('.artdeco-modal[role="dialog"]:not([aria-hidden="true"])') ||
                          document.querySelector('dialog, .jobs-easy-apply-modal, [role="dialog"], .artdeco-modal');
            if (!modal) return {error: 'no dialog found'};
            const heading = modal.querySelector('h2, h3, h1, .artdeco-modal__header')?.innerText || '';
            const fields = [];
            const buttons = [];

            // 1. Radio groups & Fieldsets
            const radioContainers = Array.from(modal.querySelectorAll('fieldset, [data-test-form-builder-radios-form-component], .fb-dash-form-element, [class*="radio-group"]'));
            const processedRadioIds = new Set();

            radioContainers.forEach(container => {
                const radios = Array.from(container.querySelectorAll('input[type="radio"]'));
                if (radios.length === 0) return;

                let question = container.querySelector('legend, [class*="label"], label, p, h3, h4')?.innerText?.trim() || '';
                if (!question) {
                    const prev = container.previousElementSibling;
                    if (prev) question = prev.innerText.trim();
                }

                const radioList = radios.map(r => {
                    processedRadioIds.add(r.id);
                    let rLabel = '';
                    if (r.id) {
                        try {
                            const lbl = modal.querySelector(`label[for="${CSS.escape(r.id)}"]`);
                            if (lbl) rLabel = lbl.innerText.trim();
                        } catch(e) {}
                    }
                    if (!rLabel) {
                        const parentLbl = r.closest('label');
                        if (parentLbl) rLabel = parentLbl.innerText.trim();
                    }
                    if (!rLabel) rLabel = r.value || '';
                    return { id: r.id, value: r.value, label: rLabel, checked: r.checked };
                });

                if (radioList.length > 0) {
                    fields.push({
                        tag: 'FIELDSET_RADIO',
                        type: 'fieldset_radio',
                        question: question,
                        radios: radioList
                    });
                }
            });

            // 2. Standalone Radios not in fieldsets
            modal.querySelectorAll('input[type="radio"]').forEach(r => {
                if (processedRadioIds.has(r.id)) return;
                let rLabel = '';
                if (r.id) {
                    try {
                        const lbl = modal.querySelector(`label[for="${CSS.escape(r.id)}"]`);
                        if (lbl) rLabel = lbl.innerText.trim();
                    } catch(e) {}
                }
                fields.push({
                    tag: 'INPUT',
                    type: 'radio',
                    id: r.id,
                    label: rLabel || r.value,
                    value: r.value,
                    checked: r.checked
                });
            });

            // 3. Checkboxes
            modal.querySelectorAll('input[type="checkbox"]').forEach(cb => {
                let label = '';
                if (cb.id) {
                    try {
                        const lbl = modal.querySelector(`label[for="${CSS.escape(cb.id)}"]`);
                        if (lbl) label = lbl.innerText.trim();
                    } catch(e) {}
                }
                if (!label) {
                    const parentLbl = cb.closest('label');
                    if (parentLbl) label = parentLbl.innerText.trim();
                }
                fields.push({
                    tag: 'INPUT',
                    type: 'checkbox',
                    id: cb.id,
                    label: label,
                    checked: cb.checked,
                    required: cb.required || cb.getAttribute('aria-required') === 'true'
                });
            });

            // 4. Select Dropdowns
            modal.querySelectorAll('select').forEach(sel => {
                let label = sel.getAttribute('aria-label') || '';
                if (!label && sel.id) {
                    try {
                        const lbl = modal.querySelector(`label[for="${CSS.escape(sel.id)}"]`);
                        if (lbl) label = lbl.innerText.trim();
                    } catch(e) {}
                }
                if (!label) {
                    const parent = sel.closest('label') || sel.closest('.fb-dash-form-element, [class*="form-element"]');
                    if (parent) {
                        const span = parent.querySelector('span, label, legend, [class*="label"]');
                        if (span) label = span.innerText.trim();
                    }
                }
                const val = (sel.value || '').trim();
                const selectedOpt = sel.options[sel.selectedIndex];
                const selectedText = (selectedOpt ? selectedOpt.text : '').trim();
                const isUnselected = !val || val.toLowerCase().startsWith('select') || val.toLowerCase().startsWith('choose') || selectedText.toLowerCase().startsWith('select') || selectedText.toLowerCase().startsWith('choose') || sel.selectedIndex <= 0;
                fields.push({
                    tag: 'SELECT',
                    type: 'select',
                    id: sel.id,
                    label: label,
                    value: isUnselected ? '' : val,
                    required: sel.required
                });
            });

            // 5. Text, Number, Textarea, and File Inputs
            modal.querySelectorAll('input, textarea').forEach(el => {
                const t = (el.type || 'text').toLowerCase();
                if (t === 'hidden' || t === 'radio' || t === 'checkbox') return;
                let label = el.getAttribute('aria-label') || '';
                if (!label && el.id) {
                    try {
                        const lbl = modal.querySelector(`label[for="${CSS.escape(el.id)}"]`);
                        if (lbl) label = lbl.innerText.trim();
                    } catch(e) {}
                }
                if (!label && el.getAttribute('aria-labelledby')) {
                    const lblEl = document.getElementById(el.getAttribute('aria-labelledby'));
                    if (lblEl) label = lblEl.innerText.trim();
                }
                if (!label) {
                    const parent = el.closest('label') || el.closest('.fb-dash-form-element, [class*="form-element"]');
                    if (parent) {
                        const span = parent.querySelector('span, label, legend, [class*="label"]');
                        if (span) label = span.innerText.trim();
                    }
                }
                if (!label) label = el.placeholder || '';
                fields.push({
                    tag: el.tagName,
                    type: t,
                    id: el.id,
                    name: el.name || '',
                    label: label,
                    value: t === 'file' ? '' : (el.value || ''),
                    required: el.required || el.getAttribute('aria-required') === 'true'
                });
            });

            // 6. Buttons
            modal.querySelectorAll('button').forEach(b => {
                const t = (b.innerText || '').trim();
                const aria = (b.getAttribute('aria-label') || '').trim();
                const isPrimary = b.classList.contains('artdeco-button--primary');
                if (t || aria) {
                    buttons.push({ text: t, aria: aria, isPrimary: isPrimary });
                }
            });

            return { heading, fields, buttons };
        }
    """)


def click_radio_by_id(page, r_id: str):
    """Safely clicks a radio button by its ID attribute, clicking its label and dispatching synthetic events."""
    if not r_id:
        return
    page.evaluate("""(id) => {
        const input = document.getElementById(id);
        if (!input) return false;
        
        // Find associated label
        let label = null;
        try {
            label = document.querySelector(`label[for="${CSS.escape(id)}"]`);
        } catch(e) {}
        if (!label) {
            label = input.closest('label') || input.parentElement?.querySelector('label');
        }
        if (!label) {
            label = input.closest('.fb-radio, .artdeco-radio, [class*="radio"]');
        }
        
        if (label) {
            label.scrollIntoView({ behavior: 'instant', block: 'nearest' });
            label.click();
        } else {
            input.scrollIntoView({ behavior: 'instant', block: 'nearest' });
            input.click();
        }
        
        input.checked = true;
        input.dispatchEvent(new Event('click', { bubbles: true }));
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
        return true;
    }""", r_id)
    time.sleep(random.uniform(0.2, 0.4))


def click_checkbox_by_id(page, cb_id: str, check: bool = True):
    """Safely checks or unchecks a checkbox by ID attribute and dispatches events."""
    if not cb_id:
        return
    page.evaluate("""([id, shouldCheck]) => {
        const cb = document.getElementById(id);
        if (!cb) return false;
        let label = null;
        try {
            label = document.querySelector(`label[for="${CSS.escape(id)}"]`);
        } catch(e) {}
        if (!label) {
            label = cb.closest('label') || cb.parentElement?.querySelector('label');
        }
        if (cb.checked !== shouldCheck) {
            if (label) {
                label.scrollIntoView({ behavior: 'instant', block: 'nearest' });
                label.click();
            } else {
                cb.scrollIntoView({ behavior: 'instant', block: 'nearest' });
                cb.click();
            }
            cb.checked = shouldCheck;
            cb.dispatchEvent(new Event('click', { bubbles: true }));
            cb.dispatchEvent(new Event('input', { bubbles: true }));
            cb.dispatchEvent(new Event('change', { bubbles: true }));
        }
        return true;
    }""", [cb_id, check])
    time.sleep(random.uniform(0.2, 0.4))


def fill_text_field(page, field_id: str, text: str) -> bool:
    """Fills an input or textarea with human keystroke cadence, respecting maxlength and randomized delays."""
    if not text or not field_id:
        return False
    text_str = str(text).strip()
    try:
        el = page.locator(f'[id="{field_id}"]').first
        if el.count() > 0:
            input_type = (el.get_attribute("type") or "").lower()
            if input_type in ("file", "hidden", "submit", "button", "checkbox", "radio"):
                return False
            max_len_attr = el.get_attribute("maxlength")
            if max_len_attr and max_len_attr.isdigit():
                max_len = int(max_len_attr)
                if len(text_str) > max_len:
                    text_str = text_str[:max_len]
            el.scroll_into_view_if_needed()
            time.sleep(random.uniform(0.15, 0.35))
            el.click(force=True)
            time.sleep(random.uniform(0.1, 0.25))
            el.fill("")
            time.sleep(0.1)
            el.type(text_str, delay=random.randint(40, 80))
            time.sleep(random.uniform(0.2, 0.4))
            return True
    except Exception:
        pass

    # Fallback to DOM evaluation if locator fails
    return page.evaluate(
        """([id, text]) => {
            const el = document.getElementById(id);
            if (!el || el.type === 'file' || el.type === 'hidden') return false;
            let val = text;
            if (el.maxLength && el.maxLength > 0 && val.length > el.maxLength) {
                val = val.substring(0, el.maxLength);
            }
            let proto = window.HTMLInputElement.prototype;
            if (el.tagName === 'TEXTAREA') {
                proto = window.HTMLTextAreaElement.prototype;
            }
            const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
            setter.call(el, val);
            el.dispatchEvent(new Event('input', {bubbles: true}));
            el.dispatchEvent(new Event('change', {bubbles: true}));
            return true;
        }""",
        [field_id, text_str],
    )


def fill_typeahead_field(page, field_id: str, text: str, preferred_match: str = "") -> bool:
    """
    Types text into an autocomplete/combobox input, selects the preferred suggestion if a dropdown appears,
    and dismisses the overlay popup cleanly.
    """
    if not text or not field_id:
        return False
    try:
        el = page.locator(f'[id="{field_id}"]').first
        if el.count() > 0:
            el.scroll_into_view_if_needed()
            time.sleep(random.uniform(0.15, 0.3))
            el.click(force=True)
            time.sleep(0.1)
            el.fill("")
            time.sleep(0.1)
            el.type(str(text), delay=random.randint(40, 80))
            time.sleep(random.uniform(0.5, 0.8))

            # Look for dropdown / typeahead list options and click
            dropdown_clicked = page.evaluate("""([preferred]) => {
                const modal = document.querySelector('dialog, .jobs-easy-apply-modal, [role="dialog"], .artdeco-modal') || document;
                const items = Array.from(modal.querySelectorAll(
                    '.artdeco-typeahead__results-list li, ' +
                    'div[role="listbox"] div[role="option"], ' +
                    '.search-basic-typeahead__results-list li, ' +
                    '.basic-typeahead__selectable-item, ' +
                    'li.artdeco-typeahead__result, ' +
                    'div[role="option"]'
                ));
                if (items.length === 0) return false;

                function fireItemClick(target) {
                    target.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, cancelable: true, view: window}));
                    target.dispatchEvent(new MouseEvent('mouseup', {bubbles: true, cancelable: true, view: window}));
                    target.click();
                }

                if (preferred) {
                    const prefLower = preferred.toLowerCase();
                    const matched = items.find(it => (it.innerText || '').toLowerCase().includes(prefLower));
                    if (matched) {
                        fireItemClick(matched);
                        return true;
                    }
                }
                // Fallback: click first visible non-empty dropdown item
                fireItemClick(items[0]);
                return true;
            }""", [preferred_match])

            time.sleep(random.uniform(0.3, 0.5))
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
            return True
    except Exception:
        pass

    # Fallback to standard fill_text_field
    return fill_text_field(page, field_id, text)


def select_dropdown_option(page, field_id: str, label: str, profile: Profile, job_context: str) -> bool:
    """
    Intelligently selects the best matching option in a <select> element based on candidate facts.
    """
    if not field_id:
        return False
    lbl_lower = (label or "").lower()

    start_year = str(profile.data.get("education_start_year", "2019"))
    grad_year = str(profile.data.get("graduation_year", "2023"))

    # Determine candidate preferences for this dropdown
    preferred_values = []
    if any(w in lbl_lower for w in ("contract", "short-term", "short term", "engagement", "comfortable", "3 months", "6 months")):
        preferred_values = ["yes", "comfortable", "true", "1", "agree", "accept"]
    elif any(w in lbl_lower for w in ("hourly", "per hour", "usd", "rate (in usd)", "rate in usd")):
        if any(w in lbl_lower for w in ("current", "present", "now")):
            preferred_values = ["15", "15-20", "10-15", "15$", "$15"]
        else:
            preferred_values = ["25", "20-25", "25-30", "20$", "25$", "$25"]
    elif any(w in lbl_lower for w in ("qualification", "degree", "education")):
        preferred_values = ["graduate", "bachelor", "b.tech", "b.e", "b tech", "degree", "undergraduate", "engineering", "diploma", "post graduate", "master"]
    elif any(w in lbl_lower for w in ("current job title", "current title", "designation", "job title", "role", "employment status")):
        preferred_values = ["full stack", "full-stack", "software developer", "software engineer", "developer", "engineer", "employed", "full time", "full-time"]
    elif any(w in lbl_lower for w in ("experience", "how many years", "years")):
        preferred_values = ["3", "3 years", "2-3", "3-5", "2 to 3", "3 to 5", "3+", "2 - 3", "3 - 5"]
    elif any(p in lbl_lower for p in ("respond '1'", "respond 1", "enter '1'", "enter 1", "immediate joiner")):
        preferred_values = ["1", "immediate", "yes"]
    elif any(w in lbl_lower for w in ("notice", "joining", "availability")):
        preferred_values = ["immediate", "0", "15", "30", "1 month", "less than 1 month", "currently serving", "1"]
    elif "gender" in lbl_lower or "sex" in lbl_lower:
        preferred_values = ["male", "man", "he/him"]
    elif "country" in lbl_lower or "nationality" in lbl_lower:
        preferred_values = ["india", "+91"]
    elif "state" in lbl_lower or "province" in lbl_lower:
        preferred_values = ["telangana", "andhra"]
    elif "city" in lbl_lower or "location" in lbl_lower:
        preferred_values = ["hyderabad", "bengaluru", "bangalore"]
    else:
        ans = get_screening_answer(label, profile, job_context)
        if ans:
            if ans.lower().startswith("yes"):
                preferred_values = ["yes", "true", "1"]
            elif ans.lower().startswith("no"):
                preferred_values = ["no", "false", "0"]
            else:
                preferred_values = [ans.lower()]


    return page.evaluate("""([id, preferredList, startYear, gradYear]) => {
        const el = document.getElementById(id);
        if (!el || el.tagName !== 'SELECT' || el.options.length <= 1) return false;

        const optionsArray = Array.from(el.options);
        const isYearSelect = optionsArray.some(o => /^(19|20)\d{2}$/.test((o.text || '').trim()));
        const isMonthSelect = optionsArray.some(o => /^(january|february|march|april|may|june|july|august|september|october|november|december)$/i.test((o.text || '').trim()));

        if (isYearSelect) {
            const parent = el.closest('.fb-dash-form-element, fieldset, div[class*="date"], div[class*="form"]') || el.parentElement || document.body;
            const parentText = (parent.innerText || '').toLowerCase();
            const lblText = (((document.querySelector(`label[for="${CSS.escape(id)}"]`)?.innerText || '') + ' ' + (el.getAttribute('aria-label') || ''))).toLowerCase();
            
            let targetYears = [gradYear, "2023", "2022"];
            if (lblText.includes('start') || lblText.includes('from') || lblText.includes('join') || parentText.includes('from') || parentText.includes('start date')) {
                targetYears = [startYear, "2019", "2020", "2018"];
            } else if (lblText.includes('end') || lblText.includes('to') || lblText.includes('graduat') || parentText.includes('to') || parentText.includes('end date')) {
                targetYears = [gradYear, "2023", "2022", "2024"];
            } else {
                const allYearSelects = Array.from(document.querySelectorAll('select')).filter(s => Array.from(s.options).some(o => /^(19|20)\d{2}$/.test((o.text || '').trim())));
                if (allYearSelects.length >= 2 && allYearSelects[0] === el) {
                    targetYears = [startYear, "2019", "2020"];
                } else {
                    targetYears = [gradYear, "2023", "2022"];
                }
            }

            for (const yr of targetYears) {
                for (let i = 0; i < el.options.length; i++) {
                    const optText = (el.options[i].text || '').trim();
                    const optVal = (el.options[i].value || '').trim();
                    if (optText === yr || optVal === yr) {
                        el.selectedIndex = i;
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                        el.dispatchEvent(new Event('input', {bubbles: true}));
                        return true;
                    }
                }
            }
        }

        if (isMonthSelect) {
            const parent = el.closest('.fb-dash-form-element, fieldset, div[class*="date"], div[class*="form"]') || el.parentElement || document.body;
            const parentText = (parent.innerText || '').toLowerCase();
            const lblText = (((document.querySelector(`label[for="${CSS.escape(id)}"]`)?.innerText || '') + ' ' + (el.getAttribute('aria-label') || ''))).toLowerCase();
            let targetMonths = ["june", "may", "july", "august", "january"];
            if (lblText.includes('start') || lblText.includes('from') || parentText.includes('from')) {
                targetMonths = ["august", "july", "september", "january"];
            }
            for (const m of targetMonths) {
                for (let i = 0; i < el.options.length; i++) {
                    const optText = (el.options[i].text || '').toLowerCase();
                    if (optText.includes(m)) {
                        el.selectedIndex = i;
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                        el.dispatchEvent(new Event('input', {bubbles: true}));
                        return true;
                    }
                }
            }
        }

        // 1. Try to match preferred candidates
        for (const pref of preferredList) {
            for (let i = 0; i < el.options.length; i++) {
                const optText = (el.options[i].text || '').toLowerCase();
                const optVal = (el.options[i].value || '').toLowerCase();
                if (optText.includes(pref) || optVal.includes(pref)) {
                    el.selectedIndex = i;
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    return true;
                }
            }
        }

        // 2. Fallback: Select first non-empty valid option that is not a placeholder and not a future year > 2026
        const currentCalendarYear = new Date().getFullYear();
        for (let i = 1; i < el.options.length; i++) {
            const val = (el.options[i].value || '').trim();
            const text = (el.options[i].text || '').trim().toLowerCase();
            const numVal = parseInt(text, 10);
            if (val !== '' && !text.includes('select') && !text.includes('choose')) {
                if (!isNaN(numVal) && numVal > currentCalendarYear + 1) {
                    continue; // Skip future years like 2036
                }
                el.selectedIndex = i;
                el.dispatchEvent(new Event('change', {bubbles: true}));
                el.dispatchEvent(new Event('input', {bubbles: true}));
                return true;
            }
        }

        // 3. Last resort
        el.selectedIndex = 1;
        el.dispatchEvent(new Event('change', {bubbles: true}));
        return true;
    }""", [field_id, preferred_values, start_year, grad_year])


def fill_form_step(page, profile: Profile, job_context: str):
    state = describe_modal(page)
    if state.get("error"):
        time.sleep(1.5)
        state = describe_modal(page)

    fields = state.get("fields", [])

    # 1. First pass: Handle Fieldset Radio Groups with Groq LLM Drafting
    for field in fields:
        if field.get("type") == "fieldset_radio":
            question = (field.get("question") or "").strip()
            radios = field.get("radios", [])
            q_clean = re.sub(r"\s*\*+\s*$", "", question).strip()
            q_lower = q_clean.lower()
            
            # Rule & Fact Checks
            ans = None
            if "authorized" in q_lower or "legally authorized" in q_lower or "right to work" in q_lower:
                ans = "yes"
            elif "sponsorship" in q_lower or "visa" in q_lower or "require sponsorship" in q_lower or "h1-b" in q_lower or "h-1b" in q_lower:
                ans = "no"
            elif "background check" in q_lower or "drug test" in q_lower or "commute" in q_lower or "comfortable" in q_lower:
                ans = "yes"
            elif "completed" in q_lower and ("bachelor" in q_lower or "degree" in q_lower or "graduation" in q_lower):
                ans = "yes"
            else:
                # Dynamic Groq LLM Drafting
                ans = get_screening_answer(q_clean, profile, job_context)
            
            print(f"  [Screening Radio] '{q_clean}' -> {ans}")

            # Match & Click Radio Option
            for r in radios:
                r_label = (r.get("label") or "").lower()
                r_val = (r.get("value") or "").lower()
                r_id = r.get("id")
                if not r_id:
                    continue

                should_click = False
                if ans and ans.lower().startswith("yes") and ("yes" in r_label or "yes" in r_val):
                    should_click = True
                elif ans and ans.lower().startswith("no") and ("no" in r_label or "no" in r_val):
                    should_click = True
                elif ans and ans.lower() in r_label:
                    should_click = True

                if should_click:
                    click_radio_by_id(page, r_id)
                    break
            else:
                # If no direct match, default to first option / Yes
                if radios and not any(r.get("checked") for r in radios):
                    click_radio_by_id(page, radios[0]["id"])

    # 2. Second pass: Handle individual form fields (Inputs, Selects, Checkboxes, File uploads)
    for field in fields:
        label = (field.get("label") or "").strip()
        field_type = (field.get("type") or "").lower()
        field_tag = (field.get("tag") or "").upper()
        field_id = field.get("id")
        current_val = field.get("value") or ""

        # File upload fields (Resume, Documents)
        if field_type == "file":
            resume_path = _resume_path(profile)
            if resume_path and field_id:
                try:
                    file_input = page.locator(f'[id="{field_id}"]')
                    if file_input.count() > 0:
                        file_input.set_input_files(str(resume_path))
                        time.sleep(random.uniform(0.5, 1.0))
                except Exception:
                    pass
            continue

        # Contact Info / Phone country code
        if field_tag == "SELECT" and ("country code" in label.lower() or "phone country" in label.lower()):
            time.sleep(random.uniform(0.2, 0.4))
            page.evaluate(f"""() => {{
                const el = document.getElementById('{field_id}');
                if (el) {{
                    for (let i = 0; i < el.options.length; i++) {{{{
                        if (el.options[i].text.includes('+91') || el.options[i].text.includes('India')) {{{{
                            el.selectedIndex = i;
                            el.dispatchEvent(new Event('change', {{bubbles: true}}));
                            break;
                        }}}}
                    }}}}
                }}
            }}""")
            continue

        # Phone Number
        if field_type in ("text", "tel", "number") and ("phone" in label.lower() or "mobile" in label.lower()):
            if not current_val:
                phone_val = str(profile.data.get("mobile_number") or profile.data.get("phone") or "7659869814")
                fill_text_field(page, field_id, phone_val)
            continue

        # Standalone Radio buttons
        if field_type == "radio":
            if not field.get("checked") and label and field_id:
                ans = get_screening_answer(label, profile, job_context)
                if ans and ans.lower().startswith("yes") and "yes" in label.lower():
                    click_radio_by_id(page, field_id)
                elif ans and ans.lower().startswith("no") and "no" in label.lower():
                    click_radio_by_id(page, field_id)
            continue

        # Checkboxes
        if field_type == "checkbox":
            if not field.get("checked") and field_id:
                lbl_lower = label.lower()
                should_check = field.get("required") or any(w in lbl_lower for w in ("terms", "agree", "confirm", "certify", "consent", "acknowledge"))
                if should_check:
                    click_checkbox_by_id(page, field_id, True)
            continue

        # Select Dropdowns with intelligent semantic matching
        if field_tag == "SELECT":
            if (not current_val or current_val.lower().startswith("select") or current_val.lower().startswith("choose")) and field_id:
                time.sleep(random.uniform(0.2, 0.4))
                select_dropdown_option(page, field_id, label, profile, job_context)
            continue

        # Text, Number, & Textarea Inputs
        if not label or current_val:
            continue

        lbl_lower = label.lower()

        # Hourly rate fields
        if any(w in lbl_lower for w in ("hourly", "per hour", "(in usd)", "in usd", "hourly rate", "rate (in usd)")):
            if any(w in lbl_lower for w in ("current", "present", "now", "currently")):
                fill_text_field(page, field_id, str(profile.data.get("current_hourly_rate_usd", 15)))
            else:
                fill_text_field(page, field_id, str(profile.data.get("expected_hourly_rate_usd", 25)))
            time.sleep(random.uniform(0.2, 0.4))
            continue

        # Immediate joiner '1' numeric prompt
        if any(p in lbl_lower for p in ("respond '1'", "respond 1", "enter '1'", "enter 1", "type '1'", "type 1", "reply '1'", "reply 1", "if you are an immediate joiner")):
            fill_text_field(page, field_id, "1")
            time.sleep(random.uniform(0.2, 0.4))
            continue

        # First Name / Given Name
        if any(w in lbl_lower for w in ("first name", "given name", "forename")):
            fill_text_field(page, field_id, str(profile.data.get("first_name", "Ganesh")))
            time.sleep(random.uniform(0.2, 0.4))
            continue


        # Last Name / Family Name / Surname
        if any(w in lbl_lower for w in ("last name", "family name", "surname")):
            fill_text_field(page, field_id, str(profile.data.get("last_name", "Pirikirala")))
            time.sleep(random.uniform(0.2, 0.4))
            continue

        # Email Address
        if any(w in lbl_lower for w in ("email address", "email id", "e-mail", "email")):
            email_val = str(profile.data.get("email", profile.data.get("email_address", "ganesh.pkl08@gmail.com")))
            fill_text_field(page, field_id, email_val)
            time.sleep(random.uniform(0.2, 0.4))
            continue

        # Location / City autocomplete fields (MUST select Hyderabad, Telangana, India)
        if any(w in lbl_lower for w in ("city", "town", "location", "current location", "where are you located", "base location")):
            fill_typeahead_field(page, field_id, "Hyderabad", preferred_match="India")
            time.sleep(random.uniform(0.2, 0.4))
            continue

        # School / University / College fields (Clean institution name only)
        if any(w in lbl_lower for w in ("school", "university", "college", "institution", "institute", "alma mater")):
            school_val = str(profile.data.get("school_name", "Chaitanya Bharathi Institute of Technology"))
            fill_typeahead_field(page, field_id, school_val, preferred_match="Chaitanya")
            time.sleep(random.uniform(0.2, 0.4))
            continue

        # Major / Field of Study / Specialization / Branch / Stream
        if any(w in lbl_lower for w in ("major", "field of study", "specialization", "branch", "stream", "discipline", "department", "course")):
            fill_text_field(page, field_id, str(profile.data.get("field_of_study", "Electronics and Communication Engineering")))
            time.sleep(random.uniform(0.2, 0.4))
            continue

        # Degree (Clean degree name only)
        if lbl_lower == "degree" or ("degree" in lbl_lower and not any(w in lbl_lower for w in ("school", "university", "college", "major", "field", "grade", "gpa"))):
            fill_text_field(page, field_id, str(profile.data.get("degree_name", "Bachelor of Technology")))
            time.sleep(random.uniform(0.2, 0.4))
            continue

        # Graduation / Passing Year vs Start Year vs Experience Years
        if any(w in lbl_lower for w in ("graduat", "passout", "pass out", "passing year", "year of passing", "completion year", "year of completion", "end year", "batch", "year of pass")):
            fill_text_field(page, field_id, str(profile.data.get("graduation_year", "2023")))
            time.sleep(random.uniform(0.2, 0.4))
            continue
        elif any(w in lbl_lower for w in ("start year", "starting year", "joining year", "commencement year")):
            fill_text_field(page, field_id, str(profile.data.get("education_start_year", "2019")))
            time.sleep(random.uniform(0.2, 0.4))
            continue

        # Postal / Zip Code
        if any(w in lbl_lower for w in ("zip", "postal", "pin code", "pincode")):
            fill_text_field(page, field_id, str(profile.data.get("postal_code", "500072")))
            time.sleep(random.uniform(0.2, 0.4))
            continue

        # State / Province
        if any(w in lbl_lower for w in ("state", "province", "region")):
            fill_text_field(page, field_id, str(profile.data.get("state_province", "Telangana")))
            time.sleep(random.uniform(0.2, 0.4))
            continue

        # Country
        if "country" in lbl_lower:
            fill_text_field(page, field_id, str(profile.data.get("country_name", "India")))
            time.sleep(random.uniform(0.2, 0.4))
            continue

        # Current Job Title
        if any(w in lbl_lower for w in ("current job title", "current title", "designation", "present title", "present designation", "official title")):
            fill_text_field(page, field_id, str(profile.data.get("current_title_official", "Full-Stack Software Developer")))
            time.sleep(random.uniform(0.2, 0.4))
            continue

        # Current Employer / Company
        if any(w in lbl_lower for w in ("current employer", "current company", "present employer", "present company")):
            fill_text_field(page, field_id, str(profile.data.get("current_employer", "Cognitivo")))
            time.sleep(random.uniform(0.2, 0.4))
            continue

        # Skill Set / Skills textarea
        if any(w in lbl_lower for w in ("skill set", "technical skills", "primary skills", "key skills", "skills")):
            skills_list = profile.data.get("skills_primary", []) + profile.data.get("skills_adjacent", [])
            skills_text = ", ".join(skills_list[:8]) if skills_list else "Java, Spring Boot, React.js, Node.js, TypeScript, JavaScript, REST APIs, MySQL"
            fill_text_field(page, field_id, skills_text)
            time.sleep(random.uniform(0.2, 0.4))
            continue

        # General text/number field: ask get_screening_answer
        ans = get_screening_answer(label, profile, job_context)

        is_numeric_expectation = (
            field_type == "number"
            or "years" in lbl_lower
            or "experience" in lbl_lower
            or "how many" in lbl_lower
            or lbl_lower.strip().endswith("?")
            or any(w in lbl_lower for w in ("rate", "salary", "ctc", "lpa", "usd", "hourly", "notice", "joining", "days"))
            or any(w in lbl_lower for w in ("engineering", "developer", "lead", "project", "python", "react", "node", "java", "sql", "aws", "azure", "docker", "kubernetes", "backend", "frontend", "full stack", "fullstack", "data", "system design", "microservices"))
        )

        if is_numeric_expectation:
            # If the answer is boolean, empty, or long text narrative, force numeric resolution
            if not ans or ans.lower().startswith("yes") or len(str(ans).split()) > 4:
                if any(w in lbl_lower for w in ("hourly", "usd", "rate")):
                    ans = str(profile.data.get("current_hourly_rate_usd", 15)) if any(w in lbl_lower for w in ("current", "present", "now")) else str(profile.data.get("expected_hourly_rate_usd", 25))
                elif any(w in lbl_lower for w in ("notice", "joining", "how soon")):
                    ans = "0" if any(w in lbl_lower for w in ("days", "in days")) else "1"
                elif any(w in lbl_lower for w in ("ctc", "salary", "compensation")):
                    ans = str(profile.data.get("current_ctc_lpa", 6)) if any(w in lbl_lower for w in ("current", "present")) else str(profile.data.get("expected_ctc_lpa", 9))
                elif any(w in lbl_lower for w in ("zip", "postal", "pin")):
                    ans = str(profile.data.get("postal_code", "500072"))
                elif any(w in lbl_lower for w in ("graduat", "passout", "year")):
                    ans = str(profile.data.get("graduation_year", "2023"))
                else:
                    ans = str(profile.data.get("years_experience", "3"))
        elif not ans:
            if "city" in lbl_lower or "location" in lbl_lower:
                ans = profile.current_city or "Hyderabad"

        if ans and field_id:
            fill_text_field(page, field_id, str(ans))
            time.sleep(random.uniform(0.2, 0.4))

    # 3. Third pass: Ensure resume radio is selected if present
    try:
        page.evaluate("""() => {
            const resumeRadios = Array.from(document.querySelectorAll('input[type="radio"][name*="resume"], input[type="radio"][id*="resume"], div[class*="document-upload"] input[type="radio"]'));
            if (resumeRadios.length > 0 && !resumeRadios.some(r => r.checked)) {
                const r0 = resumeRadios[0];
                r0.click();
                r0.checked = true;
                r0.dispatchEvent(new Event('change', {bubbles: true}));
            }
        }""")
    except Exception:
        pass


def click_modal_button(page, button_type: str) -> bool:
    """
    Clicks modal action buttons ('Next', 'Review', 'Submit application', 'Done', etc.)
    with multi-tier selector matching, content scrolling, and humanized pacing.
    """
    time.sleep(random.uniform(0.5, 0.9))
    btn_type = button_type.lower()
    
    # Scroll modal scrollable content down to ensure buttons are activated/visible
    try:
        page.evaluate("""() => {
            const modal = document.querySelector('.jobs-easy-apply-modal') ||
                          document.querySelector('dialog[open]') ||
                          document.querySelector('.artdeco-modal[role="dialog"]:not([aria-hidden="true"])') ||
                          document.querySelector('dialog, .jobs-easy-apply-modal, [role="dialog"], .artdeco-modal');
            if (modal) {
                const content = modal.querySelector('.jobs-easy-apply-modal__content, .artdeco-modal__content, div[class*="content"]');
                if (content) {
                    content.scrollTop = content.scrollHeight;
                }
            }
        }""")
    except Exception:
        pass
    
    selectors = []
    if "submit" in btn_type:
        selectors = [
            'button[aria-label*="Submit" i]',
            'button[data-live-test-easy-apply-submit-button]',
            'footer button:has-text("Submit application")',
            'footer button:has-text("Submit")',
            'footer button.artdeco-button--primary:has-text("Submit")',
            'button:has-text("Submit application")',
            'button:has-text("Submit")',
        ]
    elif "review" in btn_type:
        selectors = [
            'button[aria-label*="Review" i]',
            'footer button:has-text("Review")',
            'footer button.artdeco-button--primary:has-text("Review")',
            'button:has-text("Review your application")',
            'button:has-text("Review")',
        ]
    elif "next" in btn_type or "continue" in btn_type:
        selectors = [
            'button[aria-label*="Continue to next step" i]',
            'button[aria-label*="Next" i]',
            'button[data-easy-apply-next-button]',
            'footer button:has-text("Next")',
            'footer button:has-text("Continue")',
            'footer button.artdeco-button--primary',
            'button:has-text("Next")',
        ]
    else:
        selectors = [
            f'button:has-text("{button_type}")',
            f'button[aria-label*="{button_type}" i]',
        ]

    for sel in selectors:
        for prefix in ('.jobs-easy-apply-modal ', 'dialog[open] ', '.artdeco-modal ', '[role="dialog"] ', ''):
            try:
                loc = page.locator(f'{prefix}{sel}').first
                if loc.count() > 0 and loc.is_visible(timeout=500):
                    loc.scroll_into_view_if_needed()
                    time.sleep(random.uniform(0.2, 0.4))
                    loc.click(timeout=2000)
                    return True
            except Exception:
                pass

    # DOM Javascript Fallback
    return page.evaluate(
        """(target) => {
            const modal = document.querySelector('.jobs-easy-apply-modal') ||
                          document.querySelector('dialog[open]') ||
                          document.querySelector('.artdeco-modal[role="dialog"]:not([aria-hidden="true"])') ||
                          document.querySelector('dialog, .jobs-easy-apply-modal, [role="dialog"], .artdeco-modal');
            if (!modal) return false;
            const content = modal.querySelector('.jobs-easy-apply-modal__content, .artdeco-modal__content, div[class*="content"]');
            if (content) {
                content.scrollTop = content.scrollHeight;
            }
            const tLower = target.toLowerCase();
            const btns = Array.from(modal.querySelectorAll('button'));

            // 1. Submit
            if (tLower.includes('submit')) {
                const b = btns.find(btn => {
                    const txt = (btn.innerText || '').toLowerCase();
                    const aria = (btn.getAttribute('aria-label') || '').toLowerCase();
                    return txt.includes('submit') || aria.includes('submit') || btn.hasAttribute('data-live-test-easy-apply-submit-button');
                });
                if (b) {
                    b.scrollIntoView();
                    b.focus();
                    b.click();
                    return true;
                }
            }

            // 2. Review
            if (tLower.includes('review')) {
                const b = btns.find(btn => {
                    const txt = (btn.innerText || '').toLowerCase();
                    const aria = (btn.getAttribute('aria-label') || '').toLowerCase();
                    return txt.includes('review') || aria.includes('review');
                });
                if (b) {
                    b.scrollIntoView();
                    b.focus();
                    b.click();
                    return true;
                }
            }

            // 3. Next / Continue
            if (tLower.includes('next') || tLower.includes('continue')) {
                const b = btns.find(btn => {
                    const txt = (btn.innerText || '').toLowerCase();
                    const aria = (btn.getAttribute('aria-label') || '').toLowerCase();
                    return txt.includes('next') || txt.includes('continue') || aria.includes('next') || aria.includes('continue') || btn.hasAttribute('data-easy-apply-next-button');
                });
                if (b) {
                    b.scrollIntoView();
                    b.focus();
                    b.click();
                    return true;
                }
                const footerPrimary = modal.querySelector('footer button.artdeco-button--primary');
                if (footerPrimary) {
                    footerPrimary.scrollIntoView();
                    footerPrimary.focus();
                    footerPrimary.click();
                    return true;
                }
            }

            // 4. General match
            const general = btns.find(btn => {
                const txt = (btn.innerText || '').toLowerCase();
                const aria = (btn.getAttribute('aria-label') || '').toLowerCase();
                return txt.includes(tLower) || aria.includes(tLower);
            });
            if (general) {
                general.scrollIntoView();
                general.focus();
                general.click();
                return true;
            }
            return false;
        }""",
        btn_type,
    )


def dismiss_modal_if_open(page):
    try:
        # If modal is in a review or submittable state, attempt submit first
        is_submittable = page.evaluate("""() => {
            const modal = document.querySelector('.jobs-easy-apply-modal') ||
                          document.querySelector('dialog[open]') ||
                          document.querySelector('.artdeco-modal[role="dialog"]:not([aria-hidden="true"])') ||
                          document.querySelector('dialog, .jobs-easy-apply-modal, [role="dialog"], .artdeco-modal');
            if (!modal) return false;
            const submitBtn = Array.from(modal.querySelectorAll('button')).find(b => {
                const txt = (b.innerText || '').toLowerCase();
                return txt.includes('submit');
            });
            if (submitBtn) {
                submitBtn.scrollIntoView();
                submitBtn.focus();
                submitBtn.click();
                return true;
            }
            return false;
        }""")
        if is_submittable:
            time.sleep(2)

        page.evaluate("""() => {
            const modal = document.querySelector('.jobs-easy-apply-modal') ||
                          document.querySelector('dialog[open]') ||
                          document.querySelector('.artdeco-modal[role="dialog"]:not([aria-hidden="true"])') ||
                          document.querySelector('dialog, .jobs-easy-apply-modal, [role="dialog"], .artdeco-modal');
            if (!modal) return;
            const closeBtn = modal.querySelector('button[aria-label="Dismiss"], button[aria-label="Close"], button.artdeco-modal__dismiss');
            if (closeBtn) closeBtn.click();
        }""")
        time.sleep(1)
        page.evaluate("""() => {
            const discardBtn = Array.from(document.querySelectorAll('button')).find(b => b.innerText.trim() === 'Discard' || (b.getAttribute('aria-label') || '').includes('Discard'));
            if (discardBtn) discardBtn.click();
        }""")
    except Exception:
        pass


def _is_application_confirmation(text: str) -> bool:
    normalized = " ".join((text or "").lower().split())
    patterns = (
        r"\byour application was sent\b",
        r"\bapplication (?:has been )?submitted\b",
        r"\bsuccessfully applied\b",
    )
    return any(re.search(pattern, normalized) for pattern in patterns)


class SubmissionUnconfirmed(Exception):
    """The submit click happened, but the site did not confirm success."""


def run_one_application(page, profile: Profile, job_title: str, company: str) -> bool:
    job_context = f"{job_title} at {company}"

    # Strict pre-apply check on job description for experience requirements
    max_exp_ceiling = profile.seniority_ceiling_years if profile.seniority_ceiling_years is not None else 3
    detail_text = page.evaluate("""() => {
        const desc = document.querySelector('.jobs-description, .jobs-description__container, .jobs-box__html-content, .job-details-jobs-unified-top-card__job-insight, div[class*="job-details"]');
        return desc ? desc.innerText : '';
    }""")
    if detail_text:
        req_exp = extract_experience_requirement(detail_text[:2000])
        if req_exp is not None and req_exp > max_exp_ceiling:
            print(f"  ❌ Skipped: {job_title} @ {company} (Job requires {req_exp}+ yrs exp — strictly exceeds {max_exp_ceiling} yrs ceiling)")
            return False

    time.sleep(random.uniform(1.2, 2.2))
    is_easy_apply_clicked = page.evaluate("""() => {
        const btn = document.querySelector('.jobs-apply-button') ||
                    Array.from(document.querySelectorAll('button')).find(b => {
                        const t = (b.innerText || '').trim();
                        return t === 'Easy Apply' || t.startsWith('Easy Apply') || (b.getAttribute('aria-label') || '').includes('Easy Apply');
                    });
        if (btn) {
            btn.scrollIntoView();
            btn.click();
            return true;
        }
        return false;
    }""")
    if not is_easy_apply_clicked:
        return False
    time.sleep(random.uniform(2.0, 3.2))

    try:
        for step_idx in range(10):  # Cap on steps per application
            stop = page_has_stop_signal(page)
            if stop:
                raise RuntimeError(f"stop signal mid-application: {stop}")

            state = describe_modal(page)
            if state.get("error"):
                time.sleep(1.5)
                state = describe_modal(page)
                if state.get("error"):
                    break

            buttons = state.get("buttons", [])
            btn_texts = [(b.get("text") or "").lower() for b in buttons]
            btn_arias = [(b.get("aria") or "").lower() for b in buttons]
            all_btn_strs = btn_texts + btn_arias

            # 1. Fill current step form questions (radios, inputs, selects)
            fill_form_step(page, profile, job_context)
            time.sleep(random.uniform(1.2, 2.4))  # Human reading delay

            # 2. Check for Submit action
            if any("submit" in s for s in all_btn_strs):
                review_text = page.inner_text('dialog, .jobs-easy-apply-modal, [role="dialog"], .artdeco-modal')
                print("--- Reviewing application before submit ---")
                print(review_text[:500])
                time.sleep(random.uniform(1.5, 3.0))

                if not click_modal_button(page, "Submit application"):
                    raise SubmissionUnconfirmed("submit button click did not register")
                
                time.sleep(random.uniform(3.5, 5.5))
                confirmation_text = page.inner_text("body")
                if not _is_application_confirmation(confirmation_text):
                    raise SubmissionUnconfirmed(
                        f"LinkedIn did not show an application confirmation. Saw text starting with: {confirmation_text[:200]}"
                    )
                
                # Close post-application success modal
                time.sleep(1.0)
                click_modal_button(page, "Done")
                click_modal_button(page, "Not now")
                return True

            # 3. Check for Review action
            elif any("review" in s for s in all_btn_strs):
                click_modal_button(page, "Review")
                time.sleep(random.uniform(1.8, 3.0))

            # 4. Check for Next action
            elif any("next" in s or "continue" in s for s in all_btn_strs) or any(b.get("isPrimary") for b in buttons):
                click_modal_button(page, "Next")
                time.sleep(random.uniform(1.8, 3.0))
            else:
                # Primary button fallback in footer
                if not click_modal_button(page, "Next"):
                    raise RuntimeError(f"unrecognized modal state, buttons={all_btn_strs}")
                time.sleep(random.uniform(1.8, 3.0))

            # Re-check for validation errors and heal if necessary
            has_error = page.evaluate("""() => {
                const setReactValue = (el, val) => {
                    el.focus();
                    const proto = el.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
                    const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
                    if (setter) {
                        setter.call(el, val);
                    } else {
                        el.value = val;
                    }
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    el.dispatchEvent(new Event('blur', { bubbles: true }));
                };

                const errElements = Array.from(document.querySelectorAll('.artdeco-inline-feedback--error, [data-test-form-element-error-messages], [class*="form-element--error"]'));
                if (errElements.length === 0) return false;

                errElements.forEach(err => {
                    const parent = err.closest('.fb-dash-form-element, [class*="form-element"], fieldset, div') || err.parentElement;
                    if (!parent) return;

                    const errText = (err.innerText || '').toLowerCase();
                    const labelText = (parent.innerText || '').toLowerCase();

                    // 1. Fix Decimal / Numeric errors on text inputs
                    const input = parent.querySelector('input:not([type="radio"]):not([type="checkbox"]):not([type="hidden"])');
                    if (input && (errText.includes('decimal') || errText.includes('number') || errText.includes('larger than 0') || errText.includes('numeric') || input.type === 'number')) {
                        let corrected = '3';
                        if (labelText.includes('hourly') || labelText.includes('usd') || labelText.includes('rate')) {
                            corrected = (labelText.includes('current') || labelText.includes('present')) ? '15' : '25';
                        } else if (labelText.includes('notice') || labelText.includes('joining') || labelText.includes('how soon')) {
                            corrected = '0';
                        } else if (labelText.includes('ctc') || labelText.includes('salary') || labelText.includes('compensation')) {
                            corrected = (labelText.includes('current') || labelText.includes('present')) ? '6' : '9';
                        } else if (labelText.includes('zip') || labelText.includes('postal') || labelText.includes('pin')) {
                            corrected = '500072';
                        } else if (labelText.includes('graduat') || labelText.includes('passout') || labelText.includes('end year')) {
                            corrected = '2023';
                        } else {
                            // Technical domain, engineering skill, team lead, project lead, or general experience
                            corrected = '3';
                        }
                        setReactValue(input, corrected);
                    }

                    // 2. Fix unselected dropdowns
                    const select = parent.querySelector('select');
                    if (select && (select.selectedIndex <= 0 || (select.value || '').toLowerCase().startsWith('select') || (select.value || '').toLowerCase().startsWith('choose'))) {
                        for (let i = 1; i < select.options.length; i++) {
                            const optTxt = (select.options[i].text || '').toLowerCase();
                            if (optTxt.includes('yes') || optTxt.includes('1') || !optTxt.includes('select')) {
                                select.selectedIndex = i;
                                select.dispatchEvent(new Event('change', {bubbles: true}));
                                select.dispatchEvent(new Event('input', {bubbles: true}));
                                break;
                            }
                        }
                    }
                });
                return true;
            }""")
            if has_error:
                print("  (Validation error detected, auto-healed invalid inputs...)")
                time.sleep(1.0)
                click_modal_button(page, "Review")
                click_modal_button(page, "Next")


        raise RuntimeError("exceeded step cap without reaching submit")
    except SubmissionUnconfirmed:
        raise
    except Exception as e:
        print(f"  (Application modal error: {e})")
        dismiss_modal_if_open(page)
        return False


LINKEDIN_GEO_IDS = {
    "worldwide": "92000000",
    "remote": "92000000",
    "united states": "103644278",
    "usa": "103644278",
    "us": "103644278",
    "united kingdom": "101165590",
    "uk": "101165590",
    "canada": "101174742",
    "australia": "101452733",
    "new zealand": "105490917",
    "ireland": "104738515",
    "india": "102713980",
    "bengaluru": "105214831",
    "hyderabad": "105556991",
}


def build_linkedin_search_url(
    role: str,
    location: str,
    freshness_seconds: int,
    start_offset: int = 0,
    remote_only: bool = False,
    work_mode: str = "remote_first",
    under_10_applicants: bool = True,
) -> str:
    import urllib.parse
    params = [
        ("keywords", role),
        ("f_AL", "true"),  # Easy Apply filter
        ("sortBy", "DD"),  # Most recent first
    ]
    if location:
        params.append(("location", location))
        loc_clean = location.strip().lower()
        if loc_clean in LINKEDIN_GEO_IDS:
            params.append(("geoId", LINKEDIN_GEO_IDS[loc_clean]))
    elif remote_only or work_mode in ("remote_only", "remote_first"):
        params.append(("geoId", "92000000"))

    if freshness_seconds > 0:
        params.append(("f_TPR", f"r{freshness_seconds}"))
    if remote_only or work_mode == "remote_only":
        params.append(("f_WT", "2"))  # 100% Remote only
    elif work_mode == "remote_first":
        params.append(("f_WT", "2,3"))  # Remote (priority) + Hybrid
    if under_10_applicants:
        params.append(("f_EA", "true"))  # Under 10 applicants ("Early Applicant" filter)
    if start_offset > 0:
        params.append(("start", str(start_offset)))
    return "https://www.linkedin.com/jobs/search/?" + urllib.parse.urlencode(params)


def run(limit: int | None = None):
    profile = Profile.load()
    if not Path(SESSION_FILE).exists():
        raise SystemExit(f"{SESSION_FILE} not found. Run: python login_capture.py linkedin")

    applied_today = count_applications_today()
    platform_threshold = int(profile.data.get("linkedin_limit", profile.data.get("linkedin_run_limit", profile.data.get("linkedin_daily_limit", 15))))
    target_limit = limit if limit is not None else platform_threshold
    run_success_limit = min(target_limit, int(profile.stop_after_n_applications or 100))
    print(f"LinkedIn session starting (Platform threshold: {platform_threshold} | Applied today: {applied_today} | Target this run: {run_success_limit})")

    is_remote_mode = (profile.work_mode in ("remote_only", "remote_first"))
    under_10_applicants = bool(profile.data.get("under_10_applicants_only", True))
    max_applicants = profile.data.get("max_applicants", 10 if under_10_applicants else None)

    if is_remote_mode:
        configured_remote = profile.data.get("remote_locations")
        if configured_remote and isinstance(configured_remote, list) and len(configured_remote) > 0:
            locations = list(configured_remote)
        else:
            locations = [
                "Worldwide",
                "Remote",
                "United States",
                "European Union",
                "United Kingdom",
                "Germany",
                "Netherlands",
                "Canada",
                "Australia",
                "Ireland",
                "Switzerland",
                "Sweden",
                "Singapore",
                "United Arab Emirates",
                "New Zealand",
                "India",
            ]
        if profile.work_mode == "remote_first":
            for c in [profile.current_city, *profile.relocate_cities]:
                if c and c not in locations:
                    locations.append(c)
    else:
        locations = list(dict.fromkeys([profile.current_city, *profile.relocate_cities, "Remote"]))

    freshness_seconds = int(profile.job_freshness_days) * 86400 if hasattr(profile, "job_freshness_days") else 604800
    max_search_pages = int(profile.data.get("max_linkedin_pages", 3))

    search_targets = [
        {"role": "Personalized Preferences Feed", "loc": "Worldwide", "url": "https://www.linkedin.com/jobs/search-results/?f_AL=true&f_EA=true&f_TPR=r86400&f_WT=2%2C3", "is_pref": True}
    ]
    for role in profile.target_roles:
        for loc in locations:
            if not loc:
                continue
            for page_idx in range(max_search_pages):
                start_offset = page_idx * 25
                url = build_linkedin_search_url(
                    role=role,
                    location=loc,
                    freshness_seconds=freshness_seconds,
                    start_offset=start_offset,
                    work_mode=profile.work_mode,
                    under_10_applicants=under_10_applicants,
                )
                search_targets.append({"role": role, "loc": loc, "url": url, "is_pref": False, "page": page_idx + 1})

    applied = 0
    with sync_playwright() as p:
        # Anti-detection stealth launch
        browser = p.chromium.launch(
            headless=profile.browser_mode == "headless",
            slow_mo=300,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ]
        )
        context = browser.new_context(
            storage_state=SESSION_FILE,
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            viewport={"width": 1440, "height": 900},
            locale="en-US",
            timezone_id="Asia/Kolkata",
        )
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            window.chrome = {
                runtime: {},
            };
        """)
        page = context.new_page()

        for target in search_targets:
            if applied >= run_success_limit:
                break

            role = target["role"]
            loc = target["loc"]
            search_url = target["url"]
            is_pref = target.get("is_pref", False)
            page_label = f" [Page {target.get('page')}]" if "page" in target else ""

            print(f"\n--- Searching LinkedIn: {role} ({loc}){page_label} ---")
            try:
                page.goto(search_url, timeout=25000)
                page.wait_for_selector('.job-card-container, [data-job-id], .jobs-search-results-list, .scaffold-layout__list, button[aria-label^="Dismiss "]', timeout=12000)
            except PWTimeout:
                pass
            except Exception as e:
                print(f"  (navigation timeout or error: {e})")
            time.sleep(random.uniform(3.0, 5.0))

            stop = page_has_stop_signal(page)
            if stop:
                print(f"STOPPING: {stop}")
                log_row([datetime.now(), "linkedin", "-", "-", "stopped", stop])
                browser.close()
                return

            # Scroll the list container smoothly so lazy-loaded cards render
            try:
                for _ in range(3):
                    page.evaluate("""() => {
                        const list = document.querySelector('.jobs-search-results-list, .scaffold-layout__list, div.scaffold-layout__list-detail-inner, main');
                        if (list) list.scrollBy(0, 800);
                        else window.scrollBy(0, 800);
                    }""")
                    time.sleep(random.uniform(0.8, 1.4))
            except Exception:
                pass

            try:
                cards = page.evaluate("""
                    () => {
                        const cards = [];
                        const seen = new Set();

                    // 1. Classic layout items
                    const items = Array.from(document.querySelectorAll('li[data-occludable-job-id], .job-card-container, .jobs-search-results__list-item, .scaffold-layout__list-item, .jobs-search-results-list li, [data-job-id]'));
                    if (items.length > 0) {
                        items.forEach((c, idx) => {
                            const titleEl = c.querySelector('a.job-card-list__title--link, [class*="job-card-list__title"], a.job-card-container__link, .job-card-list__title, a[data-control-id], strong, a[href*="/jobs/view/"]');
                            let title = titleEl ? titleEl.innerText.trim() : "";
                            if (!title && titleEl && titleEl.getAttribute("aria-label")) {
                                title = titleEl.getAttribute("aria-label").trim();
                            }
                            const href = titleEl ? titleEl.href : "";
                            const compEl = c.querySelector('.artdeco-entity-lockup__subtitle, [class*="company-name"], [class*="primary-description"], .job-card-container__primary-description');
                            const timeEl = c.querySelector('time, [class*="listed-time"], [class*="footer-item"]');
                            const locEl = c.querySelector('.job-card-container__metadata-item, [class*="job-card-container__metadata-wrapper"] li, .artdeco-entity-lockup__caption, [class*="job-search-card__location"], [class*="location"]');
                            const appEl = c.querySelector('.job-card-container__applicant-count, [class*="applicant-count"], .job-card-list__footer-wrapper, [class*="tvm__text"]');
                            const key = (title + " " + (compEl ? compEl.innerText.trim() : "")).toLowerCase();
                            if (title && !seen.has(key)) {
                                seen.add(key);
                                cards.push({
                                    idx: idx,
                                    title: title,
                                    href: href,
                                    company: compEl ? compEl.innerText.trim() : "",
                                    posted: timeEl ? timeEl.innerText.trim() : "",
                                    location: locEl ? locEl.innerText.trim() : "",
                                    applicants: appEl ? appEl.innerText.trim() : "",
                                    isAiLayout: false,
                                });
                            }
                        });
                    }

                    // 2. AI-powered search results layout (uses dismiss buttons & card containers)
                    if (cards.length === 0) {
                        const dismissBtns = Array.from(document.querySelectorAll('button[aria-label^="Dismiss "]'));
                        dismissBtns.forEach((btn, idx) => {
                            const container = btn.closest('div.auymuo, div[class*="auymuo"], li') || btn.parentElement?.parentElement?.parentElement;
                            const textLines = (container ? container.innerText : "").split('\\n').map(s => s.trim()).filter(Boolean);
                            const titleMatch = (btn.getAttribute('aria-label') || '').match(/^Dismiss (.*?) job$/);
                            const title = titleMatch ? titleMatch[1] : (textLines[0] || "");
                            
                            let company = "";
                            let location = "";
                            let posted = "";
                            let applicants = "";
                            
                            for (let i = 1; i < textLines.length; i++) {
                                const line = textLines[i];
                                if (!company && !line.includes('ago') && !line.includes('applicant') && !line.includes('alumni') && !line.includes('Remote') && !line.includes('Hybrid') && !line.includes('On-site')) {
                                    company = line;
                                } else if (!location && (line.includes('Remote') || line.includes('Hybrid') || line.includes('On-site') || line.includes(',') || line.includes('United') || line.includes('India') || line.includes('Zealand') || line.includes('Kingdom'))) {
                                    location = line;
                                } else if (line.includes('ago') || line.includes('hour') || line.includes('day') || line.includes('week') || line.includes('month')) {
                                    posted = line;
                                } else if (line.includes('applicant') || line.includes('early')) {
                                    applicants = line;
                                }
                            }
                            
                            const key = (title + " " + company).toLowerCase();
                            if (title && !seen.has(key)) {
                                seen.add(key);
                                cards.push({
                                    idx: idx,
                                    title: title,
                                    href: "",
                                    company: company,
                                    posted: posted,
                                    location: location,
                                    applicants: applicants,
                                    isAiLayout: true,
                                });
                            }
                        });
                    }

                    // 3. Fallback anchors scan
                    if (cards.length === 0) {
                        const container = document.querySelector('.jobs-search-results-list, .scaffold-layout__list, div.jobs-search__results-list, main');
                        const anchors = container ? Array.from(container.querySelectorAll('a[href*="/jobs/view/"]')) : [];
                        anchors.forEach((a, idx) => {
                            const title = a.innerText.trim();
                            const href = a.href;
                            const key = (title + " " + href).toLowerCase();
                            if (!title || seen.has(key)) return;
                            seen.add(key);
                            const parent = a.closest('li, [class*="card"], div.flex-grow-1, [data-job-id]') || a.parentElement;
                            const compEl = parent ? parent.querySelector('[class*="company"], [class*="subtitle"], [class*="primary-description"]') : null;
                            const timeEl = parent ? parent.querySelector('time, [class*="time"], [class*="footer"]') : null;
                            const locEl = parent ? parent.querySelector('.job-card-container__metadata-item, [class*="job-card-container__metadata-wrapper"] li, .artdeco-entity-lockup__caption, [class*="location"]') : null;
                            const appEl = parent ? parent.querySelector('.job-card-container__applicant-count, [class*="applicant-count"], [class*="footer-item"]') : null;
                            cards.push({
                                idx: idx,
                                title: title,
                                href: href,
                                company: compEl ? compEl.innerText.trim() : "",
                                posted: timeEl ? timeEl.innerText.trim() : "",
                                location: locEl ? locEl.innerText.trim() : "",
                                applicants: appEl ? appEl.innerText.trim() : "",
                                isAiLayout: false,
                            });
                        });
                    }
                        return cards;
                    }
                """)
            except PWError as e:
                if "closed" in str(e).lower() or "target" in str(e).lower():
                    print(f"\n[!] LinkedIn browser window or tab was closed ({e}). Stopping run cleanly.")
                    return
                print(f"  (Card extraction error: {e})")
                cards = []

            print(f"Found {len(cards)} cards on {role}.")
            if len(cards) == 0:
                continue

            applied_keys = load_applied_job_keys()
            for card in cards:
                if applied >= run_success_limit:
                    break
                idx = card["idx"]
                title = card.get("title", "")
                company = card.get("company", "")
                job_loc = card.get("location", "")

                stats_tracker.record_discovered()
                if _job_key(title, company) in applied_keys:
                    stats_tracker.record_previously_applied_skipped()
                    print(f"Skipped: {title} @ {company} — already applied in an earlier run")
                    time.sleep(random.uniform(0.5, 1.2))
                    continue

                company_lower = (company or "").lower()
                if any(excl.lower() in company_lower for excl in profile.company_exclude):
                    print(f"Skipped: {title} @ {company} (Company excluded)")
                    continue
                if profile.company_include_only:
                    if not any(inc.lower() in company_lower for inc in profile.company_include_only):
                        print(f"Skipped: {title} @ {company} (Not in include-only list)")
                        continue
                
                # Enforce strict title matching based on profile.yaml unless this is personalized preferences feed
                if not is_pref:
                    required_keywords = profile.data.get("role_required_keywords", {}).get(role)
                    if not required_keywords:
                        generic = {"engineer", "developer", "administrator", "analyst", "senior", "junior", "lead"}
                        required_keywords = [w for w in role.lower().split() if w not in generic]
                    if required_keywords and not any(k.lower() in title.lower() for k in required_keywords):
                        print(f"Skipped: {title} @ {card.get('company')} (Title doesn't match {role} keywords)")
                        continue

                # Strict experience ceiling check (max <= 3 years)
                max_exp_ceiling = profile.seniority_ceiling_years if profile.seniority_ceiling_years is not None else 3
                title_lower = title.lower()
                if max_exp_ceiling <= 3:
                    matched_seniority = [kw for kw in EXCLUDED_SENIORITY_KEYWORDS if re.search(rf"\b{re.escape(kw)}\b", title_lower)]
                    if matched_seniority:
                        print(f"Skipped: {title} @ {company} (Seniority '{matched_seniority[0]}' strictly exceeds {max_exp_ceiling} yrs ceiling)")
                        continue

                exp_req_title = extract_experience_requirement(title)
                if exp_req_title is not None and exp_req_title > max_exp_ceiling:
                    print(f"Skipped: {title} @ {company} (Title specifies {exp_req_title}+ yrs exp — strictly exceeds {max_exp_ceiling} yrs ceiling)")
                    continue

                # Check remote / hybrid compatibility
                is_remote_mode = (profile.work_mode in ("remote_only", "remote_first"))
                if is_remote_mode and job_loc:
                    loc_lower = job_loc.lower()
                    if profile.work_mode == "remote_only":
                        if ("on-site" in loc_lower or "onsite" in loc_lower) and "remote" not in loc_lower:
                            print(f"Skipped: {title} @ {card.get('company')} (On-site location: {job_loc})")
                            continue
                    elif profile.work_mode == "remote_first":
                        if ("on-site" in loc_lower or "onsite" in loc_lower) and "remote" not in loc_lower and "hybrid" not in loc_lower:
                            print(f"Skipped: {title} @ {card.get('company')} (On-site location: {job_loc})")
                            continue

                # Check applicant count limit
                if max_applicants is not None:
                    app_text = (card.get("applicants") or "").lower()
                    if app_text:
                        nums = [int(s) for s in re.findall(r"\d+", app_text)]
                        if nums and nums[0] > max_applicants:
                            print(f"Skipped: {title} @ {card.get('company')} (Too many applicants: {nums[0]} > {max_applicants})")
                            continue

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
                        print(f"Skipped: {title} @ {card.get('company')} (Job is too old: {card.get('posted')})")
                        continue

                try:
                    # Natural human delay before opening job card
                    time.sleep(random.uniform(2.2, 4.0))

                    card_clicked = False
                    if card.get("isAiLayout"):
                        dismiss_btns = page.locator('button[aria-label^="Dismiss "]')
                        if dismiss_btns.count() > idx:
                            try:
                                btn = dismiss_btns.nth(idx)
                                card_container = btn.locator('xpath=ancestor::div[contains(@class, "auymuo")][1]')
                                if card_container.count() > 0:
                                    card_container.scroll_into_view_if_needed()
                                    time.sleep(random.uniform(0.3, 0.6))
                                    card_container.click()
                                else:
                                    btn.locator('..').click()
                                card_clicked = True
                                time.sleep(random.uniform(2.0, 3.2))
                            except Exception:
                                pass
                    else:
                        card_locator = page.locator(".jobs-search-results__list-item, .scaffold-layout__list-item, .jobs-search-results-list li, .scaffold-layout__list li, div[class*='job']").nth(idx)
                        if card_locator.count() > 0 and card_locator.is_visible():
                            try:
                                card_locator.scroll_into_view_if_needed()
                                time.sleep(random.uniform(0.3, 0.6))
                                card_locator.click()
                                card_clicked = True
                                time.sleep(random.uniform(2.0, 3.2))
                            except Exception:
                                pass

                    if not card_clicked and card.get("href"):
                        page.goto(card["href"])
                        time.sleep(random.uniform(2.5, 4.0))

                    success = run_one_application(page, profile, card.get("title", ""), card.get("company", ""))
                    if success:
                        applied += 1
                        applied_keys.add(_job_key(title, company))
                        log_row([datetime.now(), "linkedin", card.get("title"),
                                  card.get("company"), "applied", ""])
                        print(f"Applied: {card.get('title')} @ {card.get('company')} ({applied} total this run)")

                        # Outreach to hiring manager / recruiter if visible on the job posting
                        if profile.data.get("connect_with_hiring_manager", True):
                            try:
                                recruiter_info = extract_hiring_manager(page)
                                if recruiter_info:
                                    send_recruiter_connection_request(page, recruiter_info, card.get("title", ""), card.get("company", ""), profile)
                            except Exception:
                                pass

                        wait_before_next_application(profile)
                    else:
                        log_row([datetime.now(), "linkedin", card.get("title"),
                                  card.get("company"), "skipped", "no Easy Apply button or ineligible"])
                        from common.external_tracker import log_external_job
                        job_link = page.url
                        ext_link = page.evaluate("() => document.querySelector('.jobs-apply-button')?.href || ''")
                        log_external_job("linkedin", card.get("title") or "", card.get("company") or "", job_link or "", ext_link or "", loc, "", card.get("posted") or "")
                        print(f"Skipped (External Apply logged to CSV): {card.get('title')} @ {card.get('company')}")

                        # Outreach to hiring manager even for external/non-Easy Apply if enabled
                        if profile.data.get("connect_with_hiring_manager", True):
                            try:
                                recruiter_info = extract_hiring_manager(page)
                                if recruiter_info:
                                    send_recruiter_connection_request(page, recruiter_info, card.get("title", ""), card.get("company", ""), profile)
                            except Exception:
                                pass

                        time.sleep(random.uniform(2.0, 3.5))
                except SubmissionUnconfirmed as e:
                    print(f"UNCERTAIN: {card.get('title')} @ {card.get('company')} — {e}")
                    log_row([datetime.now(), "linkedin", card.get("title"),
                              card.get("company"), "uncertain", str(e)])
                    wait_before_next_application(profile)
                    continue
                except RuntimeError as e:
                    print(f"STOPPING: {e}")
                    log_row([datetime.now(), "linkedin", card.get("title"),
                              card.get("company"), "stopped", str(e)])
                    browser.close()
                    return
                except PWError as e:
                    if "closed" in str(e).lower() or "target" in str(e).lower():
                        print(f"\n[!] LinkedIn browser window or tab was closed ({e}). Exiting run cleanly.")
                        return
                    print(f"  (Playwright note: {e})")
                    continue

        browser.close()

    print(f"\nDone. {applied} applications submitted this run. See {LOG_FILE} for the full log.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="LinkedIn Easy Apply Bot")
    parser.add_argument("--limit", type=int, default=None, help="Target application limit for this run")
    args, _ = parser.parse_known_args()
    run(limit=args.limit)
