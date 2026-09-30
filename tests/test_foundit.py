import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

import foundit_apply
from common.profile import Profile


class FounditEngineTests(unittest.TestCase):
    def test_extract_experience_years_fresher(self):
        min_exp, max_exp = foundit_apply.extract_experience_years("Fresher")
        self.assertEqual(min_exp, 0)
        self.assertEqual(max_exp, 0)

        min_exp, max_exp = foundit_apply.extract_experience_years("fresher role")
        self.assertEqual(min_exp, 0)
        self.assertEqual(max_exp, 0)

    def test_extract_experience_years_range(self):
        self.assertEqual(foundit_apply.extract_experience_years("5 - 8 Years"), (5, 8))
        self.assertEqual(foundit_apply.extract_experience_years("0-2 Yrs"), (0, 2))
        self.assertEqual(foundit_apply.extract_experience_years("2 to 5 years"), (2, 5))

    def test_extract_experience_years_plus(self):
        self.assertEqual(foundit_apply.extract_experience_years("8+ Years"), (8, 99))
        self.assertEqual(foundit_apply.extract_experience_years("3+ yrs"), (3, 99))

    def test_extract_experience_years_single_or_empty(self):
        self.assertEqual(foundit_apply.extract_experience_years("3 Years"), (3, 3))
        self.assertEqual(foundit_apply.extract_experience_years(None), (None, None))
        self.assertEqual(foundit_apply.extract_experience_years("Not specified"), (None, None))

    def test_extract_posted_age_days(self):
        self.assertEqual(foundit_apply.extract_posted_age_days("Posted 17 hours ago"), 0)
        self.assertEqual(foundit_apply.extract_posted_age_days("Posted 2 days ago"), 2)
        self.assertEqual(foundit_apply.extract_posted_age_days("Posted 1 day ago"), 1)
        self.assertEqual(foundit_apply.extract_posted_age_days("Posted a day ago"), 1)
        self.assertEqual(foundit_apply.extract_posted_age_days("Posted 2 weeks ago"), 14)
        self.assertEqual(foundit_apply.extract_posted_age_days("Posted a month ago"), 30)
        self.assertEqual(foundit_apply.extract_posted_age_days("Posted 1 month ago"), 30)
        self.assertEqual(foundit_apply.extract_posted_age_days(None), 0)

        # Multiline card text with experience numbers
        card_text = (
            "Senior Full Stack Developer\n"
            "Tech Corp\n"
            "2 - 4 Years\n"
            "Bengaluru, India\n"
            "Posted 10 hours ago\n"
        )
        self.assertEqual(foundit_apply.extract_posted_age_days(card_text), 0)

    def test_matches_target_keywords(self):
        profile = Profile({
            "target_roles": ["Backend Developer"],
            "role_required_keywords": {
                "Backend Developer": ["backend", "python", "fastapi"]
            }
        })
        self.assertTrue(foundit_apply.matches_target_keywords("Senior Python Backend Developer", profile, "Backend Developer"))
        self.assertFalse(foundit_apply.matches_target_keywords("Digital Marketing Specialist", profile, "Backend Developer"))

    def test_passes_company_filters(self):
        profile = Profile({
            "company_exclude": ["BadCorp", "Spam Ltd"],
            "company_include_only": []
        })
        self.assertTrue(foundit_apply.passes_company_filters("GoodCorp", profile))
        self.assertFalse(foundit_apply.passes_company_filters("BadCorp India Pvt Ltd", profile))

    def test_passes_location_filters(self):
        profile = Profile({
            "current_city": "Bengaluru",
            "relocate_cities": ["Hyderabad"],
            "work_mode": "flexible"
        })
        self.assertTrue(foundit_apply.passes_location_filters("Bengaluru, India", profile))
        self.assertTrue(foundit_apply.passes_location_filters("Hyderabad", profile))
        self.assertTrue(foundit_apply.passes_location_filters("Remote / Anywhere", profile))
        self.assertFalse(foundit_apply.passes_location_filters("Mumbai, India", profile))

    def test_application_confirmation(self):
        self.assertTrue(foundit_apply._is_application_confirmation("You have successfully applied for this job"))
        self.assertTrue(foundit_apply._is_application_confirmation("", "Applied"))
        self.assertTrue(foundit_apply._is_application_confirmation("Application submitted!"))
        self.assertFalse(foundit_apply._is_application_confirmation("Apply for this opportunity today", "Apply Now"))

    def test_load_applied_job_keys_and_count_today(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "applications.csv"
            path.write_text(
                "timestamp,source,title,company,status,reason\n"
                "2026-09-30T10:00:00,foundit,Software Engineer,Infosys,applied,success\n"
                "2026-09-30T10:05:00,foundit,Data Analyst,TCS,skipped,experience mismatch\n"
                "2026-09-29T10:00:00,foundit,DevOps Engineer,Wipro,applied,success\n"
                "2026-09-30T10:10:00,naukri,Fullstack Developer,Acme,applied,success\n"
            )
            keys = foundit_apply.load_applied_job_keys(str(path))
            self.assertIn(("software engineer", "infosys"), keys)
            self.assertIn(("devops engineer", "wipro"), keys)
            self.assertNotIn(("data analyst", "tcs"), keys)

            count_today = foundit_apply.count_applications_today(str(path), today=date(2026, 9, 30))
            self.assertEqual(count_today, 1)


if __name__ == "__main__":
    unittest.main()
