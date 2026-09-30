import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

import instahyre_apply
from common.profile import Profile


class InstahyreEngineTests(unittest.TestCase):
    def test_extract_instamatch_score_low(self):
        sample_modal_text = (
            "Frontend Developer\nStar Health and Allied Insurance\n"
            "Your InstaMatch score\nLOW\n"
            "Your chances of being shortlisted for this job are Low ?\n"
            "About Star Health and Allied Insurance"
        )
        score_lvl, desc = instahyre_apply.extract_instamatch_score(sample_modal_text)
        self.assertEqual(score_lvl, "LOW")

    def test_extract_instamatch_score_high(self):
        sample_modal_text = (
            "Full Stack Developer\nAcme Corp\n"
            "Your InstaMatch score\nHIGH\n"
            "Your chances of being shortlisted for this job are High\n"
            "About Acme Corp"
        )
        score_lvl, desc = instahyre_apply.extract_instamatch_score(sample_modal_text)
        self.assertEqual(score_lvl, "HIGH")

    def test_extract_instamatch_score_medium(self):
        sample_modal_text = (
            "Backend Engineer\nBeta Tech\n"
            "Your InstaMatch score\nMEDIUM\n"
            "Your chances of being shortlisted for this job are Medium\n"
            "About Beta Tech"
        )
        score_lvl, desc = instahyre_apply.extract_instamatch_score(sample_modal_text)
        self.assertEqual(score_lvl, "MEDIUM")

    def test_extract_instamatch_score_unknown_fallback(self):
        sample_modal_text = "Software Engineer\nStartup XYZ\nAbout the job\nRequirements: Python"
        score_lvl, desc = instahyre_apply.extract_instamatch_score(sample_modal_text)
        self.assertEqual(score_lvl, "UNKNOWN")

    def test_matches_target_keywords(self):
        profile = Profile({
            "target_roles": ["Frontend Developer", "Full Stack Developer"],
            "role_required_keywords": {
                "Frontend Developer": ["frontend", "react", "typescript"],
            }
        })
        self.assertTrue(instahyre_apply.matches_target_keywords("React.js Developer with TypeScript", profile))
        self.assertFalse(instahyre_apply.matches_target_keywords("Senior Accountant with Tally experience", profile))

    def test_extract_experience_years(self):
        # Range patterns
        self.assertEqual(instahyre_apply.extract_experience_years("Frontend Developer • 7-11 Years • Hyderabad"), (7, 11))
        self.assertEqual(instahyre_apply.extract_experience_years("React Developer • 0-2 yrs • Bangalore"), (0, 2))
        self.assertEqual(instahyre_apply.extract_experience_years("Full Stack Engineer • 2 to 5 years • Remote"), (2, 5))
        # Plus pattern
        self.assertEqual(instahyre_apply.extract_experience_years("Tech Lead • 8+ Years • Remote"), (8, 99))
        self.assertEqual(instahyre_apply.extract_experience_years("Senior Software Engineer • 5+ yrs • Gurgaon"), (5, 99))
        # Unspecified
        self.assertEqual(instahyre_apply.extract_experience_years("Software Engineer • Python"), (None, None))

    def test_load_applied_job_keys(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "applications.csv"
            path.write_text(
                "timestamp,source,title,company,status,reason\n"
                "2026-09-29 07:00:00,instahyre,Frontend Developer,Star Health,applied,success\n"
                "2026-09-29 07:05:00,instahyre,DevOps Engineer,Cloud Corp,skipped,low score\n"
            )
            keys = instahyre_apply.load_applied_job_keys(str(path))
            self.assertIn(("frontend developer", "star health"), keys)
            self.assertNotIn(("devops engineer", "cloud corp"), keys)


if __name__ == "__main__":
    unittest.main()
