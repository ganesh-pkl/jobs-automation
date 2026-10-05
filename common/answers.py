"""
Centralized screening question answering system.
Combines learned answers, direct profile fact extraction, and Groq LLM dynamic drafting.
"""
import re
import common.learned_answers as learned_answers
import common.llm as llm
from common.human_input import ask_user
from common.profile import Profile

SENSITIVE_FIELD_HINTS = [
    "date of birth", "dob", "pan number", "pan card", "aadhar", "aadhaar",
    "passport", "bank account", "ifsc", "father's name", "father name",
    "mother's name", "mother name", "marital status", "blood group",
    "emergency contact", "voter id", "driving licence", "driving license",
]

TRIGGER_PHRASES = {
    "first_name": ["first name", "given name", "forename"],
    "last_name": ["last name", "family name", "surname"],
    "full_name": ["full name", "your name", "candidate name", "applicant name"],
    "dob": ["date of birth", "dob", "birth date", "birthdate", "d.o.b"],
    "email": ["email address", "email id", "e-mail", "email"],
    "phone": ["mobile number", "phone number", "contact number", "mobile", "phone"],
    "graduation_year": [
        "graduation year", "year of graduation", "passing year", "year of passing",
        "passout year", "pass out year", "completion year", "year of completion",
        "year graduated", "graduated in", "passing out year", "year of pass", "end year",
        "graduation date", "completion date",
    ],
    "education_start_year": [
        "start year", "starting year", "year of joining", "commencement year", "start date",
    ],
    "school_name": [
        "school name", "college name", "university name", "institute name", "institution name",
        "school or university", "name of school", "name of college", "name of university",
        "name of institute", "alma mater", "school", "university", "college", "institute", "institution",
    ],
    "degree_name": [
        "degree name", "qualification held", "highest qualification held", "highest qualification",
        "education level", "highest degree", "highest level of education", "degree",
    ],
    "field_of_study": [
        "field of study", "major", "specialization", "branch", "stream", "discipline", "department", "course",
    ],
    "postal_code": [
        "zip/postal code", "postal code", "zip code", "pin code", "pincode", "zip",
    ],
    "state_province": [
        "state/province", "state", "province",
    ],
    "country_name": [
        "country of residence", "nationality", "country",
    ],
    "current_job_title": [
        "current job title", "current title", "current designation", "present title",
        "present designation", "official title", "job title", "designation",
    ],
    "current_employer": [
        "current employer", "current company", "present employer", "present company",
    ],
    "skills_csv": [
        "skill set", "technical skills", "primary skills", "key skills",
    ],
    "current_hourly_rate": [
        "current hourly rate", "current hourly", "present hourly rate",
        "current rate in usd", "current rate (in usd)", "current hourly pay",
        "hourly rate (in usd)", "current rate", "current pay per hour",
    ],
    "expected_hourly_rate": [
        "expected hourly rate", "expected hourly", "desired hourly rate",
        "expected rate in usd", "expected rate (in usd)", "rate for this engagement",
        "expected hourly pay", "hourly rate for this engagement", "expected rate",
        "desired rate in usd", "rate per hour",
    ],
    "gender": [
        "gender", "sex",
    ],
    "notice_period": ["notice period", "notice", "joining time", "how soon"],
    "current_ctc": ["current ctc", "current salary", "current compensation", "present ctc", "present salary", "current pay", "current annual salary"],
    "expected_ctc": ["expected ctc", "expected salary", "expected compensation", "desired ctc", "expectation", "expected annual salary"],
    "current_city": ["current city", "current location", "which city", "current place", "located in", "base location", "city"],
    "relocate": ["relocate", "relocation", "willing to move"],
    "night_shift": ["night shift"],
    "weekend_work": ["weekend"],
}

TOTAL_EXPERIENCE_PHRASES = (
    "total experience",
    "overall experience",
    "total years of experience",
    "overall years of experience",
    "professional experience",
    "years of experience",
    "experience in years",
    "work experience",
    "how many years of experience",
)


def is_sensitive_field(question: str) -> bool:
    lower_q = question.lower()
    return any(hint in lower_q for hint in SENSITIVE_FIELD_HINTS)


