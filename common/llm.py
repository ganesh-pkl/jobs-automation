"""Dispatch free-text answer drafting to a configured provider."""
import importlib
import os
from pathlib import Path

_RETRYABLE_ERROR_HINTS = (
    "429",
    "503",
    "quota",
    "rate limit",
    "resource_exhausted",
    "exhausted",
    "unavailable",
    "high demand",
)


def _looks_retryable(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(hint in text for hint in _RETRYABLE_ERROR_HINTS)


def _configured_key(name: str) -> bool:
    from .env import setting
    return bool(setting(name))


def _provider(module_name: str):
    return importlib.import_module(f"{__package__}.{module_name}")


def draft_answer(question: str, profile_data: dict, job_context: str = "") -> str:
    has_gemini = _configured_key("GEMINI_API_KEY")
    has_groq = _configured_key("GROQ_API_KEY")
    if not has_gemini and not has_groq:
        raise RuntimeError(
            "No AI provider is configured. Add GEMINI_API_KEY or GROQ_API_KEY to .env."
        )

    if has_gemini:
        try:
            return _provider("gemini").draft_answer(question, profile_data, job_context)
        except Exception as exc:
            if not (_looks_retryable(exc) and has_groq):
                raise
            print("  (Gemini unavailable. Falling back to Groq...)")

    return _provider("groq_llm").draft_answer(question, profile_data, job_context)
