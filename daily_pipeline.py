"""
Daily Job Application Pipeline
Automated morning runner for Naukri, LinkedIn, Hirist, and Uplers.

Workflow:
1. Slices for fresh postings across all 4 platforms.
2. Strict deduplication against persistent application history (never re-applies).
3. Applies directly via platform mechanisms using candidate facts & Groq AI answers.
4. Genuinely new external/manual career portals are appended to persistent external_jobs.csv.
5. Emits daily summary and high-match opportunities report.

Usage:
    python daily_pipeline.py
"""
import csv
import sys
import time
from datetime import datetime, date
from pathlib import Path

from common.profile import Profile
from common.external_tracker import EXTERNAL_LOG_FILE, reset_external_jobs_csv
from common import stats_tracker

APPLICATIONS_LOG = "applications_log.csv"


def _clean(val: str | None) -> str:
    if not val:
        return ""
    return " ".join(val.split()).strip()


def load_historical_applied() -> set[tuple[str, str]]:
    log_path = Path(APPLICATIONS_LOG)
    if not log_path.exists():
        return set()
    applied = set()
    try:
        with log_path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("status") == "applied":
                    t = (row.get("title") or row.get("job_title") or "").strip().lower()
                    c = (row.get("company") or "").strip().lower()
                    if t and c and t != "-" and c != "-":
                        applied.add((t, c))
    except Exception:
        pass
    return applied


def get_applied_stats_today(today_date: date) -> dict[str, int]:
    log_path = Path(APPLICATIONS_LOG)
    if not log_path.exists():
        return {"naukri": 0, "linkedin": 0, "hirist": 0, "uplers": 0, "instahyre": 0, "foundit": 0, "wellfound": 0, "glassdoor": 0}
    
    seen_today = set()
    stats = {"naukri": 0, "linkedin": 0, "hirist": 0, "uplers": 0, "instahyre": 0, "foundit": 0, "wellfound": 0, "glassdoor": 0}
    
    try:
        with log_path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("status") == "applied":
                    ts_str = row.get("timestamp") or ""
                    try:
                        row_date = datetime.fromisoformat(ts_str.replace(" ", "T")).date()
                    except Exception:
                        row_date = today_date
                    
                    if row_date == today_date:
                        t = (row.get("title") or row.get("job_title") or "").strip().lower()
                        c = (row.get("company") or "").strip().lower()
                        src = (row.get("source") or row.get("platform") or "unknown").strip().lower()
                        if (t, c) not in seen_today:
                            seen_today.add((t, c))
                            if src in stats:
                                stats[src] += 1
                            elif src == "indeed":
                                stats["glassdoor"] += 1
    except Exception:
        pass
    return stats


def get_external_jobs_count() -> int:
    path = Path(EXTERNAL_LOG_FILE)
    if not path.exists():
        return 0
    try:
        with path.open(newline="", encoding="utf-8") as f:
            return sum(1 for _ in csv.DictReader(f))
    except Exception:
        return 0


def get_new_external_jobs_today(today_date: date) -> list[dict]:
    path = Path(EXTERNAL_LOG_FILE)
    if not path.exists():
        return []
    today_str = today_date.strftime("%Y-%m-%d")
    new_jobs = []
    try:
        with path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                discovered = row.get("Date Discovered") or row.get("timestamp") or ""
                if today_str in discovered:
                    new_jobs.append(row)
    except Exception:
        pass
    return new_jobs


