"""
Collect External Job Applications (Naukri, LinkedIn, Hirist, Uplers)

Scans target roles across all 4 platforms strictly filtered for:
  - Job Age <= 7 days
  - Experience <= 4 years
  - Preferred Locations (Hyderabad, Bengaluru, Remote)

Finds jobs that redirect to external company application portals (Workday, Lever, Greenhouse, etc.)
and outputs them into external_jobs.csv so you can apply to them manually.
"""
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from common.profile import Profile
from common.external_tracker import log_external_job, EXTERNAL_LOG_FILE


def collect_naukri_external_jobs(page, profile: Profile) -> int:
    print("\n--- Scanning Naukri for External Jobs (Age <= 7 days, Exp <= 4 yrs) ---")
    added = 0
    exp_param = f"0to{int(profile.seniority_ceiling_years)}"
    
    locations = [profile.current_city, *profile.relocate_cities]
    seen_locs = set()
    deduped_locs = [l.lower() for l in locations if l and l.lower() not in seen_locs and not seen_locs.add(l.lower())]
    loc_slug = "-".join(deduped_locs).replace(" ", "-")

    for role in profile.target_roles:
        role_slug = role.lower().replace(" ", "-").replace("/", "-")
        if profile.work_mode == "remote_only":
            base_path = f"{role_slug}-remote-jobs"
            query_params = f"experience={exp_param}&sort=f&wfhType=0&wfhType=2&jobAge={profile.job_freshness_days}"
        else:
            base_path = f"{role_slug}-jobs-in-{loc_slug}" if loc_slug else f"{role_slug}-jobs"
            query_params = f"experience={exp_param}&sort=f&jobAge={profile.job_freshness_days}"
        
        for page_no in range(1, int(profile.max_pages_per_role) + 1):
            page_suffix = "" if page_no == 1 else f"-{page_no}"
            url = f"https://www.naukri.com/{base_path}{page_suffix}?{query_params}"
            
            print(f"Scanning Naukri: {role} (page {page_no})...")
            try:
                page.goto(url, timeout=12000)
                page.wait_for_selector('.srp-jobtuple-wrapper[data-job-id]', timeout=8000)
            except Exception:
                break
            
            time.sleep(1.5)
            cards = page.evaluate("""
                () => Array.from(document.querySelectorAll('.srp-jobtuple-wrapper[data-job-id]'))
                  .map(c => ({
                    title: c.querySelector('a.title')?.innerText?.trim(),
                    href: c.querySelector('a.title')?.href,
                    company: c.querySelector('a.comp-name, .comp-dtls-wrap a')?.innerText?.trim(),
                    exp: c.querySelector('.expwdth')?.innerText,
                    location: c.querySelector('.locWdth')?.innerText,
                    posted: c.querySelector('.job-post-day, [class*="post-day"], .type, .sub-type')?.innerText,
                  }))
                  .filter(c => c.title && c.href)
            """)
            
            if not cards:
                break
                
            for card in cards:
                href = card.get("href")
                if not href:
                    continue
                try:
                    page.goto(href, timeout=10000)
                    time.sleep(1.5)
                    
                    # Check for external button
                    ext_btn = page.query_selector('#company-site-button')
                    if ext_btn:
                        ext_url = ext_btn.get_attribute("href") or page.url
                        title = card.get("title") or ""
                        company = card.get("company") or ""
                        loc = card.get("location") or ""
                        exp = card.get("exp") or ""
                        posted = card.get("posted") or ""
                        
                        if log_external_job("naukri", title, company, href, ext_url, loc, exp, posted):
                            added += 1
                            print(f"  [+] Logged External Job: {title} @ {company} -> {ext_url or href}")
                except Exception:
                    continue

    return added


