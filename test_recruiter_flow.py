"""
Standalone tester for LinkedIn Recruiter / Hiring Manager Connection flow.

Usage:
    # 1. Test note generation logic across sample recruiters
    python test_recruiter_flow.py --preview-notes

    # 2. Test live browser interaction on a specific LinkedIn job URL (Visible browser)
    python test_recruiter_flow.py --url "https://www.linkedin.com/jobs/view/4471591805/"

    # 3. Test on first matching job from your search preferences
    python test_recruiter_flow.py --search
"""
import sys
import time
import argparse
from pathlib import Path
from playwright.sync_api import sync_playwright

from common.profile import Profile
from common.recruiter_connect import (
    build_recruiter_connection_note,
    extract_hiring_manager,
    send_recruiter_connection_request,
    log_connection_request,
    follow_recruiter,
    MAX_NOTE_LENGTH,
    count_connections_today,
)

SESSION_FILE = "session_linkedin.json"


def test_preview_notes(profile: Profile):
    print("\n" + "=" * 60)
    print("RECRUITER NOTE GENERATION PREVIEW (<= 300 CHARS)")
    print("=" * 60)

    sample_cases = [
        ("Sankalp Sharma", "Founder & Recruiter", "Full Stack Developer", "Crossing Hurdles"),
        ("Priya Reddy", "Senior Tech Recruiter", "Java Backend Developer", "Cognizant"),
        ("Alex van der Berg", "Talent Acquisition Lead", "AI / LLM Software Engineer", "Bestuursatlas B.V."),
        ("Dr. Jane Smith", "Head of Engineering", "React.js Frontend Engineer", "Google India Technologies"),
    ]

    for name, title, role, company in sample_cases:
        note = build_recruiter_connection_note(name, role, company, profile)
        char_count = len(note)
        status = "✅ PASS" if char_count <= MAX_NOTE_LENGTH else "❌ FAIL"
        print(f"\nRecruiter: {name} ({title})")
        print(f"Target:    {role} @ {company}")
        print(f"Length:    {char_count}/{MAX_NOTE_LENGTH} chars [{status}]")
        print(f"Note:      \"{note}\"")

    today_count = count_connections_today()
    max_daily = profile.data.get("max_connection_requests_per_day", 10)
    print("\n" + "-" * 60)
    print(f"Connection requests sent today: {today_count}/{max_daily}")
    print("=" * 60 + "\n")


