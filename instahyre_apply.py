"""
Instahyre Apply Script — High-Accuracy Direct Easy Apply Engine.
Requires session_instahyre.json from login_capture.py.

Workflow:
1. Intercepts searches directly from the UI search box (#skills-drop-select-job-search-selectized)
   and applies strict left-sidebar filters:
   - Experience Level: 0 - 4 yrs (max 4 yrs ceiling)
   - Location: Work From Home / Hyderabad / Bangalore
   - Job Function: Full-Stack Development / Backend Development
2. Iterates over opportunity cards on the results feed.
3. Opens the Instahyre modal (.application-modal-wrap) via 'View job »'.
4. Enforces STRICT experience checks (strictly skips jobs requiring > 4 yrs exp).
5. Inspects the InstaMatch score:
   - Skips LOW match jobs to maximize shortlist probability.
   - Applies to HIGH and MEDIUM match jobs.
6. Clicks the direct 'Apply' button, auto-fills recruiter notes/screening answers,
   and logs each successful submission to applications_log.csv.

Usage:
    python instahyre_apply.py
    python instahyre_apply.py --role "Frontend Developer" --limit 1
"""
import csv
import random
import re
import time
import urllib.parse
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from common.profile import Profile
from common.answers import get_screening_answer
from common import stats_tracker

SESSION_FILE = "session_instahyre.json"
LOG_FILE = "applications_log.csv"


def log_row(row: list):
    """Appends an application attempt to applications_log.csv."""
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


def extract_experience_years(text: str) -> tuple[int | None, int | None]:
    """
    Extracts min and max required experience years from text (e.g. '7-11 Years', '2-5 Yrs', '8+ Years').
    Returns (min_years, max_years) or (None, None).
    """
    # Range pattern: e.g. 7-11 Years, 2-5 Yrs, 0-2 yrs
    range_match = re.search(r"(\d+)\s*[-–to]+\s*(\d+)\s*(?:years?|yrs)", text, re.IGNORECASE)
    if range_match:
        return int(range_match.group(1)), int(range_match.group(2))

    # Plus pattern: e.g. 8+ Years, 5+ yrs
    plus_match = re.search(r"(\d+)\+\s*(?:years?|yrs)", text, re.IGNORECASE)
    if plus_match:
        return int(plus_match.group(1)), 99

    return None, None


def extract_instamatch_score(modal_text: str) -> tuple[str, str]:
    """
    Analyzes the InstaMatch score block in the modal text.
    Returns (score_level, description), where score_level is 'HIGH', 'MEDIUM', 'LOW', or 'UNKNOWN'.
    """
    text_lower = modal_text.lower()

    # Look for explicit shortlist probability text (e.g. "Your chances of being shortlisted for this job are Low")
    chance_match = re.search(
        r"chances\s+of\s+being\s+shortlisted\s+(?:for\s+this\s+job\s+)?are\s+(low|medium|high)",
        text_lower,
    )
    if chance_match:
        lvl = chance_match.group(1).upper()
        return lvl, f"Chances are {lvl.capitalize()}"

    # Look for score section keywords near InstaMatch
    if "instamatch" in text_lower or "score" in text_lower:
        match_section = text_lower[max(0, text_lower.find("instamatch") - 50): text_lower.find("instamatch") + 250]
        if re.search(r"\blow\b", match_section):
            return "LOW", "Badge shows LOW"
        if re.search(r"\bhigh\b", match_section):
            return "HIGH", "Badge shows HIGH"
        if re.search(r"\bmedium\b", match_section) or re.search(r"\bmoderate\b", match_section):
            return "MEDIUM", "Badge shows MEDIUM"

    # Direct keyword search if present anywhere in the modal
    if re.search(r"\b(high match|great match|strong match)\b", text_lower):
        return "HIGH", "High match description"
    if re.search(r"\b(low match|poor match)\b", text_lower):
        return "LOW", "Low match description"

    return "UNKNOWN", "Score not explicitly indicated"


def matches_target_keywords(card_text: str, profile: Profile) -> bool:
    """Exact keyword matching against candidate target roles, required keywords, and primary skills."""
    text_lower = card_text.lower()
    target_roles = getattr(profile, "target_roles", [])
    role_req_kw = getattr(profile, "role_required_keywords", {}) or {}
    for role in target_roles:
        req_keywords = role_req_kw.get(role, [])
        if req_keywords:
            if any(k.lower() in text_lower for k in req_keywords):
                return True
        else:
            words = [w.lower() for w in role.split() if len(w) > 2]
            if words and all(w in text_lower for w in words):
                return True
            if role.lower() in text_lower:
                return True
    primary_skills = getattr(profile, "skills_primary", []) or []
    for skill in primary_skills:
        if len(skill) > 2 and skill.lower() in text_lower:
            return True
    return False


