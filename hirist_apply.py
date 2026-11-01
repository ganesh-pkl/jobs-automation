"""
Hirist.tech Apply script — High-Accuracy Direct Apply Engine with Multi-Step Screening Support.
Requires session_hirist.json from login_capture.py.

Usage:
    python hirist_apply.py
    python hirist_apply.py --limit 5
"""
import argparse
import csv
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from common.profile import Profile
from common.answers import get_screening_answer, is_sensitive_field, direct_profile_answer
from common.human_input import ask_user
from common.external_tracker import log_external_job
from common import stats_tracker

SESSION_FILE = "session_hirist.json"
LOG_FILE = "applications_log.csv"


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


def _clean_numeric_value(val, default: str = "0") -> str:
    """Extracts clean digits or decimal value for HTML5 numeric inputs."""
    if val is None:
        return default
    s = str(val).strip()
    digits = re.findall(r"\d+(?:\.\d+)?", s)
    return digits[0] if digits else default


def _should_check_checkbox(label: str, profile: Profile) -> bool:
    """Determines whether a Hirist checkbox should be checked based on candidate facts."""
    lower = " ".join((label or "").lower().split())
    if not lower:
        return True  # standalone checkbox without label is usually agreement/required

    # 1. Disqualifications / Negative questions -> DO NOT CHECK
    DISQUALIFY_HINTS = (
        "convicted", "criminal", "terminated", "fired", "disciplinary",
        "visa sponsorship", "require sponsorship", "need sponsorship",
        "bond", "service agreement", "legal proceeding",
    )
    if any(h in lower for h in DISQUALIFY_HINTS):
        return False

    # 2. Terms, Agreements, Declarations, Consents -> ALWAYS CHECK
    CONFIRM_HINTS = (
        "agree", "consent", "confirm", "declaration", "accurate", "truth",
        "certify", "accept", "terms", "condition", "policy", "acknowledge",
        "statement", "guideline", "information provided",
    )
    if any(h in lower for h in CONFIRM_HINTS):
        return True

    # 3. Relocation, Availability, Work Authorization, Remote / Hybrid -> CHECK
    AVAILABILITY_HINTS = (
        "relocate", "relocation", "immediate", "available", "authorized",
        "authorization", "eligible", "citizen", "work permit",
        "hybrid", "remote", "wfh", "work from home", "work from office",
        "comfortable", "ready", "willing", "open to", "flexible",
        "bangalore", "bengaluru", "hyderabad", "notice period",
    )
    if any(h in lower for h in AVAILABILITY_HINTS):
        return True

    # 4. Candidate Core Technical Skills -> CHECK IF MATCH
    CORE_SKILL_HINTS = (
        "java", "spring", "spring boot", "springboot", "react", "react.js", "reactjs",
        "node", "node.js", "nodejs", "javascript", "typescript", "python",
        "sql", "mysql", "postgresql", "postgres", "mongodb", "redis", "database",
        "rest", "api", "apis", "microservices", "web development", "backend",
        "frontend", "full stack", "fullstack", "docker", "git", "ci/cd",
        "cicd", "aws", "cloud", "software development", "software engineering",
        "data structures", "algorithms", "problem solving", "kafka",
    )
    for skill in CORE_SKILL_HINTS:
        if re.search(r"\b" + re.escape(skill) + r"\b", lower):
            return True

    # 5. Excluded or irrelevant technologies -> DO NOT CHECK
    EXCLUDED_TECH = (
        "php", "laravel", "ruby", "rails", "cobol", "fortran", "c++", "embedded",
        ".net", "dotnet", "c#", "csharp", "salesforce", "sap", "abap",
    )
    if any(re.search(r"\b" + re.escape(tech) + r"\b", lower) for tech in EXCLUDED_TECH):
        return False

    # 6. Default for screening forms: if it's an eligibility or confirmation item, check it
    return True


