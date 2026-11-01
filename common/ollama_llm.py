"""
Generates free-text screening answers using a local Ollama instance
(e.g., llama3.2, mistral, qwen). Completely local, private, and free.
"""
import os
import requests
from pathlib import Path
from .env import setting

DEFAULT_HOST = "http://localhost:11434"
DEFAULT_MODEL = "llama3.2:1b"

_SYSTEM_INSTRUCTIONS = """\
You are drafting a short, direct answer to a job-application screening question in the voice of the real applicant described below.

Rules:
1. The applicant has 3 years of hands-on professional experience across their full-stack software development career (including React, Node.js, JavaScript, TypeScript, Python, SQL, REST APIs, Git, Backend & Frontend Development).
2. School / College / University: If the question asks for school, college, institute, or university name, output ONLY the exact institution name: "Chaitanya Bharathi Institute of Technology" without conversational sentences (do NOT say "I completed B.Tech in...").
3. Degree & Field of Study: If asked for degree name or qualification, output "Bachelor of Technology" (or "Graduate"). If asked for field of study / major / branch, output "Electronics and Communication Engineering".
4. Graduation Year vs Experience Years:
   - If asked for Graduation Year / Passing Year / Passout Year / Completion Year / End Year, output ONLY the 4-digit calendar year: "2023".
   - If asked for College Start Year / Joining Year, output ONLY: "2019".
   - If asked for Years of Experience or numerical rating in any technology/skill/framework, answer directly with "3".
5. Location & Address:
   - City / Town / Location: output "Hyderabad, Telangana, India" (or "Hyderabad").
   - State / Province: output "Telangana".
   - Country: output "India".
   - Postal / Zip Code: output "500072".
6. Current Job Title & Employer:
   - Title / Designation: output "Full-Stack Software Developer".
   - Current Employer / Company: output "Cognitivo".
7. Technical Skills & Work Authorization:
   - If the question is a Yes/No question regarding technical skills, willingness to learn, or work authorization, answer with "Yes".
8. For short open-ended questions, keep answers to 1-3 direct sentences in first person based on the applicant profile and work history. Output single numbers or phrases cleanly without conversational filler.
9. Only write [NEEDS_HUMAN_INPUT: ...] for confidential government ID numbers (like Aadhaar, PAN, Passport).
"""


def get_ollama_host() -> str:
    host = setting("OLLAMA_HOST", DEFAULT_HOST).rstrip("/")
    if not host.startswith("http://") and not host.startswith("https://"):
        host = f"http://{host}"
    return host


def is_available() -> bool:
    host = get_ollama_host()
    try:
        r = requests.get(f"{host}/api/tags", timeout=1.5)
        return r.status_code == 200
    except Exception:
        return False


def _resolve_model(host: str) -> str:
    configured = setting("OLLAMA_MODEL")
    if configured:
        return configured
    try:
        r = requests.get(f"{host}/api/tags", timeout=1.5)
        if r.status_code == 200:
            models = [m.get("name") for m in r.json().get("models", []) if m.get("name")]
            chat_models = [m for m in models if "embed" not in m.lower()]
            if chat_models:
                return chat_models[0]
            if models:
                return models[0]
    except Exception:
        pass
    return DEFAULT_MODEL


def draft_answer(question: str, profile_data: dict, job_context: str = "") -> str:
    host = get_ollama_host()
    model = _resolve_model(host)
    prompt = f"""
APPLICANT PROFILE:
{profile_data}

JOB CONTEXT (title/company/description, may be partial):
{job_context}

SCREENING QUESTION:
{question}

Draft the answer now.
"""
    resp = requests.post(
        f"{host}/api/chat",
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": _SYSTEM_INSTRUCTIONS},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    msg = data.get("message", {}).get("content", "").strip()
    return msg

