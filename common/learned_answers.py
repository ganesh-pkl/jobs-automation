"""
Remembers answers to screening questions you've typed in yourself before,
keyed by a normalized version of the question text, so you're never asked
the same question twice across runs.

Stored as plain JSON at learned_answers.json in the project root. Sensitive
answers such as DOB and identity numbers are never written here. The remaining
answers may still be personal, so keep this file private like profile.yaml.
"""
import json
import re
from pathlib import Path

_STORE_PATH = Path(__file__).resolve().parent.parent / "learned_answers.json"


def _normalize(question: str) -> str:
    q = question.lower().strip()
    q = re.sub(r"\s+", " ", q)
    q = re.sub(r"[^\w\s]", "", q)  # drop punctuation so minor phrasing differences still match
    return q


def _load() -> dict:
    if not _STORE_PATH.exists():
        return {}
    try:
        return json.loads(_STORE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict):
    _STORE_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    _STORE_PATH.chmod(0o600)


def is_valid_screening_answer(question: str, answer: str) -> bool:
    """Validates whether an answer is sane for the given question."""
    if not answer or not str(answer).strip():
        return False
    q_norm = question.lower().strip()
    ans_norm = str(answer).strip().lower()

    # Drop obvious artifacts / UI fragments
    if any(frag in q_norm for frag in ("cargar currículum", "select resume", "upload resume", "drag and drop")):
        return False

    # Questions demanding numeric answers must not receive boolean or text answers
    is_numeric_q = any(w in q_norm for w in (
        "hourly", "rate", "usd", "in usd", "salary", "ctc", "lpa",
        "how many years", "total years", "years of experience", "work experience",
        "experience in years", "years in", "years with", "production experience",
        "handson experience", "number of years", "scale of", "scale 1",
        "end to end projects", "how many", "backend engineering", "frontend engineering",
        "data engineering", "led a team", "team lead", "project lead",
        "respond '1'", "respond 1", "enter '1'", "enter 1", "in days", "pin code", "pincode", "zip"
    ))
    if is_numeric_q:
        if ans_norm in ("yes", "no", "immediately available", "i do", "true", "false", "na", "n/a"):
            return False
        # Reject verbose narrative sentences for numeric experience questions
        if len(ans_norm.split()) > 4 or any(w in ans_norm for w in ("i have", "yes, i", "while i", "my experience", "i am")):
            return False

    # Location / Identity / Contact fields should not be Yes/No
    is_location_q = any(w in q_norm for w in ("street", "city", "state", "province", "postal", "dob", "date of birth", "email", "phone"))
    if is_location_q and ans_norm in ("yes", "no", "true", "false"):
        return False

    # Questions requiring exact Yes/No must receive only Yes or No
    if "reply with exactly yes or no" in q_norm or "options yes no" in q_norm:
        if ans_norm not in ("yes", "no"):
            return False

    return True


def get_answer(question: str) -> str | None:
    data = _load()
    ans = data.get(_normalize(question))
    if ans and not is_valid_screening_answer(question, ans):
        return None
    return ans


def save_answer(question: str, answer: str):
    if not is_valid_screening_answer(question, answer):
        return
    data = _load()
    data[_normalize(question)] = str(answer).strip()
    _save(data)

