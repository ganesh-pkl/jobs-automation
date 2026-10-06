"""
LinkedIn Recruiter & Hiring Manager Outreach Automation.
Strictly filters job postings and tech recruiters based on candidate's target roles & seniority ceiling (<=3 yrs).
Categorizes outreach across 3 specific role buckets with targeted quotas:
  - 3 AI Engineer roles
  - 5 Full Stack roles
  - 2 Backend roles
Total: 10 daily connection requests.

Crafts dynamic, JD-aware connection notes (<=300 chars), scans international + local locations,
and completes outreach via job post hiring managers with direct tech recruiter search fallback.

Usage:
    .venv/bin/python linkedin_recruiter_outreach.py --limit 10
    .venv/bin/python linkedin_recruiter_outreach.py --preview
    .venv/bin/python linkedin_recruiter_outreach.py --dry-run
"""
import sys
import time
import random
import re
import argparse
from pathlib import Path
from playwright.sync_api import sync_playwright

from common.profile import Profile
from common.stealth import (
    get_launch_kwargs,
    get_context_options,
    apply_stealth,
    check_linkedin_restrictions,
    human_move_and_click,
    human_scroll,
    human_dwell,
)
from common.recruiter_connect import (
    build_recruiter_connection_note,
    extract_hiring_manager,
    send_recruiter_connection_request,
    load_contacted_recruiters,
    count_connections_today,
    MAX_NOTE_LENGTH,
)

SESSION_FILE = "session_linkedin.json"

EXCLUDED_SENIORITY = [
    "lead", "principal", "staff", "architect", "director", "head of",
    "manager", "vp", "vice president", "sr.", "sr ", "senior", "group lead",
]

EXCLUDED_UNRELATED = [
    "dynamics", "crm", "sap", "salesforce", "qa", "tester", "quality assurance",
    "recruiter", "talent acquisition", "hr ", "human resources", "sre", "site reliability",
    "data entry", "executive", "intern", "internship"
]

OUTREACH_CATEGORIES = [
    {
        "category": "AI Engineer",
        "default_quota": 3,
        "search_roles": [
            "AI Engineer",
            "AI Software Engineer",
            "AI/LLM Engineer",
            "Generative AI Developer",
            "Machine Learning Engineer",
        ],
        "recruiter_query": '"Technical Recruiter" AND ("AI" OR "Machine Learning" OR "LLM" OR "GenAI")',
        "recruiter_note_role": "AI & Full-Stack Engineer",
        "recruiter_note_pitch": "LLMs, LangChain, RAG, Python/FastAPI & React",
    },
    {
        "category": "Full Stack",
        "default_quota": 5,
        "search_roles": [
            "Full Stack Developer",
            "Full Stack Software Engineer",
            "React.js Developer",
            "React Developer",
            "MERN Stack Developer",
            "Software Engineer",
            "Frontend Engineer",
        ],
        "recruiter_query": '"Technical Recruiter" AND ("Full Stack" OR "React" OR "MERN" OR "Frontend")',
        "recruiter_note_role": "Full-Stack Developer",
        "recruiter_note_pitch": "React, Node.js, Python, Java & REST APIs",
    },
    {
        "category": "Backend",
        "default_quota": 2,
        "search_roles": [
            "Backend Engineer",
            "Python Developer",
            "FastAPI Developer",
            "Java Developer",
            "Spring Boot Developer",
            "Node.js Developer",
        ],
        "recruiter_query": '"Technical Recruiter" AND ("Backend" OR "Python" OR "FastAPI" OR "Java" OR "Spring Boot")',
        "recruiter_note_role": "Backend Developer",
        "recruiter_note_pitch": "Python/FastAPI, Java/Spring Boot & microservices",
    },
]