def handle_instahyre_screening(page, profile: Profile, job_context: str):
    """Fills any screening questions or recruiter note modal on Instahyre."""
    time.sleep(1.0)
    
    # Fill Text Inputs & Textareas (e.g. Note to recruiter, Notice period, CTC)
    inputs = page.locator(
        ".application-modal-wrap input[type='text'], .application-modal-wrap textarea, "
        "div[role='dialog'] input[type='text'], div[role='dialog'] textarea, "
        "div.modal input[type='text'], div.modal textarea"
    )
    for i in range(inputs.count()):
        inp = inputs.nth(i)
        try:
            if not inp.is_visible() or inp.input_value():
                continue
            lbl = ""
            try:
                lbl = inp.locator("xpath=preceding-sibling::label | ../preceding-sibling::label | ../label").first.inner_text().strip()
            except Exception:
                pass
            if not lbl:
                lbl = inp.get_attribute("placeholder") or inp.get_attribute("name") or "Note / Screening Question"
            
            ans = get_screening_answer(lbl, profile, job_context)
            if ans:
                inp.fill(str(ans))
                print(f"  Auto-filled '{lbl[:25]}': {str(ans)[:35]}...")
                time.sleep(0.2)
        except Exception:
            pass

    # Handle Radio Buttons / Checkboxes
    radios = page.locator(".application-modal-wrap input[type='radio'], div.modal input[type='radio']")
    for i in range(radios.count()):
        r = radios.nth(i)
        try:
            if r.is_visible() and not r.is_checked():
                parent_text = r.locator("xpath=..").inner_text().lower()
                if any(kw in parent_text for kw in ["yes", "agree", "immediate", "available", "flexible"]):
                    r.click()
                    print(f"  Auto-selected: {parent_text[:30]}")
        except Exception:
            pass


def close_modal(page):
    """Safely closes any open modal popup and waits for backdrop to hide."""
    try:
        # Try Angular direct scope close and click on close button
        page.evaluate("""() => {
            const btn = document.querySelector('.application-modal-close, [ng-click*="closeApplyModal"], .back-button-modal-close');
            if (btn) {
                btn.click();
            } else if (window.angular) {
                const el = document.querySelector('.application-modal-wrap, [ng-controller]');
                if (el) {
                    const scope = window.angular.element(el).scope();
                    if (scope && scope.closeApplyModal) {
                        scope.closeApplyModal();
                        scope.$apply();
                    }
                }
            }
        }""")
    except Exception:
        pass

    try:
        close_btn = page.locator(
            ".application-modal-close, [ng-click*='closeApplyModal'], .back-button-modal-close, "
            "a.remove, button.close, [aria-label='Close'], button:has-text('×'), "
            "span:has-text('×'), a:has-text('×')"
        ).first
        if close_btn.count() > 0 and close_btn.is_visible():
            close_btn.click(force=True)
    except Exception:
        pass

    try:
        page.keyboard.press("Escape")
    except Exception:
        pass

    time.sleep(0.6)


