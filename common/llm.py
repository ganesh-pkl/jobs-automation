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
    from .env import setting
    chosen_provider = setting("AI_PROVIDER", "").strip().lower()

    if chosen_provider == "ollama":
        return _provider("ollama_llm").draft_answer(question, profile_data, job_context)
    if chosen_provider in ("groq", "grok"):
        return _provider("groq_llm").draft_answer(question, profile_data, job_context)
    if chosen_provider == "gemini":
        return _provider("gemini").draft_answer(question, profile_data, job_context)

    has_gemini = _configured_key("GEMINI_API_KEY")
    has_groq = _configured_key("GROQ_API_KEY")

    if has_gemini:
        try:
            return _provider("gemini").draft_answer(question, profile_data, job_context)
        except Exception as exc:
            if not (_looks_retryable(exc) and has_groq):
                raise
            print("  (Gemini unavailable. Falling back to Groq...)")

    if has_groq:
        try:
            return _provider("groq_llm").draft_answer(question, profile_data, job_context)
        except Exception as exc:
            if not _looks_retryable(exc):
                raise
            print(f"  (Groq hit {exc}. Checking Ollama...)")

    # If neither Gemini nor Groq succeeded/configured, check if local Ollama is available
    try:
        ollama = _provider("ollama_llm")
        if ollama.is_available():
            return ollama.draft_answer(question, profile_data, job_context)
    except Exception:
        pass

    if not has_gemini and not has_groq:
        raise RuntimeError(
            "No AI provider is configured. Add GEMINI_API_KEY or GROQ_API_KEY to .env, or run a local Ollama server."
        )

    return _provider("groq_llm").draft_answer(question, profile_data, job_context)
