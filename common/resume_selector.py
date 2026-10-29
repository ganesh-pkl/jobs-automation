"""
Smart Resume Selector for automated job applications.
Matches job title, description, and keywords to role-targeted resumes to maximize shortlisting rates.
"""
from pathlib import Path
import re
from typing import Any, Tuple, Optional, Dict, List

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RESUMES_DIR = PROJECT_ROOT / "resumes"

# Target resumes and their associated keyword patterns & priorities
RESUME_DEFINITIONS = [
    {
        "filename": "Ganesh_Pirikirala_AI_FullStack_Resume.pdf",
        "role_category": "AI Full Stack Developer",
        "description": "Full-Stack Software Development + Generative AI / LLMs / Agentic Systems",
        # Dual condition: Contains both an AI signal and a Full Stack / Web signal
        "dual_check": {
            "ai_pattern": re.compile(
                r"\b(ai|genai|generative\s+ai|llm|llms|rag|langchain|langgraph|agentic|artificial\s+intelligence)\b",
                re.IGNORECASE
            ),
            "fullstack_pattern": re.compile(
                r"\b(full[\s-]?stack|frontend|backend|react|react\.?js|node|node\.?js|next\.?js|web)\b",
                re.IGNORECASE
            ),
        },
        "title_patterns": [
            re.compile(r"\bai\s+full[\s-]?stack\b", re.IGNORECASE),
            re.compile(r"\bfull[\s-]?stack\s+ai\b", re.IGNORECASE),
            re.compile(r"\bgenai\s+full[\s-]?stack\b", re.IGNORECASE),
            re.compile(r"\bllm\s+full[\s-]?stack\b", re.IGNORECASE),
            re.compile(r"\bai\s+application\s+developer\b", re.IGNORECASE),
            re.compile(r"\bai\s+web\s+developer\b", re.IGNORECASE),
        ],
        "jd_keywords": [
            r"\blangchain\b", r"\blanggraph\b", r"\brag\b", r"\bvector\s+search\b",
            r"\bpgvector\b", r"\bqdrant\b", r"\bllm\b", r"\bgenai\b"
        ]
    },
    {
        "filename": "Ganesh_Pirikirala_AI_Engineer_Resume.pdf",
        "role_category": "AI / ML / RAG / LLM Engineer",
        "description": "ATS-Optimized for Generative AI, RAG, LLMs, and Agentic Systems Engineering",
        "title_patterns": [
            re.compile(r"\bai\s+engineer\b", re.IGNORECASE),
            re.compile(r"\bartificial\s+intelligence\b", re.IGNORECASE),
            re.compile(r"\bmachine\s+learning\b", re.IGNORECASE),
            re.compile(r"\bml\s+engineer\b", re.IGNORECASE),
            re.compile(r"\bdeep\s+learning\b", re.IGNORECASE),
            re.compile(r"\bprompt\s+engineer\b", re.IGNORECASE),
            re.compile(r"\b(genai|generative\s+ai)\b", re.IGNORECASE),
            re.compile(r"\b(llm|llms)\b", re.IGNORECASE),
            re.compile(r"\b(rag|langchain|langgraph|agentic)\b", re.IGNORECASE),
            re.compile(r"\b(vector\s+search|embeddings|qdrant)\b", re.IGNORECASE),
            re.compile(r"\b(nlp|natural\s+language)\b", re.IGNORECASE),
            re.compile(r"\bai\s+developer\b", re.IGNORECASE),
            re.compile(r"\bai\s+specialist\b", re.IGNORECASE),
            re.compile(r"\bai\s+researcher\b", re.IGNORECASE),
            re.compile(r"\bcomputer\s+vision\b", re.IGNORECASE),
        ],
        "jd_keywords": [
            r"\bllm\b", r"\bllms\b", r"\brag\b", r"\blangchain\b", r"\blanggraph\b",
            r"\bprompt\s+engineering\b", r"\bvector\s+database\b", r"\bqdrant\b",
            r"\bpgvector\b", r"\bembeddings\b", r"\bgenerative\s+ai\b", r"\bgenai\b",
            r"\bnlp\b", r"\bpytorch\b", r"\btensorflow\b", r"\btransformer\b"
        ]
    },
    {
        "filename": "Ganesh_Pirikirala_Java_FullStack_Resume.pdf",
        "role_category": "Java Full Stack Developer",
        "description": "Enterprise Java, Spring Boot, Microservices, Spring Data JPA, Hibernate",
        "title_patterns": [
            re.compile(r"\bjava\b", re.IGNORECASE),
            re.compile(r"\bspring\s*boot\b", re.IGNORECASE),
            re.compile(r"\bspring\s*mvc\b", re.IGNORECASE),
            re.compile(r"\bspring\s*data\b", re.IGNORECASE),
            re.compile(r"\bspring\s*security\b", re.IGNORECASE),
            re.compile(r"\bj2ee\b", re.IGNORECASE),
            re.compile(r"\bhibernate\b", re.IGNORECASE),
            re.compile(r"\bjvm\b", re.IGNORECASE),
            re.compile(r"\bcore\s+java\b", re.IGNORECASE),
        ],
        "jd_keywords": [
            r"\bjava\b", r"\bspring\s*boot\b", r"\bspring\b", r"\bhibernate\b",
            r"\bjpa\b", r"\bj2ee\b", r"\bmicroservices\b", r"\bhikaricp\b",
            r"\bjvm\b", r"\bmaven\b", r"\bgradle\b"
        ]
    },
    {
        "filename": "Ganesh_Pirikirala_MEAN_Stack_Resume.pdf",
        "role_category": "MEAN Stack / Angular Developer",
        "description": "MongoDB, Express.js, Angular, Node.js, RxJS, TypeScript",
        "title_patterns": [
            re.compile(r"\bmean\s*stack\b", re.IGNORECASE),
            re.compile(r"\bmean\b", re.IGNORECASE),
            re.compile(r"\bangular\b", re.IGNORECASE),
            re.compile(r"\bangularjs\b", re.IGNORECASE),
            re.compile(r"\bangular\.js\b", re.IGNORECASE),
            re.compile(r"\brxjs\b", re.IGNORECASE),
            re.compile(r"\bngrx\b", re.IGNORECASE),
        ],
        "jd_keywords": [
            r"\bangular\b", r"\bangularjs\b", r"\brxjs\b", r"\bngrx\b",
            r"\bmean\s*stack\b", r"\bangular\s*material\b"
        ]
    },
    {
        "filename": "Ganesh_Pirikirala_MERN_Stack_Resume.pdf",
        "role_category": "MERN Stack / React + Node Developer",
        "description": "MongoDB, Express.js, React.js, Node.js, Next.js, Redux",
        "title_patterns": [
            re.compile(r"\bmern\s*stack\b", re.IGNORECASE),
            re.compile(r"\bmern\b", re.IGNORECASE),
            re.compile(r"\breact\b", re.IGNORECASE),
            re.compile(r"\breact\.?js\b", re.IGNORECASE),
            re.compile(r"\breactjs\b", re.IGNORECASE),
            re.compile(r"\bnode\.?js\b", re.IGNORECASE),
            re.compile(r"\bnodejs\b", re.IGNORECASE),
            re.compile(r"\bexpress\.?js\b", re.IGNORECASE),
            re.compile(r"\bexpressjs\b", re.IGNORECASE),
            re.compile(r"\bnext\.?js\b", re.IGNORECASE),
            re.compile(r"\bnextjs\b", re.IGNORECASE),
            re.compile(r"\bredux\b", re.IGNORECASE),
        ],
        "jd_keywords": [
            r"\breact\b", r"\breact\.?js\b", r"\bnode\.?js\b", r"\bexpress\.?js\b",
            r"\bnext\.?js\b", r"\bredux\b", r"\bmongodb\b", r"\bmern\b"
        ]
    },
    {
        "filename": "Ganesh_Pirikirala_FullStack_Resume.pdf",
        "role_category": "Full Stack / Software Developer",
        "description": "Full-Stack Software Engineering, Python, FastAPI, Web Development, Microservices",
        "title_patterns": [
            re.compile(r"\bfull[\s-]?stack\b", re.IGNORECASE),
            re.compile(r"\bsoftware\s+developer\b", re.IGNORECASE),
            re.compile(r"\bsoftware\s+engineer\b", re.IGNORECASE),
            re.compile(r"\bsde\b", re.IGNORECASE),
            re.compile(r"\bweb\s+developer\b", re.IGNORECASE),
            re.compile(r"\bpython\b", re.IGNORECASE),
            re.compile(r"\bfastapi\b", re.IGNORECASE),
            re.compile(r"\bbackend\b", re.IGNORECASE),
            re.compile(r"\bfrontend\b", re.IGNORECASE),
            re.compile(r"\bproduct\s+engineer\b", re.IGNORECASE),
        ],
        "jd_keywords": [
            r"\bpython\b", r"\bfastapi\b", r"\bdjango\b", r"\bflask\b",
            r"\bpostgresql\b", r"\brest\s+api\b", r"\bdocker\b"
        ]
    }
]