def _decide_hirist_checkbox_options(question: str, options: list[dict], profile: Profile) -> dict[int, bool]:
    """
    Decides which checkboxes to check (True) or leave unchecked (False) within a question group.
    Prevents checking multiple mutually exclusive options (e.g. checking all notice period ranges).
    """
    decisions = {}
    lower_q = " ".join((question or "").lower().split())
    # Clean noise from question
    for noise in (
        "tell the recruiter more about yourself",
        "before you submit your application",
        "* mandatory question",
        "mandatory question",
    ):
        lower_q = lower_q.replace(noise, "")
    lower_q = lower_q.strip()

    # 1. Notice period / Joining timeline group
    if any(w in lower_q for w in ("how soon", "join with us", "notice period", "joining time", "when can you join", "days to join", "availability")):
        # Candidate has notice_period_days = 0 (Immediate joiner)
        matched_immediate = False
        for opt in options:
            idx = opt["idx"]
            lbl_lower = (opt.get("label") or "").strip().lower()
            if not matched_immediate and any(h in lbl_lower for h in ("0- 7", "0-7", "0- 15", "0-15", "immediate", "immediately", "<= 15", "15 days or less")):
                decisions[idx] = True
                matched_immediate = True
            else:
                decisions[idx] = False
        # Fallback if no explicit immediate label matched: pick the smallest day range or first option
        if not matched_immediate and options:
            decisions[options[0]["idx"]] = True
            for opt in options[1:]:
                decisions[opt["idx"]] = False
        return decisions

    # 2. Relocation / Location willingness group
    if any(w in lower_q for w in ("relocate", "relocation", "willing to move", "preferred location", "which city")):
        for opt in options:
            idx = opt["idx"]
            lbl_lower = (opt.get("label") or "").strip().lower()
            if any(h in lbl_lower for h in ("yes", "bengaluru", "bangalore", "hyderabad", "remote", "open to relocate")):
                decisions[idx] = True
            elif any(h in lbl_lower for h in ("no", "not willing")):
                decisions[idx] = False
            else:
                decisions[idx] = False
        return decisions

    # 3. Multiple technical skills selection
    if any(w in lower_q for w in ("skills", "technologies", "tech stack", "tools", "hands-on experience")):
        for opt in options:
            idx = opt["idx"]
            decisions[idx] = _should_check_checkbox(opt.get("label", ""), profile)
        return decisions

    # 4. Mutually exclusive Yes / No group
    has_yes_no = any((opt.get("label") or "").strip().lower() in ("yes", "no") for opt in options)
    if has_yes_no:
        for opt in options:
            idx = opt["idx"]
            lbl_lower = (opt.get("label") or "").strip().lower()
            decisions[idx] = (lbl_lower == "yes")
        return decisions

    # 5. For general agreements, declarations, or standalone checkboxes
    for opt in options:
        idx = opt["idx"]
        decisions[idx] = _should_check_checkbox(opt.get("label", ""), profile)

    return decisions


def _auto_decide_hirist_radio(question: str, options: list[dict], profile: Profile) -> dict | None:
    """Selects the best matching radio button for a Hirist screening question."""
    if not options:
        return None
    lower_q = " ".join((question or "").lower().split())

    def find_opt(target: str) -> dict | None:
        t_low = target.lower()
        for opt in options:
            lbl = opt.get("label", "").strip().lower()
            val = opt.get("value", "").strip().lower()
            if lbl == t_low or val == t_low or t_low in lbl or t_low in val:
                return opt
        return None

    # Notice period
    if any(w in lower_q for w in ("notice period", "how soon", "joining")):
        for pref in ("immediate", "0-15", "0 to 15", "15 days", "30 days", "yes"):
            match = find_opt(pref)
            if match:
                return match

    # Relocation
    if any(w in lower_q for w in ("relocate", "relocation", "location", "bangalore", "bengaluru", "hyderabad")):
        for pref in ("yes", "bangalore", "bengaluru", "hyderabad", "can relocate"):
            match = find_opt(pref)
            if match:
                return match

    # Disqualification / Military / Layoff / Bond / Visa
    if any(w in lower_q for w in ("military", "laid off", "layoff", "sponsorship", "visa", "bond", "convicted", "court")):
        for pref in ("no", "never served", "none"):
            match = find_opt(pref)
            if match:
                return match

    # Experience / Technical capability / Weekend / Remote
    if any(w in lower_q for w in ("experience", "comfortable", "willing", "available", "remote", "wfh", "interview", "hands on")):
        match = find_opt("yes")
        if match:
            return match

    # Experience ranges (e.g. 1-3, 3-5, 3+)
    exp_years = float(profile.total_experience_years or 3)
    for opt in options:
        lbl = opt.get("label", "").lower()
        digits = [float(s) for s in re.findall(r"\d+(?:\.\d+)?", lbl)]
        if len(digits) == 2 and digits[0] <= exp_years <= digits[1]:
            return opt
        elif len(digits) == 1 and "+" in lbl and exp_years >= digits[0]:
            return opt

    # Prefer Yes if available
    yes_match = find_opt("yes")
    if yes_match:
        return yes_match

    return options[0]


