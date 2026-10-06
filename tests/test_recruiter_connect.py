import unittest
from datetime import date
from common.profile import Profile
from common.recruiter_connect import (
    build_recruiter_connection_note,
    MAX_NOTE_LENGTH,
    count_connections_today,
    load_contacted_recruiters,
)


class RecruiterConnectTests(unittest.TestCase):
    def setUp(self):
        self.profile = Profile({
            "first_name": "Ganesh",
            "last_name": "Pirikirala",
            "total_experience_years": 3,
            "notice_period_days": 0,
            "immediately_available": True,
            "current_ctc_lpa": 6,
            "expected_ctc_lpa": 9,
            "ctc_disclosure_policy": "real_numbers",
            "current_city": "Hyderabad",
            "relocate_cities": ["Bengaluru"],
            "night_shift_ok": True,
            "weekend_ok": False,
            "max_connection_requests_per_day": 10,
        })

    def test_note_length_strictly_under_300_chars(self):
        cases = [
            ("Sankalp Sharma", "Full Stack Developer", "Google"),
            ("Dr. Jane Doe, Ph.D.", "Senior Lead Full-Stack Software Engineer (React / Node)", "Microsoft Technologies Corporation India Pvt Ltd"),
            ("Alex", "Backend Engineer", "Stripe"),
            ("Priya R.", "Java Spring Boot Developer (AI / ML Integrations)", "Amazon Web Services India"),
            ("Michael van der Berg", "Software Engineer - Full Stack & Distributed Systems", "Meta Platforms"),
        ]

        for name, role, company in cases:
            note = build_recruiter_connection_note(name, role, company, self.profile)
            self.assertLessEqual(len(note), MAX_NOTE_LENGTH, f"Note exceeded 300 chars ({len(note)} chars): {note}")
            self.assertTrue(note.startswith("Hi "), f"Note should start with greeting: {note}")
            self.assertIn("Ganesh", note)

    def test_dynamic_role_specific_pitch_generation(self):
        # 1. AI / LLM role
        ai_note = build_recruiter_connection_note("Sarah Connor", "AI Engineer", "Anthropic", self.profile)
        self.assertLessEqual(len(ai_note), MAX_NOTE_LENGTH)
        self.assertIn("AI/LLM", ai_note)
        self.assertIn("LangChain", ai_note)

        # 2. Java / Spring Boot role
        java_note = build_recruiter_connection_note("Rajesh Kumar", "Java Developer", "Infosys", self.profile)
        self.assertLessEqual(len(java_note), MAX_NOTE_LENGTH)
        self.assertIn("Java", java_note)
        self.assertIn("Spring Boot", java_note)

        # 3. Python / FastAPI role
        py_note = build_recruiter_connection_note("Elena Rostova", "Python FastAPI Engineer", "DataCorp", self.profile)
        self.assertLessEqual(len(py_note), MAX_NOTE_LENGTH)
        self.assertIn("Python", py_note)
        self.assertIn("FastAPI", py_note)

        # 4. React / Frontend role
        fe_note = build_recruiter_connection_note("David Miller", "Frontend React Developer", "Meta", self.profile)
        self.assertLessEqual(len(fe_note), MAX_NOTE_LENGTH)
        self.assertIn("React", fe_note)
        self.assertIn("TypeScript", fe_note)

        # 5. Full-Stack role
        fs_note = build_recruiter_connection_note("Anita Sen", "Full Stack Developer", "TCS", self.profile)
        self.assertLessEqual(len(fs_note), MAX_NOTE_LENGTH)
        self.assertIn("Full-Stack", fs_note)

    def test_category_quota_distribution(self):
        from linkedin_recruiter_outreach import compute_category_quotas
        quotas_10 = compute_category_quotas(10)
        self.assertEqual(quotas_10["AI Engineer"], 3)
        self.assertEqual(quotas_10["Full Stack"], 5)
        self.assertEqual(quotas_10["Backend"], 2)
        self.assertEqual(sum(quotas_10.values()), 10)

        quotas_5 = compute_category_quotas(5)
        self.assertEqual(sum(quotas_5.values()), 5)

    def test_job_match_filtering(self):
        from linkedin_recruiter_outreach import is_job_match
        # Accept valid roles
        self.assertTrue(is_job_match("Full Stack Developer", "Full Stack Developer", self.profile)[0])
        self.assertTrue(is_job_match("AI Software Engineer", "AI Engineer", self.profile)[0])
        self.assertTrue(is_job_match("Python Backend Developer", "Python Developer", self.profile)[0])
        self.assertTrue(is_job_match("React.js Frontend Engineer", "React.js Developer", self.profile)[0])
        self.assertTrue(is_job_match("Java Spring Boot Developer", "Java Developer", self.profile)[0])

        # Reject senior/lead/unrelated
        self.assertFalse(is_job_match("Technical Lead", "Software Engineer", self.profile)[0])
        self.assertFalse(is_job_match("Dynamics CRM Developer", "Full Stack Developer", self.profile)[0])
        self.assertFalse(is_job_match("Lead Python Backend Engineer", "Python Developer", self.profile)[0])
        self.assertFalse(is_job_match("Senior Java Architect", "Java Developer", self.profile)[0])
        self.assertFalse(is_job_match("SAP Consultant", "Full Stack Developer", self.profile)[0])


if __name__ == "__main__":
    unittest.main()
