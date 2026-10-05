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


def get_relevant_skills_snippet(job_title: str) -> str:
    """
    Dynamically selects the most relevant core skills from candidate profile based on target role.
    """
    title_lower = (job_title or "").lower()
    if any(k in title_lower for k in ["python", "fastapi", "django", "flask"]):
        return "Python, FastAPI, React, AI/LLMs"
    elif any(k in title_lower for k in ["java", "spring", "spring boot"]):
        return "Java, Spring Boot, React, Node.js"
    elif any(k in title_lower for k in ["frontend", "react", "next", "ui developer"]):
        return "React, Next.js, TypeScript, Node.js"
    elif any(k in title_lower for k in ["backend", "node", "express"]):
        return "Node.js, Express, Java, REST APIs"
    elif any(k in title_lower for k in ["ai", "llm", "agentic", "generative"]):
        return "AI/LLMs, LangChain, Python, Full-Stack"
    else:
        return "React, Node.js, Java, AI/LLMs"


def build_recruiter_connection_note(recruiter_name: str, job_title: str, company: str, profile: Profile) -> str:
    """
    Builds a personalized LinkedIn connection note strictly <= 300 characters.
    Dynamically extracts recruiter name, target company, role, and custom skill highlights.
    """
    raw_name = (recruiter_name or "").strip()
    clean_name = re.sub(r"^(?:mr\.|ms\.|dr\.|mrs\.)\s*", "", raw_name, flags=re.I)
    first_name = clean_name.split()[0] if clean_name else "there"
    first_name = re.sub(r"[^a-zA-Z]", "", first_name) or "there"

    clean_role = re.sub(r"\s*\(.*?\)", "", job_title or "the role").strip()
    clean_company = re.sub(r"\s*(?:ltd|inc|pvt|llc|corporation|technologies|services)\.?$", "", company or "", flags=re.I).strip()

    candidate_name = profile.data.get("first_name", "Ganesh")
    skills_snippet = get_relevant_skills_snippet(job_title)

    # Primary comprehensive template (< 300 chars)
    template = f"Hi {first_name}, I noticed your opening for {clean_role} at {clean_company} and applied! I bring 3 years of full-stack experience ({skills_snippet}). Would love to connect and share how I can contribute to your team. Best, {candidate_name}"

    if len(template) <= MAX_NOTE_LENGTH:
        return template

    # Compact fallback (< 300 chars)
    compact = f"Hi {first_name}, I applied for {clean_role} at {clean_company}! With 3y in Full-Stack ({skills_snippet}), I'd love to connect & share how I can contribute. Best, {candidate_name}"
    if len(compact) <= MAX_NOTE_LENGTH:
        return compact

    return compact[:MAX_NOTE_LENGTH]


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


def send_recruiter_connection_request(page, recruiter_info: dict, job_title: str, company: str, profile: Profile) -> bool:
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

    note = build_recruiter_connection_note(recruiter_name, job_title, company, profile)
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
    """Helper to click 'Add a note', type note, and click 'Send' (strictly preventing 'Send without a note')."""
    try:
        time.sleep(2.0)
        # Step 1: Click "Add a note" button in initial invitation modal
        add_note_btn = page.locator('button:has-text("Add a note"), button[aria-label*="Add a note" i]').first
        if add_note_btn.count() > 0:
            add_note_btn.click()
            time.sleep(1.2)
        else:
            page.evaluate("""() => {
                const btns = Array.from(document.querySelectorAll('button, a[role="button"]')).filter(b => b.getBoundingClientRect().width > 0);
                for (const b of btns) {
                    const t = (b.innerText || b.getAttribute('aria-label') || '').toLowerCase();
                    if (t.includes('add a note') || t.includes('add note')) {
                        b.click();
                        return true;
                    }
                }
                return false;
            }""")
            time.sleep(1.2)

        # Step 2: Ensure textarea is present and type the note
        textarea = page.locator('textarea[name="message"], textarea#custom-message, .send-invite textarea, textarea').first
        if textarea.count() > 0:
            textarea.wait_for(state="visible", timeout=3000)
            textarea.click()
            time.sleep(0.2)
            textarea.fill(note)
            time.sleep(0.8)
        else:
            print("  [Recruiter Connect] ❌ Error: Note textarea not found; aborting to prevent sending without note.")
            return False

        if dry_run:
            print(f"  [Recruiter Connect] Dry-run mode: Note typed ({len(note)} chars) in modal.")
            return True

        # Step 3: Click real Send button (STRICTLY excluding 'Send without a note')
        send_btn = page.locator('dialog button, [role="dialog"] button, .artdeco-modal button').filter(has_text=re.compile(r'^(?:Send|Done)$', re.I)).filter(has_not_text=re.compile(r'without', re.I)).first
        if send_btn.count() == 0:
            send_btn = page.locator('button[aria-label*="Send invitation" i], button[aria-label*="Send now" i], button:has-text("Send")').filter(has_not_text=re.compile(r'without', re.I)).first

        if send_btn.count() > 0:
            send_btn.click()
            time.sleep(2.5)
            return True

        # JS fallback for Send button excluding without
        clicked_send = page.evaluate("""() => {
            const btns = Array.from(document.querySelectorAll('button')).filter(b => b.getBoundingClientRect().width > 0);
            for (const b of btns) {
                const t = (b.innerText || b.getAttribute('aria-label') || '').trim().toLowerCase();
                if ((t === 'send' || t === 'done' || t === 'send now') && !t.includes('without')) {
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
        time.sleep(3.5)

        # 1. Follow recruiter if not already followed
        follow_recruiter(tab)
        time.sleep(1.0)

        # 2. Check direct Connect or More actions menu on top card
        result = tab.evaluate("""() => {
            const topCard = document.querySelector('main section, [data-view-name*="profile-card"], .pv-top-card') || document.querySelector('main') || document;
            const btns = Array.from(topCard.querySelectorAll('button, a[role="button"]')).filter(b => !b.closest('aside') && b.getBoundingClientRect().width > 0);
            
            let connectBtn = null;
            let moreBtn = null;
            for (const b of btns) {
                const t = (b.innerText || '').trim().toLowerCase();
                const aria = (b.getAttribute('aria-label') || '').trim().toLowerCase();
                if (t === 'connect') {
                    connectBtn = b;
                }
                if (aria === 'more' || aria === 'more actions' || t === 'more') {
                    moreBtn = b;
                }
            }
            
            if (connectBtn) {
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
            tab.evaluate("""() => {
                const items = Array.from(document.querySelectorAll('[role="menu"] [role="menuitem"], [role="menu"] a, .artdeco-dropdown__content [role="menuitem"], .artdeco-dropdown__content a, .artdeco-dropdown__content button'));
                for (const item of items) {
                    const t = (item.innerText || '').toLowerCase();
                    const key = (item.getAttribute('componentkey') || '').toLowerCase();
                    if (t.includes('connect') || key.includes('connect')) {
                        item.click();
                        return true;
                    }
                }
                return false;
            }""")
            time.sleep(2.0)

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