def _format_input_answer(label: str, input_type: str, profile: Profile, job_context: str) -> str:
    """Formats answer appropriately for text vs number inputs."""
    lower_lbl = (label or "").lower()
    for noise in (
        "tell the recruiter more about yourself",
        "before you submit your application",
        "* mandatory question",
        "mandatory question",
    ):
        lower_lbl = lower_lbl.replace(noise, "")
    lower_lbl = lower_lbl.strip()

    is_numeric = input_type == "number" or any(w in lower_lbl for w in ("in lpa", "in lakhs", "in years", "in days", "numeric", "number of"))

    # Current CTC
    if any(w in lower_lbl for w in ("current ctc", "present ctc", "current salary", "present salary")):
        ctc_val = str(profile.data.get("current_ctc_lpa", 6))
        return _clean_numeric_value(ctc_val, default="6") if is_numeric else f"{ctc_val} LPA"

    # Expected CTC
    if any(w in lower_lbl for w in ("expected ctc", "desired ctc", "expected salary", "desired salary", "ctc expectation")):
        ctc_val = str(profile.data.get("expected_ctc_lpa", 9))
        return _clean_numeric_value(ctc_val, default="9") if is_numeric else f"{ctc_val} LPA"

    # Total Experience
    if any(w in lower_lbl for w in ("total experience", "overall experience", "total years", "years of experience")):
        exp_val = str(profile.total_experience_years or 3)
        return _clean_numeric_value(exp_val, default="3") if is_numeric else exp_val

    # Notice Period
    if any(w in lower_lbl for w in ("notice period", "how soon", "joining time", "days to join")):
        days_val = str(profile.data.get("notice_period_days", 0))
        if is_numeric or "in days" in lower_lbl or "days" in lower_lbl:
            return _clean_numeric_value(days_val, default="0")
        return "Immediately available"

    # Relevant skill experience (e.g. "Experience in React")
    if any(w in lower_lbl for w in ("experience in", "experience with", "relevant experience", "hands-on experience")):
        from common.answers import EXCLUDED_SKILLS
        if any(s in lower_lbl for s in EXCLUDED_SKILLS):
            return "0"
        exp_val = str(profile.total_experience_years or 3)
        return _clean_numeric_value(exp_val, default="3") if is_numeric else exp_val

    # Phone / Mobile
    if any(w in lower_lbl for w in ("phone", "mobile", "contact number")):
        return str(profile.data.get("phone", profile.data.get("mobile_number", "7659869814")))

    # Email
    if any(w in lower_lbl for w in ("email", "e-mail")):
        return str(profile.data.get("email", "ganesh.pkl08@gmail.com"))

    # Location / City
    if any(w in lower_lbl for w in ("current city", "current location", "residing in")):
        return str(profile.data.get("current_city", "Hyderabad"))

    if any(w in lower_lbl for w in ("preferred city", "preferred location", "preferred work location")):
        return "Bengaluru, Hyderabad"

    # LinkedIn / GitHub
    if "linkedin" in lower_lbl:
        return str(profile.data.get("linkedin_url", profile.data.get("linkedin", "https://linkedin.com/in/ganesh-pirikirala")))
    if "github" in lower_lbl:
        return str(profile.data.get("github_url", profile.data.get("github", "https://github.com/ganeshpirikirala")))
    if any(w in lower_lbl for w in ("portfolio", "website", "personal site")):
        return str(profile.data.get("portfolio_url", profile.data.get("portfolio", "")))

    # Pitch / Cover note / Summary
    if any(w in lower_lbl for w in ("cover note", "cover letter", "summary", "why should we hire", "pitch", "brief note", "note to recruiter")):
        return (
            "Full-Stack Software Developer with 3 years of experience in Java, Spring Boot, React.js, Node.js, and REST APIs. "
            "Experienced in building scalable web applications and microservices. Immediately available to join."
        )

    # General screening answer fallback
    ans = direct_profile_answer(label, profile.answer_library())
    if ans:
        return _clean_numeric_value(ans) if is_numeric else ans

    gen_ans = get_screening_answer(label, profile, job_context)
    if gen_ans:
        return _clean_numeric_value(gen_ans) if is_numeric else gen_ans

    return "0" if is_numeric else "Yes"


def _fill_hirist_text_inputs(page, profile: Profile, job_context: str):
    """Finds all visible inputs and textareas and fills them with type-safe answers."""
    try:
        fields = page.evaluate("""
            () => {
                const root = document.querySelector('.modal, .modal-dialog, [role="dialog"], .screening-modal, .apply-modal, [class*="modal" i], [class*="popup" i], [class*="drawer" i], form') || document;
                const elements = Array.from(root.querySelectorAll(
                    'input[type="text"], input[type="number"], input[type="email"], input[type="tel"], input[type="url"], input:not([type]), textarea, div[contenteditable="true"]'
                ));
                
                return elements.map((el, idx) => {
                    const isVisible = el.offsetParent !== null && window.getComputedStyle(el).display !== 'none';
                    if (!isVisible || el.disabled || el.readOnly) return null;
                    
                    const val = (el.value || el.innerText || '').trim();
                    
                    let lbl = '';
                    // 1. Look for closest Hirist question container
                    const qc = el.closest(
                        '.short-answer-question-container, .multi-answer-question-container, .single-answer-question-container, .screening-question-container, [class*="question-container"], .question, .form-group'
                    );
                    if (qc) {
                        const heading = qc.querySelector('.question-text, .mandatory-question, [class*="question-text"], legend, .label, label');
                        if (heading) {
                            lbl = (heading.innerText || '').trim();
                        }
                    }
                    
                    // 2. Direct labels
                    if (!lbl && el.labels && el.labels.length > 0) {
                        lbl = (el.labels[0].innerText || '').trim();
                    }
                    if (!lbl && el.id) {
                        try {
                            const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
                            if (l) lbl = (l.innerText || '').trim();
                        } catch (_) {}
                    }
                    if (!lbl) {
                        const parent = el.closest('.form-group, .question, [class*="form-group"], [class*="field"], [class*="input"]');
                        if (parent) {
                            const heading = parent.querySelector('label, h3, h4, h5, p, span, .label, [class*="label"], [class*="title"]');
                            if (heading && heading !== el) lbl = (heading.innerText || '').trim();
                        }
                    }
                    if (!lbl) {
                        lbl = el.getAttribute('placeholder') || el.getAttribute('aria-label') || el.getAttribute('name') || '';
                    }
                    
                    // Clean up label noise
                    lbl = lbl.replace(/^[0-9]+\\.\\s*/, '')
                             .replace(/\\*\\s*Mandatory Question/gi, '')
                             .replace(/[0-9]+\\s*characters?\\s*left/gi, '')
                             .replace(/tell the recruiter more about yourself/gi, '')
                             .replace(/before you submit your application/gi, '')
                             .trim();
                    
                    return {
                        idx,
                        type: el.type || 'text',
                        value: val,
                        label: lbl,
                        tag: el.tagName.toLowerCase(),
                        isContentEditable: el.isContentEditable
                    };
                }).filter(Boolean);
            }
        """)
        for field in fields:
            if field["value"]:
                continue  # already filled
            
            lbl = field["label"] or "Question"
            ans = _format_input_answer(lbl, field["type"], profile, job_context)
            
            page.evaluate("""
                ({idx, val}) => {
                    const root = document.querySelector('.modal, .modal-dialog, [role="dialog"], .screening-modal, .apply-modal, [class*="modal" i], [class*="popup" i], [class*="drawer" i], form') || document;
                    const elements = Array.from(root.querySelectorAll(
                        'input[type="text"], input[type="number"], input[type="email"], input[type="tel"], input[type="url"], input:not([type]), textarea, div[contenteditable="true"]'
                    )).filter(el => el.offsetParent !== null && !el.disabled && !el.readOnly);
                    
                    const el = elements[idx];
                    if (!el) return;
                    
                    el.focus();
                    if (el.isContentEditable) {
                        el.innerText = val;
                        el.dispatchEvent(new InputEvent('input', {bubbles: true, cancelable: true, inputType: 'insertText', data: val}));
                    } else {
                        const proto = el.tagName === 'TEXTAREA' ? window.HTMLTextAreaElement.prototype : window.HTMLInputElement.prototype;
                        const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
                        if (setter) setter.call(el, val);
                        else el.value = val;
                        el.dispatchEvent(new Event('input', {bubbles: true}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                    }
                    el.dispatchEvent(new Event('blur', {bubbles: true}));
                }
            """, {"idx": field["idx"], "val": ans})
            print(f"    Auto-filled '{lbl[:35]}': {ans}")
    except Exception as e:
        print(f"    (Note on text fields: {e})")


