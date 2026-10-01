"""
LinkedIn Easy Apply. Requires session_linkedin.json from login_capture.py.

LinkedIn's automation detection is meaningfully tighter than Naukri's — keep
pace_seconds_between_actions generous, and expect this to need more manual
babysitting than the Naukri script. Stop instantly on any CAPTCHA, login
prompt, or "you've reached the Easy Apply limit" message.

Usage:
    python linkedin_apply.py
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
            const modal = document.querySelector('dialog, .jobs-easy-apply-modal, [role="dialog"], .artdeco-modal');
            if (!modal) return {error: 'no dialog found'};
            const out = {heading: modal.querySelector('h2')?.innerText, fields: [], buttons: []};
            modal.querySelectorAll('input, textarea, select').forEach(el => {
                if (el.type === 'hidden') return;
                let label = el.getAttribute('aria-label') || '';
                if (!label && el.id) {
                    const lbl = modal.querySelector(`label[for="${el.id}"]`);
                    if (lbl) label = lbl.innerText.trim();
                }
                if (!label && el.getAttribute('aria-labelledby')) {
                    const lblId = el.getAttribute('aria-labelledby');
                    const lblEl = document.getElementById(lblId);
                    if (lblEl) label = lblEl.innerText.trim();
                }
                if (!label) {
                    const parent = el.closest('label') || el.closest('.fb-dash-form-element, .jobs-easy-apply-form-element, [class*="form-element"]');
                    if (parent) {
                        const span = parent.querySelector('span, label, p, legend, [class*="label"], [class*="title"]');
                        if (span) label = span.innerText.trim();
                    }
                }
                if (!label) {
                    label = el.placeholder || '';
                }
                out.fields.push({tag: el.tagName, type: el.type || '', id: el.id,
                                  label, value: el.value, checked: el.checked});
            });
            modal.querySelectorAll('button').forEach(b => {
                const t = b.innerText.trim();
                if (t) out.buttons.push(t);
            });
            return out;
        }
    """)


def fill_text_field(page, field_id: str, text: str):
    page.evaluate(
        """([id, text]) => {
            const el = document.getElementById(id);
            if (!el) return false;
            let proto = window.HTMLInputElement.prototype;
            if (el.tagName === 'TEXTAREA') {
                proto = window.HTMLTextAreaElement.prototype;
            }
            const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
            setter.call(el, text);
            el.dispatchEvent(new Event('input', {bubbles: true}));
            el.dispatchEvent(new Event('change', {bubbles: true}));
            return true;
        }""",
        [field_id, text],
    )


def fill_form_step(page, profile: Profile, job_context: str):
    from common.answers import get_screening_answer
    state = describe_modal(page)
    if state.get("error"):
        time.sleep(1.5)
        state = describe_modal(page)

    for field in state.get("fields", []):
        label = (field.get("label") or "").strip()
        field_type = (field.get("type") or "").lower()
        field_tag = (field.get("tag") or "").upper()
        field_id = field.get("id")

        if field_type == "radio":
            # For radio buttons, check if already checked; if not, check based on screening answer
            if not field.get("checked") and label and field_id:
                ans = get_screening_answer(label, profile, job_context)
                if ans and ans.lower() in ("yes", "true", "1") and "yes" in label.lower():
                    page.evaluate(f"() => {{ const el = document.getElementById('{field_id}'); if (el) {{ el.click(); el.checked = true; el.dispatchEvent(new Event('change', {{bubbles: true}})); }} }}")
                elif ans and ans.lower() in ("no", "false", "0") and "no" in label.lower():
                    page.evaluate(f"() => {{ const el = document.getElementById('{field_id}'); if (el) {{ el.click(); el.checked = true; el.dispatchEvent(new Event('change', {{bubbles: true}})); }} }}")
            continue

        if field_type == "checkbox":
            if not field.get("checked") and field_id:
                page.evaluate(f"() => {{ const el = document.getElementById('{field_id}'); if (el) {{ el.click(); el.checked = true; }} }}")
            continue

        if field_tag == "SELECT":
            # If select dropdown has no value or first option
            if not field.get("value") and field_id:
                page.evaluate(f"""() => {{
                    const el = document.getElementById('{field_id}');
                    if (el && el.options.length > 1) {{
                        el.selectedIndex = 1;
                        el.dispatchEvent(new Event('change', {{bubbles: true}}));
                    }}
                }}""")
            continue

        if not label or field.get("value"):
            continue  # already filled or unlabeled

        ans = get_screening_answer(label, profile, job_context)
        if ans and field_id:
            fill_text_field(page, field_id, str(ans))


