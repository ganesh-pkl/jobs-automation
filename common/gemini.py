"""
Generates free-text screening answers using Google's free-tier Gemini API,
via the current `google-genai` SDK (the old `google-generativeai` package is
retired and will 404).

Get a free key at https://aistudio.google.com/apikey (no card required for the
free tier as of writing -- check current limits there, they change).
Set it as an environment variable before running:
    export GEMINI_API_KEY="your-key-here"
"""
import os
from .env import setting
from pathlib import Path
from google import genai
from google.genai import types

# Use a concrete stable model. The moving `gemini-flash-latest` alias can route
# to a newly launched or overloaded model and return transient 503 errors.
MODEL = "gemini-3.5-flash-lite"

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

def _load_dotenv_key():
    """Reads GEMINI_API_KEY out of a local .env file if the environment
    variable isn't already set. Looks in the project root (two levels up
    from this file: common/gemini.py -> project root)."""
    if os.environ.get("GEMINI_API_KEY"):
        return
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if key.strip() == "GEMINI_API_KEY":
            os.environ["GEMINI_API_KEY"] = value.strip()
            return


_client = None


def _get_client():
    global _client
    if _client is None:
        key = setting("GEMINI_API_KEY")
        if not key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Either run "
                "$env:GEMINI_API_KEY=\"your-key\" in PowerShell, or create a .env "
                "file in the project folder (copy .env.example) with your key in it. "
                "Get a free key at https://aistudio.google.com/apikey"
            )
        _client = genai.Client(api_key=key)
    return _client


def draft_answer(question: str, profile_data: dict, job_context: str = "") -> str:
    """Returns a drafted answer, or a string starting with [NEEDS_HUMAN_INPUT: ...]
    if the profile doesn't actually cover what's being asked."""
    client = _get_client()
    prompt = f"""
APPLICANT PROFILE:
{profile_data}

JOB CONTEXT (title/company/description, may be partial):
{job_context}

SCREENING QUESTION:
{question}

Draft the answer now.
"""
    resp = client.models.generate_content(
        model=setting("GEMINI_MODEL", MODEL),
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=_SYSTEM_INSTRUCTIONS,
        ),
    )
    return resp.text.strip()