def compute_category_quotas(limit: int = 10) -> dict[str, int]:
    """
    Computes exact role category quotas.
    Default distribution for 10 requests: 3 AI Engineer, 5 Full Stack, 2 Backend.
    """
    if limit == 10:
        return {"AI Engineer": 3, "Full Stack": 5, "Backend": 2}

    # Proportional scaling for custom limit: 30% AI, 50% Full Stack, 20% Backend
    q_ai = max(0, int(round(limit * 0.3)))
    q_fs = max(0, int(round(limit * 0.5)))
    q_be = max(0, limit - q_ai - q_fs)

    while q_ai + q_fs + q_be > limit:
        if q_fs > 1:
            q_fs -= 1
        elif q_ai > 1:
            q_ai -= 1
        elif q_be > 0:
            q_be -= 1
    while q_ai + q_fs + q_be < limit:
        q_fs += 1

    return {"AI Engineer": q_ai, "Full Stack": q_fs, "Backend": q_be}


def get_search_locations(profile: Profile) -> list[str]:
    """Loads full international country and tech hub location list matching normal LinkedIn flow."""
    locations = []
    configured = profile.data.get("remote_locations")
    if configured and isinstance(configured, list):
        for loc in configured:
            if loc and loc not in locations and loc.lower() != "remote":
                locations.append(loc)

    global_countries = [
        "Worldwide",
        "United States",
        "United Kingdom",
        "Canada",
        "Germany",
        "Netherlands",
        "Australia",
        "Ireland",
        "Singapore",
        "United Arab Emirates",
        "New Zealand",
        "European Union",
        "Switzerland",
        "Sweden",
        "India",
    ]
    for c in global_countries:
        if c not in locations:
            locations.append(c)

    for city in [profile.current_city, *profile.relocate_cities]:
        if city and city not in locations:
            locations.append(city)

    return locations


def is_job_match(title: str, target_role: str, profile: Profile) -> tuple[bool, str]:
    """Strictly validates if a job title matches candidate role & seniority ceiling."""
    if not title:
        return False, "empty title"
    title_lower = title.lower()

    # 1. Reject unrelated domains
    for unrelated in EXCLUDED_UNRELATED:
        if unrelated in title_lower:
            return False, f"unrelated domain ({unrelated})"

    # 2. Reject senior / lead roles (ceiling <= 3 years)
    for sen in EXCLUDED_SENIORITY:
        if re.search(rf"\b{re.escape(sen)}\b", title_lower):
            return False, f"seniority ceiling exceeded ({sen})"

    # 3. Role-specific keyword matching
    role_lower = target_role.lower()
    if any(k in role_lower for k in ["ai", "llm", "machine learning", "genai", "generative"]):
        if not any(k in title_lower for k in ["ai", "llm", "genai", "generative", "agentic", "machine learning", "ml", "nlp", "artificial intelligence"]):
            return False, "title lacks AI/LLM keywords"
    elif any(k in role_lower for k in ["java", "spring"]):
        if not any(k in title_lower for k in ["java", "spring"]):
            return False, "title lacks Java/Spring keywords"
    elif any(k in role_lower for k in ["python", "fastapi", "django"]):
        if not any(k in title_lower for k in ["python", "fastapi", "django", "flask"]):
            return False, "title lacks Python keywords"
    elif any(k in role_lower for k in ["react", "frontend", "front-end", "front end"]):
        if not any(k in title_lower for k in ["react", "frontend", "front-end", "front end", "next", "typescript", "ui developer", "ui engineer"]):
            return False, "title lacks React/Frontend keywords"
    elif any(k in role_lower for k in ["backend", "back-end", "back end", "node"]):
        if not any(k in title_lower for k in ["backend", "back-end", "back end", "node", "python", "java", "api", "software engineer", "developer"]):
            return False, "title lacks Backend keywords"
    elif any(k in role_lower for k in ["full stack", "fullstack", "full-stack", "mern", "software engineer", "software developer", "developer"]):
        if not any(k in title_lower for k in ["full stack", "fullstack", "full-stack", "mern", "software developer", "software engineer", "developer", "engineer", "web developer"]):
            return False, "title lacks Full-Stack/Software keywords"

    return True, "ok"


def get_chrome_channel() -> str | None:
    """Returns 'chrome' if official Google Chrome app is installed on the user's system."""
    mac_chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if mac_chrome.exists():
        return "chrome"
    return None


