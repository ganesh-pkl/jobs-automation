"""
Dynamic Resume Generator for automated job applications.
Tailors ATS-optimized LaTeX resumes from candidate facts and compiles them to PDF on-the-fly per job application.
"""
from datetime import datetime
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESUMES_DIR = PROJECT_ROOT / "resumes"
GENERATED_DIR = RESUMES_DIR / "generated"

# Catalog of candidate's authentic skills (strictly verified facts, no fabrication)
AUTHENTIC_SKILLS_CATALOG = {
    "java": ("Core Java", r"\b(java|core\s+java|jvm|j2ee)\b"),
    "spring_boot": ("Spring Boot", r"\b(spring\s*boot|spring\s*mvc|spring\s*framework)\b"),
    "spring_data": ("Spring Data JPA", r"\b(spring\s*data|spring\s*data\s*jpa|jpa)\b"),
    "hibernate": ("Hibernate", r"\b(hibernate|orm)\b"),
    "microservices": ("Microservices", r"\b(microservices|microservice|distributed\s+systems)\b"),
    "rest_apis": ("REST APIs", r"\b(rest|restful|rest\s*apis?)\b"),
    "postgresql": ("PostgreSQL", r"\b(postgres|postgresql|psql)\b"),
    "mysql": ("MySQL", r"\b(mysql)\b"),
    "mongodb": ("MongoDB", r"\b(mongodb|mongo)\b"),
    "redis": ("Redis", r"\b(redis|caching)\b"),
    "sql": ("SQL", r"\b(sql|relational\s+database)\b"),
    "hikaricp": ("HikariCP", r"\b(hikaricp|connection\s+pool)\b"),
    "docker": ("Docker", r"\b(docker|containerization|containers)\b"),
    "kubernetes": ("Kubernetes", r"\b(kubernetes|k8s)\b"),
    "aws": ("AWS", r"\b(aws|amazon\s+web\s+services|ec2|s3)\b"),
    "azure": ("Azure", r"\b(azure)\b"),
    "git": ("Git", r"\b(git|github|gitlab|version\s+control)\b"),
    "ci_cd": ("CI/CD", r"\b(ci[\s/-]?cd|github\s+actions|jenkins|pipelines)\b"),
    "python": ("Python", r"\b(python|python3)\b"),
    "fastapi": ("FastAPI", r"\b(fastapi)\b"),
    "django": ("Django", r"\b(django)\b"),
    "flask": ("Flask", r"\b(flask)\b"),
    "react": ("React.js", r"\b(react|react\.?js|reactjs)\b"),
    "nextjs": ("Next.js", r"\b(next|next\.?js|nextjs)\b"),
    "angular": ("Angular", r"\b(angular|angularjs|angular\.js)\b"),
    "typescript": ("TypeScript", r"\b(typescript|ts)\b"),
    "javascript": ("JavaScript", r"\b(javascript|js|es6)\b"),
    "tailwind": ("Tailwind CSS", r"\b(tailwind|tailwind\s*css)\b"),
    "material_ui": ("Material UI", r"\b(material[\s-]?ui|mui)\b"),
    "node": ("Node.js", r"\b(node|node\.?js|nodejs)\b"),
    "express": ("Express.js", r"\b(express|express\.?js|expressjs)\b"),
    "redux": ("Redux", r"\b(redux|redux\s+toolkit)\b"),
    "rxjs": ("RxJS", r"\b(rxjs)\b"),
    "ngrx": ("NgRx", r"\b(ngrx)\b"),
    "rag": ("RAG", r"\b(rag|retrieval[\s-]augmented\s+generation)\b"),
    "langchain": ("LangChain", r"\b(langchain)\b"),
    "langgraph": ("LangGraph", r"\b(langgraph)\b"),
    "llm": ("LLMs", r"\b(llm|llms|large\s+language\s+models?)\b"),
    "genai": ("Generative AI", r"\b(genai|generative\s+ai)\b"),
    "vector_db": ("Vector DB", r"\b(vector\s+(?:db|database|search)|qdrant|pgvector|pinecone|chroma)\b"),
    "qdrant": ("Qdrant", r"\b(qdrant)\b"),
    "ollama": ("Ollama", r"\b(ollama)\b"),
    "spring_ai": ("Spring AI", r"\b(spring\s*ai)\b"),
    "system_design": ("System Design (LLD/HLD)", r"\b(system\s+design|lld|hld|low[\s-]level\s+design|high[\s-]level\s+design)\b"),
    "solid": ("SOLID Principles", r"\b(solid|clean\s+architecture|design\s+patterns)\b"),
    "junit": ("JUnit", r"\b(junit|junit5|unit\s+test|mockito)\b"),
    "postman": ("Postman", r"\b(postman|swagger|api\s+docs)\b"),
}

