"""
Unit tests for common/resume_generator.py
"""
from pathlib import Path
import unittest
from unittest.mock import patch

from common.resume_generator import (
    find_tectonic,
    clean_company_name,
    sanitize_filename_token,
    extract_matching_skills,
    reorder_projects_in_latex,
    tailor_professional_summary,
    generate_targeted_resume,
    PROJECT_ROOT,
    RESUMES_DIR,
)
from common.resume_selector import select_resume, get_resume_path


class ResumeGeneratorTests(unittest.TestCase):
    def test_find_tectonic_found(self):
        tectonic = find_tectonic()
        self.assertIsNotNone(tectonic, "Tectonic binary should be found on system")

    def test_clean_company_name(self):
        cases = [
            ("Google LLC", "Google"),
            ("Infosys Limited", "Infosys"),
            ("Amazon Web Services (AWS)", "Amazon Web"),
            ("MBS Global", "MBS Global"),
            ("TCS Pvt Ltd.", "TCS"),
        ]
        for raw, expected in cases:
            self.assertEqual(clean_company_name(raw), expected)

    def test_sanitize_filename_token(self):
        self.assertEqual(sanitize_filename_token("MBS Global"), "MBS_Global")
        self.assertEqual(sanitize_filename_token("Google / DeepMind!"), "Google_DeepMind")

    def test_extract_matching_skills(self):
        jd = "Looking for Core Java, Spring Boot, Microservices, Docker, PostgreSQL, and JUnit skills."
        matched = extract_matching_skills(jd)
        keys = [k for k, _ in matched]
        self.assertIn("java", keys)
        self.assertIn("spring_boot", keys)
        self.assertIn("microservices", keys)
        self.assertIn("docker", keys)
        self.assertIn("postgresql", keys)
        self.assertIn("junit", keys)

    def test_reorder_projects_for_enterprise_ecommerce(self):
        tex_path = RESUMES_DIR / "Ganesh_Pirikirala_Java_FullStack_Resume.tex"
        tex = tex_path.read_text(encoding="utf-8")
        jd = "We need an enterprise Java developer to build e-commerce microservices with Spring Boot, Hibernate, and PostgreSQL."
        tailored = reorder_projects_in_latex(tex, jd)
        # SpringBoot-E-commerce-Backend should now appear before SkillVault
        idx_ecom = tailored.find("SpringBoot-E-commerce-Backend")
        idx_skillvault = tailored.find("SkillVault")
        self.assertTrue(idx_ecom != -1 and idx_skillvault != -1)
        self.assertLess(idx_ecom, idx_skillvault, "E-commerce backend project should be prioritized for e-commerce JD")

    def test_reorder_projects_for_rag_ai(self):
        tex_path = RESUMES_DIR / "Ganesh_Pirikirala_Java_FullStack_Resume.tex"
        tex = tex_path.read_text(encoding="utf-8")
        jd = "Looking for RAG, Vector DB, Qdrant, Ollama, LangChain, and Spring AI expertise."
        tailored = reorder_projects_in_latex(tex, jd)
        idx_rag = tailored.find("AI PDF Knowledge Assistant")
        idx_skillvault = tailored.find("SkillVault")
        self.assertTrue(idx_rag != -1 and idx_skillvault != -1)
        self.assertLess(idx_rag, idx_skillvault, "RAG project should be prioritized for AI/RAG JD")

    def test_tailor_professional_summary(self):
        tex_path = RESUMES_DIR / "Ganesh_Pirikirala_Java_FullStack_Resume.tex"
        tex = tex_path.read_text(encoding="utf-8")
        matched = [("java", "Core Java"), ("spring_boot", "Spring Boot"), ("microservices", "Microservices")]
        tailored = tailor_professional_summary(tex, "Java Full Stack Developer", "MBS Global", "", matched)
        self.assertIn("Core Java, Spring Boot, Microservices", tailored)
        self.assertIn("nearly 3 years of experience", tailored)

    def test_end_to_end_generate_targeted_resume(self):
        pdf, reason = generate_targeted_resume(
            job_title="Senior Java / Spring Boot Developer",
            company="MBS Global",
            job_description="Core Java, Spring Boot, Microservices, Docker, PostgreSQL",
            force_regenerate=True
        )
        self.assertIsNotNone(pdf)
        self.assertTrue(pdf.is_file())
        self.assertGreater(pdf.stat().st_size, 1000)
        self.assertIn("MBS_Global", pdf.name)

        # Test caching
        cached_pdf, cached_reason = generate_targeted_resume(
            job_title="Senior Java / Spring Boot Developer",
            company="MBS Global",
            job_description="Core Java, Spring Boot, Microservices, Docker, PostgreSQL",
            force_regenerate=False
        )
        self.assertEqual(cached_pdf, pdf)
        self.assertIn("cached", cached_reason)

    def test_select_resume_with_company_triggers_generator(self):
        path, reason = select_resume(
            job_title="AI Engineer",
            company="Google",
            job_description="Hands-on LLM, RAG, LangChain, Qdrant, Python FastAPI"
        )
        self.assertIsNotNone(path)
        self.assertTrue(path.is_file())
        self.assertIn("Google", path.name)

    def test_get_resume_path_with_job_context_syntax(self):
        path = get_resume_path(
            job_context="Full Stack Developer at Infosys",
            job_description="React, Node.js, Python, PostgreSQL, Docker"
        )
        self.assertIsNotNone(path)
        self.assertTrue(path.is_file())
        self.assertIn("Infosys", path.name)


if __name__ == "__main__":
    unittest.main()