# Fallback default resume filename
DEFAULT_RESUME_NAME = "Ganesh_Pirikirala_FullStack_Resume.pdf"


def resolve_file_on_disk(filename: str) -> Optional[Path]:
    """Finds the absolute path of a resume file in resumes/ or project root."""
    if not filename:
        return None
    p = Path(filename).expanduser()
    if p.is_absolute() and p.is_file():
        return p

    # Check resumes/ directory first
    in_resumes = RESUMES_DIR / p.name
    if in_resumes.is_file():
        return in_resumes

    # Check project root directory
    in_root = PROJECT_ROOT / p.name
    if in_root.is_file():
        return in_root

    # Check relative to cwd
    in_cwd = Path.cwd() / p
    if in_cwd.is_file():
        return in_cwd

    return None


def select_resume(
    job_title: str = "",
    job_description: str = "",
    profile: Any = None
) -> Tuple[Optional[Path], str]:
    """
    Selects the optimal resume path given a job title, job description, and applicant profile.
    Analyzes both Title and JD keywords to pick the most relevant resume.

    Returns:
        (resume_path, reason_string)
    """
    title_clean = (job_title or "").strip()
    jd_clean = (job_description or "").strip()
    full_context = f"{title_clean} {jd_clean}".strip()

    # 1. Check custom user mappings in profile if configured (e.g. profile.data.get("role_resumes"))
    if profile is not None and hasattr(profile, "data") and isinstance(profile.data, dict):
        custom_mapping = profile.data.get("role_resumes")
        if isinstance(custom_mapping, dict) and full_context:
            for role_key, resume_target in custom_mapping.items():
                if re.search(rf"\b{re.escape(role_key)}\b", full_context, re.IGNORECASE):
                    resolved = resolve_file_on_disk(resume_target)
                    if resolved:
                        return resolved, f"profile.yaml role_resumes matched '{role_key}'"

    # 2. Phase 1: High-Confidence Title Matching
    # If the job title specifically mentions a specialized stack, title takes precedence!
    if title_clean:
        # Check AI Full Stack in title
        defn_ai_fs = RESUME_DEFINITIONS[0]
        dual = defn_ai_fs.get("dual_check")
        if dual and dual["ai_pattern"].search(title_clean) and dual["fullstack_pattern"].search(title_clean):
            path = resolve_file_on_disk(defn_ai_fs["filename"])
            if path:
                return path, f"Title matched AI Full Stack"

        for pattern in defn_ai_fs.get("title_patterns", []):
            m = pattern.search(title_clean)
            if m:
                path = resolve_file_on_disk(defn_ai_fs["filename"])
                if path:
                    return path, f"Title matched AI Full Stack keyword '{m.group(0)}'"

        # Check AI Engineer, Java, MEAN, MERN specific patterns in title (indices 1 to 4)
        for defn in RESUME_DEFINITIONS[1:5]:
            for pattern in defn.get("title_patterns", []):
                m = pattern.search(title_clean)
                if m:
                    path = resolve_file_on_disk(defn["filename"])
                    if path:
                        return path, f"Title matched {defn['role_category']} keyword '{m.group(0)}'"

    # 3. Phase 2: Deep Job Description (JD) Analysis
    # When title is generic (e.g. "Software Engineer", "Full Stack Developer", "Backend Developer")
    # or when extra stack signals exist in the JD, score the JD across categories!
    if jd_clean:
        jd_scores: Dict[str, Tuple[int, List[str], Dict[str, Any]]] = {}

        # Check AI Full Stack dual presence in JD
        defn_ai_fs = RESUME_DEFINITIONS[0]
        dual = defn_ai_fs.get("dual_check")
        if dual:
            ai_hits = dual["ai_pattern"].findall(jd_clean)
            fs_hits = dual["fullstack_pattern"].findall(jd_clean)
            if ai_hits and fs_hits:
                score = (len(ai_hits) * 3) + (len(fs_hits) * 2)
                jd_scores[defn_ai_fs["filename"]] = (score, list(set(ai_hits[:3] + fs_hits[:2])), defn_ai_fs)

        # Score other specialized stacks in JD
        for defn in RESUME_DEFINITIONS[1:5]:
            matched_keywords = []
            score = 0
            for kw_pat in defn.get("jd_keywords", []):
                hits = re.findall(kw_pat, jd_clean, re.IGNORECASE)
                if hits:
                    score += len(hits) * 2
                    matched_keywords.append(hits[0])

            if score > 0:
                jd_scores[defn["filename"]] = (score, list(set(matched_keywords)), defn)

        # If JD has clear signals for a specialized stack, pick the highest scoring stack!
        if jd_scores:
            best_filename = max(jd_scores.keys(), key=lambda fn: jd_scores[fn][0])
            score, kw_list, best_defn = jd_scores[best_filename]
            # Require at least 2 hits or confidence to override generic title
            if score >= 2:
                path = resolve_file_on_disk(best_filename)
                if path:
                    kw_str = ", ".join(kw_list[:4])
                    return path, f"JD matched {best_defn['role_category']} keywords: [{kw_str}]"

    # 4. Phase 3: Title Fallback (General Full Stack patterns)
    if title_clean:
        defn_general = RESUME_DEFINITIONS[5]  # FullStack_Resume
        for pattern in defn_general.get("title_patterns", []):
            m = pattern.search(title_clean)
            if m:
                path = resolve_file_on_disk(defn_general["filename"])
                if path:
                    return path, f"Title matched {defn_general['role_category']} keyword '{m.group(0)}'"

    # 5. Fallback: profile.resume_file_name if configured and exists
    if profile is not None and hasattr(profile, "data") and isinstance(profile.data, dict):
        configured = str(profile.data.get("resume_file_name", "")).strip()
        if configured:
            path = resolve_file_on_disk(configured)
            if path:
                return path, f"configured profile.resume_file_name ({path.name})"

    # 6. Fallback: default fullstack resume
    default_path = resolve_file_on_disk(DEFAULT_RESUME_NAME)
    if default_path:
        return default_path, f"default resume ({DEFAULT_RESUME_NAME})"

    # 7. Fallback: Any PDF found in resumes/ or root
    if RESUMES_DIR.is_dir():
        pdfs = list(RESUMES_DIR.glob("*.pdf"))
        if pdfs:
            return pdfs[0], f"fallback directory PDF ({pdfs[0].name})"

    root_pdfs = list(PROJECT_ROOT.glob("*.pdf"))
    if root_pdfs:
        return root_pdfs[0], f"fallback root PDF ({root_pdfs[0].name})"

    return None, "no resume found on disk"


def get_resume_path(profile: Any = None, job_context: str = "", job_description: str = "") -> Optional[Path]:
    """
    Drop-in replacement for legacy _resume_path(profile) that also accepts optional job_context and job_description.
    """
    path, _ = select_resume(job_title=job_context, job_description=job_description, profile=profile)
    return path


def get_targeted_resume_name(job_context: str = "", job_description: str = "", profile: Any = None) -> str:
    """Returns the filename of the targeted resume for the given job context and JD."""
    path = get_resume_path(profile, job_context=job_context, job_description=job_description)
    return path.name if path else DEFAULT_RESUME_NAME