def _fill_hirist_checkboxes(page, profile: Profile, job_context: str):
    """Finds all checkboxes and groups them by question, selecting only appropriate options."""
    try:
        groups = page.evaluate("""
            () => {
                const root = document.querySelector('.modal, .modal-dialog, [role="dialog"], .screening-modal, .apply-modal, [class*="modal" i], [class*="popup" i], [class*="drawer" i], form') || document;
                const cbs = Array.from(root.querySelectorAll('input[type="checkbox"], [role="checkbox"]'));
                
                const groupMap = {};
                cbs.forEach((cb, idx) => {
                    let qTitle = '';
                    const qc = cb.closest(
                        '.short-answer-question-container, .multi-answer-question-container, .single-answer-question-container, .screening-question-container, [class*="question-container"], .question, .form-group'
                    );
                    if (qc) {
                        const heading = qc.querySelector('.question-text, .mandatory-question, [class*="question-text"], legend, .label, label');
                        if (heading) qTitle = heading.innerText.trim();
                    }
                    qTitle = qTitle.replace(/^[0-9]+\\.\\s*/, '')
                                   .replace(/\\*\\s*Mandatory Question/gi, '')
                                   .replace(/tell the recruiter more about yourself/gi, '')
                                   .replace(/before you submit your application/gi, '')
                                   .trim();
                    
                    const grpKey = qTitle || `group_${cb.name || idx}`;
                    if (!groupMap[grpKey]) {
                        groupMap[grpKey] = { question: qTitle, options: [] };
                    }
                    
                    let lbl = '';
                    if (cb.labels && cb.labels.length > 0) {
                        lbl = (cb.labels[0].innerText || '').trim();
                    }
                    if (!lbl && cb.id) {
                        try {
                            const l = document.querySelector(`label[for="${CSS.escape(cb.id)}"]`);
                            if (l) lbl = (l.innerText || '').trim();
                        } catch (_) {}
                    }
                    if (!lbl) {
                        const parent = cb.closest('label, .checkbox, [class*="checkbox"], .form-check, div');
                        if (parent) lbl = (parent.innerText || '').trim();
                    }
                    if (!lbl) {
                        lbl = cb.getAttribute('aria-label') || cb.getAttribute('name') || cb.getAttribute('value') || '';
                    }
                    
                    const isChecked = cb.tagName === 'INPUT' ? cb.checked : (cb.getAttribute('aria-checked') === 'true' || cb.classList.contains('checked'));
                    groupMap[grpKey].options.push({
                        idx,
                        id: cb.id || '',
                        label: lbl,
                        checked: isChecked,
                        required: cb.hasAttribute('required')
                    });
                });
                
                return Object.values(groupMap);
            }
        """)
        
        for grp in groups:
            q_text = grp.get("question") or ""
            opts = grp.get("options") or []
            if not opts:
                continue
                
            decisions = _decide_hirist_checkbox_options(q_text, opts, profile)
            
            for opt in opts:
                idx = opt["idx"]
                should_check = decisions.get(idx, False)
                if opt["checked"] != should_check:
                    page.evaluate("""
                        ({idx, shouldCheck}) => {
                            const root = document.querySelector('.modal, .modal-dialog, [role="dialog"], .screening-modal, .apply-modal, [class*="modal" i], [class*="popup" i], [class*="drawer" i], form') || document;
                            const cbs = Array.from(root.querySelectorAll('input[type="checkbox"], [role="checkbox"]'));
                            const cb = cbs[idx];
                            if (!cb) return;
                            
                            cb.scrollIntoView({behavior: 'instant', block: 'center'});
                            
                            let label = cb.closest('label');
                            if (!label && cb.id) {
                                try {
                                    label = document.querySelector(`label[for="${CSS.escape(cb.id)}"]`);
                                } catch (_) {}
                            }
                            
                            const isCurrentlyChecked = cb.tagName === 'INPUT' ? cb.checked : (cb.getAttribute('aria-checked') === 'true' || cb.classList.contains('checked'));
                            if (isCurrentlyChecked !== shouldCheck) {
                                if (label && label.offsetParent !== null) {
                                    label.click();
                                } else {
                                    cb.click();
                                }
                                
                                if (cb.tagName === 'INPUT' && cb.checked !== shouldCheck) {
                                    const proto = window.HTMLInputElement.prototype;
                                    const setter = Object.getOwnPropertyDescriptor(proto, 'checked')?.set;
                                    if (setter) setter.call(cb, shouldCheck);
                                    else cb.checked = shouldCheck;
                                    cb.dispatchEvent(new Event('input', {bubbles: true}));
                                    cb.dispatchEvent(new Event('change', {bubbles: true}));
                                }
                            }
                        }
                    """, {"idx": idx, "shouldCheck": should_check})
                    action_desc = "Checked" if should_check else "Unchecked"
                    print(f"    {action_desc} checkbox: '{opt['label'][:40]}' (Group: '{q_text[:30]}')")
    except Exception as e:
        print(f"    (Note on checkboxes: {e})")


