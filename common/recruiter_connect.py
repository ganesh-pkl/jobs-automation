"""
LinkedIn Recruiter & Hiring Manager Outreach Automation.
Detects job posters/hiring team members, drafts personalized <200 character notes,
and sends connection requests with daily limit safety.
"""
import csv
import re
import time
import random
from datetime import datetime, date
from pathlib import Path

from common.profile import Profile
from common.stealth import (
    human_move_and_click,
    human_type,
    human_scroll,
    human_dwell,
    check_linkedin_restrictions,
)

CONNECTION_LOG_FILE = "connection_requests_log.csv"
MAX_NOTE_LENGTH = 300


def count_connections_today(today_date: date | None = None) -> int:
    """Returns the number of connection requests sent today."""
    if today_date is None:
        today_date = date.today()
    log_path = Path(CONNECTION_LOG_FILE)
    if not log_path.exists():
        return 0
    count = 0
    try:
        with open(log_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("status") == "sent":
                    ts_str = row.get("timestamp") or ""
                    try:
                        row_date = datetime.fromisoformat(ts_str.replace(" ", "T")).date()
                    except Exception:
                        row_date = today_date
                    if row_date == today_date:
                        count += 1
    except Exception:
        pass
    return count


def load_contacted_recruiters() -> set[str]:
    """Returns a set of normalized recruiter profile URLs and names already contacted."""
    log_path = Path(CONNECTION_LOG_FILE)
    if not log_path.exists():
        return set()
    contacted = set()
    try:
        with open(log_path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                url = (row.get("recruiter_profile_url") or "").strip().lower()
                name = (row.get("recruiter_name") or "").strip().lower()
                company = (row.get("company") or "").strip().lower()
                if url:
                    clean_url = url.split("?")[0].rstrip("/")
                    contacted.add(clean_url)
                if name and company:
                    contacted.add(f"{name}@{company}")
    except Exception:
        pass
    return contacted


def log_connection_request(recruiter_name: str, recruiter_profile_url: str, job_title: str, company: str, note: str, status: str):
    """Appends outreach attempt to connection_requests_log.csv."""
    new_file = not Path(CONNECTION_LOG_FILE).exists()
    if new_file:
        Path(CONNECTION_LOG_FILE).touch(mode=0o600)
    with open(CONNECTION_LOG_FILE, "a", newline="", encoding="utf-8") as f:
        try:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        except Exception:
            pass
        try:
            w = csv.writer(f)
            if new_file and f.tell() == 0:
                w.writerow(["timestamp", "recruiter_name", "recruiter_profile_url", "job_title", "company", "note", "status"])
            w.writerow([datetime.now().isoformat(), recruiter_name, recruiter_profile_url, job_title, company, note, status])
            f.flush()
        finally:
            try:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass


def follow_recruiter(page_or_tab) -> bool:
    """
    Clicks the '+ Follow' button on a recruiter card or profile if available and not already followed.
    Following increases visibility so the recruiter sees your activity and note.
    """
    try:
        followed = page_or_tab.evaluate("""() => {
            const btns = Array.from(document.querySelectorAll('button, a[role="button"]')).filter(b => b.getBoundingClientRect().width > 0);
            for (const b of btns) {
                const text = (b.innerText || '').trim().toLowerCase();
                const aria = (b.getAttribute('aria-label') || '').trim().toLowerCase();
                const isFollow = (text === 'follow' || text === '+ follow' || text === '+follow' || (aria.startsWith('follow ') && !aria.startsWith('following')));
                const isAlreadyFollowing = text.includes('following') || aria.includes('following') || text.includes('unfollow');
                if (isFollow && !isAlreadyFollowing) {
                    b.click();
                    return true;
                }
            }
            return false;
        }""")
        if followed:
            time.sleep(1.0)
            print("  [Recruiter Connect] 👤 Followed recruiter on LinkedIn.")
            return True
    except Exception as e:
        print(f"  [Recruiter Connect] Follow note: {e}")
    return False


def clean_role_title(job_title: str) -> str:
    """Cleans role title for natural insertion in connection notes."""
    if not job_title:
        return "the role"
    clean = re.sub(r"\(.*?\)|\[.*?\]", "", job_title)
    clean = re.sub(
        r"\s*-\s*(?:pan india|remote|hybrid|immediate joiner|urgent|hiring|3-5\s*yrs?|\d+\+?\s*yrs?).*",
        "",
        clean,
        flags=re.I,
    )
    clean = re.sub(r"\s+", " ", clean).strip()
    if len(clean) > 36:
        clean = clean[:33].rstrip() + "..."
    return clean or "the role"


def clean_company_name(company: str) -> str:
    """Cleans company name by stripping legal suffixes and excess noise."""
    if not company:
        return ""
    clean = re.sub(
        r"\s*(?:ltd|inc|pvt|llc|corporation|technologies|services|solutions|consulting|private limited|india private limited)\.?$",
        "",
        company,
        flags=re.I,
    ).strip()
    clean = re.sub(r"\s+", " ", clean).strip()
    if len(clean) > 25:
        clean = clean[:22].rstrip() + "..."
    return clean


def get_role_specialization_and_pitch(job_title: str, job_description: str = "") -> tuple[str, str, str]:
    """
    Dynamically selects candidate domain specialization, pitch, and skills snippet
    based on the target job title and JD keywords.
    Returns: (role_spec, pitch, skills_snippet)
    """
    text = f"{job_title or ''} {job_description or ''}".lower()
    title_lower = (job_title or "").lower()

    # 1. AI / LLM / GenAI / Machine Learning (Top Priority for AI roles)
    if any(k in text for k in [
        "ai engineer", "ai developer", "llm", "genai", "generative ai", "generative",
        "agentic", "langchain", "rag", "prompt engineer", "machine learning",
        "deep learning", "nlp", "artificial intelligence"
    ]):
        return (
            "AI & Full-Stack",
            "building AI/LLM pipelines (LangChain, RAG, Python/FastAPI)",
            "AI/LLMs, LangChain, RAG, Python/FastAPI"
        )

    # 2. Full-Stack / MERN / General Full-Stack roles
    if any(k in title_lower for k in ["full stack", "fullstack", "full-stack", "mern", "mean"]) or (
        "full stack" in text and ("node" in text or "python" in text or "backend" in text)
    ):
        return (
            "Full-Stack",
            "Full-Stack development (React, Node.js, Python/Java & APIs)",
            "React, Node.js, Python, Java, REST APIs"
        )

    # 3. Java / Spring Boot / Backend
    if any(k in text for k in ["java", "spring boot", "springboot", "spring mvc", "j2ee", "hibernate", "microservices"]):
        return (
            "Java Backend",
            "Java, Spring Boot microservices & REST APIs",
            "Java, Spring Boot, Microservices, REST APIs"
        )

    # 4. Python / FastAPI / Django / Backend
    if any(k in text for k in ["fastapi", "django", "flask", "python developer", "python backend", "python engineer"]):
        return (
            "Python Backend",
            "scalable Python/FastAPI backends & cloud APIs",
            "Python, FastAPI, Django, REST APIs"
        )

    # 5. Frontend / React / Next.js / UI
    if any(k in text for k in ["frontend", "front end", "front-end", "react", "next.js", "nextjs", "typescript", "ui developer", "ui engineer", "angular"]):
        return (
            "Frontend",
            "React, Next.js, TypeScript & modern UI engineering",
            "React, Next.js, TypeScript, UI/UX"
        )

    # 6. Node.js / Express / Backend
    if any(k in text for k in ["node.js", "nodejs", "node developer", "express.js", "express", "backend engineer", "backend developer"]):
        return (
            "Backend",
            "Node.js, Express, microservices & scalable backends",
            "Node.js, Express, REST APIs, Microservices"
        )

    # 7. Default: Full-Stack / Software Engineer
    return (
        "Full-Stack",
        "Full-Stack development (React, Node.js, Python & APIs)",
        "React, Node.js, Python, Spring Boot"
    )


def get_relevant_skills_snippet(job_title: str, job_description: str = "") -> str:
    """Backward compatibility helper returning relevant skills snippet."""
    _, _, snippet = get_role_specialization_and_pitch(job_title, job_description)
    return snippet


def build_recruiter_connection_note(recruiter_name: str, job_title: str, company: str, profile: Profile, job_description: str = "") -> str:
    """
    Builds a personalized LinkedIn connection note strictly <= 300 characters.
    Dynamically customizes the pitch to match the job role/JD (AI/LLM, Java, Python, React, Full-Stack).
    """
    raw_name = (recruiter_name or "").strip()
    clean_name = re.sub(r"^(?:mr\.|ms\.|dr\.|mrs\.)\s*", "", raw_name, flags=re.I)
    first_name = clean_name.split()[0] if clean_name else "there"
    first_name = re.sub(r"[^a-zA-Z]", "", first_name) or "there"

    role = clean_role_title(job_title)
    comp = clean_company_name(company)
    candidate_name = profile.data.get("first_name", "Ganesh")

    role_spec, pitch, skills_snippet = get_role_specialization_and_pitch(job_title, job_description)
    comp_phrase = f" at {comp}" if comp else ""

    # Primary comprehensive template (< 300 chars)
    template = f"Hi {first_name}, I noticed your opening for {role}{comp_phrase} and applied! With 3y exp in {pitch}, I'd love to connect & share how I can contribute to your team. Best, {candidate_name}"
    if len(template) <= MAX_NOTE_LENGTH:
        return template

    # Compact template 1
    compact1 = f"Hi {first_name}, I applied for {role}{comp_phrase}! With 3y exp in {pitch}, I'd love to connect & share how I can add value. Best, {candidate_name}"
    if len(compact1) <= MAX_NOTE_LENGTH:
        return compact1

    # Compact template 2
    compact2 = f"Hi {first_name}, I applied for {role}{comp_phrase}! With 3y in {role_spec} ({skills_snippet}), I'd love to connect & discuss how I can contribute. Best, {candidate_name}"
    if len(compact2) <= MAX_NOTE_LENGTH:
        return compact2

    # Ultra-compact fallback
    fallback = f"Hi {first_name}, I applied for {role}{comp_phrase}! Bringing 3y exp in {skills_snippet}. Would love to connect. Best, {candidate_name}"
    return fallback[:MAX_NOTE_LENGTH]


def extract_hiring_manager(page) -> dict | None:
    """
    Scans the current LinkedIn job detail page for a Job Poster / Hiring Team card.
    """
    try:
        return page.evaluate("""() => {
            const posterEl = document.querySelector(
                '.jobs-poster, [class*="hirer-card"], [class*="hiring-team"], ' +
                '.jobs-details-premium-features-hiring-team, [class*="meet-the-hiring-team"], ' +
                '.jobs-poster__name, div[data-view-name*="job-details-hiring-team"]'
            );
            if (!posterEl) return null;

            const container = posterEl.closest('.jobs-poster, [class*="hirer-card"], [class*="hiring-team"], .artdeco-card, section') || posterEl;
            
            const nameEl = container.querySelector(
                '.jobs-poster__name, [class*="hirer-card"] h3, [class*="hirer-card"] a, ' +
                'a[href*="/in/"] strong, a[href*="/in/"] span'
            );
            const name = (nameEl ? nameEl.innerText : '').trim();
            if (!name) return null;

            const linkEl = container.querySelector('a[href*="/in/"]');
            const profileUrl = linkEl ? linkEl.href.split('?')[0] : '';

            const headlineEl = container.querySelector('.jobs-poster__headline, [class*="hirer-card"] p, [class*="headline"]');
            const headline = (headlineEl ? headlineEl.innerText : '').trim();

            const buttons = Array.from(container.querySelectorAll('button, a[role="button"]'));
            const connectBtn = buttons.find(b => {
                const t = (b.innerText || b.getAttribute('aria-label') || '').toLowerCase();
                return t.includes('connect') || t.includes('invite');
            });

            return {
                name: name,
                headline: headline,
                profileUrl: profileUrl,
                hasConnectButton: !!connectBtn
            };
        }""")
    except Exception:
        return None


def send_recruiter_connection_request(page, recruiter_info: dict, job_title: str, company: str, profile: Profile, job_description: str = "") -> bool:
    """
    Sends a connection request with a personalized <=300 character note to the recruiter.
    Also follows the recruiter to maximize visibility and connection acceptance.
    """
    if not recruiter_info or not recruiter_info.get("name"):
        return False

    recruiter_name = recruiter_info["name"]
    profile_url = (recruiter_info.get("profileUrl") or "").strip()
    clean_url = profile_url.split("?")[0].rstrip("/") if profile_url else ""

    contacted = load_contacted_recruiters()
    if clean_url and clean_url in contacted:
        print(f"  [Recruiter Connect] Already contacted {recruiter_name} ({clean_url}) — skipping")
        return False
    if f"{recruiter_name.lower()}@{company.lower()}" in contacted:
        print(f"  [Recruiter Connect] Already contacted {recruiter_name} @ {company} — skipping")
        return False

    max_daily = int(profile.data.get("max_connection_requests_per_day", 10))
    sent_today = count_connections_today()
    if sent_today >= max_daily:
        print(f"  [Recruiter Connect] Reached daily connection request limit ({sent_today}/{max_daily}) — skipping")
        return False

    if not job_description and page:
        try:
            job_description = page.evaluate("() => (document.querySelector('.jobs-description, #job-details, .job-details-module')?.innerText || '').slice(0, 1000)")
        except Exception:
            job_description = ""

    note = build_recruiter_connection_note(recruiter_name, job_title, company, profile, job_description=job_description)
    print(f"  [Recruiter Connect] Outreach to {recruiter_name} ({len(note)} chars): \"{note}\"")

    try:
        # Step 1: Follow recruiter on job card if button is present
        follow_recruiter(page)

        # Step 2: Check if direct Connect button exists on card
        connect_clicked = page.evaluate("""() => {
            const posterEl = document.querySelector(
                '.jobs-poster, [class*="hirer-card"], [class*="hiring-team"], ' +
                '.jobs-details-premium-features-hiring-team, [class*="meet-the-hiring-team"]'
            );
            if (!posterEl) return false;
            const container = posterEl.closest('.jobs-poster, [class*="hirer-card"], [class*="hiring-team"], .artdeco-card, section') || posterEl;
            const buttons = Array.from(container.querySelectorAll('button, a[role="button"]'));
            const connectBtn = buttons.find(b => {
                const t = (b.innerText || b.getAttribute('aria-label') || '').toLowerCase();
                return t.includes('connect') || t.includes('invite');
            });
            if (connectBtn) {
                connectBtn.click();
                return true;
            }
            return false;
        }""")

        if connect_clicked:
            time.sleep(random.uniform(1.2, 2.0))
            if _handle_invitation_modal(page, note):
                log_connection_request(recruiter_name, clean_url, job_title, company, note, "sent")
                print(f"  [Recruiter Connect] Successfully sent personalized connection request to {recruiter_name}!")
                return True

        # If card had 'Message' or no direct Connect, open recruiter profile in a new tab/page
        if clean_url:
            print(f"  [Recruiter Connect] Card shows 'Message' / non-connect. Opening profile: {clean_url}")
            sent = _send_connection_via_profile_tab(page.context, clean_url, note)
            if sent:
                log_connection_request(recruiter_name, clean_url, job_title, company, note, "sent")
                print(f"  [Recruiter Connect] Successfully sent connection request via profile to {recruiter_name}!")
                return True

    except Exception as e:
        print(f"  [Recruiter Connect] Error sending connection request: {e}")

    return False


def _handle_invitation_modal(page, note: str, dry_run: bool = False) -> bool:
    """Helper to click 'Add a note', type note humanly, and click 'Send' (strictly preventing 'Send without a note')."""
    try:
        time.sleep(1.5)

        # Step 0: Check for active restrictions / limits
        is_restricted, reason = check_linkedin_restrictions(page)
        if is_restricted:
            print(f"  [Recruiter Connect] ⚠️ {reason} — stopping outreach immediately.")
            return False

        # Step 0b: Check for intermediate "How do you know" dialog inside an actual dialog container
        page.evaluate("""() => {
            const modals = Array.from(document.querySelectorAll('dialog, [role="dialog"], .send-invite, .artdeco-modal, div[data-view-name*="send-invite"]'));
            for (const modal of modals) {
                const text = (modal.innerText || '').toLowerCase();
                if (text.includes('how do you know') || text.includes('we don\\'t know') || text.includes('connect with') || text.includes('help us keep linkedin')) {
                    const otherOption = Array.from(modal.querySelectorAll('button, input[type="radio"], label, [role="radio"]')).find(el => {
                        const t = (el.innerText || el.getAttribute('aria-label') || el.value || '').toLowerCase();
                        return t.includes('other') || t.includes('we don\\'t know');
                    });
                    if (otherOption) {
                        otherOption.click();
                        const connectBtn = Array.from(modal.querySelectorAll('button')).find(b => {
                            const t = (b.innerText || b.getAttribute('aria-label') || '').toLowerCase().trim();
                            return t === 'connect' || t === 'next' || t === 'continue';
                        });
                        if (connectBtn) {
                            connectBtn.click();
                        }
                    }
                }
            }
        }""")
        time.sleep(1.2)

        # Step 1: Check if textarea is already open; if not, click "Add a note" button
        textarea_loc = page.locator('textarea[name="message"], textarea#custom-message, [role="dialog"] textarea, .send-invite textarea, textarea').first
        if textarea_loc.count() == 0 or not textarea_loc.is_visible():
            for _ in range(5):
                add_note_btn = page.locator('button:has-text("Add a note"), button[aria-label*="Add a note" i], button[aria-label*="Add note" i]').first
                if add_note_btn.count() > 0 and add_note_btn.is_visible():
                    human_move_and_click(page, add_note_btn, delay_after=1.2)
                    break
                else:
                    clicked = page.evaluate("""() => {
                        const btns = Array.from(document.querySelectorAll('dialog button, [role="dialog"] button, .artdeco-modal button, button, a[role="button"]')).filter(b => b.getBoundingClientRect().width > 0);
                        for (const b of btns) {
                            const t = (b.innerText || b.getAttribute('aria-label') || '').toLowerCase();
                            if (t.includes('add a note') || t.includes('add note')) {
                                b.click();
                                return true;
                            }
                        }
                        return false;
                    }""")
                    if clicked:
                        time.sleep(1.2)
                        break
                time.sleep(0.6)

        # Step 2: Ensure textarea is present and type the note with natural human cadence
        textarea = None
        for _ in range(6):
            loc = page.locator('textarea[name="message"], textarea#custom-message, [role="dialog"] textarea, .send-invite textarea, textarea').first
            if loc.count() > 0 and loc.is_visible():
                textarea = loc
                break
            time.sleep(0.6)

        if textarea is not None:
            # Human-like typing with randomized cadence and pause
            human_type(page, textarea, note, min_delay_ms=25, max_delay_ms=75)
            time.sleep(0.3)
            # Dispatch synthetic events to ensure character counter and send button update in React
            page.evaluate("""(text) => {
                const ta = document.querySelector('textarea[name="message"], textarea#custom-message, [role="dialog"] textarea, .send-invite textarea, textarea');
                if (ta) {
                    ta.value = text;
                    ta.dispatchEvent(new Event('input', { bubbles: true }));
                    ta.dispatchEvent(new Event('change', { bubbles: true }));
                }
            }""", note)
            time.sleep(0.8)
        else:
            print("  [Recruiter Connect] ❌ Error: Note textarea not found; aborting to prevent sending without note.")
            return False

        if dry_run:
            print(f"  [Recruiter Connect] Dry-run mode: Note typed ({len(note)} chars) in modal.")
            return True

        # Step 3: Click real Send button (STRICTLY excluding 'Send without a note')
        send_btn = page.locator('dialog button, [role="dialog"] button, .artdeco-modal button').filter(has_text=re.compile(r'^(?:Send|Done|Send now)$', re.I)).filter(has_not_text=re.compile(r'without', re.I)).first
        if send_btn.count() == 0:
            send_btn = page.locator('button[aria-label*="Send invitation" i], button[aria-label*="Send now" i], button:has-text("Send")').filter(has_not_text=re.compile(r'without', re.I)).first

        if send_btn.count() > 0 and send_btn.is_visible():
            human_move_and_click(page, send_btn, delay_after=2.5)
            return True

        # JS fallback for Send button excluding without
        clicked_send = page.evaluate("""() => {
            const btns = Array.from(document.querySelectorAll('dialog button, [role="dialog"] button, .artdeco-modal button, button')).filter(b => b.getBoundingClientRect().width > 0);
            for (const b of btns) {
                const t = (b.innerText || b.getAttribute('aria-label') || '').trim().toLowerCase();
                if ((t === 'send' || t === 'done' || t === 'send now' || t.includes('send invitation')) && !t.includes('without')) {
                    b.click();
                    return true;
                }
            }
            return false;
        }""")
        if clicked_send:
            time.sleep(2.5)
            return True

        print("  [Recruiter Connect] ❌ Error: Real Send button not found after typing note.")
        return False
    except Exception as e:
        print(f"  [Recruiter Connect] Modal error: {e}")
        return False


def _send_connection_via_profile_tab(context, profile_url: str, note: str, dry_run: bool = False) -> bool:
    """Opens profile in a new tab, clicks Connect (or More -> Connect), and sends note."""
    tab = None
    try:
        tab = context.new_page()
        tab.goto(profile_url, wait_until="domcontentloaded")
        time.sleep(random.uniform(2.5, 4.0))

        # Check restrictions on tab
        is_restricted, reason = check_linkedin_restrictions(tab)
        if is_restricted:
            print(f"  [Recruiter Connect] ⚠️ {reason} — aborting profile tab.")
            return False

        # 1. Follow recruiter if not already followed
        follow_recruiter(tab)
        time.sleep(1.0)

        # 2. Check direct Connect or More actions menu on top card
        result = tab.evaluate("""() => {
            const topCard = document.querySelector('main section, [data-view-name*="profile-card"], .pv-top-card, .scaffold-layout__main') || document.querySelector('main') || document;
            const btns = Array.from(topCard.querySelectorAll('button, a[role="button"]')).filter(b => !b.closest('aside') && b.getBoundingClientRect().width > 0);
            
            let connectBtn = null;
            let moreBtn = null;
            for (const b of btns) {
                const t = (b.innerText || '').trim().toLowerCase();
                const aria = (b.getAttribute('aria-label') || '').trim().toLowerCase();
                const isConnect = (t === 'connect' || t === '+ connect' || t === '+connect' || t.includes('connect') || aria.includes('connect') || aria.includes('invite')) &&
                                  !t.includes('pending') && !aria.includes('pending') && !t.includes('remove') && !t.includes('withdraw') && !t.includes('message') && !t.includes('following');
                if (isConnect && !connectBtn) {
                    connectBtn = b;
                }
                if (aria === 'more' || aria === 'more actions' || t === 'more' || aria.includes('more actions') || t === '…') {
                    moreBtn = b;
                }
            }
            
            if (connectBtn) {
                connectBtn.scrollIntoView({behavior: 'instant', block: 'center'});
                connectBtn.click();
                return 'clicked_direct_connect';
            } else if (moreBtn) {
                moreBtn.click();
                return 'clicked_more';
            }
            return 'none_found';
        }""")

        if result == 'clicked_more':
            time.sleep(1.5)
            # Find "Connect" inside role="menu" or dropdown
            menu_clicked = tab.evaluate("""() => {
                const items = Array.from(document.querySelectorAll('[role="menu"] [role="menuitem"], [role="menu"] a, .artdeco-dropdown__content [role="menuitem"], .artdeco-dropdown__content a, .artdeco-dropdown__content button, [class*="dropdown-item"]'));
                for (const item of items) {
                    const t = (item.innerText || '').toLowerCase();
                    const key = (item.getAttribute('componentkey') || '').toLowerCase();
                    const aria = (item.getAttribute('aria-label') || '').toLowerCase();
                    if ((t.includes('connect') || key.includes('connect') || aria.includes('connect') || t.includes('invite') || aria.includes('invite')) && !t.includes('remove') && !t.includes('pending')) {
                        item.click();
                        return true;
                    }
                }
                return false;
            }""")
            if not menu_clicked:
                try:
                    tab.keyboard.press("Escape")
                except Exception:
                    pass
            time.sleep(1.8)

        return _handle_invitation_modal(tab, note, dry_run=dry_run)
    except Exception as e:
        print(f"  [Recruiter Connect] Profile tab error: {e}")
        return False
    finally:
        if tab and not dry_run:
            try:
                tab.close()
            except Exception:
                pass