def run_outreach(limit: int = 10, dry_run: bool = False):
    profile = Profile.load()
    max_daily = int(profile.data.get("max_connection_requests_per_day", 10))
    target_limit = min(limit, max_daily)

    already_sent = count_connections_today()
    if already_sent >= target_limit and not dry_run:
        print(f"\n[!] Daily connection limit already reached today ({already_sent}/{target_limit}).")
        print("    Try again tomorrow to keep your LinkedIn account safe.\n")
        return

    remaining = target_limit - already_sent if not dry_run else target_limit
    quotas = compute_category_quotas(remaining)

    print("\n" + "=" * 65)
    print("       LINKEDIN CATEGORIZED RECRUITER OUTREACH PIPELINE")
    print("=" * 65)
    print(f"Candidate:             {profile.data.get('first_name', 'Ganesh')} Pirikirala (3y exp)")
    print(f"Total Sent Today:      {already_sent}/{max_daily}")
    print(f"Target This Run:       {remaining} connection requests")
    print(f"Role Quotas This Run:")
    print(f"  • AI Engineer:       {quotas['AI Engineer']} roles")
    print(f"  • Full Stack:        {quotas['Full Stack']} roles")
    print(f"  • Backend:           {quotas['Backend']} roles")
    print(f"Mode:                  {'DRY-RUN (Preview Only)' if dry_run else 'LIVE OUTREACH'}")
    print("=" * 65 + "\n")

    if not Path(SESSION_FILE).exists():
        print(f"[!] {SESSION_FILE} not found. Run '.venv/bin/python login_capture.py linkedin' first.")
        return

    category_counts = {"AI Engineer": 0, "Full Stack": 0, "Backend": 0}
    sent_this_run = 0
    contacted_set = load_contacted_recruiters()
    search_locations = get_search_locations(profile)

    with sync_playwright() as p:
        launch_kwargs = get_launch_kwargs(headless=False, slow_mo=60)
        browser = p.chromium.launch(**launch_kwargs)
        context_opts = get_context_options(storage_state=SESSION_FILE)
        context = browser.new_context(**context_opts)
        apply_stealth(context)
        page = context.new_page()

        # Step 1: Verify LinkedIn session status
        print("[+] Verifying LinkedIn session with stealth...")
        page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=30000)
        time.sleep(3.0)

        # Active restriction check on feed
        is_restricted, reason = check_linkedin_restrictions(page)
        if is_restricted:
            print(f"\n[!] ⚠️ {reason}")
            print("    Halting LinkedIn outreach to protect your account.")
            browser.close()
            return

        if any(marker in page.url.lower() for marker in ("/login", "authwall", "checkpoint", "uas/login", "signup")):
            print("\n[!] LinkedIn session expired or requires login.")
            print("    Please log in via the browser and press Enter here.")
            input("Press Enter after logging in: ")
            context.storage_state(path=SESSION_FILE)
            Path(SESSION_FILE).chmod(0o600)
            print("    ✅ Session saved. Continuing...")

        # Stream A: Targeted Job Post Hiring Managers by Category
        for cat_spec in OUTREACH_CATEGORIES:
            cat_name = cat_spec["category"]
            cat_quota = quotas[cat_name]

            if category_counts[cat_name] >= cat_quota or sent_this_run >= remaining:
                continue

            print(f"\n{'=' * 65}")
            print(f"📂 CATEGORY: {cat_name.upper()} — Target: {cat_quota} requests (Completed: {category_counts[cat_name]}/{cat_quota})")
            print(f"{'=' * 65}")

            for role in cat_spec["search_roles"]:
                if category_counts[cat_name] >= cat_quota or sent_this_run >= remaining:
                    break

                encoded_role = role.replace(" ", "%20")
                for loc in search_locations:
                    if category_counts[cat_name] >= cat_quota or sent_this_run >= remaining:
                        break

                    encoded_loc = loc.replace(" ", "%20")
                    print(f"\n🔎 [{cat_name}] Scanning '{role}' in {loc} (Easy Apply + External Company Apply)...")

                    for page_num in range(1, 3):
                        if category_counts[cat_name] >= cat_quota or sent_this_run >= remaining:
                            break

                        start_offset = (page_num - 1) * 25
                        search_url = f"https://www.linkedin.com/jobs/search/?f_TPR=r604800&keywords={encoded_role}&location={encoded_loc}&sortBy=DD&start={start_offset}"

                        try:
                            page.goto(search_url, wait_until="domcontentloaded", timeout=25000)
                            time.sleep(random.uniform(3.0, 4.5))
                        except Exception as e:
                            print(f"   (Navigation note: {e})")
                            continue

                        # Check restrictions after navigation
                        is_restricted, reason = check_linkedin_restrictions(page)
                        if is_restricted:
                            print(f"\n[!] ⚠️ {reason} — stopping to protect account.")
                            browser.close()
                            return

                        human_scroll(page, distance=300, steps=3)

                        cards = page.locator('.jobs-search-results__list-item, .scaffold-layout__list-item, .jobs-search-results-list li, div[data-job-id]')
                        card_count = cards.count()
                        if card_count == 0:
                            break

                        for idx in range(min(card_count, 20)):
                            if category_counts[cat_name] >= cat_quota or sent_this_run >= remaining:
                                break

                            try:
                                card = cards.nth(idx)
                                human_move_and_click(page, card, delay_after=2.0)

                                job_title = page.evaluate("() => (document.querySelector('.job-details-jobs-unified-top-card__job-title, h1, .jobs-unified-top-card__job-title')?.innerText || '').trim()")
                                company = page.evaluate("() => (document.querySelector('.job-details-jobs-unified-top-card__company-name, .jobs-unified-top-card__company-name, [class*=\"company-name\"]')?.innerText || '').trim()") or "Company"
                                job_desc = page.evaluate("() => (document.querySelector('.jobs-description, #job-details, .job-details-module')?.innerText || '').slice(0, 1000)")

                                # Detect apply type (Easy Apply vs External Company Portal)
                                apply_type = page.evaluate("""() => {
                                    const btn = document.querySelector('.jobs-apply-button, button[class*="jobs-apply"], a[class*="jobs-apply"]');
                                    if (btn) {
                                        const t = (btn.innerText || btn.getAttribute('aria-label') || '').toLowerCase();
                                        if (t.includes('easy apply')) return 'Easy Apply';
                                        return 'External Apply';
                                    }
                                    return 'Direct Listing';
                                }""")

                                # Strict validation of job title & seniority
                                ok, reason = is_job_match(job_title, role, profile)
                                if not ok:
                                    continue

                                recruiter_info = extract_hiring_manager(page)
                                if not recruiter_info or not recruiter_info.get("name"):
                                    continue

                                rec_name = recruiter_info["name"]
                                rec_url = (recruiter_info.get("profileUrl") or "").strip().split("?")[0].rstrip("/")
                                rec_key = rec_url if rec_url else f"{rec_name.lower()}@{company.lower()}"

                                if rec_key in contacted_set or (rec_url and rec_url in contacted_set):
                                    continue

                                note = build_recruiter_connection_note(rec_name, job_title, company, profile, job_description=job_desc)

                                print("\n" + "-" * 60)
                                print(f"🎯 FOUND [{cat_name.upper()}] HIRING MANAGER [{category_counts[cat_name] + 1}/{cat_quota}] (Total: {sent_this_run + 1}/{remaining}) ({apply_type}):")
                                print(f"   Recruiter:   {rec_name} ({recruiter_info.get('headline', 'Hiring Team')})")
                                print(f"   Target Job:  [{apply_type}] {job_title} @ {company}")
                                print(f"   Note ({len(note)} chars): \"{note}\"")
                                print("-" * 60)

                                if dry_run:
                                    category_counts[cat_name] += 1
                                    sent_this_run += 1
                                    contacted_set.add(rec_key)
                                    print(f"   ✅ [Preview] Note generated and verified under {MAX_NOTE_LENGTH} chars.")
                                    time.sleep(1.0)
                                else:
                                    success = send_recruiter_connection_request(page, recruiter_info, job_title, company, profile, job_description=job_desc)
                                    if success:
                                        category_counts[cat_name] += 1
                                        sent_this_run += 1
                                        contacted_set.add(rec_key)
                                        cooldown = random.uniform(12.0, 18.0)
                                        print(f"   ⏳ Human pacing cooldown: waiting {cooldown:.1f}s before next recruiter...")
                                        time.sleep(cooldown)
                                    else:
                                        print(f"   [-] Could not complete connection request for {rec_name}.")

                            except Exception:
                                continue

        # Stream B: Direct Technical Recruiter Search per Category (if any quota is remaining)
        for cat_spec in OUTREACH_CATEGORIES:
            cat_name = cat_spec["category"]
            cat_quota = quotas[cat_name]

            if category_counts[cat_name] < cat_quota and sent_this_run < remaining:
                needed = cat_quota - category_counts[cat_name]
                print(f"\n⚡ Stream B: Direct Technical Recruiter Search for [{cat_name}] ({needed} needed to meet quota)...")

                query = cat_spec["recruiter_query"]
                encoded_q = query.replace('"', '%22').replace(" ", "%20")
                people_url = f"https://www.linkedin.com/search/results/people/?keywords={encoded_q}&origin=GLOBAL_SEARCH_HEADER"
                print(f"   Searching Tech Recruiters: {query}")

                try:
                    page.goto(people_url, wait_until="domcontentloaded", timeout=25000)
                    time.sleep(random.uniform(3.0, 4.5))
                except Exception:
                    continue

                is_restricted, reason = check_linkedin_restrictions(page)
                if is_restricted:
                    print(f"\n[!] ⚠️ {reason} — stopping to protect account.")
                    browser.close()
                    return

                human_scroll(page, distance=350, steps=3)

                people_cards = page.locator('.reusable-search__result-container, li.grid, div[data-view-name*="search-entity-result-universal-template"]')
                p_count = people_cards.count()

                for p_idx in range(min(p_count, 15)):
                    if category_counts[cat_name] >= cat_quota or sent_this_run >= remaining:
                        break

                    try:
                        p_card = people_cards.nth(p_idx)
                        name_el = p_card.locator('span[aria-hidden="true"], a[href*="/in/"]').first
                        raw_name = name_el.inner_text().strip() if name_el.count() > 0 else ""
                        clean_name = raw_name.split("\n")[0].strip()
                        if not clean_name or "LinkedIn Member" in clean_name:
                            continue

                        link_el = p_card.locator('a[href*="/in/"]').first
                        profile_url = link_el.get_attribute("href").split("?")[0].rstrip("/") if link_el.count() > 0 else ""

                        headline_el = p_card.locator('.entity-result__primary-subtitle, [class*="subtitle"]').first
                        headline = headline_el.inner_text().strip() if headline_el.count() > 0 else f"{cat_name} Recruiter"

                        rec_key = profile_url if profile_url else clean_name.lower()
                        if rec_key in contacted_set:
                            continue

                        first_name = clean_name.split()[0]
                        first_name = re.sub(r"[^a-zA-Z]", "", first_name) or "there"
                        candidate_name = profile.data.get("first_name", "Ganesh")

                        note_role = cat_spec["recruiter_note_role"]
                        note_pitch = cat_spec["recruiter_note_pitch"]
                        note = f"Hi {first_name}, I'm an engineer with 3y exp in {note_pitch}. Noticed you hire tech talent in this space and would love to connect for future {note_role} opportunities! Best, {candidate_name}"
                        if len(note) > MAX_NOTE_LENGTH:
                            note = f"Hi {first_name}, I bring 3y exp in {note_pitch}. Would love to connect for future {note_role} roles! Best, {candidate_name}"

                        print("\n" + "-" * 60)
                        print(f"🎯 FOUND [{cat_name.upper()}] TECH RECRUITER [{category_counts[cat_name] + 1}/{cat_quota}] (Total: {sent_this_run + 1}/{remaining}):")
                        print(f"   Recruiter:  {clean_name} ({headline})")
                        print(f"   Profile:    {profile_url}")
                        print(f"   Note ({len(note)} chars): \"{note}\"")
                        print("-" * 60)

                        if dry_run:
                            category_counts[cat_name] += 1
                            sent_this_run += 1
                            contacted_set.add(rec_key)
                            print(f"   ✅ [Preview] Note verified under {MAX_NOTE_LENGTH} chars.")
                            time.sleep(1.0)
                        else:
                            rec_info = {"name": clean_name, "profileUrl": profile_url, "headline": headline}
                            success = send_recruiter_connection_request(page, rec_info, note_role, "Tech Recruiting", profile)
                            if success:
                                category_counts[cat_name] += 1
                                sent_this_run += 1
                                contacted_set.add(rec_key)
                                cooldown = random.uniform(12.0, 18.0)
                                print(f"   ⏳ Human pacing cooldown: waiting {cooldown:.1f}s before next recruiter...")
                                time.sleep(cooldown)
                    except Exception:
                        continue

        browser.close()

    total_today = count_connections_today()
    print("\n" + "=" * 65)
    print("              RECRUITER OUTREACH COMPLETE")
    print("=" * 65)
    print("Outreach Breakdown:")
    print(f"  • AI Engineer:       {category_counts['AI Engineer']}/{quotas['AI Engineer']} sent")
    print(f"  • Full Stack:        {category_counts['Full Stack']}/{quotas['Full Stack']} sent")
    print(f"  • Backend:           {category_counts['Backend']}/{quotas['Backend']} sent")
    print(f"Total sent this run:   {sent_this_run}/{remaining}")
    print(f"Total sent today:      {total_today}/{max_daily}")
    print(f"Log updated:           connection_requests_log.csv")
    print("=" * 65 + "\n")