def _fill_hirist_radios(page, profile: Profile, job_context: str):
    """Finds radio button groups and auto-selects appropriate options."""
    try:
        groups = page.evaluate("""
            () => {
                const root = document.querySelector('.modal, .modal-dialog, [role="dialog"], .screening-modal, .apply-modal, [class*="modal" i], [class*="popup" i], [class*="drawer" i], form') || document;
                const radios = Array.from(root.querySelectorAll('input[type="radio"]'));
                
                const byName = {};
                radios.forEach((r, idx) => {
                    const name = r.name || `unnamed_group_${idx}`;
                    if (!byName[name]) {
                        let qText = '';
                        const fieldset = r.closest('fieldset');
                        if (fieldset) {
                            const legend = fieldset.querySelector('legend');
                            if (legend) qText = (legend.innerText || '').trim();
                        }
                        if (!qText) {
                            const parent = r.closest('.form-group, .question, [class*="question"], div');
                            if (parent) {
                                const heading = parent.querySelector('label, h3, h4, h5, p, span, .label, [class*="label"], [class*="title"]');
                                if (heading) qText = (heading.innerText || '').trim();
                            }
                        }
                        byName[name] = { name, question: qText, options: [] };
                    }
                    
                    let lbl = '';
                    if (r.labels && r.labels.length > 0) lbl = (r.labels[0].innerText || '').trim();
                    if (!lbl && r.id) {
                        const l = document.querySelector(`label[for="${r.id}"]`);
                        if (l) lbl = (l.innerText || '').trim();
                    }
                    if (!lbl) {
                        const p = r.closest('label, div');
                        if (p) lbl = (p.innerText || '').trim();
                    }
                    byName[name].options.push({
                        idx,
                        value: r.value || '',
                        label: lbl || r.value || `Option ${byName[name].options.length + 1}`,
                        checked: r.checked
                    });
                });
                
                return Object.values(byName);
            }
        """)
        for grp in groups:
            has_checked = any(o["checked"] for o in grp["options"])
            if has_checked:
                continue
            
            q = grp["question"] or grp["name"]
            chosen = _auto_decide_hirist_radio(q, grp["options"], profile)
            if chosen:
                page.evaluate("""
                    (idx) => {
                        const root = document.querySelector('.modal, .modal-dialog, [role="dialog"], .screening-modal, .apply-modal, [class*="modal" i], [class*="popup" i], [class*="drawer" i], form') || document;
                        const radios = Array.from(root.querySelectorAll('input[type="radio"]'));
                        const r = radios[idx];
                        if (!r) return;
                        r.scrollIntoView({behavior: 'instant', block: 'center'});
                        const label = r.closest('label') || document.querySelector(`label[for="${r.id}"]`);
                        if (label && label.offsetParent !== null) {
                            label.click();
                        } else {
                            r.click();
                        }
                        r.checked = true;
                        r.dispatchEvent(new Event('input', {bubbles: true}));
                        r.dispatchEvent(new Event('change', {bubbles: true}));
                    }
                """, chosen["idx"])
                print(f"    Selected radio option '{chosen['label']}' for: {q[:35]}")
    except Exception as e:
        print(f"    (Note on radios: {e})")


