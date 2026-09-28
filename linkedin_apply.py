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
    "easy apply limit", "unusual activity", "verify it's you",
    "captcha", "security verification", "we've restricted",
]


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
            const modal = document.querySelector('.jobs-easy-apply-modal, [role="dialog"], .artdeco-modal');
            if (!modal) return {error: 'no dialog found'};
            const out = {heading: modal.querySelector('h2')?.innerText, fields: [], buttons: []};
            modal.querySelectorAll('input, textarea, select').forEach(el => {
                if (el.type === 'hidden') return;
                let label = '';
                if (el.id) {
                    const lbl = modal.querySelector(`label[for="${el.id}"]`);
                    if (lbl) label = lbl.innerText.trim();
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
            const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
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
        if not label or field.get("value"):
            continue  # already filled or unlabeled, leave it

        ans = get_screening_answer(label, profile, job_context)
        if ans:
            if field["tag"] == "SELECT":
                pass  # dropdowns handled separately if needed
            else:
                fill_text_field(page, field["id"], str(ans))



def click_modal_button(page, text: str) -> bool:
    return page.evaluate(
        """(text) => {
            const modal = document.querySelector('.jobs-easy-apply-modal, [role="dialog"], .artdeco-modal');
            if (!modal) return false;
            const btn = [...modal.querySelectorAll('button')].find(b => b.innerText.trim() === text);
            if (!btn) return false;
            btn.click();
            return true;
        }""",
        text,
    )


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

    is_easy_apply_clicked = page.evaluate("""() => {
        const btn = document.querySelector('.jobs-apply-button');
        if (btn && btn.innerText.includes('Easy Apply')) { btn.click(); return true; }
        return false;
    }""")
    if not is_easy_apply_clicked:
        return False
    time.sleep(1.5)

    for _ in range(10):  # hard cap on steps per application
        stop = page_has_stop_signal(page)
        if stop:
            raise RuntimeError(f"stop signal mid-application: {stop}")

        state = describe_modal(page)
        buttons = state.get("buttons", [])

        fill_form_step(page, profile, job_context)
        time.sleep(0.8)

        if "Submit application" in buttons:
            # Read the full review text before submitting — this is the safety net.
            review_text = page.inner_text(
                '.jobs-easy-apply-modal, [role="dialog"], .artdeco-modal'
            )
            print("--- Review before submit ---")
            print(review_text[:800])
            if not click_modal_button(page, "Submit application"):
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


def run():
    profile = Profile.load()
    if not Path(SESSION_FILE).exists():
        raise SystemExit(f"{SESSION_FILE} not found. Run: python login_capture.py linkedin")

    applied = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=200)
        context = browser.new_context(storage_state=SESSION_FILE)
        page = context.new_page()

        locations = list(dict.fromkeys([profile.current_city, *profile.relocate_cities, "Remote"]))
        freshness_seconds = int(profile.job_freshness_days) * 86400 if hasattr(profile, "job_freshness_days") else 604800
        for role in profile.target_roles:
            for loc in locations:
                if not loc:
                    continue
                if applied >= profile.stop_after_n_applications:
                    break
                search_url = (
                    "https://www.linkedin.com/jobs/search/?keywords="
                    + role.replace(" ", "%20")
                    + "&location=" + loc.replace(" ", "%20")
                    + "&f_AL=true"  # Easy Apply filter
                    + f"&f_TPR=r{freshness_seconds}" # Based on job freshness days
                )
                print(f"\n--- Searching LinkedIn: {role} ({loc}) ---")
                try:
                    page.goto(search_url, timeout=20000)
                    page.wait_for_selector('.job-card-container, [data-job-id], .jobs-search-results-list, .scaffold-layout__list', timeout=10000)
                except PWTimeout:
                    pass
                except Exception as e:
                    print(f"  (navigation timeout or error: {e})")
                time.sleep(3)

                stop = page_has_stop_signal(page)
                if stop:
                    print(f"STOPPING: {stop}")
                    log_row([datetime.now(), "linkedin", "-", "-", "stopped", stop])
                    browser.close()
                    return

                cards = page.evaluate("""
                    () => {
                        const cards = [];
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
                                if (title) {
                                    cards.push({
                                        idx: idx,
                                        title: title,
                                        href: href,
                                        company: compEl ? compEl.innerText.trim() : "",
                                        posted: timeEl ? timeEl.innerText.trim() : "",
                                    });
                                }
                            });
                        }
                        if (cards.length === 0) {
                            const anchors = Array.from(document.querySelectorAll('a[href*="/jobs/view/"]'));
                            const seen = new Set();
                            anchors.forEach((a, idx) => {
                                const title = a.innerText.trim();
                                const href = a.href;
                                if (!title || seen.has(href)) return;
                                seen.add(href);
                                const parent = a.closest('li, [class*="card"], div.flex-grow-1, [data-job-id]') || a.parentElement;
                                const compEl = parent ? parent.querySelector('[class*="company"], [class*="subtitle"], [class*="primary-description"]') : null;
                                const timeEl = parent ? parent.querySelector('time, [class*="time"], [class*="footer"]') : null;
                                cards.push({
                                    idx: idx,
                                    title: title,
                                    href: href,
                                    company: compEl ? compEl.innerText.trim() : "",
                                    posted: timeEl ? timeEl.innerText.trim() : "",
                                });
                            });
                        }
                        return cards;
                    }
                """)
                print(f"Found {len(cards)} cards on the page.")

                applied_keys = load_applied_job_keys()
                for card in cards:
                    if applied >= profile.stop_after_n_applications:
                        break
                    idx = card["idx"]
                    title = card.get("title", "")
                    company = card.get("company", "")

                    stats_tracker.record_discovered()
                    if _job_key(title, company) in applied_keys:
                        stats_tracker.record_previously_applied_skipped()
                        print(f"Skipped: {title} @ {company} — already applied in an earlier run")
                        continue
                    
                    # Enforce strict title matching based on profile.yaml
                    required_keywords = profile.data.get("role_required_keywords", {}).get(role)
                    if required_keywords:
                        if not any(k.lower() in title.lower() for k in required_keywords):
                            print(f"Skipped: {title} @ {card.get('company')} (Title doesn't match {role} keywords)")
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
                        page.locator(".jobs-search-results__list-item, .scaffold-layout__list-item, .jobs-search-results-list li, .scaffold-layout__list li").nth(idx).click()
                        time.sleep(1.5)
                        success = run_one_application(page, profile, card.get("title", ""), card.get("company", ""))
                        if success:
                            applied += 1
                            log_row([datetime.now(), "linkedin", card.get("title"),
                                      card.get("company"), "applied", ""])
                            print(f"Applied: {card.get('title')} @ {card.get('company')} ({applied} total)")
                            time.sleep(profile.min_delay_seconds_between_applications)
                        else:
                            log_row([datetime.now(), "linkedin", card.get("title"),
                                      card.get("company"), "skipped", "no Easy Apply button"])
                            from common.external_tracker import log_external_job
                            job_link = page.url
                            ext_link = page.evaluate("() => document.querySelector('.jobs-apply-button')?.href || ''")
                            log_external_job("linkedin", card.get("title") or "", card.get("company") or "", job_link or "", ext_link or "", loc, "", card.get("posted") or "")
                            print(f"Skipped (External Apply logged to CSV): {card.get('title')} @ {card.get('company')}")
                            time.sleep(3)
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
