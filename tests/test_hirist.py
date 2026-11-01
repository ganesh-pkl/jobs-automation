import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

import hirist_apply
from common.profile import Profile


class HiristEngineTests(unittest.TestCase):
    def test_load_applied_job_keys(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "applications.csv"
            path.write_text(
                "timestamp,source,title,company,status,reason\n"
                "2026-10-07T06:51:34,hirist,React Native Engineer,Mobile Tech,applied,success\n"
                "2026-10-07T06:52:19,hirist,Java Developer,Acme,skipped,experience mismatch\n"
            )
            keys = hirist_apply.load_applied_job_keys(str(path))
            self.assertIn(("react native engineer", "mobile tech"), keys)
            self.assertNotIn(("java developer", "acme"), keys)

    def test_clean_numeric_value(self):
        self.assertEqual(hirist_apply._clean_numeric_value("6 LPA"), "6")
        self.assertEqual(hirist_apply._clean_numeric_value("3.5 years"), "3.5")
        self.assertEqual(hirist_apply._clean_numeric_value("0 days"), "0")
        self.assertEqual(hirist_apply._clean_numeric_value("", default="0"), "0")
        self.assertEqual(hirist_apply._clean_numeric_value(None, default="0"), "0")

    def test_should_check_checkbox(self):
        profile = Profile({
            "relocate_cities": ["Bengaluru"],
            "skills_primary": ["Java", "Spring Boot", "React.js"],
        })
        # 1. Agreements & Confirmations
        self.assertTrue(hirist_apply._should_check_checkbox("I agree to terms and conditions", profile))
        self.assertTrue(hirist_apply._should_check_checkbox("I confirm the information is accurate", profile))
        self.assertTrue(hirist_apply._should_check_checkbox("I consent to share my profile with employer", profile))
        # 2. Availability & Relocation
        self.assertTrue(hirist_apply._should_check_checkbox("Willing to relocate to Bangalore / Bengaluru", profile))
        self.assertTrue(hirist_apply._should_check_checkbox("Immediate joiner / available within 15 days", profile))
        self.assertTrue(hirist_apply._should_check_checkbox("Authorized to work in India", profile))
        self.assertTrue(hirist_apply._should_check_checkbox("Comfortable working in hybrid / remote model", profile))
        # 3. Core Technical Skills
        self.assertTrue(hirist_apply._should_check_checkbox("Java", profile))
        self.assertTrue(hirist_apply._should_check_checkbox("Spring Boot", profile))
        self.assertTrue(hirist_apply._should_check_checkbox("React.js", profile))
        self.assertTrue(hirist_apply._should_check_checkbox("Microservices & REST APIs", profile))
        # 4. Disqualifications & Excluded Technologies
        self.assertFalse(hirist_apply._should_check_checkbox("Do you require visa sponsorship?", profile))
        self.assertFalse(hirist_apply._should_check_checkbox("Have you ever been convicted or terminated?", profile))
        self.assertFalse(hirist_apply._should_check_checkbox("Experience in PHP & Laravel", profile))
        # 5. Empty label
        self.assertTrue(hirist_apply._should_check_checkbox("", profile))

    def test_auto_decide_hirist_radio(self):
        profile = Profile({
            "relocate_cities": ["Bengaluru"],
            "total_experience_years": 3,
            "current_ctc_lpa": 6,
            "expected_ctc_lpa": 9,
            "notice_period_days": 0,
        })
        # Notice period
        notice_opts = [{"idx": 0, "label": "Immediate"}, {"idx": 1, "label": "30 Days"}, {"idx": 2, "label": "60 Days"}]
        self.assertEqual(hirist_apply._auto_decide_hirist_radio("What is your notice period?", notice_opts, profile)["label"], "Immediate")
        # Relocation
        reloc_opts = [{"idx": 0, "label": "Yes"}, {"idx": 1, "label": "No"}]
        self.assertEqual(hirist_apply._auto_decide_hirist_radio("Willing to relocate to Bengaluru?", reloc_opts, profile)["label"], "Yes")
        # Military / Layoff
        mil_opts = [{"idx": 0, "label": "Never served"}, {"idx": 1, "label": "Served"}]
        self.assertEqual(hirist_apply._auto_decide_hirist_radio("Served in military?", mil_opts, profile)["label"], "Never served")
        # Experience range
        exp_opts = [{"idx": 0, "label": "0-1 years"}, {"idx": 1, "label": "1-3 years"}, {"idx": 2, "label": "5+ years"}]
        self.assertEqual(hirist_apply._auto_decide_hirist_radio("Total experience range", exp_opts, profile)["label"], "1-3 years")

    def test_format_input_answer(self):
        profile = Profile({
            "current_ctc_lpa": 6,
            "expected_ctc_lpa": 9,
            "total_experience_years": 3,
            "notice_period_days": 0,
            "current_city": "Hyderabad",
            "phone": "7659869814",
            "email": "ganesh.pkl08@gmail.com",
        })
        # Numeric inputs
        self.assertEqual(hirist_apply._format_input_answer("Current CTC (in LPA)", "number", profile, ""), "6")
        self.assertEqual(hirist_apply._format_input_answer("Expected CTC (in LPA)", "number", profile, ""), "9")
        self.assertEqual(hirist_apply._format_input_answer("Total Experience (in years)", "number", profile, ""), "3")
        self.assertEqual(hirist_apply._format_input_answer("Notice Period (in days)", "number", profile, ""), "0")
        # Text inputs
        self.assertEqual(hirist_apply._format_input_answer("Current CTC", "text", profile, ""), "6 LPA")
        self.assertEqual(hirist_apply._format_input_answer("Expected CTC", "text", profile, ""), "9 LPA")
        self.assertEqual(hirist_apply._format_input_answer("Current Location", "text", profile, ""), "Hyderabad")
        self.assertEqual(hirist_apply._format_input_answer("Mobile / Phone number", "text", profile, ""), "7659869814")

    def test_is_hirist_success(self):
        self.assertTrue(hirist_apply._is_hirist_success("https://www.hirist.tech/job/applied/12345", ""))
        self.assertTrue(hirist_apply._is_hirist_success("", "Your application has been received successfully!"))
        self.assertTrue(hirist_apply._is_hirist_success("", "Application submitted"))
        self.assertTrue(hirist_apply._is_hirist_success("", "Thank you for applying"))
        self.assertTrue(hirist_apply._is_hirist_success("", "", has_applied_button=True))
        self.assertFalse(hirist_apply._is_hirist_success("https://www.hirist.tech/j/12345", "Please answer all questions"))

    def test_decide_hirist_checkbox_options(self):
        profile = Profile({
            "notice_period_days": 0,
            "relocate_cities": ["Bengaluru"],
            "skills_primary": ["Java", "Spring Boot", "React.js"],
        })
        # 1. Notice period question with options
        notice_opts = [
            {"idx": 0, "label": "0- 7 Days"},
            {"idx": 1, "label": "7- 15 Days"},
            {"idx": 2, "label": "15- 30 Days"},
            {"idx": 3, "label": "30- 45 Days"},
            {"idx": 4, "label": "45+ Days"},
        ]
        decisions = hirist_apply._decide_hirist_checkbox_options(
            "If selected. how soon you can join with us ?", notice_opts, profile
        )
        self.assertTrue(decisions[0])   # 0- 7 Days must be checked
        self.assertFalse(decisions[1])  # 7- 15 Days must NOT be checked
        self.assertFalse(decisions[2])  # 15- 30 Days must NOT be checked
        self.assertFalse(decisions[3])  # 30- 45 Days must NOT be checked
        self.assertFalse(decisions[4])  # 45+ Days must NOT be checked

        # 2. Relocation question
        reloc_opts = [{"idx": 0, "label": "Yes"}, {"idx": 1, "label": "No"}]
        r_dec = hirist_apply._decide_hirist_checkbox_options("Are you willing to relocate to Bengaluru?", reloc_opts, profile)
        self.assertTrue(r_dec[0])
        self.assertFalse(r_dec[1])

        # 3. Prompt noise in question title must be stripped and still match CTC
        ctc_ans = hirist_apply._format_input_answer(
            "tell the recruiter more about yourself\nWhat is your current CTC*", "text", profile, ""
        )
        self.assertEqual(ctc_ans, "6 LPA")

    def test_job_key(self):
        self.assertEqual(
            hirist_apply._job_key("  Full Stack  Developer  ", " Acme Corp "),
            ("full stack developer", "acme corp")
        )


if __name__ == "__main__":
    unittest.main()
