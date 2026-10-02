import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

import wellfound_apply
from common.profile import Profile


class WellfoundEngineTests(unittest.TestCase):
    def test_extract_experience_years_ranges_and_levels(self):
        self.assertEqual(wellfound_apply.extract_experience_years("No experience required"), (0, 1))
        self.assertEqual(wellfound_apply.extract_experience_years("0-2 Yrs"), (0, 2))
        self.assertEqual(wellfound_apply.extract_experience_years("1-3 years"), (1, 3))
        self.assertEqual(wellfound_apply.extract_experience_years("3+ years"), (3, 99))
        self.assertEqual(wellfound_apply.extract_experience_years("5+ years"), (5, 99))
        self.assertEqual(wellfound_apply.extract_experience_years("6+ years in Engineering role"), (6, 99))
        self.assertEqual(wellfound_apply.extract_experience_years("Senior Software Engineer"), (5, 99))
        self.assertEqual(wellfound_apply.extract_experience_years(None), (None, None))

    def test_extract_posted_age_days(self):
        self.assertEqual(wellfound_apply.extract_posted_age_days("POSTED 4 WEEKS AGO"), 28)
        self.assertEqual(wellfound_apply.extract_posted_age_days("POSTED 1 WEEK AGO"), 7)
        self.assertEqual(wellfound_apply.extract_posted_age_days("POSTED 2 DAYS AGO"), 2)
        self.assertEqual(wellfound_apply.extract_posted_age_days("POSTED 10 HOURS AGO"), 0)
        self.assertEqual(wellfound_apply.extract_posted_age_days("POSTED 1 MONTH AGO"), 30)
        self.assertEqual(wellfound_apply.extract_posted_age_days(None), 0)

    def test_location_eligibility(self):
        profile = Profile({
            "current_city": "Hyderabad",
            "relocate_cities": ["Bengaluru"],
            "remote_locations": ["India", "Remote", "Worldwide"],
            "target_roles": ["Software Engineer"],
            "total_experience_years": 3,
            "notice_period_days": 0,
            "current_ctc_lpa": 6,
            "expected_ctc_lpa": 9,
            "resume_file_name": "resume.pdf",
        })

        self.assertTrue(wellfound_apply.is_location_eligible("Remote (India)", profile))
        self.assertTrue(wellfound_apply.is_location_eligible("Hires remotely in India", profile))
        self.assertTrue(wellfound_apply.is_location_eligible("Hyderabad, India", profile))
        self.assertTrue(wellfound_apply.is_location_eligible("Worldwide Remote", profile))

        self.assertFalse(wellfound_apply.is_location_eligible("US Only (San Francisco, CA)", profile))
        self.assertFalse(wellfound_apply.is_location_eligible("Europe Only (London, UK)", profile))

    def test_matches_target_keywords(self):
        profile = Profile({
            "target_roles": ["Python Developer", "Full Stack Developer"],
            "role_required_keywords": {
                "Python Developer": ["python", "django", "fastapi"],
                "Full Stack Developer": ["full stack", "react", "node"]
            },
            "skills_primary": ["React.js", "Node.js", "Python", "TypeScript"],
            "total_experience_years": 3,
            "notice_period_days": 0,
            "current_ctc_lpa": 6,
            "expected_ctc_lpa": 9,
            "resume_file_name": "resume.pdf",
            "current_city": "Hyderabad",
        })

        self.assertTrue(wellfound_apply.matches_target_keywords(
            "Hiring Full Time Remote Python Developers - Parsewave.ai",
            "Python Django REST",
            profile,
            "Python Developer"
        ))
        self.assertTrue(wellfound_apply.matches_target_keywords(
            "Full Stack Software Engineer",
            "React Node",
            profile
        ))
        # Non-tech / Excluded roles must strictly return False
        self.assertFalse(wellfound_apply.matches_target_keywords(
            "Founding Content & Community Lead",
            "Social Media, Product Marketing, SaaS, Copywriting",
            profile
        ))
        self.assertFalse(wellfound_apply.matches_target_keywords(
            "Sales Executive",
            "Cold calling lead generation",
            profile,
            "Python Developer"
        ))
        self.assertFalse(wellfound_apply.matches_target_keywords(
            "Recruiter / Talent Acquisition Specialist",
            "Screening, Sourcing, Hiring",
            profile
        ))

    def test_pitch_note_generation_fallback(self):
        profile = Profile({
            "target_roles": ["Python Developer"],
            "total_experience_years": 3,
            "notice_period_days": 0,
            "current_ctc_lpa": 6,
            "expected_ctc_lpa": 9,
            "resume_file_name": "resume.pdf",
            "current_city": "Hyderabad",
            "work_history_narrative": "I am a Full-Stack Software Engineer with 3 years of experience in React, Node, Python, Spring Boot.",
        })

        note = wellfound_apply.generate_pitch_note("Python Developer", "Parsewave", "Python backend", profile)
        self.assertIsInstance(note, str)
        self.assertGreater(len(note), 30)

    def test_application_confirmation(self):
        self.assertTrue(wellfound_apply._is_application_confirmation("Application submitted!"))
        self.assertTrue(wellfound_apply._is_application_confirmation("Applied to Parsewave"))
        self.assertTrue(wellfound_apply._is_application_confirmation("Want to improve your odds? Complete a quick, reusable AI interview to show off your skills"))
        self.assertTrue(wellfound_apply._is_application_confirmation("", "Applied"))
        self.assertFalse(wellfound_apply._is_application_confirmation("Apply on Wellfound", "Apply"))

    def test_load_applied_job_keys_and_count_today(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "applications.csv"
            path.write_text(
                "timestamp,source,title,company,status,reason\n"
                "2026-10-01T10:00:00,wellfound,Python Developer,Parsewave,applied,success\n"
                "2026-10-01T10:05:00,wellfound,Staff Engineer,US Only,skipped,location restricted\n"
                "2026-09-30T10:00:00,wellfound,Backend Developer,Certa,applied,success\n"
                "2026-10-01T10:10:00,foundit,Fullstack Developer,Acme,applied,success\n"
            )
            keys = wellfound_apply.load_applied_job_keys(str(path))
            self.assertIn(("python developer", "parsewave"), keys)
            self.assertIn(("backend developer", "certa"), keys)
            self.assertNotIn(("staff engineer", "us only"), keys)

            count_today = wellfound_apply.count_applications_today(str(path), today=date(2026, 10, 1))
            self.assertEqual(count_today, 1)


if __name__ == "__main__":
    unittest.main()
