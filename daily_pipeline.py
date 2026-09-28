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
        return {"naukri": 0, "linkedin": 0, "hirist": 0, "uplers": 0}
    
    seen_today = set()
    stats = {"naukri": 0, "linkedin": 0, "hirist": 0, "uplers": 0}
    
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


def run_pipeline():
    today = datetime.now().date()
    today_str = today.strftime("%Y-%m-%d")
    stats_tracker.reset_stats()

    # CRITICAL: Reset the external-jobs working CSV at the start of every run.
    # Permanent application history (applications_log.csv) is never cleared.
    reset_external_jobs_csv()

    initial_applied_history = load_historical_applied()

    print("=" * 65)
    print(f"       STARTING DAILY JOB APPLICATION PIPELINE — {today_str}")
    print("=" * 65)
    print(f"Permanent Application History:      {len(initial_applied_history)} jobs recorded")
    print(f"Today's External Jobs CSV:          Reset to 0 rows (active working queue)")
    print("=" * 65 + "\n")

    # 1. Run Hirist
    print(">>> [1/4] Running Hirist Automation...")
    try:
        import hirist_apply
        hirist_apply.run()
    except Exception as e:
        print(f"  (Hirist execution note: {e})")

    # 2. Run Naukri
    print("\n>>> [2/4] Running Naukri Automation...")
    try:
        import naukri_apply
        naukri_apply.run()
    except Exception as e:
        print(f"  (Naukri execution note: {e})")

    # 3. Run LinkedIn
    print("\n>>> [3/4] Running LinkedIn Easy Apply...")
    try:
        import linkedin_apply
        linkedin_apply.run()
    except Exception as e:
        print(f"  (LinkedIn execution note: {e})")

    # 4. Run Uplers
    print("\n>>> [4/4] Running Uplers Automation...")
    try:
        import uplers_apply
        uplers_apply.run()
    except Exception as e:
        print(f"  (Uplers execution note: {e})")

    # Compute Final Results
    final_applied_history = load_historical_applied()
    final_external_count = get_external_jobs_count()
    final_stats = get_applied_stats_today(today)
    run_stats = stats_tracker.get_stats()

    naukri_applied_today = final_stats["naukri"]
    linkedin_applied_today = final_stats["linkedin"]
    hirist_applied_today = final_stats["hirist"]
    uplers_applied_today = final_stats["uplers"]
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


if __name__ == "__main__":
    run_pipeline()
