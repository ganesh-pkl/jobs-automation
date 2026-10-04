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
MAX_NOTE_LENGTH = 200


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


def build_recruiter_connection_note(recruiter_name: str, job_title: str, company: str, profile: Profile) -> str:
    """
    Builds a personalized LinkedIn connection note strictly <= 200 characters.
    """
    raw_name = (recruiter_name or "").strip()
    clean_name = re.sub(r"^(?:mr\.|ms\.|dr\.|mrs\.)\s*", "", raw_name, flags=re.I)
    first_name = clean_name.split()[0] if clean_name else "there"
    first_name = re.sub(r"[^a-zA-Z]", "", first_name) or "there"

    clean_role = re.sub(r"\s*\(.*?\)", "", job_title or "the role").strip()
    clean_role = re.sub(r"(?:senior|sr\.?|lead|staff)\s+", "", clean_role, flags=re.I).strip()
    clean_company = re.sub(r"\s*(?:ltd|inc|pvt|llc|corporation|technologies|services)\.?$", "", company or "", flags=re.I).strip()

    if len(clean_role) > 24:
        clean_role = clean_role[:22] + ".."
    if len(clean_company) > 18:
        clean_company = clean_company[:16] + ".."

    candidate_name = profile.data.get("first_name", "Ganesh")

    template = f"Hi {first_name}, I applied for {clean_role} at {clean_company}! With 3y in Full-Stack (React, Node, Java, AI), I'd love to connect & share how I can contribute. Best, {candidate_name}"

    if len(template) <= MAX_NOTE_LENGTH:
        return template

    compact = f"Hi {first_name}, I applied for {clean_role} at {clean_company}! With 3y Full-Stack exp (React, Node, Java, AI), I'd love to connect and discuss the role. - {candidate_name}"
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
    Sends a connection request with a personalized <=200 character note to the recruiter.
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

        time.sleep(random.uniform(1.2, 2.0))

        modal_handled = page.evaluate("""([noteText]) => {
            const modal = document.querySelector('dialog, [role="dialog"], .send-invite, .artdeco-modal');
            if (!modal) return false;

            const addNoteBtn = Array.from(modal.querySelectorAll('button')).find(b => {
                const t = (b.innerText || b.getAttribute('aria-label') || '').toLowerCase();
                return t.includes('add a note') || t.includes('add note');
            });
            if (addNoteBtn) {
                addNoteBtn.click();
            }

            return true;
        }""", [note])

        time.sleep(random.uniform(0.8, 1.4))

        textarea_filled = page.evaluate("""([noteText]) => {
            const modal = document.querySelector('dialog, [role="dialog"], .send-invite, .artdeco-modal') || document;
            const textarea = modal.querySelector('textarea[name="message"], textarea#custom-message, textarea');
            if (!textarea) return false;

            textarea.focus();
            let proto = window.HTMLTextAreaElement.prototype;
            const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
            setter.call(textarea, noteText);
            textarea.dispatchEvent(new Event('input', {bubbles: true}));
            textarea.dispatchEvent(new Event('change', {bubbles: true}));
            return true;
        }""", [note])

        time.sleep(random.uniform(0.6, 1.2))

        send_clicked = page.evaluate("""() => {
            const modal = document.querySelector('dialog, [role="dialog"], .send-invite, .artdeco-modal') || document;
            const sendBtn = Array.from(modal.querySelectorAll('button')).find(b => {
                const t = (b.innerText || b.getAttribute('aria-label') || '').toLowerCase();
                return (t.includes('send') || t.includes('done')) && !t.includes('without');
            });
            if (sendBtn) {
                sendBtn.click();
                return true;
            }
            return false;
        }""")

        if send_clicked:
            time.sleep(random.uniform(1.5, 2.5))
            log_connection_request(recruiter_name, clean_url, job_title, company, note, "sent")
            print(f"  [Recruiter Connect] Successfully sent personalized connection request to {recruiter_name}!")
            return True

    except Exception as e:
        print(f"  [Recruiter Connect] Error sending connection request: {e}")

    return False