# Project scoring keyword rules for project reordering
PROJECT_KEYWORDS = {
    "SpringBoot-E-commerce-Backend": [
        "java", "spring boot", "spring", "microservices", "hibernate", "jpa",
        "ecommerce", "e-commerce", "sql", "postgresql", "order", "payment",
        "backend", "enterprise", "junit"
    ],
    "AI PDF Knowledge Assistant": [
        "rag", "llm", "langchain", "vector", "qdrant", "embeddings", "ollama",
        "spring ai", "genai", "generative ai", "semantic search", "nlp", "ai"
    ],
    "BRAHMO Clinical AI": [
        "clinical", "healthcare", "medical", "langgraph", "agentic", "agents",
        "fastapi", "python", "multi-agent", "reasoning", "patient"
    ],
    "DevOS": [
        "devos", "developer", "engineering os", "agentic", "langchain", "langgraph",
        "multi-agent", "react", "next", "tools"
    ],
    "SkillVault": [
        "full stack", "fullstack", "next", "next.js", "react", "typescript",
        "learning", "telemetry", "hikaricp", "platform", "docker", "redis",
        "postgresql", "curricula", "markdown"
    ],
    "DriveEasy": [
        "python", "fastapi", "booking", "vehicle", "postgres", "full stack",
        "vehicle booking", "react"
    ],
    "Production-Grade Custom API Gateway": [
        "gateway", "api gateway", "rate limiting", "redis", "reverse proxy",
        "express", "node", "proxy", "high throughput"
    ],
    "LetsChat": [
        "chat", "real-time", "realtime", "socket", "socket.io", "websocket",
        "messaging", "react", "node", "redis"
    ],
    "E-Commerce Store --- Full-Stack MEAN": [
        "angular", "mean", "mean stack", "mongodb", "express", "node", "rxjs"
    ],
    "E-Commerce Store --- Full-Stack MERN": [
        "mern", "mern stack", "react", "mongodb", "express", "node", "razorpay"
    ],
}


def find_tectonic() -> Optional[str]:
    """Finds the path to the tectonic binary on the system."""
    found = shutil.which("tectonic")
    if found:
        return found
    candidates = [
        "/opt/homebrew/bin/tectonic",
        "/usr/local/bin/tectonic",
        str(Path.home() / ".cargo/bin/tectonic"),
    ]
    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def sanitize_filename_token(text: str) -> str:
    """Sanitizes text to be safe for filenames and identifiers."""
    cleaned = re.sub(r"[^\w\s-]", "", text)
    cleaned = re.sub(r"[\s-]+", "_", cleaned).strip("_")
    return cleaned[:30]


def clean_company_name(raw: str) -> str:
    """Strips corporate suffixes for clean display and filenames."""
    if not raw:
        return ""
    c = raw.strip()
    c = re.sub(r"\b(inc|incorporated|llc|ltd|limited|pvt\s*ltd|private\s*limited|technologies|solutions|corp|corporation|group|services)\b[\.,]?", "", c, flags=re.IGNORECASE)
    c = re.sub(r"[\(\[].*?[\)\]]", "", c)  # remove parenthesized suffixes
    c = re.sub(r"[^\w\s]", "", c)
    c = re.sub(r"\s+", " ", c).strip()
    return c or "Target_Company"


def extract_matching_skills(jd_and_title: str) -> List[Tuple[str, str]]:
    """
    Extracts authentic candidate skills found in the job context.
    Returns a list of (skill_key, display_name).
    """
    if not jd_and_title:
        return []
    matched = []
    text = jd_and_title.lower()
    for key, (display_name, pattern) in AUTHENTIC_SKILLS_CATALOG.items():
        if re.search(pattern, text, re.IGNORECASE):
            matched.append((key, display_name))
    return matched