def collect_linkedin_external_jobs(page, profile: Profile) -> int:
    print("\n--- Scanning LinkedIn for External Jobs (Age <= 7 days) ---")
    added = 0
    remote_only = (profile.work_mode == "remote_only")
    if remote_only:
        configured_remote = profile.data.get("remote_locations")
        if configured_remote and isinstance(configured_remote, list) and len(configured_remote) > 0:
            locations = configured_remote
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
    else:
        locations = list(dict.fromkeys([profile.current_city, *profile.relocate_cities, "Remote"]))

    freshness_seconds = int(profile.job_freshness_days) * 86400

    under_10_applicants = bool(profile.data.get("under_10_applicants_only", True))

    from linkedin_apply import LINKEDIN_GEO_IDS

    for role in profile.target_roles[:5]:  # Top role queries
        for loc in locations:
            if not loc:
                continue
            gid_param = ""
            loc_clean = loc.strip().lower()
            if loc_clean in LINKEDIN_GEO_IDS:
                gid_param = f"&geoId={LINKEDIN_GEO_IDS[loc_clean]}"
            elif remote_only:
                gid_param = "&geoId=92000000"

            url = (
                "https://www.linkedin.com/jobs/search/?keywords="
                + role.replace(" ", "%20")
                + "&location=" + loc.replace(" ", "%20")
                + gid_param
                + f"&f_TPR=r{freshness_seconds}"
                + ("&f_WT=2" if profile.work_mode == "remote_only" else ("&f_WT=2%2C3" if profile.work_mode == "remote_first" else ""))
                + ("&f_EA=true" if under_10_applicants else "")
            )
            print(f"Scanning LinkedIn: {role} ({loc})...")
            try:
                page.goto(url, timeout=15000)
                time.sleep(4)
            except Exception:
                continue

            cards = page.evaluate("""
                () => Array.from(document.querySelectorAll('.jobs-search-results__list-item, .scaffold-layout__list-item, .jobs-search-results-list li, .scaffold-layout__list li'))
                  .map((c, idx) => ({
                    idx: idx,
                    title: c.querySelector('[class*="job-card-list__title"]')?.innerText?.trim(),
                    company: c.querySelector('.artdeco-entity-lockup__subtitle, [class*="job-card-container__company-name"]')?.innerText?.trim(),
                    posted: c.querySelector('time, [class*="listed-time"], [class*="footer-item"]')?.innerText?.trim(),
                  })).filter(c => c.title)
            """)

            for card in cards[:10]:
                try:
                    idx = card["idx"]
                    page.locator(".jobs-search-results__list-item, .scaffold-layout__list-item, .jobs-search-results-list li, .scaffold-layout__list li").nth(idx).click()
                    time.sleep(1.5)
                    
                    is_easy_apply = page.evaluate("""() => {
                        const btn = document.querySelector('.jobs-apply-button');
                        return btn && btn.innerText.includes('Easy Apply');
                    }""")
                    
                    if not is_easy_apply:
                        job_link = page.url
                        ext_link = page.evaluate("() => document.querySelector('.jobs-apply-button')?.href || ''")
                        title = card.get("title") or ""
                        company = card.get("company") or ""
                        posted = card.get("posted") or ""
                        
                        if log_external_job("linkedin", title, company, job_link, ext_link, loc, f"<= {profile.seniority_ceiling_years} yrs", posted):
                            added += 1
                            print(f"  [+] Logged External Job: {title} @ {company} -> {ext_link or job_link}")
                except Exception:
                    continue
                    continue

