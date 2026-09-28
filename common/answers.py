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
    "notice_period": ["notice period", "notice", "joining time", "how soon"],
    "current_ctc": ["current ctc", "current salary", "current compensation", "present ctc", "present salary", "current pay"],
    "expected_ctc": ["expected ctc", "expected salary", "expected compensation", "desired ctc", "expectation"],
    "current_city": ["current city", "current location", "which city", "current place", "located in", "base location"],
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
    "work experience",
)


def is_sensitive_field(question: str) -> bool:
    lower_q = question.lower()
    return any(hint in lower_q for hint in SENSITIVE_FIELD_HINTS)


def direct_profile_answer(question: str, answers: dict) -> str | None:
    """Answer questions that unambiguously map to a profile fact in answer_library."""
    lower_q = " ".join(question.lower().split())
    asks_skill_specific_experience = bool(
        re.search(
            r"\b(?:experience|years?)\b[^?]{0,80}\b(?:in|with|using|on)\b",
            lower_q,
        )
        or re.search(r"\byears?\s+of\s+[^?]+\s+experience\b", lower_q)
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

    for key, phrases in TRIGGER_PHRASES.items():
        if key in answers and any(phrase in lower_q for phrase in phrases):
            return str(answers[key])
    return None


def get_screening_answer(question_text: str, profile: Profile, job_context: str = "") -> str | None:
    """
    Returns an answer string for any screening question using:
    1. Learned answers repository
    2. Direct profile fact matching (CTC, notice period, location, total experience)
    3. Groq LLM dynamic drafting with applicant profile & facts
    """
    if is_sensitive_field(question_text):
        return ask_user(f"Sensitive screening question:\n{question_text}")

    # 1. Check remembered/learned answers
    stored = learned_answers.get_answer(question_text)
    if stored:
        return stored

    # 2. Check direct profile facts
    answers = profile.answer_library()
    direct = direct_profile_answer(question_text, answers)
    if direct is not None:
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

    # 4. Fallback if LLM output was [NEEDS_HUMAN_INPUT
    lower_q = question_text.lower()
    if "ctc" in lower_q or "salary" in lower_q:
        return answers.get("expected_ctc", "Negotiable")
    if "notice" in lower_q:
        return answers.get("notice_period", "Immediately available")
    if "location" in lower_q or "city" in lower_q:
        return answers.get("current_city", "Hyderabad")
    if "experience" in lower_q:
        return answers.get("years_experience", "3")

    ans = ask_user(f"Screening question needed:\n{question_text}")
    if ans:
        learned_answers.save_answer(question_text, ans)
    return ans