def reorder_projects_in_latex(tex_content: str, jd_text: str) -> str:
    """
    Reorders the 3 projects in the Featured Projects section according to keyword overlap with JD.
    Keeps exact formatting and guarantees valid LaTeX.
    """
    if not jd_text:
        return tex_content

    # Match the Featured Projects section
    m = re.search(
        r'(\\section\{Featured [^}]+\}\s*)([\s\S]*?)(\s*\\section\{Education\})',
        tex_content
    )
    if not m:
        return tex_content

    section_header = m.group(1)
    projects_body = m.group(2)
    next_section = m.group(3)

    # Split into individual project chunks (each starts with \noindent \textbf{)
    parts = re.split(r'(?=\\noindent\s+\\textbf\{)', projects_body)
    prefix = parts[0] if parts else ""
    project_chunks = [p.strip() for p in parts[1:] if p.strip()]

    if len(project_chunks) <= 1:
        return tex_content

    jd_lower = jd_text.lower()

    # Score each project chunk
    scored = []
    for chunk in project_chunks:
        score = 0
        chunk_clean = re.sub(r'\\vspace\{[^}]+\}', '', chunk).strip()
        chunk_lower = chunk_clean.lower()
        # Check against known project keywords
        for proj_name, keywords in PROJECT_KEYWORDS.items():
            if proj_name.lower() in chunk_lower:
                for kw in keywords:
                    if re.search(rf"\b{re.escape(kw)}\b", jd_lower):
                        score += 3
        # General overlap
        for key, (display_name, pat) in AUTHENTIC_SKILLS_CATALOG.items():
            if re.search(pat, chunk_lower) and re.search(pat, jd_lower):
                score += 1
        scored.append((score, chunk_clean))

    # Stable sort descending by score
    scored.sort(key=lambda x: x[0], reverse=True)
    reordered_chunks = [chunk for _, chunk in scored]

    # Join with standard LaTeX project vertical spacing
    new_projects_body = "\n\n\\vspace{2.5pt}\n".join(reordered_chunks)
    new_section = f"{section_header}\n{prefix}\n{new_projects_body}\n\n{next_section}"

    return tex_content[:m.start()] + new_section + tex_content[m.end():]