def direct_profile_answer(question: str, answers: dict) -> str | None:
    """Answer questions that unambiguously map to a profile fact in answer_library."""
    lower_q = " ".join(question.lower().split())

    # 1. Check Name and Contact fields
    for phrase in TRIGGER_PHRASES["first_name"]:
        if phrase in lower_q:
            return str(answers.get("first_name", "Ganesh"))

    for phrase in TRIGGER_PHRASES["last_name"]:
        if phrase in lower_q:
            return str(answers.get("last_name", "Pirikirala"))

    for phrase in TRIGGER_PHRASES["full_name"]:
        if phrase in lower_q:
            return str(answers.get("full_name", "Ganesh Pirikirala"))

    for phrase in TRIGGER_PHRASES["dob"]:
        if phrase in lower_q:
            return str(answers.get("dob", answers.get("date_of_birth", "15/08/2001")))

    for phrase in TRIGGER_PHRASES["email"]:
        if phrase in lower_q:
            return str(answers.get("email", "ganesh.pkl08@gmail.com"))

    for phrase in TRIGGER_PHRASES["phone"]:
        if phrase in lower_q:
            return str(answers.get("phone", "7659869814"))

    # 2. Check Hourly Rates (USD) before general CTC/Salary
    if any(w in lower_q for w in ("hourly", "per hour", "usd", "rate (in usd)", "rate in usd")):
        if any(w in lower_q for w in ("current", "present", "now", "today", "currently")):
            return str(answers.get("current_hourly_rate_usd", answers.get("current_hourly_rate", 15)))
        return str(answers.get("expected_hourly_rate_usd", answers.get("expected_hourly_rate", 25)))

    for phrase in TRIGGER_PHRASES["current_hourly_rate"]:
        if phrase in lower_q:
            return str(answers.get("current_hourly_rate_usd", 15))

    for phrase in TRIGGER_PHRASES["expected_hourly_rate"]:
        if phrase in lower_q:
            return str(answers.get("expected_hourly_rate_usd", 25))

    # 3. Check Immediate Joiner numeric prompts ('1' if immediate joiner)
    if any(p in lower_q for p in ("respond '1'", "respond 1", "enter '1'", "enter 1", "type '1'", "type 1", "reply '1'", "reply 1")):
        return "1"

    # 4. Check Short-term contract / flexible engagement comfort
    if any(w in lower_q for w in ("contract", "short-term", "short term", "engagement", "3 months", "6 months")) and any(w in lower_q for w in ("comfortable", "open to", "interested", "willing", "ready", "agree", "accept")):
        return "Yes"

    # 5. Check Graduation / Education Years FIRST so "graduation year" is never mistaken for experience years
    for phrase in TRIGGER_PHRASES["graduation_year"]:
        if phrase in lower_q:
            return str(answers.get("graduation_year", "2023"))

    for phrase in TRIGGER_PHRASES["education_start_year"]:
        if phrase in lower_q:
            return str(answers.get("education_start_year", "2019"))

    # 6. Check School / University / College Name
    for phrase in TRIGGER_PHRASES["school_name"]:
        if phrase in lower_q and not any(w in lower_q for w in ("degree", "grade", "gpa", "percentage", "cgpa")):
            return str(answers.get("school_name", "Chaitanya Bharathi Institute of Technology"))

    # 7. Check Degree & Field of Study
    for phrase in TRIGGER_PHRASES["field_of_study"]:
        if phrase in lower_q:
            return str(answers.get("field_of_study", "Electronics and Communication Engineering"))

    for phrase in TRIGGER_PHRASES["degree_name"]:
        if phrase in lower_q:
            return str(answers.get("degree_name", "Bachelor of Technology"))

    # 8. Check Location & Postal fields
    for phrase in TRIGGER_PHRASES["postal_code"]:
        if phrase in lower_q:
            return str(answers.get("postal_code", "500072"))

    for phrase in TRIGGER_PHRASES["state_province"]:
        if phrase in lower_q:
            return str(answers.get("state_province", "Telangana"))

    for phrase in TRIGGER_PHRASES["country_name"]:
        if phrase in lower_q:
            return str(answers.get("country_name", "India"))

    # 9. Check Excluded / Unsupported skills (0 years)
    EXCLUDED_SKILLS = (
        "sfcc", "salesforce", ".net", "c#", "c and net", "net core", "asp.net",
        "entity framework", "dapper", "alation", "ibm db2", "db2", "opencl",
        "sap", "abap", "cobol", "fortran", "netapp", "flexera",
    )
    if any(s in lower_q for s in EXCLUDED_SKILLS) and any(w in lower_q for w in ("experience", "years", "yrs", "handson", "production")):
        return "0"

    # 10. Check Leadership / Team Leading / Project Management
    LEADERSHIP_PHRASES = (
        "led a team or project", "led a team", "led a project", "lead a team", "lead a project",
        "team lead", "project lead", "technical lead", "tech lead", "leadership",
        "project management", "team management", "leading a team",
    )
    if any(p in lower_q for p in LEADERSHIP_PHRASES):
        return str(answers.get("years_experience", "3"))

    # 11. Check Enterprise Projects / Deployed Count
    if any(w in lower_q for w in ("end to end projects", "enterprise grade", "projects developed and deployed", "how many projects")):
        return "3"

    # 12. Check Rating scale (e.g. rate yourself on a scale of 1 to 5)
    if "scale of 1" in lower_q or "scale 1" in lower_q:
        return "4"

    # 13. Check Candidate Core Technical Domains & Skill Experience
    # Open-ended descriptive questions (e.g. "Describe your experience with React.js") should route to LLM
    if any(lower_q.startswith(w) for w in ("describe", "explain", "tell us", "share", "why", "how did you", "what was", "detail", "write")):
        return None

    CORE_SKILLS = (
        "backend", "back-end", "back end", "frontend", "front-end", "front end", "full stack",
        "full-stack", "fullstack", "mern", "mean", "web development", "software development",
        "software engineering", "programming", "python", "fastapi", "ipython", "django", "flask",
        "javascript", "typescript", "react", "reactjs", "react.js", "next", "nextjs", "next.js",
        "node", "nodejs", "node.js", "express", "html", "css", "tailwind", "java", "spring",
        "spring boot", "rest", "rest api", "restful", "apis", "microservices", "system design",
        "distributed system", "distributed systems", "concurrency", "sql", "mysql", "postgresql",
        "postgres", "mongodb", "redis", "relational database", "aws", "azure", "cloud", "docker",
        "kubernetes", "ci/cd", "cicd", "devops", "git", "github actions", "ai", "genai",
        "generative ai", "llm", "rag", "agentic", "ai agents", "langchain", "langgraph",
        "machine learning", "data engineering", "problem solving", "sensors", "systems engineering",
        "ecommerce", "saas product", "saas",
    )

    # Check if question is a short skill/competence prompt (e.g. "Backend engineering ?", "Python ?")
    clean_q = re.sub(r"[?*:\s]+$", "", lower_q).strip()
    if clean_q in CORE_SKILLS or any(clean_q == f"{s} engineering" for s in ("backend", "frontend", "data", "cloud", "software", "systems")):
        return str(answers.get("years_experience", "3"))

    # Check skill-specific experience or general experience
    asks_skill_specific_experience = bool(
        re.search(
            r"\b(?:experience|years?)\b[^?]{0,80}\b(?:in|with|using|on)\s+(?!years?\b|yrs?\b|months?\b)[a-z0-9#+.]+",
            lower_q,
        )
        or re.search(r"\byears?\s+of\s+(?!experience\b)[a-z0-9#+.]+\s+experience\b", lower_q)
    )
    if asks_skill_specific_experience:
        # Check if the requested skill is in core skills
        if any(s in lower_q for s in CORE_SKILLS):
            return str(answers.get("years_experience", "3"))
        try:
            if float(answers.get("years_experience", 0)) <= 0:
                return "0"
        except (TypeError, ValueError):
            pass

    if (
        "years_experience" in answers
        and not asks_skill_specific_experience
        and any(phrase in lower_q for phrase in TOTAL_EXPERIENCE_PHRASES)
    ):
        return str(answers["years_experience"])

    # 14. Check other Trigger phrases
    for key, phrases in TRIGGER_PHRASES.items():
        if key in answers and any(phrase in lower_q for phrase in phrases):
            return str(answers[key])
    return None