def print_summary_report(today: date, today_str: str):
    final_applied_history = load_historical_applied()
    final_external_count = get_external_jobs_count()
    final_stats = get_applied_stats_today(today)
    run_stats = stats_tracker.get_stats()

    naukri_applied_today = final_stats["naukri"]
    linkedin_applied_today = final_stats["linkedin"]
    hirist_applied_today = final_stats["hirist"]
    uplers_applied_today = final_stats["uplers"]
    instahyre_applied_today = final_stats["instahyre"]
    foundit_applied_today = final_stats["foundit"]
    wellfound_applied_today = final_stats["wellfound"]
    glassdoor_applied_today = final_stats["glassdoor"]
    total_applied_today = sum(final_stats.values())
    total_applied_historical = len(final_applied_history)
    new_external_count = final_external_count

    new_external_jobs = get_new_external_jobs_today(today)

    # Print Formatted Daily Summary Report matching exact requirements
    print("\n" + "=" * 65)
    print(f"Date: {today_str}\n")
    print(f"Fresh jobs discovered: {run_stats.fresh_discovered}")
    print(f"Previously applied jobs skipped: {run_stats.previously_applied_skipped}")
    print(f"Duplicate jobs skipped: {run_stats.duplicate_skipped}\n")
    print(f"Naukri:")
    print(f"  Applied: {naukri_applied_today}\n")
    print(f"LinkedIn:")
    print(f"  Applied: {linkedin_applied_today}\n")
    print(f"Hirist:")
    print(f"  Applied: {hirist_applied_today}\n")
    print(f"Uplers:")
    print(f"  Applied: {uplers_applied_today}\n")
    print(f"Instahyre:")
    print(f"  Applied: {instahyre_applied_today}\n")
    print(f"Foundit:")
    print(f"  Applied: {foundit_applied_today}\n")
    print(f"Wellfound:")
    print(f"  Applied: {wellfound_applied_today}\n")
    print(f"Glassdoor / Indeed:")
    print(f"  Applied: {glassdoor_applied_today}\n")
    print(f"Total successful applications today: {total_applied_today}\n")
    print(f"Total successful applications historically: {total_applied_historical}\n")
    print(f"New external/manual jobs added to CSV: {new_external_count}\n")
    print(f"Total external jobs currently in CSV: {final_external_count}")
    print("=" * 65)

    if new_external_jobs:
        print("\n--- HIGH-MATCH EXTERNAL OPPORTUNITIES ADDED TODAY ---")
        for idx, job in enumerate(new_external_jobs[:10], 1):
            title = job.get("Job Title") or job.get("title")
            comp = job.get("Company") or job.get("company")
            loc = job.get("Location") or job.get("location")
            ext_url = job.get("External Company Careers/Application URL") or job.get("external_link")
            skills = job.get("Matching Skills") or ""
            print(f"{idx}. [{job.get('Platform')}] {title} @ {comp} ({loc})")
            if skills:
                print(f"   Skills: {skills}")
            print(f"   Careers Link: {ext_url}\n")


def run_parallel():
    import subprocess
    today = datetime.now().date()
    today_str = today.strftime("%Y-%m-%d")
    stats_tracker.reset_stats()
    reset_external_jobs_csv()

    profile = Profile.load()
    total_target = int(profile.data.get("daily_application_limit", 100))
    naukri_safe_limit = int(profile.data.get("naukri_daily_limit", 40))
    linkedin_safe_limit = int(profile.data.get("linkedin_daily_limit", 15))
    foundit_safe_limit = int(profile.data.get("foundit_daily_limit", 30))
    wellfound_safe_limit = int(profile.data.get("wellfound_daily_limit", 25))
    glassdoor_safe_limit = int(profile.data.get("glassdoor_daily_limit", 25))

    initial_applied_history = load_historical_applied()

    print("=" * 65)
    print(f"       STARTING PARALLEL JOB APPLICATION PIPELINE — {today_str}")
    print("=" * 65)
    print(f"Target Total Applications (Across All Platforms): {total_target}")
    print(f"Naukri Safe Cap:                                  {naukri_safe_limit}")
    print(f"LinkedIn Safe Cap:                                {linkedin_safe_limit}")
    print(f"Foundit Safe Cap:                                 {foundit_safe_limit}")
    print(f"Wellfound Safe Cap:                               {wellfound_safe_limit}")
    print(f"Glassdoor Safe Cap:                               {glassdoor_safe_limit}")
    print(f"Permanent Application History:                    {len(initial_applied_history)} jobs recorded")
    print(f"Mode:                                             8 Platforms Running Concurrently")
    print("=" * 65 + "\n")

    tasks = [
        ("Hirist", [sys.executable, "hirist_apply.py"]),
        ("Naukri", [sys.executable, "naukri_apply.py"]),
        # ("LinkedIn", [sys.executable, "linkedin_apply.py"]),  # Temporarily paused due to account block
        ("Uplers", [sys.executable, "uplers_apply.py"]),
        ("Instahyre", [sys.executable, "instahyre_apply.py"]),
        ("Foundit", [sys.executable, "foundit_apply.py"]),
        ("Wellfound", [sys.executable, "wellfound_apply.py"]),
        ("Glassdoor", [sys.executable, "glassdoor_apply.py"]),
    ]

    processes = {}
    for name, cmd in tasks:
        print(f"⚡ [PARALLEL] Spawning {name} worker process...")
        p = subprocess.Popen(cmd)
        processes[name] = p

    print("\n🚀 All active platforms are actively running in parallel!")
    print("   (Waiting for all browser sessions to complete...)\n")

    try:
        for name, p in processes.items():
            p.wait()
            print(f"🏁 {name} worker finished (exit code {p.returncode})")
    except KeyboardInterrupt:
        print("\n[!] Parallel run cancelled by user. Terminating all active processes...")
        for p in processes.values():
            try:
                p.terminate()
            except Exception:
                pass
        sys.exit(130)

    print_summary_report(today, today_str)


