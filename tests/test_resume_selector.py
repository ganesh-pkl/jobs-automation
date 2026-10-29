"""
Unit tests for common/resume_selector.py
"""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from common.profile import Profile
from common.resume_selector import (
    select_resume,
    get_resume_path,
    get_targeted_resume_name,
    resolve_file_on_disk
)


class ResumeSelectorTests(unittest.TestCase):
    def test_ai_engineer_matching(self):
        titles = [
            "AI Engineer",
            "Junior Machine Learning Engineer",
            "Generative AI Specialist",
            "RAG & LLM Developer",
            "Prompt Engineer",
            "NLP Research Engineer"
        ]
        for t in titles:
            path, reason = select_resume(job_title=t)
            self.assertIsNotNone(path, f"Failed on title: {t}")
            self.assertEqual(path.name, "Ganesh_Pirikirala_AI_Engineer_Resume.pdf", f"Expected AI Engineer for '{t}', got '{path.name}' ({reason})")

    def test_ai_fullstack_dual_matching(self):
        titles = [
            "AI Full Stack Developer",
            "Full Stack AI Engineer",
            "GenAI Full Stack Developer",
            "AI Application Developer (React/Node)",
            "Senior Full Stack Engineer - GenAI"
        ]
        for t in titles:
            path, reason = select_resume(job_title=t)
            self.assertIsNotNone(path, f"Failed on title: {t}")
            self.assertEqual(path.name, "Ganesh_Pirikirala_AI_FullStack_Resume.pdf", f"Expected AI FullStack for '{t}', got '{path.name}' ({reason})")

    def test_java_spring_matching_and_not_javascript(self):
        titles = [
            "Java Developer",
            "Senior Java / Spring Boot Engineer",
            "Java Full Stack Developer",
            "Backend Engineer (Spring Boot / Microservices)",
            "Core Java Consultant"
        ]
        for t in titles:
            path, reason = select_resume(job_title=t)
            self.assertIsNotNone(path, f"Failed on title: {t}")
            self.assertEqual(path.name, "Ganesh_Pirikirala_Java_FullStack_Resume.pdf", f"Expected Java for '{t}', got '{path.name}' ({reason})")

        # Crucial test: JavaScript must NOT match Java!
        js_title = "JavaScript Developer"
        path_js, _ = select_resume(job_title=js_title)
        self.assertIsNotNone(path_js)
        self.assertNotEqual(path_js.name, "Ganesh_Pirikirala_Java_FullStack_Resume.pdf", "JavaScript Developer matched Java resume!")

    def test_mern_react_node_matching(self):
        titles = [
            "MERN Stack Developer",
            "React.js Developer",
            "React Developer",
            "Node.js Backend Engineer",
            "Full Stack React & Node Developer",
            "Next.js Frontend Engineer"
        ]
        for t in titles:
            path, reason = select_resume(job_title=t)
            self.assertIsNotNone(path, f"Failed on title: {t}")
            self.assertEqual(path.name, "Ganesh_Pirikirala_MERN_Stack_Resume.pdf", f"Expected MERN for '{t}', got '{path.name}' ({reason})")

    def test_mean_angular_matching(self):
        titles = [
            "MEAN Stack Developer",
            "Angular Developer",
            "Senior Angular / TypeScript Engineer",
            "RxJS / Angular Specialist"
        ]
        for t in titles:
            path, reason = select_resume(job_title=t)
            self.assertIsNotNone(path, f"Failed on title: {t}")
            self.assertEqual(path.name, "Ganesh_Pirikirala_MEAN_Stack_Resume.pdf", f"Expected MEAN for '{t}', got '{path.name}' ({reason})")

    def test_fullstack_general_matching(self):
        titles = [
            "Full Stack Developer",
            "Software Engineer",
            "Software Developer",
            "Python Developer",
            "FastAPI Backend Developer",
            "Web Developer",
            "SDE-1"
        ]
        for t in titles:
            path, reason = select_resume(job_title=t)
            self.assertIsNotNone(path, f"Failed on title: {t}")
            self.assertEqual(path.name, "Ganesh_Pirikirala_FullStack_Resume.pdf", f"Expected FullStack for '{t}', got '{path.name}' ({reason})")

    def test_custom_profile_override(self):
        custom_profile = Profile({
            "target_roles": ["DevOps Engineer"],
            "total_experience_years": 3,
            "notice_period_days": 0,
            "current_ctc_lpa": 6,
            "expected_ctc_lpa": 10,
            "current_city": "Hyderabad",
            "resume_file_name": "Ganesh_Pirikirala_FullStack_Resume.pdf",
            "role_resumes": {
                "Cloud Architect": "Ganesh_Pirikirala_AI_Engineer_Resume.pdf"
            }
        })
        path, reason = select_resume(job_title="Cloud Architect", profile=custom_profile)
        self.assertEqual(path.name, "Ganesh_Pirikirala_AI_Engineer_Resume.pdf")
        self.assertIn("role_resumes matched 'Cloud Architect'", reason)

    def test_empty_job_context_backward_compatibility(self):
        with TemporaryDirectory() as tmp_dir:
            temp_resume = Path(tmp_dir) / "custom_named_resume.pdf"
            temp_resume.write_bytes(b"sample pdf")

            profile = Profile({
                "target_roles": ["Software Engineer"],
                "total_experience_years": 3,
                "notice_period_days": 0,
                "current_ctc_lpa": 6,
                "expected_ctc_lpa": 10,
                "current_city": "Hyderabad",
                "resume_file_name": str(temp_resume)
            })

            # Without job context, it should resolve the configured resume_file_name
            resolved = get_resume_path(profile=profile, job_context="")
            self.assertEqual(resolved, temp_resume)


    def test_generic_title_with_specific_jd_matching(self):
        # 1. Generic title 'Software Engineer' with Java JD
        p_java, r_java = select_resume(
            job_title="Software Engineer",
            job_description="We are seeking an engineer experienced in Java, Spring Boot, Hibernate, and Microservices."
        )
        self.assertEqual(p_java.name, "Ganesh_Pirikirala_Java_FullStack_Resume.pdf")
        self.assertIn("JD matched Java Full Stack Developer keywords", r_java)

        # 2. Generic title 'Full Stack Developer' with AI / LLM JD
        p_ai_fs, r_ai_fs = select_resume(
            job_title="Full Stack Developer",
            job_description="Seeking a Full Stack Engineer to build Generative AI web apps using LangChain, RAG, and React."
        )
        self.assertEqual(p_ai_fs.name, "Ganesh_Pirikirala_AI_FullStack_Resume.pdf")

        # 3. Generic title 'Backend Developer' with pure AI / LLM JD
        p_ai, r_ai = select_resume(
            job_title="Backend Developer",
            job_description="Working on LLMs, embeddings, Qdrant vector search, fine-tuning, and RAG pipelines."
        )
        self.assertEqual(p_ai.name, "Ganesh_Pirikirala_AI_Engineer_Resume.pdf")
        self.assertIn("JD matched AI / ML / RAG / LLM Engineer keywords", r_ai)

        # 4. Generic title 'Web Developer' with React/Node/MERN JD
        p_mern, r_mern = select_resume(
            job_title="Web Developer",
            job_description="Must be proficient in React.js, Next.js, Node.js, and MongoDB."
        )
        self.assertEqual(p_mern.name, "Ganesh_Pirikirala_MERN_Stack_Resume.pdf")

        # 5. Generic title 'Frontend Developer' with Angular/MEAN JD
        p_mean, r_mean = select_resume(
            job_title="Frontend Developer",
            job_description="Enterprise single-page application development using Angular, RxJS, and TypeScript."
        )
        self.assertEqual(p_mean.name, "Ganesh_Pirikirala_MEAN_Stack_Resume.pdf")


if __name__ == "__main__":
    unittest.main()
