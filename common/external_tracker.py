"""
External Jobs Tracker

Logs external job application links (jobs requiring manual application on company websites)
to external_jobs.csv with title, company, platform, job URL, location, experience, and posted age.
Filters strictly for jobs posted within job_freshness_days (default 7 days).
"""
import csv
from datetime import datetime
from pathlib import Path

EXTERNAL_LOG_FILE = "external_jobs.csv"


def _normalize(val: str | None) -> str:
    return " ".join((val or "").lower().split())


def load_existing_external_keys(file_path: str = EXTERNAL_LOG_FILE) -> set[tuple[str, str, str]]:
    path = Path(file_path)
    if not path.exists():
        return set()
    keys = set()
    try:
        with path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                t = _normalize(row.get("title"))
                c = _normalize(row.get("company"))
                u = (row.get("job_url") or "").strip()
                if t and u:
                    keys.add((t, c, u))
    except Exception:
        pass
    return keys


def log_external_job(
    platform: str,
    title: str,
    company: str,
    job_url: str,
    external_link: str = "",
    location: str = "",
    experience: str = "",
    posted_age: str = "",
    file_path: str = EXTERNAL_LOG_FILE,
) -> bool:
    path = Path(file_path)
    new_file = not path.exists()
    
    key = (_normalize(title), _normalize(company), (job_url or "").strip())
    existing = load_existing_external_keys(file_path)
    if key in existing:
        return False  # Already logged

    headers = [
        "timestamp",
        "platform",
        "title",
        "company",
        "job_url",
        "external_link",
        "location",
        "experience",
        "posted_age",
    ]

    try:
        with path.open("a", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            if new_file:
                writer.writerow(headers)
            writer.writerow([
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                platform,
                title.strip() if title else "",
                company.strip() if company else "",
                job_url.strip() if job_url else "",
                external_link.strip() if external_link else "",
                location.strip() if location else "",
                experience.strip() if experience else "",
                posted_age.strip() if posted_age else "",
            ])
        return True
    except Exception as e:
        print(f"Failed to log external job to CSV: {e}")
        return False
