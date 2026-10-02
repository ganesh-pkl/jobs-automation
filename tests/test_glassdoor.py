import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

import glassdoor_apply
from common.profile import Profile


class GlassdoorEngineTests(unittest.TestCase):
    def test_extract_experience_years(self):
        self.assertEqual(glassdoor_apply.extract_experience_years("Fresher"), (0, 0))
        self.assertEqual(glassdoor_apply.extract_experience_years("Entry Level Engineer"), (0, 0))
        self.assertEqual(glassdoor_apply.extract_experience_years("0-2 Yrs"), (0, 2))
        self.assertEqual(glassdoor_apply.extract_experience_years("3-5 years"), (3, 5))
        self.assertEqual(glassdoor_apply.extract_experience_years("5+ Years"), (5, 99))
        self.assertEqual(glassdoor_apply.extract_experience_years("3 years"), (3, 3))
        self.assertEqual(glassdoor_apply.extract_experience_years(None), (None, None))

    def test_extract_posted_age_days(self):
        self.assertEqual(glassdoor_apply.extract_posted_age_days("24h"), 0)
        self.assertEqual(glassdoor_apply.extract_posted_age_days("10 hours ago"), 0)
        self.assertEqual(glassdoor_apply.extract_posted_age_days("Just now"), 0)
        self.assertEqual(glassdoor_apply.extract_posted_age_days("2d"), 2)
        self.assertEqual(glassdoor_apply.extract_posted_age_days("3 days ago"), 3)
        self.assertEqual(glassdoor_apply.extract_posted_age_days("1w"), 7)
        self.assertEqual(glassdoor_apply.extract_posted_age_days("1 month ago"), 30)
        self.assertEqual(glassdoor_apply.extract_posted_age_days(None), 0)

    def test_matches_target_keywords(self):
        profile = Profile({
            "target_roles": ["Full Stack Developer", "Python Developer"],
            "skills_primary": ["React.js", "Python", "TypeScript", "Node.js"],
            "total_experience_years": 3,
            "notice_period_days": 0,
            "current_ctc_lpa": 6,
            "expected_ctc_lpa": 9,
            "resume_file_name": "resume.pdf",
            "current_city": "Hyderabad",
        })

        self.assertTrue(glassdoor_apply.matches_target_keywords(
            "Software Engineer-II",
            profile,
            "Full Stack Developer"
        ))
        self.assertTrue(glassdoor_apply.matches_target_keywords(
            "Senior Full-Stack AI Application Developer",
            profile,
            "Full Stack Developer"
        ))
        self.assertTrue(glassdoor_apply.matches_target_keywords(
            "Python Backend Developer",
            profile,
            "Python Developer"
        ))

        # Anti-pattern filtering
        self.assertFalse(glassdoor_apply.matches_target_keywords(
            "HR Manager / Recruiter",
            profile,
            "Full Stack Developer"
        ))
        self.assertFalse(glassdoor_apply.matches_target_keywords(
            "Sales Executive & Lead Generation",
            profile,
            "Full Stack Developer"
        ))
        self.assertFalse(glassdoor_apply.matches_target_keywords(
            "Graphic Designer",
            profile,
            "Full Stack Developer"
        ))

    def test_passes_company_filters(self):
        profile = Profile({
            "target_roles": ["Full Stack Developer"],
            "skills_primary": ["React.js", "Python"],
            "total_experience_years": 3,
            "notice_period_days": 0,
            "current_ctc_lpa": 6,
            "expected_ctc_lpa": 9,
            "resume_file_name": "resume.pdf",
            "current_city": "Hyderabad",
            "company_exclude": ["BadCorp", "ScamCo"],
        })

        self.assertTrue(glassdoor_apply.passes_company_filters("Spreetail", profile))
        self.assertTrue(glassdoor_apply.passes_company_filters("Google", profile))
        self.assertFalse(glassdoor_apply.passes_company_filters("BadCorp Inc", profile))
        self.assertFalse(glassdoor_apply.passes_company_filters("ScamCo Tech", profile))

    def test_load_applied_job_keys_and_count_today(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "applications.csv"
            path.write_text(
                "timestamp,source,title,company,status,reason\n"
                "2026-10-02T10:00:00,glassdoor,Software Engineer-II,Spreetail,applied,success\n"
                "2026-10-02T10:05:00,glassdoor,Python Developer,OtherCorp,skipped,excluded\n"
                "2026-10-01T10:00:00,glassdoor,Backend Dev,OldCorp,applied,success\n"
                "2026-10-02T10:10:00,naukri,Fullstack Dev,Acme,applied,success\n"
            )
            keys = glassdoor_apply.load_applied_job_keys(str(path))
            self.assertIn(("software engineer-ii", "spreetail"), keys)
            self.assertIn(("backend dev", "oldcorp"), keys)
            self.assertNotIn(("python developer", "othercorp"), keys)

            count_today = glassdoor_apply.count_applications_today(str(path), today=date(2026, 10, 2))
            self.assertEqual(count_today, 1)


if __name__ == "__main__":
    unittest.main()