def collect_foundit_external_jobs(page, profile: Profile) -> int:
    print("\n--- Scanning Foundit for External Jobs (Age <= 7 days, Exp <= 4 yrs) ---")
    added = 0
    import urllib.parse
    loc_tokens = [profile.current_city] + [c for c in profile.relocate_cities if c]
    loc_query = ",".join(loc_tokens) if loc_tokens else ""

    for role in profile.target_roles:
        encoded_query = urllib.parse.quote_plus(role)
        encoded_loc = urllib.parse.quote_plus(loc_query) if loc_query else ""

        for page_no in range(1, int(profile.max_pages_per_role) + 1):
            start_index = (page_no - 1) * 15
            search_url = f"https://www.foundit.in/srp/results?query={encoded_query}"
            if encoded_loc:
                search_url += f"&locations={encoded_loc}"
            if start_index > 0:
                search_url += f"&start={start_index}"

            print(f"Scanning Foundit: {role} (page {page_no})...")
            try:
                page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
                time.sleep(3)
            except Exception:
                break

            card_locators = page.locator(".srpResultCardContainer .cardContainer, [class*='cardContainer']").all()
            if not card_locators:
                break

            for card in card_locators:
                try:
                    title_el = card.locator(".jobTitle, #jobCardTitle, [class*='jobTitle']").first
                    comp_el = card.locator(".companyName, [class*='companyName']").first
                    exp_el = card.locator(".experienceSalary .details, .iconContainer + .details").first
                    loc_el = card.locator(".location, .details.location").first
                    
                    title = title_el.inner_text().strip() if title_el.count() > 0 else ""
                    company = comp_el.inner_text().strip() if comp_el.count() > 0 else "Unknown"
                    exp_text = exp_el.inner_text().strip() if exp_el.count() > 0 else ""
                    loc_text = loc_el.inner_text().strip() if loc_el.count() > 0 else ""
                    card_text = card.inner_text().strip()

                    if not title:
                        continue

                    card.scroll_into_view_if_needed()
                    card.click()
                    time.sleep(1.5)

                    apply_btn = page.locator("#applyNowBtn, button:has-text('Apply Now'), button:has-text('Apply'), a:has-text('Apply')")
                    for b_i in range(apply_btn.count()):
                        btn = apply_btn.nth(b_i)
                        if btn.is_visible():
                            btn_text = btn.inner_text().strip().lower()
                            if "company site" in btn_text or "company website" in btn_text or "external" in btn_text:
                                ext_url = page.url
                                if log_external_job("foundit", title, company, search_url, ext_url, loc_text, exp_text, "Recent"):
                                    added += 1
                                    print(f"  [+] Logged External Job: {title} @ {company} -> {ext_url}")
                            break
                except Exception:
                    continue

    return added


def main():
    profile = Profile.load()
    print(f"Collecting external website job listings for {profile.data.get('current_title_functional', 'Developer')}:")
    print(f"  - Max Job Age: {profile.job_freshness_days} days")
    print(f"  - Max Experience: {profile.seniority_ceiling_years} years")
    print(f"  - Preferred Locations: {profile.current_city}, {', '.join(profile.relocate_cities)}, Remote")
    print(f"  - Output File: {EXTERNAL_LOG_FILE}\n")

    total_added = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"]
        )
        
        # 1. Naukri scanning
        naukri_session = "session_naukri.json"
        if Path(naukri_session).exists():
            context = browser.new_context(storage_state=naukri_session)
        else:
            context = browser.new_context()
        page = context.new_page()
        total_added += collect_naukri_external_jobs(page, profile)
        context.close()

        # 2. LinkedIn scanning (Temporarily paused due to account block)
        # linkedin_session = "session_linkedin.json"
        # if Path(linkedin_session).exists():
        #     context = browser.new_context(storage_state=linkedin_session)
        #     page = context.new_page()
        #     total_added += collect_linkedin_external_jobs(page, profile)
        #     context.close()

        # 3. Foundit scanning
        foundit_session = "session_foundit.json"
        if Path(foundit_session).exists():
            context = browser.new_context(storage_state=foundit_session)
        else:
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            )
        page = context.new_page()
        total_added += collect_foundit_external_jobs(page, profile)
        context.close()

        browser.close()

    print(f"\nFinished collecting external jobs. {total_added} new external jobs logged to {EXTERNAL_LOG_FILE}.")


if __name__ == "__main__":
    main()