def preview_notes_demo():
    profile = Profile.load()
    quotas = compute_category_quotas(10)
    print("\n" + "=" * 65)
    print("       DYNAMIC CATEGORIZED RECRUITER NOTE SAMPLES")
    print("=" * 65)
    print(f"Configured Quotas: AI: {quotas['AI Engineer']} | Full Stack: {quotas['Full Stack']} | Backend: {quotas['Backend']}\n")

    cases = [
        ("AI Engineer Category", "Sankalp Sharma", "AI Engineer", "Anthropic", "Building LLM pipelines, LangChain, and RAG architectures."),
        ("Full Stack Category", "Michael Vance", "Full Stack Developer", "TechCorp Global", "End-to-end web applications with React, Node.js, and cloud backend."),
        ("Full Stack Category", "Neha Gupta", "Frontend Engineer (React / Next.js)", "Razorpay", "Modern web app frontend in React, Next.js, TypeScript, performance tuning."),
        ("Backend Category", "Alex Turner", "Python / FastAPI Backend Engineer", "DataFlow Systems", "FastAPI, PostgreSQL, asynchronous backends and distributed APIs."),
        ("Backend Category", "Priya Reddy", "Java Developer", "Infosys Ltd", "Spring Boot microservices, high scale REST APIs, Kafka, Hibernate."),
    ]

    for cat_label, name, role, comp, jd in cases:
        note = build_recruiter_connection_note(name, role, comp, profile, job_description=jd)
        status = "✅ PASS" if len(note) <= MAX_NOTE_LENGTH else "❌ FAIL"
        print(f"[{cat_label}]")
        print(f"Recruiter:  {name}")
        print(f"Role/Comp:  {role} @ {comp}")
        print(f"Length:     {len(note)}/{MAX_NOTE_LENGTH} chars [{status}]")
        print(f"Note:       \"{note}\"\n")

    print("=" * 65 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LinkedIn Dynamic Categorized Recruiter Outreach Automation")
    parser.add_argument("--limit", type=int, default=10, help="Maximum number of connection requests to send (default: 10)")
    parser.add_argument("--preview", action="store_true", help="Preview dynamic notes without launching browser")
    parser.add_argument("--dry-run", action="store_true", help="Launch browser and find recruiters without clicking final Send")
    args = parser.parse_args()

    if args.preview:
        preview_notes_demo()
    else:
        run_outreach(limit=args.limit, dry_run=args.dry_run)