def _fill_hirist_selects(page, profile: Profile, job_context: str):
    """Finds select dropdowns and picks the best matching option."""
    try:
        select_data = page.evaluate("""
            () => {
                const root = document.querySelector('.modal, .modal-dialog, [role="dialog"], .screening-modal, .apply-modal, [class*="modal" i], [class*="popup" i], [class*="drawer" i], form') || document;
                const selects = Array.from(root.querySelectorAll('select'));
                
                return selects.map((sel, idx) => {
                    const isVisible = sel.offsetParent !== null && window.getComputedStyle(sel).display !== 'none';
                    if (!isVisible || sel.disabled) return null;
                    
                    let lbl = '';
                    if (sel.labels && sel.labels.length > 0) lbl = (sel.labels[0].innerText || '').trim();
                    if (!lbl && sel.id) {
                        const l = document.querySelector(`label[for="${sel.id}"]`);
                        if (l) lbl = (l.innerText || '').trim();
                    }
                    if (!lbl) {
                        const p = sel.closest('.form-group, .question, div');
                        if (p) {
                            const h = p.querySelector('label, h3, h4, h5, p, span, .label');
                            if (h && h !== sel) lbl = (h.innerText || '').trim();
                        }
                    }
                    
                    const opts = Array.from(sel.options).map((o, optIdx) => ({
                        optIdx,
                        value: o.value,
                        text: (o.text || o.innerText || '').trim(),
                        selected: o.selected
                    }));
                    
                    return { idx, label: lbl, options: opts, selectedIndex: sel.selectedIndex };
                }).filter(Boolean);
            }
        """)
        for sel_info in select_data:
            curr_idx = sel_info["selectedIndex"]
            options = sel_info["options"]
            if not options:
                continue
                
            non_placeholders = [
                o for o in options
                if o["value"] and not any(p in o["text"].lower() for p in ("select", "choose", "--", "none"))
            ]
            if not non_placeholders:
                non_placeholders = options[1:] if len(options) > 1 else options
                
            lbl_low = sel_info["label"].lower()
            best_opt = None
            
            if any(w in lbl_low for w in ("notice", "joining")):
                for pref in ("immediate", "0-15", "15", "30", "1 month"):
                    match = next((o for o in non_placeholders if pref in o["text"].lower()), None)
                    if match:
                        best_opt = match
                        break
            elif any(w in lbl_low for w in ("experience", "years")):
                for pref in ("3", "3-5", "1-3"):
                    match = next((o for o in non_placeholders if pref in o["text"].lower()), None)
                    if match:
                        best_opt = match
                        break
            elif any(w in lbl_low for w in ("qualification", "degree", "education")):
                for pref in ("b.tech", "bachelor", "engineering", "graduate"):
                    match = next((o for o in non_placeholders if pref in o["text"].lower()), None)
                    if match:
                        best_opt = match
                        break
            elif any(w in lbl_low for w in ("relocate", "comfortable", "willing", "available")):
                match = next((o for o in non_placeholders if "yes" in o["text"].lower()), None)
                if match:
                    best_opt = match

            if not best_opt and non_placeholders:
                best_opt = non_placeholders[0]
                
            if best_opt and best_opt["optIdx"] != curr_idx:
                page.evaluate("""
                    ({selIdx, optIdx}) => {
                        const root = document.querySelector('.modal, .modal-dialog, [role="dialog"], .screening-modal, .apply-modal, [class*="modal" i], [class*="popup" i], [class*="drawer" i], form') || document;
                        const selects = Array.from(root.querySelectorAll('select')).filter(s => s.offsetParent !== null && !s.disabled);
                        const sel = selects[selIdx];
                        if (!sel) return;
                        sel.selectedIndex = optIdx;
                        sel.dispatchEvent(new Event('input', {bubbles: true}));
                        sel.dispatchEvent(new Event('change', {bubbles: true}));
                    }
                """, {"selIdx": sel_info["idx"], "optIdx": best_opt["optIdx"]})
                print(f"    Selected dropdown option '{best_opt['text']}' for: {sel_info['label'][:35]}")
    except Exception as e:
        print(f"    (Note on selects: {e})")


def _upload_hirist_resume(page, profile: Profile, job_context: str):
    """Uploads candidate resume if file input is present."""
    try:
        file_inputs = page.locator("input[type='file'], input[name*='resume' i], input[name*='cv' i]").all()
        if file_inputs:
            from common.resume_selector import get_resume_path
            resume_path = get_resume_path(profile, job_context=job_context, generate_dynamic=True)
            if resume_path and resume_path.is_file():
                for fi in file_inputs:
                    try:
                        fi.set_input_files(str(resume_path.resolve()))
                        print(f"    Auto-uploaded role-targeted resume: {resume_path.name}")
                        time.sleep(1)
                        break
                    except Exception:
                        pass
    except Exception:
        pass


def _fill_hirist_pills(page, profile: Profile, job_context: str):
    """Selects appropriate pill-style button options (e.g. notice period pill buttons)."""
    try:
        page.evaluate("""
            () => {
                const containers = document.querySelectorAll('.pill-answer-options, [class*="pill-option"]');
                if (containers.length === 0) return;
                
                const allPills = Array.from(document.querySelectorAll('button.pill-option'));
                const target = allPills.find(b => {
                    const t = (b.innerText || '').toLowerCase();
                    return t.includes('immediate') || t.includes('0- 7') || t.includes('0-7') || t.includes('15 days');
                });
                if (target && !target.classList.contains('selected')) {
                    target.click();
                }
            }
        """)
    except Exception:
        pass


