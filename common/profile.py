"""Loads and validates the applicant profile from profile.yaml."""
import yaml
from pathlib import Path


class Profile:
    LLM_CONTEXT_FIELDS = (
        "first_name",
        "last_name",
        "full_name",
        "total_experience_years",
        "current_title_official",
        "current_title_functional",
        "current_employer",
        "highest_qualification",
        "school_name",
        "degree_name",
        "field_of_study",
        "graduation_year",
        "education_start_year",
        "education_end_year",
        "current_city",
        "state_province",
        "country_name",
        "postal_code",
        "certifications",
        "skills_primary",
        "skills_adjacent",
        "work_history_narrative",
    )

    def __init__(self, data: dict):
        self.data = data

    @classmethod
    def load(cls, path: str = "profile.yaml") -> "Profile":
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(
                f"{path} not found. Copy profile.example.yaml to profile.yaml and fill in your real details first."
            )
        with p.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
        cls._validate(data)
        return cls(data)

    @staticmethod
    def _validate(data: dict):
        if not isinstance(data, dict):
            raise ValueError("profile.yaml must contain a YAML mapping of profile fields.")
        required_blocking = [
            "target_roles",
            "total_experience_years",
            "notice_period_days",
            "current_ctc_lpa",
            "expected_ctc_lpa",
            "resume_file_name",
        ]
        missing = [k for k in required_blocking if data.get(k) in (None, "")]
        if missing:
            raise ValueError(
                "profile.yaml is missing required fields before this can run safely: "
                + ", ".join(missing)
                + ". These gate real screening questions — fill them in with your real numbers."
            )
        if not isinstance(data["target_roles"], list) or not data["target_roles"]:
            raise ValueError("target_roles must be a non-empty list.")
        for key in ("target_roles", "skills_primary", "skills_adjacent", "certifications",
                    "company_exclude", "company_include_only", "relocate_cities", "remote_locations"):
            value = data.get(key, [])
            if not isinstance(value, list) or any(not isinstance(v, str) or not v.strip() for v in value):
                raise ValueError(f"{key} must be a list of non-empty strings.")
        import math
        for key in ("total_experience_years", "notice_period_days", "current_ctc_lpa", "expected_ctc_lpa",
                    "seniority_floor_years", "seniority_ceiling_years", "salary_floor_lpa",
                    "min_delay_seconds_between_applications", "max_delay_seconds_between_applications"):
            value = data.get(key)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                      or not math.isfinite(value) or value < 0):
                raise ValueError(f"{key} must be a finite non-negative number.")
        for key in ("stop_after_n_applications", "stop_after_n_attempts", "daily_application_limit",
                    "max_pages_per_role", "human_input_timeout_seconds", "job_freshness_days",
                    "naukri_daily_limit", "linkedin_daily_limit", "foundit_daily_limit", "wellfound_daily_limit",
                    "glassdoor_daily_limit", "apna_daily_limit", "max_applicants"):
            if key in data:
                value = data[key]
                if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                    raise ValueError(f"{key} must be a positive integer.")
        for key in ("night_shift_ok", "weekend_ok", "immediately_available", "under_10_applicants_only"):
            if key in data and not isinstance(data[key], bool):
                raise ValueError(f"{key} must be true or false, without quotes.")
        for key in ("current_city", "resume_file_name"):
            if not isinstance(data.get(key), str) or not data[key].strip():
                raise ValueError(f"{key} must be a non-empty string.")
        if data.get("work_mode", "flexible") not in {"onsite_or_hybrid", "remote_only", "remote_first", "flexible"}:
            raise ValueError("Invalid work_mode.")
        if data.get("ctc_disclosure_policy", "negotiable") not in {"negotiable", "real_numbers"}:
            raise ValueError("Invalid ctc_disclosure_policy.")
        for lower, upper in (("seniority_floor_years", "seniority_ceiling_years"),
                             ("min_delay_seconds_between_applications", "max_delay_seconds_between_applications")):
            if lower in data and upper in data and data[lower] > data[upper]:
                raise ValueError(f"{lower} must not exceed {upper}.")
        defaults = {"company_exclude": [], "company_include_only": [], "relocate_cities": [], "remote_locations": [],
                    "work_mode": "flexible", "under_10_applicants_only": True, "max_applicants": 10,
                    "seniority_floor_years": 0, "seniority_ceiling_years": 5,
                    "job_freshness_days": 1, "stop_after_n_applications": 100, "ctc_disclosure_policy": "negotiable",
                    "naukri_daily_limit": 40, "linkedin_daily_limit": 15, "foundit_daily_limit": 30, "wellfound_daily_limit": 25,
                    "glassdoor_daily_limit": 15, "apna_daily_limit": 25}
        for key, value in defaults.items():
            data.setdefault(key, value)
        if data.get("browser_mode", "visible") not in {"visible", "minimized", "headless"}:
            raise ValueError("browser_mode must be visible, minimized, or headless.")

    def llm_context(self) -> dict:
        """Return only professional fields suitable for an external LLM."""
        return {
            key: self.data[key]
            for key in self.LLM_CONTEXT_FIELDS
            if key in self.data
        }

    def __getattr__(self, item):
        if item in self.data:
            return self.data[item]
        raise AttributeError(item)

    def answer_library(self) -> dict:
        """Common recurring screening-question answers, pre-computed once."""
        d = self.data
        skills_list = d.get("skills_primary", []) + d.get("skills_adjacent", [])
        return {
            "first_name": d.get("first_name", "Ganesh"),
            "last_name": d.get("last_name", "Pirikirala"),
            "full_name": d.get("full_name", f"{d.get('first_name', 'Ganesh')} {d.get('last_name', 'Pirikirala')}").strip(),
            "dob": d.get("dob", d.get("date_of_birth", "10/06/2001")),
            "date_of_birth": d.get("date_of_birth", d.get("dob", "10/06/2001")),
            "email": d.get("email", d.get("email_address", "ganesh.pkl08@gmail.com")),
            "phone": str(d.get("mobile_number", d.get("phone", "7659869814"))),
            "mobile_number": str(d.get("mobile_number", d.get("phone", "7659869814"))),
            "years_experience": str(d.get("total_experience_years", 3)),
            "notice_period": (
                "Immediately available" if d.get("immediately_available")
                else f"{d.get('notice_period_days', 0)} days"
            ),
            "current_ctc": (
                "Prefer to discuss" if d.get("ctc_disclosure_policy") == "negotiable"
                else f"{d.get('current_ctc_lpa', 6)} LPA"
            ),
            "expected_ctc": (
                "Negotiable" if d.get("ctc_disclosure_policy") == "negotiable"
                else f"{d.get('expected_ctc_lpa', 9)} LPA"
            ),
            "current_hourly_rate": str(d.get("current_hourly_rate_usd", 15)),
            "expected_hourly_rate": str(d.get("expected_hourly_rate_usd", 25)),
            "current_hourly_rate_usd": str(d.get("current_hourly_rate_usd", 15)),
            "expected_hourly_rate_usd": str(d.get("expected_hourly_rate_usd", 25)),

            "current_city": d.get("current_city", "Hyderabad"),
            "full_location": f"{d.get('current_city', 'Hyderabad')}, {d.get('state_province', 'Telangana')}, {d.get('country_name', 'India')}",
            "state_province": d.get("state_province", "Telangana"),
            "country_name": d.get("country_name", "India"),
            "postal_code": str(d.get("postal_code", "500072")),
            "school_name": d.get("school_name", "Chaitanya Bharathi Institute of Technology"),
            "degree_name": d.get("degree_name", "Bachelor of Technology"),
            "highest_qualification": d.get("highest_qualification", "B.Tech in Electronics and Communication Engineering, Chaitanya Bharathi Institute of Technology"),
            "field_of_study": d.get("field_of_study", "Electronics and Communication Engineering"),
            "graduation_year": str(d.get("graduation_year", 2023)),
            "passout_year": str(d.get("passout_year", 2023)),
            "education_start_year": str(d.get("education_start_year", 2019)),
            "education_end_year": str(d.get("education_end_year", 2023)),
            "current_job_title": d.get("current_title_official", "Full-Stack Software Developer"),
            "current_employer": d.get("current_employer", "Cognitivo"),
            "gender": d.get("gender", "Male"),
            "skills_csv": ", ".join(skills_list[:8]) if skills_list else "Java, Spring Boot, React.js, Node.js, TypeScript, JavaScript, REST APIs, MySQL",
            "relocate": "Yes" if d.get("relocate_cities") else "No",
            "night_shift": "Yes" if d.get("night_shift_ok") else "No",
            "weekend_work": "Yes" if d.get("weekend_ok") else "No",
        }