def tailor_professional_summary(
    tex_content: str,
    role_category: str,
    company: str,
    jd_text: str,
    matched_skills: List[Tuple[str, str]]
) -> str:
    """
    Fine-tunes the Professional Summary to emphasize matching authentic skills and target company.
    Guarantees no false claims while optimizing ATS keyword density.
    """
    m = re.search(
        r'(\\section\{Professional Summary\}\s*)([\s\S]*?)((\s*%.*)*\s*\\section\{Technical Skills\})',
        tex_content
    )
    if not m:
        return tex_content

    current_summary = m.group(2).strip()

    # If specific high-priority skills are in JD, format them into bold highlights
    core_skill_names = [display for _, display in matched_skills[:7]]

    # Tailor summary based on role category and matching skills
    tailored = current_summary
    if "Java" in role_category:
        has_microservices = any(k in ["microservices", "spring_boot", "spring_data"] for k, _ in matched_skills)
        has_cloud = any(k in ["aws", "azure", "docker", "ci_cd", "kubernetes"] for k, _ in matched_skills)
        has_db = any(k in ["postgresql", "mysql", "sql", "hikaricp"] for k, _ in matched_skills)

        skills_str = ", ".join(core_skill_names) if core_skill_names else "Core Java, Spring Boot, Spring Data JPA, Hibernate, REST APIs, and PostgreSQL"
        tailored = (
            f"Java Full Stack Developer with nearly 3 years of experience architecting and implementing "
            f"scalable enterprise microservices, high-throughput REST APIs, and reactive full-stack web applications. "
            f"Skilled in \\textbf{{{skills_str}}}, with hands-on frontend expertise in \\textbf{{React.js, Next.js, and Angular}}. "
            f"Proficient with \\textbf{{Docker, Azure, AWS, Git, CI/CD, and JUnit/Mockito}}, alongside modern AI application engineering (Spring AI, RAG). "
            f"Adept at clean system design (LLD/HLD, SOLID) and database connection pool tuning with HikariCP."
        )
    elif "AI / ML" in role_category or "AI Engineer" in role_category:
        skills_str = ", ".join(core_skill_names) if core_skill_names else "LangChain, LangGraph, RAG, Ollama, Qdrant, and Vector Search"
        tailored = (
            f"AI \\& LLM Systems Engineer with nearly 3 years of software engineering experience specializing in "
            f"production \\textbf{{Retrieval-Augmented Generation (RAG), Agentic AI architectures, and LLM orchestration}}. "
            f"Hands-on expertise deploying citation-grounded semantic search pipelines and multi-agent workflows using "
            f"\\textbf{{{skills_str}}}. Strong full-stack and microservices foundation across \\textbf{{Python, FastAPI, Spring Boot, and PostgreSQL}}, "
            f"focused on low-latency inference, deterministic validation, and patient-safe AI system design."
        )
    elif "AI Full Stack" in role_category:
        skills_str = ", ".join(core_skill_names) if core_skill_names else "Python, FastAPI, Next.js, React, RAG, LangChain, and Qdrant"
        tailored = (
            f"AI Full Stack Developer with nearly 3 years of experience building modern web platforms paired with "
            f"production \\textbf{{Generative AI, LLMs, and RAG pipelines}}. Skilled across the end-to-end stack in "
            f"\\textbf{{{skills_str}}}, with robust database design in PostgreSQL and Qdrant. "
            f"Proven track record delivering scalable SaaS applications, real-time agentic workflows, and high-performance user interfaces."
        )
    elif "MERN" in role_category:
        skills_str = ", ".join(core_skill_names) if core_skill_names else "MongoDB, Express.js, React.js, Node.js, and Next.js"
        tailored = (
            f"MERN Stack Developer with nearly 3 years of hands-on experience building high-throughput web applications, "
            f"RESTful microservices, and reactive user interfaces. Skilled in \\textbf{{{skills_str}}}, Redux, TypeScript, and Redis. "
            f"Experienced in payment gateway integrations (Razorpay, Stripe), real-time WebSockets, Docker containerization, and clean architectural design."
        )
    elif "MEAN" in role_category:
        skills_str = ", ".join(core_skill_names) if core_skill_names else "Angular, Node.js, Express.js, MongoDB, and TypeScript"
        tailored = (
            f"MEAN Stack Developer with nearly 3 years of experience delivering scalable enterprise web solutions "
            f"using \\textbf{{{skills_str}}}, RxJS, and NgRx. Proficient in modular component architecture, reactive state management, "
            f"REST API development, and secure authentication flows, backed by Docker and automated CI/CD pipelines."
        )
    else:  # General Full Stack
        skills_str = ", ".join(core_skill_names) if core_skill_names else "Python, FastAPI, Java, Spring Boot, React.js, Next.js, and PostgreSQL"
        tailored = (
            f"Full Stack Developer with nearly 3 years of experience engineering high-performance web applications, "
            f"distributed microservices, and modern frontend interfaces. Proficient in \\textbf{{{skills_str}}}, Docker, and Cloud integrations (AWS, Azure). "
            f"Passionate about clean code, low-latency API design, connection pool optimization, and reliable system architecture."
        )

    return tex_content[:m.start(2)] + tailored + tex_content[m.end(2):]


def tailor_header_comment(tex_content: str, role_category: str, company: str, matched_skills: List[Tuple[str, str]]) -> str:
    """Updates the top LaTeX comment header with ATS targeting information."""
    comp_clean = clean_company_name(company) if company else "Enterprise Roles"
    skills_preview = ", ".join([d for _, d in matched_skills[:6]]) if matched_skills else role_category
    header = (
        f"%-----------------------------------------------------------------------------------------------------------------------------------------------%\n"
        f"%   Ganesh Pirikirala - {role_category} Resume (Tailored for {comp_clean})\n"
        f"%   ATS-Optimized for {skills_preview}\n"
        f"%-----------------------------------------------------------------------------------------------------------------------------------------------%"
    )
    # Replace the initial comment block if present
    return re.sub(r"^%--+[\s\S]*?%--+", header, tex_content, count=1)


