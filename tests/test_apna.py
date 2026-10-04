import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

import apna_apply
from common.profile import Profile


class ApnaEngineTests(unittest.TestCase):
    def test_parse_experience_years(self):
        self.assertEqual(apna_apply.parse_experience_years("Min. 2 years"), (2, 2))
        self.assertEqual(apna_apply.parse_experience_years("Min. 1 year"), (1, 1))
        self.assertEqual(apna_apply.parse_experience_years("0 - 3 yrs"), (0, 3))
        self.assertEqual(apna_apply.parse_experience_years("2 - 5 years"), (2, 5))
        self.assertEqual(apna_apply.parse_experience_years("3+ yrs"), (3, 99))
        self.assertEqual(apna_apply.parse_experience_years("Any experience"), (0, 0))
        self.assertEqual(apna_apply.parse_experience_years("Fresher"), (0, 0))
        self.assertEqual(apna_apply.parse_experience_years(None), (None, None))

    def test_matches_target_keywords(self):
        profile = Profile({
            "target_roles": ["Full Stack Developer", "Software Engineer"],
            "skills_primary": ["React.js", "Python", "Node.js", "TypeScript"],
            "total_experience_years": 3,
            "notice_period_days": 0,
            "current_ctc_lpa": 6,
            "expected_ctc_lpa": 9,
            "resume_file_name": "resume.pdf",
            "current_city": "Hyderabad",
        })

        # Tech matches
        self.assertTrue(apna_apply.matches_target_keywords(
            "Full Stack Developer",
            profile,
            "Full Stack Developer"
        ))
        self.assertTrue(apna_apply.matches_target_keywords(
            "Software Engineer - React / Python",
            profile,
            "Full Stack Developer"
        ))

        # Anti-pattern non-tech jobs (from user screenshot)
        self.assertFalse(apna_apply.matches_target_keywords(
            "Delivery Partner",
            profile,
            "Full Stack Developer"
        ))
        self.assertFalse(apna_apply.matches_target_keywords(
            "Typist",
            profile,
            "Full Stack Developer"
        ))
        self.assertFalse(apna_apply.matches_target_keywords(
            "Junior Architect",
            profile,
            "Full Stack Developer"
        ))
        self.assertFalse(apna_apply.matches_target_keywords(
            "Graphic Designer",
            profile,
            "Full Stack Developer"
        ))
        self.assertFalse(apna_apply.matches_target_keywords(
            "MIS Coordinator",
            profile,
            "Full Stack Developer"
        ))
        self.assertFalse(apna_apply.matches_target_keywords(
            "Digital Marketing Executive",
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

        self.assertTrue(apna_apply.passes_company_filters("Kumar Shirts", profile))
        self.assertTrue(apna_apply.passes_company_filters("Zepto", profile))
        self.assertFalse(apna_apply.passes_company_filters("BadCorp India Pvt Ltd", profile))
        self.assertFalse(apna_apply.passes_company_filters("ScamCo Tech", profile))

    def test_passes_location_filters(self):
        profile = Profile({
            "target_roles": ["Full Stack Developer"],
            "skills_primary": ["React.js", "Python"],
            "total_experience_years": 3,
            "notice_period_days": 0,
            "current_ctc_lpa": 6,
            "expected_ctc_lpa": 9,
            "resume_file_name": "resume.pdf",
            "current_city": "Hyderabad",
            "relocate_cities": ["Bengaluru"],
        })

        self.assertTrue(apna_apply.passes_location_filters("Work from home", profile))
        self.assertTrue(apna_apply.passes_location_filters("Remote", profile))
        self.assertTrue(apna_apply.passes_location_filters("Banjara Hills, Hyderabad", profile))
        self.assertTrue(apna_apply.passes_location_filters("Secunderabad", profile))
        self.assertFalse(apna_apply.passes_location_filters("Bengaluru, Karnataka", profile))
        self.assertFalse(apna_apply.passes_location_filters("Kalapatti, Coimbatore", profile))
        self.assertFalse(apna_apply.passes_location_filters("Jaipur, Rajasthan", profile))

    def test_is_job_eligible(self):
        profile = Profile({
            "target_roles": ["Full Stack Developer"],
            "skills_primary": ["React.js", "Python", "Node.js"],
            "total_experience_years": 3,
            "notice_period_days": 0,
            "current_ctc_lpa": 6,
            "expected_ctc_lpa": 9,
            "resume_file_name": "resume.pdf",
            "current_city": "Hyderabad",
            "relocate_cities": ["Bengaluru"],
        })

        # Eligible
        job_good = {
            "title": "Full Stack Developer",
            "company": "Cognitivo Tech",
            "location": "Hyderabad",
            "experience_text": "Min. 2 years"
        }
        ok, reason = apna_apply.is_job_eligible(job_good, profile, "Full Stack Developer")
        self.assertTrue(ok)
        self.assertEqual(reason, "Eligible")

        # Ineligible due to experience ceiling
        job_too_senior = {
            "title": "Full Stack Developer",
            "company": "Cognitivo Tech",
            "location": "Hyderabad",
            "experience_text": "Min. 7 years"
        }
        ok, reason = apna_apply.is_job_eligible(job_too_senior, profile, "Full Stack Developer")
        self.assertFalse(ok)
        self.assertIn("exceeds candidate profile", reason)

        # Ineligible due to role mismatch
        job_wrong_role = {
            "title": "Delivery Partner",
            "company": "Zepto",
            "location": "Hyderabad",
            "experience_text": "Any experience"
        }
        ok, reason = apna_apply.is_job_eligible(job_wrong_role, profile, "Full Stack Developer")
        self.assertFalse(ok)
        self.assertIn("does not match target role", reason)

    def test_load_applied_job_keys_and_count_today(self):
        with TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "applications_log.csv"
            log_file.write_text(
                "timestamp,source,title,company,status,reason\n"
                f"{date.today().isoformat()}T10:00:00,apna,Full Stack Developer,Acme Corp,applied,Success\n"
                f"{date.today().isoformat()}T11:00:00,apna,Backend Developer,Beta LLC,applied,Success\n"
                "2026-01-01T10:00:00,apna,Frontend Engineer,Old Corp,applied,Success\n",
                encoding="utf-8"
            )

            keys = apna_apply.load_applied_job_keys(str(log_file))
            self.assertIn(("full stack developer", "acme corp"), keys)
            self.assertIn(("backend developer", "beta llc"), keys)
            self.assertIn(("frontend engineer", "old corp"), keys)

            count = apna_apply.count_applications_today(str(log_file), date.today())
            self.assertEqual(count, 2)


if __name__ == "__main__":
    unittest.main()