def test_search_and_demo(profile: Profile, dry_run: bool = True, screenshot_dir: Path = None):
    """
    Searches LinkedIn jobs for an active listing with a Hiring Team card,
    opens it in a visible browser, highlights the recruiter card,
    clicks Connect, opens 'Add a note', types the note, captures screenshots, and sends.
    """
    if not Path(SESSION_FILE).exists():
        print(f"[!] {SESSION_FILE} not found. Run 'python login_capture.py linkedin' first.")
        return

    if screenshot_dir is None:
        screenshot_dir = Path("screenshots")
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    print("\n[+] Launching VISIBLE browser to find a live job with a Hiring Manager...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=100)
        context = browser.new_context(storage_state=SESSION_FILE, viewport={"width": 1280, "height": 850})
        page = context.new_page()

        search_url = "https://www.linkedin.com/jobs/search/?f_AL=true&f_TPR=r604800&keywords=Full%20Stack%20Developer&location=Worldwide&f_WT=2"
        print(f"[+] Navigating to: {search_url}")
        page.goto(search_url)
        time.sleep(3.5)

        # Get job cards
        cards = page.locator('.jobs-search-results__list-item, .scaffold-layout__list-item, .jobs-search-results-list li, div[data-job-id]')
        card_count = cards.count()
        print(f"[+] Found {card_count} job cards on page. Scanning for Hiring Manager...")

        found_recruiter = False
        for idx in range(min(card_count, 15)):
            try:
                card = cards.nth(idx)
                card.scroll_into_view_if_needed()
                time.sleep(0.5)
                card.click()
                time.sleep(2.5)

                job_title = page.evaluate("() => document.querySelector('.job-details-jobs-unified-top-card__job-title, h1, .jobs-unified-top-card__job-title')?.innerText || 'Full Stack Developer'").strip()
                company = page.evaluate("() => document.querySelector('.job-details-jobs-unified-top-card__company-name, .jobs-unified-top-card__company-name, [class*=\"company-name\"]')?.innerText || 'Company'").strip()

                # Check for hiring team / recruiter card on right pane
                recruiter_info = extract_hiring_manager(page)
                if recruiter_info and recruiter_info.get("name"):
                    found_recruiter = True
                    recruiter_name = recruiter_info["name"]
                    print("\n" + "=" * 60)
                    print(f"🎯 FOUND HIRING MANAGER ON LISTING #{idx + 1}!")
                    print(f"   Job Title:        {job_title}")
                    print(f"   Company:          {company}")
                    print(f"   Hiring Manager:   {recruiter_name} ({recruiter_info.get('headline')})")
                    print(f"   Profile:          {recruiter_info.get('profileUrl')}")
                    print("=" * 60)

                    # Highlight the hiring manager section with a bright green border in UI
                    page.evaluate("""() => {
                        const posterEl = document.querySelector('.jobs-poster, [class*="hirer-card"], [class*="hiring-team"], .jobs-details-premium-features-hiring-team, [class*="meet-the-hiring-team"]');
                        if (posterEl) {
                            const c = posterEl.closest('.jobs-poster, [class*="hirer-card"], [class*="hiring-team"], .artdeco-card, section') || posterEl;
                            c.scrollIntoView({behavior: 'smooth', block: 'center'});
                            c.style.border = '4px solid #00a651';
                            c.style.boxShadow = '0 0 15px rgba(0, 166, 81, 0.6)';
                            c.style.borderRadius = '8px';
                            c.style.transition = 'all 0.4s ease';
                        }
                    }""")
                    time.sleep(1.5)
                    ss1 = screenshot_dir / "1_recruiter_highlighted.png"
                    page.screenshot(path=str(ss1))
                    print(f"[📷] Screenshot saved: {ss1}")

                    # Build note
                    note = build_recruiter_connection_note(recruiter_name, job_title, company, profile)
                    print(f"\n[+] Generated Note ({len(note)}/{MAX_NOTE_LENGTH} chars):\n    \"{note}\"")

                    # Step 1: Follow recruiter and Connect
                    print("\n[1/4] Checking for 'Connect' / 'Follow' in Hiring Team card...")
                    follow_recruiter(page)
                    direct_connect = page.locator('.jobs-poster button:has-text("Connect"), [class*="hirer-card"] button:has-text("Connect")').first
                    target_page = page

                    if direct_connect.count() > 0 and direct_connect.is_visible():
                        print("[+] Found direct Connect button on card! Clicking...")
                        direct_connect.click()
                        time.sleep(2.0)
                    elif recruiter_info.get("profileUrl"):
                        profile_url = recruiter_info["profileUrl"]
                        print(f"[*] Card has 'Message' button. Navigating directly to recruiter profile: {profile_url}")
                        page.goto(profile_url)
                        time.sleep(3.5)

                        # Follow recruiter on profile page
                        follow_recruiter(page)
                        time.sleep(1.0)

                        print("[+] Checking for Connect button or 'More' actions menu on profile...")
                        result = page.evaluate("""() => {
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
                            print("[+] Selecting 'Connect' from dropdown menu...")
                            page.evaluate("""() => {
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
                            time.sleep(2.5)

                    ss2 = screenshot_dir / "2_connect_dialog.png"
                    target_page.screenshot(path=str(ss2))
                    print(f"[📷] Screenshot saved: {ss2}")

                    # Step 2: Click "Add a note" button in invitation modal
                    print("[2/4] Clicking 'Add a note' in invitation modal...")
                    add_note_btn = target_page.locator('button:has-text("Add a note"), button[aria-label*="Add a note" i]').first
                    if add_note_btn.count() > 0:
                        add_note_btn.click()
                        time.sleep(1.5)
                    else:
                        target_page.evaluate("""() => {
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
                        time.sleep(1.5)

                    # Step 3: Type note into message box
                    print(f"[3/4] Typing {len(note)}-character note into message textarea...")
                    textarea = target_page.locator('textarea[name="message"], textarea#custom-message, .send-invite textarea, textarea').first
                    if textarea.count() > 0:
                        textarea.wait_for(state="visible", timeout=4000)
                        textarea.click()
                        time.sleep(0.2)
                        textarea.fill("")
                        time.sleep(0.1)
                        for char in note:
                            textarea.type(char, delay=20)
                    else:
                        print("[-] Warning: Textarea not visible!")

                    time.sleep(1.5)
                    ss3 = screenshot_dir / "3_note_typed.png"
                    target_page.screenshot(path=str(ss3))
                    print(f"[📷] Screenshot saved: {ss3}")

                    # Step 4: Verify and highlight Send button
                    if dry_run:
                        print("[4/4] 👁️  UI DEMO PAUSE: Note is typed and visible in modal!")
                        print("     (Dry-run mode: Holding modal open for 6 seconds)")
                        time.sleep(6.0)
                    else:
                        print("[4/4] Clicking Send to submit invitation (strictly excluding 'without note')...")
                        send_btn = target_page.locator('dialog button, [role="dialog"] button, .artdeco-modal button').filter(has_text=re.compile(r'^(?:Send|Done)$', re.I)).filter(has_not_text=re.compile(r'without', re.I)).first
                        if send_btn.count() == 0:
                            send_btn = target_page.locator('button[aria-label*="Send invitation" i], button[aria-label*="Send now" i], button:has-text("Send")').filter(has_not_text=re.compile(r'without', re.I)).first
                        if send_btn.count() > 0:
                            send_btn.click()
                            time.sleep(2.5)
                        ss4 = screenshot_dir / "4_invitation_sent.png"
                        target_page.screenshot(path=str(ss4))
                        print(f"[📷] Screenshot saved: {ss4}")
                        log_connection_request(recruiter_name, recruiter_info.get("profileUrl") or "", job_title, company, note, "sent")
                        print("[+] Connection request submitted and logged successfully!")

                    break
            except Exception as e:
                print(f"  (Scanning card note: {e})")
                continue

        if not found_recruiter:
            print("[-] Scanned first 15 job cards, none displayed an open Hiring Team card. Try specific URL.")

        time.sleep(3.0)
        browser.close()


def test_live_job_url(job_url: str, profile: Profile, dry_run: bool = True, screenshot_dir: Path = None):
    """Test on a specific job URL."""
    if not Path(SESSION_FILE).exists():
        print(f"[!] {SESSION_FILE} not found. Run 'python login_capture.py linkedin' first.")
        return

    if screenshot_dir is None:
        screenshot_dir = Path("screenshots")
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n[+] Launching VISIBLE browser to inspect: {job_url}")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=100)
        context = browser.new_context(storage_state=SESSION_FILE, viewport={"width": 1280, "height": 850})
        page = context.new_page()

        page.goto(job_url)
        time.sleep(3.5)

        job_title = page.evaluate("() => document.querySelector('.job-details-jobs-unified-top-card__job-title, h1, .jobs-unified-top-card__job-title')?.innerText || 'Full Stack Developer'").strip()
        company = page.evaluate("() => document.querySelector('.job-details-jobs-unified-top-card__company-name, .jobs-unified-top-card__company-name, [class*=\"company-name\"]')?.innerText || 'Company'").strip()

        recruiter_info = extract_hiring_manager(page)
        if not recruiter_info or not recruiter_info.get("name"):
            print("[-] No hiring manager found on this specific job listing.")
            browser.close()
            return

        recruiter_name = recruiter_info["name"]
        print("\n" + "=" * 60)
        print(f"🎯 FOUND HIRING MANAGER!")
        print(f"   Job Title:        {job_title}")
        print(f"   Company:          {company}")
        print(f"   Hiring Manager:   {recruiter_name}")
        print(f"   Profile:          {recruiter_info.get('profileUrl')}")
        print("=" * 60)

        # Highlight
        page.evaluate("""() => {
            const posterEl = document.querySelector('.jobs-poster, [class*="hirer-card"], [class*="hiring-team"], .jobs-details-premium-features-hiring-team, [class*="meet-the-hiring-team"]');
            if (posterEl) {
                const c = posterEl.closest('.jobs-poster, [class*="hirer-card"], [class*="hiring-team"], .artdeco-card, section') || posterEl;
                c.scrollIntoView({behavior: 'smooth', block: 'center'});
                c.style.border = '4px solid #00a651';
                c.style.boxShadow = '0 0 15px rgba(0, 166, 81, 0.6)';
                c.style.borderRadius = '8px';
            }
        }""")
        time.sleep(1.5)
        ss1 = screenshot_dir / "1_recruiter_highlighted.png"
        page.screenshot(path=str(ss1))

        note = build_recruiter_connection_note(recruiter_name, job_title, company, profile)
        print(f"\n[+] Generated Note ({len(note)}/{MAX_NOTE_LENGTH} chars):\n    \"{note}\"")

        # Step 1: Follow & Connect
        follow_recruiter(page)
        connect_clicked = page.evaluate("""() => {
            const posterEl = document.querySelector('.jobs-poster, [class*="hirer-card"], [class*="hiring-team"], .jobs-details-premium-features-hiring-team, [class*="meet-the-hiring-team"]');
            if (!posterEl) return false;
            const container = posterEl.closest('.jobs-poster, [class*="hirer-card"], [class*="hiring-team"], .artdeco-card, section') || posterEl;
            const buttons = Array.from(container.querySelectorAll('button, a[role="button"]'));
            const connectBtn = buttons.find(b => {
                const t = (b.innerText || b.getAttribute('aria-label') || '').toLowerCase();
                return t.includes('connect') || t.includes('invite');
            });
            if (connectBtn) {
                connectBtn.scrollIntoView({behavior: 'smooth', block: 'center'});
                connectBtn.click();
                return true;
            }
            return false;
        }""")
        time.sleep(2.0)
        ss2 = screenshot_dir / "2_connect_dialog.png"
        page.screenshot(path=str(ss2))

        # Step 2: Add Note
        add_note_btn = page.locator('button:has-text("Add a note"), button[aria-label*="Add a note" i]').first
        if add_note_btn.count() > 0:
            add_note_btn.click()
            time.sleep(1.5)
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
            time.sleep(1.5)

        # Step 3: Type
        textarea = page.locator('textarea[name="message"], textarea#custom-message, .send-invite textarea, textarea').first
        if textarea.count() > 0:
            textarea.wait_for(state="visible", timeout=4000)
            textarea.click()
            time.sleep(0.2)
            textarea.fill("")
            time.sleep(0.1)
            for char in note:
                textarea.type(char, delay=20)
        else:
            print("[-] Warning: Textarea not visible!")

        time.sleep(1.5)
        ss3 = screenshot_dir / "3_note_typed.png"
        page.screenshot(path=str(ss3))

        if dry_run:
            print("[4/4] 👁️  Holding modal open for 8 seconds...")
            time.sleep(8.0)
        else:
            page.evaluate("""() => {
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
            time.sleep(2.5)
            ss4 = screenshot_dir / "4_invitation_sent.png"
            page.screenshot(path=str(ss4))
            log_connection_request(recruiter_name, recruiter_info.get("profileUrl") or "", job_title, company, note, "sent")
            print("[+] Connection request submitted and logged successfully!")

        time.sleep(3.0)
        browser.close()


def main():
    parser = argparse.ArgumentParser(description="Test LinkedIn Recruiter Connection Flow")
    parser.add_argument("--preview-notes", action="store_true", help="Preview generated notes for sample recruiters")
    parser.add_argument("--url", type=str, help="Test live connection request flow on a specific LinkedIn job URL")
    parser.add_argument("--search", action="store_true", help="Search live LinkedIn jobs and demonstrate UI connection flow")
    parser.add_argument("--dry-run", action="store_true", default=True, help="Hold modal open to view without clicking send")
    parser.add_argument("--send", action="store_true", help="Actually click send to submit connection request")
    parser.add_argument("--screenshots-dir", type=str, default=None, help="Directory to save screenshots")
    args = parser.parse_args()

    profile = Profile.load()
    is_dry_run = not args.send
    ss_dir = Path(args.screenshots_dir) if args.screenshots_dir else Path("screenshots")

    if args.url:
        test_live_job_url(args.url, profile, dry_run=is_dry_run, screenshot_dir=ss_dir)
    elif args.search or len(sys.argv) == 1:
        test_search_and_demo(profile, dry_run=is_dry_run, screenshot_dir=ss_dir)
    elif args.preview_notes:
        test_preview_notes(profile)
    else:
        test_search_and_demo(profile, dry_run=is_dry_run, screenshot_dir=ss_dir)


if __name__ == "__main__":
    main()