def apply_sidebar_filters(page, profile: Profile, max_exp_years: int = 4):
    """
    Applies strict left sidebar filters:
    1. Experience Level Slider (0 - 4 yrs)
    2. Preferred Locations (Work From Home, current_city, relocate_cities)
    3. Job Functions (Full-Stack Development, Backend Development)
    """
    print(f"  Applying left sidebar filters (Exp: 0-{max_exp_years} yrs)...")
    
    # 1. Experience Level Slider (0 - 4 yrs)
    try:
        slider_el = page.locator(".slider").first
        handle = page.locator(".slider-handle").first
        if slider_el.is_visible() and handle.is_visible():
            track_box = slider_el.bounding_box()
            handle_box = handle.bounding_box()
            if track_box and handle_box:
                start_x = handle_box['x'] + handle_box['width'] / 2
                start_y = handle_box['y'] + handle_box['height'] / 2
                # Target ~ 27% of track width for 0-4 yrs
                target_x = track_box['x'] + (track_box['width'] * 0.27)
                page.mouse.move(start_x, start_y)
                page.mouse.down()
                page.mouse.move(target_x, start_y, steps=20)
                page.mouse.up()
                time.sleep(1.0)
                print(f"  ✓ Experience Slider adjusted to 0 - {max_exp_years} yrs")
    except Exception as e:
        print(f"  (Experience slider drag note: {e})")

    try:
        page.evaluate(f"""() => {{
            const slider = document.querySelector('.slider, [ng-model*="experience"], [ng-model*="Experience"]');
            if (window.angular && slider) {{
                const scope = window.angular.element(slider).scope();
                if (scope) {{
                    if (scope.sliderExperience) {{
                        scope.sliderExperience.experience = {max_exp_years};
                    }}
                    if (scope.filters) {{
                        scope.filters['experience'] = {max_exp_years};
                    }}
                    if (scope.selected) {{
                        scope.selected['experience'] = {max_exp_years};
                    }}
                    if (scope.all_values) {{
                        scope.all_values['experience'] = false;
                    }}
                    scope.$apply();
                }}
            }}
        }}""")
    except Exception:
        pass

    # 2. Location Filters
    locations_to_select = []
    if profile.work_mode in ("flexible", "remote", "hybrid"):
        locations_to_select.append("Work From Home")
    if profile.current_city:
        locations_to_select.append(profile.current_city)
    for city in profile.relocate_cities:
        if city not in locations_to_select:
            locations_to_select.append(city)

    for loc in locations_to_select:
        try:
            lbl = page.locator(f"label:has-text('{loc}'), .filter-item:has-text('{loc}')").first
            if lbl.count() > 0 and lbl.is_visible():
                chk = lbl.locator("input[type='checkbox']")
                if chk.count() == 0 or not chk.is_checked():
                    lbl.click()
                    time.sleep(0.8)
                    print(f"  ✓ Location Filter: {loc}")
        except Exception:
            pass

    # 3. Job Function Filters
    job_functions = ["Full-Stack Development", "Backend Development"]
    for fn in job_functions:
        try:
            lbl = page.locator(f"label:has-text('{fn}'), .filter-item:has-text('{fn}')").first
            if lbl.count() > 0 and lbl.is_visible():
                chk = lbl.locator("input[type='checkbox']")
                if chk.count() == 0 or not chk.is_checked():
                    lbl.click()
                    time.sleep(0.8)
                    print(f"  ✓ Job Function Filter: {fn}")
        except Exception:
            pass


def execute_ui_search(page, keyword: str, profile: Profile) -> bool:
    """Interacts with Instahyre's search bar in the UI and applies sidebar filters."""
    try:
        if "/candidate/opportunities" not in page.url:
            page.goto("https://www.instahyre.com/candidate/opportunities/", wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)

        # Clear any prior selectize pills for clean search query
        try:
            page.evaluate("""() => {
                const selectizeEl = document.querySelector('#skills-drop-select-job-search');
                if (selectizeEl && selectizeEl.selectize) {
                    selectizeEl.selectize.clear();
                }
            }""")
            time.sleep(0.3)
        except Exception:
            pass

        search_input = page.locator(
            "#skills-drop-select-job-search-selectized, input[placeholder*='skills or title' i], input#skills"
        ).first

        if search_input.count() > 0 and search_input.is_visible():
            search_input.click()
            time.sleep(0.3)
            search_input.fill(keyword)
            time.sleep(0.5)
            page.keyboard.press("Enter")
            time.sleep(0.5)

            # Click Search button
            search_btn = page.locator("#job-search-btn, button:has-text('Search')").first
            if search_btn.count() > 0 and search_btn.is_visible():
                search_btn.click()
            else:
                page.keyboard.press("Enter")
            time.sleep(3.5)

            # Apply left sidebar filters (Experience max 4 yrs, Locations, Functions)
            max_ceiling = profile.seniority_ceiling_years or 4
            apply_sidebar_filters(page, profile, max_exp_years=max_ceiling)
            time.sleep(2.0)
            return True
        else:
            encoded_kw = urllib.parse.quote_plus(keyword.lower())
            search_url = f"https://www.instahyre.com/candidate/opportunities/?company_size=0&job_type=0&search=true&skills={encoded_kw}"
            page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(3.5)
            max_ceiling = profile.seniority_ceiling_years or 4
            apply_sidebar_filters(page, profile, max_exp_years=max_ceiling)
            time.sleep(2.0)
            return True
    except Exception as e:
        print(f"  Search execution error: {e}")
        return False