def run_pipeline():
    today = datetime.now().date()
    today_str = today.strftime("%Y-%m-%d")
    stats_tracker.reset_stats()

    # CRITICAL: Reset the external-jobs working CSV at the start of every run.
    # Permanent application history (applications_log.csv) is never cleared.
    reset_external_jobs_csv()

    profile = Profile.load()
    total_target = int(profile.data.get("daily_application_limit", 100))
    naukri_safe_limit = int(profile.data.get("naukri_daily_limit", 40))
    linkedin_safe_limit = int(profile.data.get("linkedin_daily_limit", 15))
    foundit_safe_limit = int(profile.data.get("foundit_daily_limit", 30))
    wellfound_safe_limit = int(profile.data.get("wellfound_daily_limit", 25))
    glassdoor_safe_limit = int(profile.data.get("glassdoor_daily_limit", 25))

    initial_applied_history = load_historical_applied()

    print("=" * 65)
    print(f"       STARTING SEQUENTIAL JOB APPLICATION PIPELINE — {today_str}")
    print("=" * 65)
    print(f"Target Total Applications (Across All Platforms): {total_target}")
    print(f"Naukri Safe Daily Cap:                            {naukri_safe_limit}")
    print(f"LinkedIn Safe Daily Cap:                          {linkedin_safe_limit}")
    print(f"Foundit Safe Daily Cap:                           {foundit_safe_limit}")
    print(f"Wellfound Safe Daily Cap:                         {wellfound_safe_limit}")
    print(f"Glassdoor Safe Daily Cap:                         {glassdoor_safe_limit}")
    print(f"Permanent Application History:                    {len(initial_applied_history)} jobs recorded")
    print(f"Today's External Jobs CSV:                        Reset to 0 rows (active working queue)")
    print("=" * 65 + "\n")

    def get_remaining_global() -> int:
        stats = get_applied_stats_today(today)
        applied_so_far = sum(stats.values())
        return max(0, total_target - applied_so_far)

    # 1. Run Hirist
    remaining = get_remaining_global()
    if remaining > 0:
        print(f">>> [1/8] Running Hirist Automation (Target remaining: {remaining})...")
        try:
            import hirist_apply
            hirist_apply.run(limit=remaining)
        except Exception as e:
            print(f"  (Hirist execution note: {e})")
    else:
        print(">>> [1/8] Skipping Hirist — daily application target already reached.")

    # 2. Run Naukri
    remaining = get_remaining_global()
    current_stats = get_applied_stats_today(today)
    naukri_remaining = max(0, naukri_safe_limit - current_stats.get("naukri", 0))
    naukri_alloc = min(remaining, naukri_remaining)
    if remaining > 0 and naukri_alloc > 0:
        print(f"\n>>> [2/8] Running Naukri Automation (Safe cap allocation: {naukri_alloc})...")
        try:
            import naukri_apply
            naukri_apply.run(limit=naukri_alloc)
        except Exception as e:
            print(f"  (Naukri execution note: {e})")
    else:
        print(f"\n>>> [2/8] Skipping Naukri — {'safe daily limit reached' if naukri_remaining <= 0 else 'daily target reached'}.")

    # 3. Run Foundit
    remaining = get_remaining_global()
    current_stats = get_applied_stats_today(today)
    foundit_remaining = max(0, foundit_safe_limit - current_stats.get("foundit", 0))
    foundit_alloc = min(remaining, foundit_remaining)
    if remaining > 0 and foundit_alloc > 0:
        print(f"\n>>> [3/8] Running Foundit Automation (Safe cap allocation: {foundit_alloc})...")
        try:
            import foundit_apply
            foundit_apply.run(limit=foundit_alloc)
        except Exception as e:
            print(f"  (Foundit execution note: {e})")
    else:
        print(f"\n>>> [3/8] Skipping Foundit — {'safe daily limit reached' if foundit_remaining <= 0 else 'daily target reached'}.")

    # 4. Run Wellfound (AngelList Talent)
    remaining = get_remaining_global()
    current_stats = get_applied_stats_today(today)
    wellfound_remaining = max(0, wellfound_safe_limit - current_stats.get("wellfound", 0))
    wellfound_alloc = min(remaining, wellfound_remaining)
    if remaining > 0 and wellfound_alloc > 0:
        print(f"\n>>> [4/8] Running Wellfound Automation (Safe cap allocation: {wellfound_alloc})...")
        try:
            import wellfound_apply
            wellfound_apply.run(limit=wellfound_alloc)
        except Exception as e:
            print(f"  (Wellfound execution note: {e})")
    else:
        print(f"\n>>> [4/8] Skipping Wellfound — {'safe daily limit reached' if wellfound_remaining <= 0 else 'daily target reached'}.")

    # 5. Run Glassdoor / Indeed
    remaining = get_remaining_global()
    current_stats = get_applied_stats_today(today)
    glassdoor_remaining = max(0, glassdoor_safe_limit - current_stats.get("glassdoor", 0))
    glassdoor_alloc = min(remaining, glassdoor_remaining)
    if remaining > 0 and glassdoor_alloc > 0:
        print(f"\n>>> [5/8] Running Glassdoor/Indeed Automation (Safe cap allocation: {glassdoor_alloc})...")
        try:
            import glassdoor_apply
            glassdoor_apply.run(limit=glassdoor_alloc)
        except Exception as e:
            print(f"  (Glassdoor execution note: {e})")
    else:
        print(f"\n>>> [5/8] Skipping Glassdoor — {'safe daily limit reached' if glassdoor_remaining <= 0 else 'daily target reached'}.")

    # 6. Run Uplers
    remaining = get_remaining_global()
    if remaining > 0:
        print(f"\n>>> [6/8] Running Uplers Automation (Target remaining: {remaining})...")
        try:
            import uplers_apply
            uplers_apply.run(limit=remaining)
        except Exception as e:
            print(f"  (Uplers execution note: {e})")
    else:
        print("\n>>> [6/8] Skipping Uplers — daily application target already reached.")

    # 7. Run Instahyre (High-capacity platform to fulfill all remaining quota up to total_target)
    remaining = get_remaining_global()
    if remaining > 0:
        print(f"\n>>> [7/8] Running Instahyre Automation (Allocating remaining target: {remaining})...")
        try:
            import instahyre_apply
            instahyre_apply.run(limit=remaining)
        except Exception as e:
            print(f"  (Instahyre execution note: {e})")
    else:
        print("\n>>> [7/8] Skipping Instahyre — daily application target already reached.")

    print_summary_report(today, today_str)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Multi-portal Job Application Pipeline")
    parser.add_argument("--serial", action="store_true", help="Run sequentially one portal at a time")
    parser.add_argument("--parallel", action="store_true", default=True, help="Run all 7 portals in parallel (default)")
    args = parser.parse_args()

    if args.serial:
        run_pipeline()
    else:
        run_parallel()