def generate_targeted_resume(
    job_title: str = "",
    company: str = "",
    job_description: str = "",
    profile: Any = None,
    force_regenerate: bool = False
) -> Tuple[Optional[Path], str]:
    """
    Dynamically generates and compiles a job-specific ATS LaTeX resume into PDF.
    
    Returns:
        (pdf_path, reason_string)
    """
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Check if tectonic compiler is available
    tectonic_bin = find_tectonic()
    if not tectonic_bin:
        # Fall back to static selector without compiler
        return None, "tectonic compiler not found"

    # 2. Check if dynamic resume generation is enabled in profile
    if profile is not None and hasattr(profile, "data") and isinstance(profile.data, dict):
        if profile.data.get("generate_dynamic_resumes") is False:
            return None, "dynamic resume generation disabled in profile.yaml"

    # 3. Determine the best base role category & template
    from common.resume_selector import RESUME_DEFINITIONS, select_resume
    static_path, match_reason = select_resume(
        job_title=job_title,
        job_description=job_description,
        profile=profile,
        generate_dynamic=False
    )

    if not static_path:
        return None, "no base resume matched"

    base_tex_name = static_path.stem + ".tex"
    base_tex_path = RESUMES_DIR / base_tex_name
    if not base_tex_path.is_file():
        # Fallback to general fullstack if specific tex doesn't exist
        base_tex_path = RESUMES_DIR / "Ganesh_Pirikirala_FullStack_Resume.tex"
        if not base_tex_path.is_file():
            return static_path, f"using static base resume ({static_path.name})"

    # Find the role definition for title
    role_def = next((d for d in RESUME_DEFINITIONS if d["filename"] == static_path.name), None)
    role_category = role_def["role_category"] if role_def else "Full Stack Developer"

    # 4. Compute unique cached filename
    comp_clean = clean_company_name(company)
    comp_slug = sanitize_filename_token(comp_clean) if comp_clean else "Target"
    role_slug = sanitize_filename_token(role_category.split("/")[0].strip())

    target_tex_name = f"Ganesh_Pirikirala_{comp_slug}_{role_slug}_Resume.tex"
    target_pdf_name = f"Ganesh_Pirikirala_{comp_slug}_{role_slug}_Resume.pdf"
    target_tex_path = GENERATED_DIR / target_tex_name
    target_pdf_path = GENERATED_DIR / target_pdf_name

    # Check cache: If PDF exists, is non-empty, and newer than base template, reuse immediately!
    if not force_regenerate and target_pdf_path.is_file() and target_pdf_path.stat().st_size > 1000:
        if target_pdf_path.stat().st_mtime >= base_tex_path.stat().st_mtime:
            return target_pdf_path, f"cached dynamic resume for {comp_clean} ({role_category})"

    # 5. Extract matching skills from JD + Title
    combined_ctx = f"{job_title} {job_description}".strip()
    matched_skills = extract_matching_skills(combined_ctx)

    # 6. Read base LaTeX template
    try:
        tex_content = base_tex_path.read_text(encoding="utf-8")
    except Exception as e:
        return static_path, f"failed to read base tex: {e}"

    # 7. Apply Tailoring
    try:
        # A. Header ATS comment
        tex_content = tailor_header_comment(tex_content, role_category, company, matched_skills)

        # B. Professional Summary
        tex_content = tailor_professional_summary(tex_content, role_category, company, job_description, matched_skills)

        # C. Reorder Featured Projects based on JD match
        tex_content = reorder_projects_in_latex(tex_content, combined_ctx)

        # Save tailored .tex
        target_tex_path.write_text(tex_content, encoding="utf-8")
    except Exception as e:
        print(f"  [Resume Generator] Error tailoring LaTeX: {e}. Falling back to base.")
        return static_path, f"LaTeX tailoring fallback ({static_path.name})"

    # 8. Compile using Tectonic
    try:
        proc = subprocess.run(
            [tectonic_bin, "-o", str(GENERATED_DIR), str(target_tex_path)],
            capture_output=True,
            text=True,
            timeout=25
        )
        if proc.returncode == 0 and target_pdf_path.is_file() and target_pdf_path.stat().st_size > 1000:
            return target_pdf_path, f"dynamically generated ATS resume for {comp_clean} ({role_category})"
        else:
            print(f"  [Resume Generator] Tectonic build note: returncode {proc.returncode}. Falling back to base PDF.")
            return static_path, f"compilation fallback to {static_path.name}"
    except Exception as e:
        print(f"  [Resume Generator] Subprocess error during compilation: {e}. Falling back to base.")
        return static_path, f"compilation error fallback ({static_path.name})"