def click_modal_button(page, text: str) -> bool:
    return page.evaluate(
        """(text) => {
            const modal = document.querySelector('dialog, .jobs-easy-apply-modal, [role="dialog"], .artdeco-modal');
            if (!modal) return false;
            const btn = [...modal.querySelectorAll('button')].find(b => {
                const t = b.innerText.trim();
                const aria = b.getAttribute('aria-label') || '';
                return t === text || t.startsWith(text) || aria === text || aria.startsWith(text);
            });
            if (!btn) return false;
            btn.click();
            return true;
        }""",
        text,
    )


def dismiss_modal_if_open(page):
    try:
        page.evaluate("""() => {
            const modal = document.querySelector('dialog, .jobs-easy-apply-modal, [role="dialog"], .artdeco-modal');
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

    is_easy_apply_clicked = page.evaluate("""() => {
        const btn = document.querySelector('.jobs-apply-button') ||
                    Array.from(document.querySelectorAll('button')).find(b => {
                        const t = (b.innerText || '').trim();
                        return t === 'Easy Apply' || t.startsWith('Easy Apply') || (b.getAttribute('aria-label') || '').includes('Easy Apply');
                    });
        if (btn) { btn.click(); return true; }
        return false;
    }""")
    if not is_easy_apply_clicked:
        return False
    time.sleep(1.5)

    try:
        for _ in range(10):  # hard cap on steps per application
            stop = page_has_stop_signal(page)
            if stop:
                raise RuntimeError(f"stop signal mid-application: {stop}")

            state = describe_modal(page)
            buttons = state.get("buttons", [])

            fill_form_step(page, profile, job_context)
            time.sleep(0.8)

            if "Submit application" in buttons or any("submit" in b.lower() for b in buttons):
                # Read the full review text before submitting — this is the safety net.
                review_text = page.inner_text(
                    'dialog, .jobs-easy-apply-modal, [role="dialog"], .artdeco-modal'
                )
                print("--- Review before submit ---")
                print(review_text[:800])
                if not (click_modal_button(page, "Submit application") or click_modal_button(page, "Submit")):
                    raise SubmissionUnconfirmed("submit button click did not register")
                time.sleep(4)
                confirmation_text = page.inner_text("body")
                if not _is_application_confirmation(confirmation_text):
                    raise SubmissionUnconfirmed(
                        f"LinkedIn did not show an application confirmation. Saw text starting with: {confirmation_text[:200]}"
                    )
                # Try to close the success modal using any known close buttons
                click_modal_button(page, "Done")
                click_modal_button(page, "Not now")
                return True
            elif "Review" in buttons:
                click_modal_button(page, "Review")
            elif "Next" in buttons:
                click_modal_button(page, "Next")
            else:
                raise RuntimeError(f"unrecognized modal state, buttons={buttons}")
            time.sleep(1.2)

        raise RuntimeError("exceeded step cap without reaching submit")
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
    # LinkedIn automation is temporarily commented out / paused due to account block.
    print("\n" + "=" * 65)
    print(" [!] LinkedIn automation is temporarily PAUSED due to account block.")
    print("     To resume in the future, remove this pause return once unblocked.")
    print("=" * 65 + "\n")
    return

    profile = Profile.load()
    if not Path(SESSION_FILE).exists():
        raise SystemExit(f"{SESSION_FILE} not found. Run: python login_capture.py linkedin")

    linkedin_daily_limit = int(profile.data.get("linkedin_daily_limit", 15))
    applied_today = count_applications_today()
    remaining_today = max(0, linkedin_daily_limit - applied_today)
    target_limit = limit if limit is not None else profile.stop_after_n_applications
    run_success_limit = min(target_limit, remaining_today)

    if run_success_limit <= 0:
        print(f"LinkedIn safe daily application limit reached ({applied_today}/{linkedin_daily_limit}). Stopping.")
        return

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
        browser = p.chromium.launch(headless=profile.browser_mode == "headless", slow_mo=200)
        context = browser.new_context(storage_state=SESSION_FILE)
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
                page.goto(search_url, timeout=20000)
                page.wait_for_selector('.job-card-container, [data-job-id], .jobs-search-results-list, .scaffold-layout__list, button[aria-label^="Dismiss "]', timeout=10000)
            except PWTimeout:
                pass
            except Exception as e:
                print(f"  (navigation timeout or error: {e})")
            time.sleep(2)

            stop = page_has_stop_signal(page)
            if stop:
                print(f"STOPPING: {stop}")
                log_row([datetime.now(), "linkedin", "-", "-", "stopped", stop])
                browser.close()
                return

            # Scroll the list container so all lazy-loaded cards render
            try:
                for _ in range(4):
                    page.evaluate("""() => {
                        const list = document.querySelector('.jobs-search-results-list, .scaffold-layout__list, div.scaffold-layout__list-detail-inner, main');
                        if (list) list.scrollBy(0, 1000);
                        else window.scrollBy(0, 1000);
                    }""")
                    time.sleep(0.5)
            except Exception:
                pass

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
                    # Try opening card or directly navigating to job href
                    card_clicked = False
                    if card.get("isAiLayout"):
                        dismiss_btns = page.locator('button[aria-label^="Dismiss "]')
                        if dismiss_btns.count() > idx:
                            try:
                                btn = dismiss_btns.nth(idx)
                                card_container = btn.locator('xpath=ancestor::div[contains(@class, "auymuo")][1]')
                                if card_container.count() > 0:
                                    card_container.scroll_into_view_if_needed()
                                    card_container.click()
                                else:
                                    btn.locator('..').click()
                                card_clicked = True
                                time.sleep(2)
                            except Exception:
                                pass
                    else:
                        card_locator = page.locator(".jobs-search-results__list-item, .scaffold-layout__list-item, .jobs-search-results-list li, .scaffold-layout__list li, div[class*='job']").nth(idx)
                        if card_locator.count() > 0 and card_locator.is_visible():
                            try:
                                card_locator.scroll_into_view_if_needed()
                                card_locator.click()
                                card_clicked = True
                                time.sleep(1.5)
                            except Exception:
                                pass

                    if not card_clicked and card.get("href"):
                        page.goto(card["href"])
                        time.sleep(2)

                    success = run_one_application(page, profile, card.get("title", ""), card.get("company", ""))
                    if success:
                        applied += 1
                        applied_keys.add(_job_key(title, company))
                        log_row([datetime.now(), "linkedin", card.get("title"),
                                  card.get("company"), "applied", ""])
                        print(f"Applied: {card.get('title')} @ {card.get('company')} ({applied} total this run)")
                        time.sleep(profile.min_delay_seconds_between_applications)
                    else:
                        log_row([datetime.now(), "linkedin", card.get("title"),
                                  card.get("company"), "skipped", "no Easy Apply button or ineligible"])
                        from common.external_tracker import log_external_job
                        job_link = page.url
                        ext_link = page.evaluate("() => document.querySelector('.jobs-apply-button')?.href || ''")
                        log_external_job("linkedin", card.get("title") or "", card.get("company") or "", job_link or "", ext_link or "", loc, "", card.get("posted") or "")
                        print(f"Skipped (External Apply logged to CSV): {card.get('title')} @ {card.get('company')}")
                        time.sleep(2)
                except SubmissionUnconfirmed as e:
                    print(f"UNCERTAIN: {card.get('title')} @ {card.get('company')} — {e}")
                    log_row([datetime.now(), "linkedin", card.get("title"),
                              card.get("company"), "uncertain", str(e)])
                    time.sleep(profile.min_delay_seconds_between_applications)
                    continue
                except RuntimeError as e:
                    print(f"STOPPING: {e}")
                    log_row([datetime.now(), "linkedin", card.get("title"),
                              card.get("company"), "stopped", str(e)])
                    browser.close()
                    return

        browser.close()

    print(f"\nDone. {applied} applications submitted this run. See {LOG_FILE} for the full log.")


if __name__ == "__main__":
    run()