def get_screening_answer(question_text: str, profile: Profile, job_context: str = "") -> str | None:
    """
    Returns an answer string for any screening question using:
    1. Direct profile fact matching (CTC, hourly rates, notice period, location, total & skill experience, DOB, name)
    2. Learned answers repository (with validation)
    3. Groq LLM dynamic drafting with applicant profile & facts
    4. Smart, strictly-typed profile defaults
    """
    answers = profile.answer_library()
    if is_sensitive_field(question_text):
        direct = direct_profile_answer(question_text, answers)
        if direct is not None:
            return direct
        return ask_user(f"Sensitive screening question:\n{question_text}")

    # 1. Direct profile facts FIRST (highest accuracy, never corrupted)
    direct = direct_profile_answer(question_text, answers)
    if direct is not None:
        learned_answers.save_answer(question_text, direct)
        return direct

    # 2. Check remembered/learned answers
    stored = learned_answers.get_answer(question_text)
    if stored:
        return stored

    # 3. Dynamic Groq LLM generation
    try:
        full_context = {**profile.llm_context(), **answers}
        draft = llm.draft_answer(question_text, full_context, job_context)
        if not draft.startswith("[NEEDS_HUMAN_INPUT") and learned_answers.is_valid_screening_answer(question_text, draft):
            learned_answers.save_answer(question_text, draft)
            return draft
    except Exception as e:
        print(f"  (LLM drafting warning: {e})")

    # 4. Strict, type-safe fallback defaults
    lower_q = question_text.lower()

    # Numeric hourly rate
    if any(w in lower_q for w in ("hourly", "per hour", "usd", "rate (in usd)", "rate in usd")):
        if any(w in lower_q for w in ("current", "present", "now")):
            fallback = str(answers.get("current_hourly_rate_usd", "15"))
        else:
            fallback = str(answers.get("expected_hourly_rate_usd", "25"))
    elif any(p in lower_q for p in ("respond '1'", "respond 1", "enter '1'", "enter 1", "if you are an immediate joiner")):
        fallback = "1"
    elif any(w in lower_q for w in ("graduat", "passout", "passing year", "completion year", "end year")):
        fallback = str(answers.get("graduation_year", "2023"))
    elif any(w in lower_q for w in ("school", "university", "college", "institute")):
        fallback = str(answers.get("school_name", "Chaitanya Bharathi Institute of Technology"))
    elif any(w in lower_q for w in ("degree", "qualification")):
        fallback = str(answers.get("degree_name", "Bachelor of Technology"))
    elif any(w in lower_q for w in ("major", "branch", "stream", "field of study")):
        fallback = str(answers.get("field_of_study", "Electronics and Communication Engineering"))
    elif any(w in lower_q for w in ("zip", "postal", "pin code", "pincode")):
        fallback = str(answers.get("postal_code", "500072"))
    elif any(w in lower_q for w in ("state", "province")):
        fallback = str(answers.get("state_province", "Telangana"))
    elif "country" in lower_q:
        fallback = str(answers.get("country_name", "India"))
    elif any(w in lower_q for w in ("current ctc", "current salary", "present ctc", "current compensation")):
        fallback = str(answers.get("current_ctc_lpa", "6"))
    elif any(w in lower_q for w in ("expected ctc", "expected salary", "desired ctc", "expected compensation")):
        fallback = str(answers.get("expected_ctc_lpa", "9"))
    elif "ctc" in lower_q or "salary" in lower_q or "compensation" in lower_q:
        fallback = str(answers.get("expected_ctc_lpa", "9"))
    elif "notice" in lower_q or "how soon" in lower_q or "joining" in lower_q:
        if any(w in lower_q for w in ("in days", "days", "number of days", "numeric")):
            fallback = "0"
        else:
            fallback = "1"
    elif "location" in lower_q or "city" in lower_q:
        fallback = str(answers.get("current_city", "Hyderabad"))
    elif any(w in lower_q for w in ("experience", "years", "yrs", "how many years", "total years", "backend", "frontend", "full stack", "fullstack", "engineering", "lead", "leadership", "scale")):
        fallback = str(answers.get("years_experience", "3"))
    elif any(w in lower_q for w in ("willing", "ready", "open to", "comfortable", "have experience", "worked on", "authorized", "agree", "accept")):
        fallback = "Yes"
    elif any(w in lower_q for w in ("sponsorship", "visa required", "require visa", "require sponsorship")):
        fallback = "No"
    elif lower_q.startswith(("is ", "are ", "do ", "does ", "did ", "have ", "has ", "can ", "will ", "would ", "were ", "was ")):
        fallback = "Yes"
    elif lower_q.strip().endswith("?") and not any(w in lower_q for w in ("what", "where", "why", "which", "how")):
        # Short technical competence question (e.g. "Backend engineering ?")
        fallback = str(answers.get("years_experience", "3"))
    else:
        fallback = "Yes"

    learned_answers.save_answer(question_text, fallback)
    return fallback