def _click_hirist_action_button(page) -> str | None:
    """Un-disables and clicks the Submit or Next button using Playwright locator and JS fallback."""
    try:
        # First un-disable any disabled buttons inside DOM
        page.evaluate("""
            () => {
                document.querySelectorAll('button[disabled], input[disabled], .disabled, [aria-disabled="true"]').forEach(b => {
                    b.removeAttribute('disabled');
                    b.removeAttribute('aria-disabled');
                    b.classList.remove('disabled');
                    b.style.pointerEvents = 'auto';
                });
            }
        """)

        # Priority 1: Submit Application buttons
        submit_selectors = [
            "button:has-text('Submit Application')",
            "button:has-text('Submit')",
            "button:has-text('Confirm Application')",
            "button:has-text('Send Application')",
            "button:has-text('Apply Now')",
            "button:has-text('Finish')",
            "button:has-text('Done')",
            ".submission-btn button:has-text('Submit')",
        ]
        for sel in submit_selectors:
            try:
                loc = page.locator(sel).first
                if loc.is_visible():
                    loc.scroll_into_view_if_needed()
                    loc.click(timeout=3000)
                    return "submit"
            except Exception:
                continue

        # Priority 2: Next Step / Continue buttons
        next_selectors = [
            ".submission-btn button:has-text('Next')",
            ".submission-btn button",
            "button.a-button:has-text('Next')",
            "button:has-text('Save & Next')",
            "button:has-text('Save and Next')",
            "button:has-text('Next Step')",
            "button:has-text('Next')",
            "button:has-text('Proceed')",
            "button:has-text('Continue')",
        ]
        for sel in next_selectors:
            try:
                loc = page.locator(sel).first
                if loc.is_visible():
                    loc.scroll_into_view_if_needed()
                    loc.click(timeout=3000)
                    return "next"
            except Exception:
                continue

        # Priority 3: Fallback via DOM evaluation if locators failed to find visible button
        return page.evaluate("""
            () => {
                const root = document.querySelector('.modal, .modal-dialog, [role="dialog"], .screening-modal, .apply-modal, [class*="modal" i], [class*="popup" i], [class*="drawer" i], form') || document;
                const allBtns = Array.from(root.querySelectorAll(
                    'button, input[type="submit"], input[type="button"], a[role="button"], div[role="button"], .btn, [class*="btn"], [class*="button"]'
                ));
                
                const submitPhrases = ['submit application', 'submit', 'confirm application', 'apply now', 'finish', 'done'];
                for (const phrase of submitPhrases) {
                    const target = allBtns.find(el => {
                        if (!el || el.offsetParent === null) return false;
                        const t = (el.innerText || el.value || '').trim().toLowerCase();
                        return t === phrase || t.startsWith(phrase);
                    });
                    if (target) {
                        target.scrollIntoView({behavior: 'instant', block: 'center'});
                        target.click();
                        return 'submit';
                    }
                }

                const nextPhrases = ['save & next', 'save and next', 'next step', 'next', 'proceed', 'continue'];
                for (const phrase of nextPhrases) {
                    const target = allBtns.find(el => {
                        if (!el || el.offsetParent === null) return false;
                        const t = (el.innerText || el.value || '').trim().toLowerCase();
                        return t === phrase || t.startsWith(phrase);
                    });
                    if (target) {
                        target.scrollIntoView({behavior: 'instant', block: 'center'});
                        target.click();
                        return 'next';
                    }
                }
                return null;
            }
        """)
    except Exception:
        return None


def _is_hirist_success(url: str, text: str, has_applied_button: bool = False) -> bool:
    """Verifies whether the application has successfully been submitted on Hirist."""
    lower_url = (url or "").lower()
    lower_text = " ".join((text or "").lower().split())

    if "/job/applied" in lower_url or "/success" in lower_url or "/applied" in lower_url:
        return True
    if has_applied_button:
        return True

    success_phrases = (
        "applied successfully",
        "application submitted",
        "thank you for applying",
        "application has been received",
        "your application has been received",
        "application sent",
        "you have applied",
        "already applied to this job",
        "already applied",
        "successfully applied",
        "application recorded",
    )
    return any(p in lower_text for p in success_phrases)


def verify_hirist_applied(page, timeout_s: int = 5) -> bool:
    """Polls over a few seconds to verify application confirmation on Hirist."""
    start_time = time.time()
    while time.time() - start_time < timeout_s:
        try:
            url = page.url.lower()
            text = page.locator("body").inner_text(timeout=500).lower()
            has_applied_btn = page.locator(
                "button:has-text('Applied'), a:has-text('Applied'), [class*='applied-badge'], [class*='apply-success']"
            ).count() > 0
            
            if _is_hirist_success(url, text, has_applied_button=has_applied_btn):
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def _save_debug_screenshot(page, title: str) -> str:
    """Saves a screenshot and HTML dump when application outcome cannot be confirmed."""
    debug_dir = Path("debug_screenshots")
    debug_dir.mkdir(mode=0o700, exist_ok=True)
    safe_name = "".join(c if c.isalnum() else "_" for c in (title or "unknown"))[:60]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    png_path = debug_dir / f"hirist_{safe_name}_{timestamp}.png"
    html_path = debug_dir / f"hirist_{safe_name}_{timestamp}.html"
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


