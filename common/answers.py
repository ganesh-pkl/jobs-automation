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
    "gender": [
        "gender", "sex",
    ],
    "notice_period": ["notice period", "notice", "joining time", "how soon"],
    "current_ctc": ["current ctc", "current salary", "current compensation", "present ctc", "present salary", "current pay"],
    "expected_ctc": ["expected ctc", "expected salary", "expected compensation", "desired ctc", "expectation"],
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

    # 2. Check Graduation / Education Years FIRST so "graduation year" is never mistaken for experience years
    for phrase in TRIGGER_PHRASES["graduation_year"]:
        if phrase in lower_q:
            return str(answers.get("graduation_year", "2023"))

    for phrase in TRIGGER_PHRASES["education_start_year"]:
        if phrase in lower_q:
            return str(answers.get("education_start_year", "2019"))

    # 3. Check School / University / College Name
    for phrase in TRIGGER_PHRASES["school_name"]:
        if phrase in lower_q and not any(w in lower_q for w in ("degree", "grade", "gpa", "percentage", "cgpa")):
            return str(answers.get("school_name", "Chaitanya Bharathi Institute of Technology"))

    # 4. Check Degree & Field of Study
    for phrase in TRIGGER_PHRASES["field_of_study"]:
        if phrase in lower_q:
            return str(answers.get("field_of_study", "Electronics and Communication Engineering"))

    for phrase in TRIGGER_PHRASES["degree_name"]:
        if phrase in lower_q:
            return str(answers.get("degree_name", "Bachelor of Technology"))

    # 5. Check Location & Postal fields
    for phrase in TRIGGER_PHRASES["postal_code"]:
        if phrase in lower_q:
            return str(answers.get("postal_code", "500072"))

    for phrase in TRIGGER_PHRASES["state_province"]:
        if phrase in lower_q:
            return str(answers.get("state_province", "Telangana"))

    for phrase in TRIGGER_PHRASES["country_name"]:
        if phrase in lower_q:
            return str(answers.get("country_name", "India"))

    # 6. Check Experience fields
    asks_skill_specific_experience = bool(
        re.search(
            r"\b(?:experience|years?)\b[^?]{0,80}\b(?:in|with|using|on)\s+(?!years?\b|yrs?\b|months?\b)[a-z0-9#+.]+",
            lower_q,
        )
        or re.search(r"\byears?\s+of\s+(?!experience\b)[a-z0-9#+.]+\s+experience\b", lower_q)
    )
    if asks_skill_specific_experience:
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

    # 7. Check other Trigger phrases
    for key, phrases in TRIGGER_PHRASES.items():
        if key in answers and any(phrase in lower_q for phrase in phrases):
            return str(answers[key])
    return None


def get_screening_answer(question_text: str, profile: Profile, job_context: str = "") -> str | None:
    """
    Returns an answer string for any screening question using:
    1. Learned answers repository
    2. Direct profile fact matching (CTC, notice period, location, total & skill experience, DOB, name)
    3. Groq LLM dynamic drafting with applicant profile & facts
    4. Smart profile defaults
    """
    answers = profile.answer_library()
    if is_sensitive_field(question_text):
        direct = direct_profile_answer(question_text, answers)
        if direct is not None:
            return direct
        return ask_user(f"Sensitive screening question:\n{question_text}")

    # 1. Check remembered/learned answers
    stored = learned_answers.get_answer(question_text)
    if stored:
        return stored

    # 2. Check direct profile facts
    direct = direct_profile_answer(question_text, answers)
    if direct is not None:
        learned_answers.save_answer(question_text, direct)
        return direct

    # 3. Dynamic Groq LLM generation
    try:
        full_context = {**profile.llm_context(), **answers}
        draft = llm.draft_answer(question_text, full_context, job_context)
        if not draft.startswith("[NEEDS_HUMAN_INPUT"):
            learned_answers.save_answer(question_text, draft)
            return draft
    except Exception as e:
        print(f"  (LLM drafting warning: {e})")

    # 4. Fallback defaults if LLM could not parse or output was [NEEDS_HUMAN_INPUT
    lower_q = question_text.lower()
    if any(w in lower_q for w in ("graduat", "passout", "passing year", "completion year", "end year")):
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
    elif "ctc" in lower_q or "salary" in lower_q:
        fallback = str(answers.get("expected_ctc", "Negotiable"))
    elif "notice" in lower_q:
        fallback = str(answers.get("notice_period", "Immediately available"))
    elif "location" in lower_q or "city" in lower_q:
        fallback = str(answers.get("current_city", "Hyderabad"))
    elif "experience" in lower_q or "years" in lower_q or "yrs" in lower_q:
        fallback = str(answers.get("years_experience", "3"))
    elif "?" in question_text and any(w in lower_q for w in ("willing", "ready", "open to", "comfortable", "have experience", "worked on")):
        fallback = "Yes"
    else:
        fallback = "Yes"

    learned_answers.save_answer(question_text, fallback)
    return fallback
