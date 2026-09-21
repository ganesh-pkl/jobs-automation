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
    answers = profile.answer_library()
    state = describe_modal(page)
    if state.get("error"):
        time.sleep(1.5)
        state = describe_modal(page)

    for field in state.get("fields", []):
        label = (field.get("label") or "").lower()
        if not label or field.get("value"):
            continue  # already filled or unlabeled, leave it

        matched = None
        for key, val in answers.items():
            if key.replace("_", " ") in label:
                matched = val
                break

        if matched is not None:
            if field["tag"] == "SELECT":
                pass  # dropdowns handled separately below if needed
            else:
                fill_text_field(page, field["id"], str(matched))
        elif field["tag"] in ("INPUT",) and field["type"] == "text":
            draft = llm.draft_answer(label, profile.llm_context(), job_context)
            if draft.startswith("[NEEDS_HUMAN_INPUT"):
                raise RuntimeError(f"profile gap on '{label}': {draft}")
            fill_text_field(page, field["id"], draft)


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

    if not click_modal_button(page, "Easy Apply"):
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
            time.sleep(2)
            confirmation_text = page.inner_text("body")
            if not _is_application_confirmation(confirmation_text):
                raise SubmissionUnconfirmed(
                    "submit clicked, but LinkedIn did not show an application confirmation"
                )
            click_modal_button(page, "Done")
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

        for role in profile.target_roles:
            if applied >= profile.stop_after_n_applications:
                break
            search_url = (
                "https://www.linkedin.com/jobs/search/?keywords="
                + role.replace(" ", "%20")
                + "&f_AL=true"  # Easy Apply filter
            )
            print(f"\n--- Searching LinkedIn: {role} ---")
            page.goto(search_url)
            time.sleep(3)

            stop = page_has_stop_signal(page)
            if stop:
                print(f"STOPPING: {stop}")
                log_row([datetime.now(), "linkedin", "-", "-", "stopped", stop])
                browser.close()
                return

            cards = page.evaluate("""
                () => Array.from(document.querySelectorAll('.jobs-search-results-list li, .scaffold-layout__list li'))
                  .map(c => ({
                    title: c.querySelector('[class*="job-card-list__title"]')?.innerText?.trim(),
                    company: c.querySelector('[class*="job-card-container__company-name"]')?.innerText?.trim(),
                  })).filter(c => c.title)
            """)

            for idx, card in enumerate(cards):
                if applied >= profile.stop_after_n_applications:
                    break
                try:
                    page.click(f"(.jobs-search-results-list li, .scaffold-layout__list li) >> nth={idx}")
                    time.sleep(1.5)
                    success = run_one_application(page, profile, card.get("title", ""), card.get("company", ""))
                    if success:
                        applied += 1
                        log_row([datetime.now(), "linkedin", card.get("title"),
                                  card.get("company"), "applied", ""])
                        print(f"Applied: {card.get('title')} @ {card.get('company')} ({applied} total)")
                    else:
                        log_row([datetime.now(), "linkedin", card.get("title"),
                                  card.get("company"), "skipped", "no Easy Apply button"])
                except SubmissionUnconfirmed as e:
                    print(f"UNCERTAIN: {card.get('title')} @ {card.get('company')} — {e}")
                    log_row([datetime.now(), "linkedin", card.get("title"),
                              card.get("company"), "uncertain", str(e)])
                    continue
                except RuntimeError as e:
                    print(f"STOPPING: {e}")
                    log_row([datetime.now(), "linkedin", card.get("title"),
                              card.get("company"), "stopped", str(e)])
                    browser.close()
                    return

                time.sleep(profile.pace_seconds_between_actions)

        browser.close()

    print(f"\nDone. {applied} applications submitted this run. See {LOG_FILE} for the full log.")


if __name__ == "__main__":
    run()