def handle_hirist_screening(job_page, profile: Profile, title: str, company: str) -> bool:
    """
    Handles Hirist screening forms, modals, checkboxes, and multi-step questionnaires.
    Loops through form steps (Next -> Next -> ... -> Submit) until application is confirmed.
    """
    job_context = f"{title} at {company}"
    max_steps = 10

    for step in range(max_steps):
        time.sleep(1.0)
        
        # 1. Check if already confirmed applied before running step
        if verify_hirist_applied(job_page, timeout_s=1):
            print("    Application confirmation detected on Hirist!")
            return True

        # 2. Fill inputs, checkboxes, radios, pills, selects, resume
        _fill_hirist_text_inputs(job_page, profile, job_context)
        _fill_hirist_checkboxes(job_page, profile, job_context)
        _fill_hirist_radios(job_page, profile, job_context)
        _fill_hirist_pills(job_page, profile, job_context)
        _fill_hirist_selects(job_page, profile, job_context)
        _upload_hirist_resume(job_page, profile, job_context)
        
        time.sleep(0.5)

        # 3. Click Submit or Next
        action = _click_hirist_action_button(job_page)
        if not action:
            time.sleep(1.0)
            action = _click_hirist_action_button(job_page)
            if not action:
                print("    No more Next or Submit action buttons visible on Hirist.")
                break

        print(f"    Clicked '{action}' action button on Step {step + 1}...")

        # 4. Check if clicking this action button completed the application!
        # Both "Next" (single-page screening) and "Submit" (multi-step screening) can complete the application!
        time.sleep(1.5)
        if verify_hirist_applied(job_page, timeout_s=4):
            print(f"    ✅ Application successfully submitted and confirmed on Step {step + 1}!")
            return True

        # If not yet confirmed, proceed to the next step
        print(f"    Proceeding through multi-step form to Step {step + 2}...")

    # Final verification check
    return verify_hirist_applied(job_page, timeout_s=3)



def run(limit: int | None = None):
    profile = Profile.load()
    roles = profile.target_roles
    platform_threshold = int(profile.data.get("hirist_limit", profile.data.get("hirist_run_limit", profile.data.get("hirist_daily_limit", 30))))
    target_limit = limit if limit is not None else platform_threshold
    run_limit = min(target_limit, int(profile.stop_after_n_applications or 100))
    print(f"Hirist session starting (Platform threshold: {platform_threshold} | Target this run: {run_limit})")
    applied = 0
    applied_keys = load_applied_job_keys()
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=profile.browser_mode == "headless")
        context = browser.new_context(storage_state=SESSION_FILE)
        page = context.new_page()
        
        for role in roles:
            if applied >= run_limit:
                break
            slug = role.lower().replace(".", "").replace(" ", "-")
            
            for page_no in range(1, 4):
                if applied >= run_limit:
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
                        
                        apply_candidates = job_page.locator(
                            "button:has-text('Apply'), a:has-text('Apply'), button[class*='apply' i], a[class*='apply' i], .apply-btn, #apply-button"
                        ).all()
                        apply_btn = None
                        for btn in apply_candidates:
                            if btn.is_visible():
                                txt = btn.inner_text().strip().lower()
                                if "filter" in txt:
                                    continue
                                apply_btn = btn
                                break

                        if apply_btn:
                            btn_text = apply_btn.inner_text().strip().lower()
                            if "company website" in btn_text or "external" in btn_text:
                                print("  External application. Logging to CSV.")
                                log_row([datetime.now().isoformat(), "hirist", title, company, "skipped", "external link"])
                                log_external_job("hirist", title, company, job_url, job_page.url, "", exp_text, posted_text)
                            elif "applied" in btn_text or "already applied" in btn_text:
                                print(f"  Already applied to {title} @ {company}.")
                                applied_keys.add(_job_key(title, company))
                            else:
                                print("  Found Apply button! Clicking...")
                                apply_btn.scroll_into_view_if_needed()
                                apply_btn.click()
                                time.sleep(1.5)
                                
                                # Handle screening questionnaire (multi-step & checkboxes) if present
                                handle_hirist_screening(job_page, profile, title, company)
                                
                                # Check if successfully applied
                                is_success = verify_hirist_applied(job_page, timeout_s=5)
                                if is_success:
                                    applied += 1
                                    applied_keys.add(_job_key(title, company))
                                    print(f"  ✅ Successfully applied: {title} @ {company} ({applied} total this run)")
                                    log_row([datetime.now().isoformat(), "hirist", title, company, "applied", "success"])
                                    time.sleep(profile.min_delay_seconds_between_applications)
                                else:
                                    print("  ❓ Clicked apply, but couldn't verify success. Saving debug capture...")
                                    _save_debug_screenshot(job_page, title)
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
    parser = argparse.ArgumentParser(description="Hirist Job Application Automation")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of applications to submit")
    args = parser.parse_args()
    run(limit=args.limit)