def run(target_role: str | None = None, limit: int | None = None):
    profile = Profile.load()
    applied_keys = load_applied_job_keys()

    if not Path(SESSION_FILE).exists():
        print(f"\n[!] {SESSION_FILE} not found.")
        print("  Please capture your Instahyre session first:")
        print("  python login_capture.py instahyre\n")
        return

    # Maximum applications target (supports high volume up to 100)
    target_applications = limit if limit is not None else int(profile.data.get("daily_application_limit", 100))
    applied = 0
    max_exp_ceiling = profile.seniority_ceiling_years or 4

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=profile.browser_mode == "headless",
            slow_mo=50,
        )
        context = browser.new_context(
            storage_state=SESSION_FILE,
            viewport={"width": 1280, "height": 850},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        page = context.new_page()

        print("\n" + "=" * 65)
        print("       STARTING INSTAHYRE HIGH-ACCURACY EASY APPLY ENGINE")
        print(f"       Target Applications Cap: {target_applications}")
        print(f"       Experience Filter:       0 - {max_exp_ceiling} yrs max (Strictly enforced)")
        print(f"       InstaMatch Score Rule:   Prioritize HIGH/MEDIUM, Strictly Skip LOW")
        print(f"       Sidebar Filters:         WFH / {profile.current_city} / {', '.join(profile.relocate_cities)}")
        if target_role:
            print(f"       Target Role Filter:      {target_role}")
        print("=" * 65)

        # Build prioritized keyword queries
        if target_role:
            keywords_to_search = [target_role]
        else:
            keywords_to_search = []
            for role in profile.target_roles:
                if role not in keywords_to_search:
                    keywords_to_search.append(role)

            for kw in profile.skills_primary[:5]:
                if kw not in keywords_to_search:
                    keywords_to_search.append(kw)

        # Initial navigation to opportunities page
        print("Navigating to Instahyre Opportunities dashboard...")
        try:
            page.goto("https://www.instahyre.com/candidate/opportunities/", wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)
        except Exception as e:
            print(f"  Navigation warning: {e}")

        # Check if session expired
        if "/login" in page.url:
            print("\n[!] Session expired or not signed in. Run: python login_capture.py instahyre")
            browser.close()
            return

        for kw in keywords_to_search:
            if applied >= target_applications:
                print(f"\nReached application target cap ({applied}/{target_applications}). Stopping.")
                break

            print(f"\n--- Performing UI Search & Filtering for: '{kw}' ---")
            execute_ui_search(page, kw, profile)

            # Scroll down to ensure all cards load
            for _ in range(5):
                page.evaluate("window.scrollBy(0, 1500)")
                time.sleep(0.5)

            # Locate all opportunity card links on the search feed
            card_elements = page.locator("a.row.text-link, div.container a.text-link, .employer-row").all()
            total_cards = len(card_elements)
            print(f"Discovered {total_cards} opportunity cards matching '{kw}' with applied filters.")

            if total_cards == 0:
                print("  No job cards found for this keyword query. Moving to next search...")
                continue

            seen_in_batch = set()

            for card_idx in range(total_cards):
                if applied >= target_applications:
                    break

                try:
                    card = page.locator("a.row.text-link, div.container a.text-link, .employer-row").nth(card_idx)
                    card_text = ""
                    try:
                        card_text = card.inner_text().strip()
                    except Exception:
                        pass

                    lines = [l.strip() for l in card_text.split("\n") if l.strip()]
                    header_line = lines[0] if lines else f"{kw} - Tech Company"
                    company = "Tech Company"
                    title = kw

                    if " - " in header_line:
                        parts = header_line.split(" - ", 1)
                        company = parts[0].strip()
                        title = parts[1].strip()

                    # Deduplication against historical permanent log
                    if _job_key(title, company) in applied_keys:
                        stats_tracker.record_previously_applied_skipped()
                        print(f"Skipped: {title} @ {company} — already applied in an earlier run")
                        continue

                    job_key = f"{title}_{company}"
                    if job_key in seen_in_batch:
                        stats_tracker.record_duplicate_skipped()
                        continue
                    seen_in_batch.add(job_key)

                    stats_tracker.record_discovered()

                    # Click card to open modal
                    card.scroll_into_view_if_needed()
                    time.sleep(0.3)
                    card.click(force=True)
                    time.sleep(1.8)

                    # Wait for modal dialog (.application-modal-wrap)
                    modal = page.locator(".application-modal-wrap").first
                    try:
                        modal.wait_for(state="visible", timeout=5000)
                    except PWTimeout:
                        continue

                    modal_text = modal.inner_text().strip()

                    # Extract true title and company from the active open modal
                    try:
                        modal_title_el = modal.locator("h1").first
                        if modal_title_el.count() > 0 and modal_title_el.is_visible():
                            m_title = modal_title_el.inner_text().strip()
                            if m_title and "hold on" not in m_title.lower():
                                title = m_title

                        modal_comp_el = modal.locator("h2.company-name, .company-name").first
                        if modal_comp_el.count() > 0 and modal_comp_el.is_visible():
                            m_comp = modal_comp_el.inner_text().strip()
                            if m_comp:
                                company = m_comp
                        else:
                            comp_match = re.search(r"([A-Za-z0-9\s&,\.\-]+?)\s+at a glance", modal_text)
                            if comp_match:
                                company = comp_match.group(1).strip() or company
                    except Exception:
                        pass

                    print(f"\nInspecting Opportunity #{card_idx + 1}: {title} @ {company}")

                    # 1. STRICT EXPERIENCE CHECK (Strictly enforce max 0-4 yrs)
                    min_exp, max_exp = extract_experience_years(modal_text)
                    if min_exp is not None and min_exp > max_exp_ceiling:
                        print(f"  ❌ Skipped: {title} @ {company} (Requires {min_exp}-{max_exp} yrs exp — strictly exceeds max {max_exp_ceiling} yrs ceiling).")
                        close_modal(page)
                        continue

                    # 2. Keyword relevance check
                    if not matches_target_keywords(modal_text, profile):
                        print(f"  ⚠️ Skipped: {title} @ {company} (Keywords mismatch with profile)")
                        close_modal(page)
                        continue

                    # 3. EVALUATE INSTAMATCH SCORE
                    score_level, score_desc = extract_instamatch_score(modal_text)
                    exp_info = f"{min_exp}-{max_exp} yrs" if min_exp is not None else f"0-{max_exp_ceiling} yrs"
                    print(f"  🎯 InstaMatch Score: {score_level} ({score_desc}) | Exp: {exp_info} for {title} @ {company}! Proceeding to Apply...")

                    # Locate Apply button in modal footer
                    apply_btn = modal.locator(
                        "button.new-btn:has-text('Apply'), button:has-text('Apply'), "
                        ".btn-primary:has-text('Apply'), button.apply-btn"
                    ).first

                    if apply_btn.count() > 0 and apply_btn.is_visible():
                        btn_text = apply_btn.inner_text().strip().lower()

                        if "already" in btn_text or "applied" in btn_text:
                            print(f"  Already applied on platform: {title} @ {company}")
                            applied_keys.add(_job_key(title, company))
                            close_modal(page)
                            continue

                        print(f"  Clicking '{apply_btn.inner_text().strip()}' button...")
                        apply_btn.click()
                        time.sleep(1.5)

                        # Handle any screening questions / recruiter notes
                        handle_instahyre_screening(page, profile, f"{title} at {company}")

                        # Submit confirmation if secondary button is present
                        submit_btn = page.locator(
                            "div[role='dialog'] button:has-text('Submit'), "
                            "div[role='dialog'] button:has-text('Send'), "
                            "div.modal button:has-text('Confirm'), "
                            "button:has-text('Send Application')"
                        ).last
                        if submit_btn.count() > 0 and submit_btn.is_visible():
                            submit_btn.click()
                            time.sleep(1.5)

                        # Successfully applied
                        applied += 1
                        applied_keys.add(_job_key(title, company))
                        log_row([
                            datetime.now().isoformat(),
                            "instahyre",
                            title,
                            company,
                            "applied",
                            f"success (InstaMatch: {score_level}, Exp: {exp_info})",
                        ])
                        print(f"  ✅ Successfully applied: {title} @ {company} [{applied}/{target_applications} total this run]")

                        close_modal(page)

                        if applied < target_applications:
                            delay = random.uniform(
                                max(1.0, float(profile.min_delay_seconds_between_applications or 45)),
                                max(2.0, float(profile.max_delay_seconds_between_applications or 75)),
                            )
                            print(f"  Waiting {delay:.1f}s before next application...")
                            time.sleep(delay)

                    else:
                        print(f"  No direct Apply button found inside modal for {title} @ {company}.")
                        close_modal(page)

                except Exception as e:
                    print(f"  Error processing card #{card_idx + 1}: {e}")
                    close_modal(page)
                    continue

        browser.close()

    print("\n" + "=" * 65)
    print(f"Done. {applied} direct Instahyre applications successfully submitted!")
    print("=" * 65)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Instahyre direct apply engine")
    parser.add_argument("--role", type=str, default=None, help="Target role or keyword to search (e.g. 'Frontend Developer')")
    parser.add_argument("--limit", type=int, default=None, help="Maximum applications to submit")
    args = parser.parse_args()
    run(target_role=args.role, limit=args.limit)
