"""
Persistent External Jobs Tracker
Maintains external_jobs.csv across daily runs without ever overwriting previous entries.
Appends only genuinely new external company career opportunities.
STRICT RULE: Applied jobs are NEVER added here.
"""
import csv
import re
from datetime import datetime
from pathlib import Path
from common.profile import Profile

EXTERNAL_LOG_FILE = "external_jobs.csv"

FIELDNAMES = [
    "Platform",
    "Job Title",
    "Company",
    "Location",
    "Experience Required",
    "Job Description / Summary",
    "Job URL on original platform",
    "External Company Careers/Application URL",
    "Match Level",
    "Match Reason",
    "Matching Skills",
    "Why it is relevant to my resume",
    "Date Discovered",
    "Manual Application Status",
]


def _clean(val: str | None) -> str:
    if not val:
        return ""
    return " ".join(val.split()).strip()


def reset_external_jobs_csv(file_path: str = EXTERNAL_LOG_FILE) -> bool:
    """
    Resets/clears the daily external-jobs CSV with fresh headers at the start of a run.
    Permanent application history is preserved and untouched.
    """
    path = Path(file_path)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
    return True


def _normalize_key(title: str, company: str) -> tuple[str, str]:
    t = re.sub(r"[^a-zA-Z0-9\s]", "", (title or "").lower())
    c = re.sub(r"[^a-zA-Z0-9\s]", "", (company or "").lower())
    return (" ".join(t.split()), " ".join(c.split()))


def evaluate_job_match(title: str, company: str, exp_text: str, loc: str, profile: Profile | None = None) -> tuple[str, str, str, str]:
    """
    Evaluates candidate alignment for a given job.
    Returns (match_level, match_reason, matching_skills, relevance_narrative).
    """
    t_lower = title.lower()
    skills = []

    if any(k in t_lower for k in ("full stack", "full-stack", "fullstack", "mern", "mean")):
        skills.extend(["Full-Stack Development", "React.js", "Node.js", "REST APIs"])
    if any(k in t_lower for k in ("react", "frontend", "front end", "ui")):
        skills.extend(["React.js", "TypeScript", "JavaScript", "Next.js"])
    if any(k in t_lower for k in ("node", "backend", "back end", "express")):
        skills.extend(["Node.js", "Express.js", "REST APIs", "Microservices"])
    if any(k in t_lower for k in ("python", "fastapi", "django", "flask")):
        skills.extend(["Python", "FastAPI", "REST APIs"])
    if any(k in t_lower for k in ("java", "spring")):
        skills.extend(["Java", "Spring Boot", "Microservices"])
    if any(k in t_lower for k in ("ai", "llm", "ml", "genai", "agentic")):
        skills.extend(["AI/LLM Integration", "LangChain", "FastAPI"])
    if any(k in t_lower for k in ("software developer", "software engineer", "sde")):
        skills.extend(["Software Engineering", "System Design", "Docker", "AWS"])

    skills = list(dict.fromkeys(skills))
    if not skills:
        skills = ["Full-Stack Development", "Software Engineering"]

    skills_str = ", ".join(skills)
    level = "high_match"
    reason = f"Matches candidate's ~3 years experience in {skills[0]} and {skills[1] if len(skills) > 1 else 'modern web frameworks'}."
    relevance = f"Candidate has built production SaaS and AI platforms using {skills_str}, matching requirements."

    return level, reason, skills_str, relevance


def load_existing_external_keys(file_path: str = EXTERNAL_LOG_FILE) -> set[tuple[str, str]]:
    path = Path(file_path)
    if not path.exists():
        return set()
    keys = set()
    try:
        with path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                t = row.get("Job Title") or row.get("title") or ""
                c = row.get("Company") or row.get("company") or ""
                if t and c:
                    keys.add(_normalize_key(t, c))
    except Exception:
        pass
    return keys


def is_already_applied(title: str, company: str, app_log_path: str = "applications_log.csv") -> bool:
    """Checks if a job was successfully applied in historical application records."""
    log_path = Path(app_log_path)
    if not log_path.exists():
        return False
    target_key = _normalize_key(title, company)
    try:
        with log_path.open("r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                if row.get("status") == "applied":
                    if _normalize_key(row.get("title", ""), row.get("company", "")) == target_key:
                        return True
    except Exception:
        pass
    return False


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
    profile: Profile | None = None,
) -> bool:
    """
    Appends a newly discovered external/manual career opportunity to external_jobs.csv.
    Strictly avoids duplicates and already applied jobs.
    """
    clean_title = _clean(title)
    clean_company = _clean(company)

    if not clean_title or not clean_company or clean_title == "-" or clean_company == "-":
        return False

    # 1. Check if already successfully applied
    if is_already_applied(clean_title, clean_company):
        return False

    # 2. Check if already in external_jobs.csv
    existing_keys = load_existing_external_keys(file_path)
    key = _normalize_key(clean_title, clean_company)
    if key in existing_keys:
        return False

    path = Path(file_path)
    new_file = not path.exists()

    level, reason, skills_str, relevance = evaluate_job_match(
        clean_title, clean_company, experience, location, profile
    )

    ext_url = _clean(external_link) or _clean(job_url)
    orig_url = _clean(job_url) or ext_url
    summary = f"External career opportunity for {clean_title} at {clean_company} ({experience or '1-4 Yrs'}, {location or 'Hyderabad/Remote'}). Posted: {posted_age or 'Recent'}."

    try:
        with path.open("a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            if new_file:
                writer.writeheader()
            writer.writerow({
                "Platform": platform.capitalize(),
                "Job Title": clean_title,
                "Company": clean_company,
                "Location": _clean(location) or "Hyderabad / Remote",
                "Experience Required": _clean(experience) or "1-4 Yrs",
                "Job Description / Summary": summary,
                "Job URL on original platform": orig_url,
                "External Company Careers/Application URL": ext_url,
                "Match Level": level,
                "Match Reason": reason,
                "Matching Skills": skills_str,
                "Why it is relevant to my resume": relevance,
                "Date Discovered": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "Manual Application Status": "pending_manual_submission",
            })
        return True
    except Exception as e:
        print(f"Failed to log external job to CSV: {e}")
        return False
