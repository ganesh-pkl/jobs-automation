import os
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import naukri_apply
import linkedin_apply
from common import llm
from common.profile import Profile


class ApplicationConfirmationTests(unittest.TestCase):
    def test_explicit_confirmation_is_accepted(self):
        self.assertTrue(
            naukri_apply._is_application_confirmation(
                'Applied to "Data Engineer"'
            )
        )
        self.assertTrue(
            naukri_apply._is_application_confirmation("", "Already Applied")
        )
        self.assertTrue(
            naukri_apply._is_application_confirmation("Thank you for your responses.")
        )

    def test_arbitrary_changed_button_is_not_success(self):
        self.assertFalse(
            naukri_apply._is_application_confirmation("Job details", "Unavailable")
        )

    def test_linkedin_requires_explicit_confirmation(self):
        self.assertTrue(
            linkedin_apply._is_application_confirmation(
                "Your application was sent to Example Corp"
            )
        )
        self.assertFalse(linkedin_apply._is_application_confirmation("Done"))

    def test_screening_greeting_is_not_completion(self):
        self.assertFalse(
            naukri_apply._is_completion_message(
                "Hi, thank you for showing interest. Please answer the questions."
            )
        )
        self.assertTrue(
            naukri_apply._is_completion_message("Your application was submitted")
        )
        self.assertTrue(
            naukri_apply._is_completion_message("Thank you for your responses.")
        )


class DirectAnswerTests(unittest.TestCase):
    def setUp(self):
        self.answers = {
            "years_experience": "6",
            "notice_period": "30 days",
        }

    def test_total_experience_uses_profile_value(self):
        self.assertEqual(
            naukri_apply._direct_profile_answer(
                "What is your total experience?", self.answers
            ),
            "6",
        )

    def test_skill_experience_is_not_replaced_with_total(self):
        self.assertIsNone(
            naukri_apply._direct_profile_answer(
                "How many years of Python experience do you have?", self.answers
            )
        )
        self.assertIsNone(
            naukri_apply._direct_profile_answer(
                "What is your overall experience in Snowflake?", self.answers
            )
        )

    def test_skill_experience_is_zero_for_fresher(self):
        self.assertEqual(
            naukri_apply._direct_profile_answer(
                "How many years of experience do you have in Spring Boot?",
                {"years_experience": "0"},
            ),
            "0",
        )

    def test_sensitive_field_detection(self):
        self.assertTrue(naukri_apply._is_sensitive_field("Enter your Aadhaar number"))
        self.assertTrue(naukri_apply._is_sensitive_field("What is your DOB?"))
        self.assertFalse(naukri_apply._is_sensitive_field("What is your notice period?"))

    def test_hourly_rate_answers(self):
        from common.answers import direct_profile_answer
        answers = {"current_hourly_rate_usd": "15", "expected_hourly_rate_usd": "25"}
        self.assertEqual(direct_profile_answer("What is your current hourly rate (in USD)?*", answers), "15")
        self.assertEqual(direct_profile_answer("What is your expected hourly rate (in USD) for this engagement?*", answers), "25")

    def test_immediate_joiner_respond_one(self):
        from common.answers import direct_profile_answer
        self.assertEqual(direct_profile_answer("How soon can you join? Respond '1' if you are an immediate joiner. *", {}), "1")

    def test_contract_comfort_answer(self):
        from common.answers import direct_profile_answer
        self.assertEqual(direct_profile_answer("This is a short-term contract engagement of 3 months. Are you comfortable with this? *", {}), "Yes")

    def test_learned_answer_validation(self):
        from common.learned_answers import is_valid_screening_answer
        self.assertFalse(is_valid_screening_answer("what is your current hourly rate in usd", "Yes"))
        self.assertFalse(is_valid_screening_answer("how soon can you join respond 1 if you are an immediate joiner", "Immediately available"))
        self.assertFalse(is_valid_screening_answer("Backend engineering ? *", "Yes"))
        self.assertFalse(is_valid_screening_answer("Led a team or project ?*", "Yes, I have led full-stack development for scalable SaaS and AI platforms."))
        self.assertTrue(is_valid_screening_answer("what is your current hourly rate in usd", "15"))
        self.assertTrue(is_valid_screening_answer("are you comfortable working hybrid", "Yes"))
        self.assertTrue(is_valid_screening_answer("Backend engineering ? *", "3"))
        self.assertTrue(is_valid_screening_answer("Led a team or project ?*", "3"))


    def test_fresher_current_ctc_is_skipped(self):
        fresher = Profile({"current_ctc_lpa": 0})
        experienced = Profile({"current_ctc_lpa": 5})
        question = "What is your current CTC in Lacs per annum?"
        self.assertTrue(naukri_apply._should_skip_question(question, fresher))
        self.assertFalse(naukri_apply._should_skip_question(question, experienced))
        self.assertFalse(
            naukri_apply._should_skip_question("What is your expected CTC?", fresher)
        )


class ProviderDispatchTests(unittest.TestCase):
    def test_groq_can_be_used_without_gemini_key(self):
        provider = type(
            "Provider", (), {"draft_answer": staticmethod(lambda *_: "answer")}
        )
        with patch.dict(os.environ, {"GROQ_API_KEY": "test"}, clear=True), patch(
            "common.llm._provider", return_value=provider
        ) as load_provider, patch.object(llm.Path, "exists", return_value=False):
            self.assertEqual(llm.draft_answer("question", {}), "answer")
            load_provider.assert_called_once_with("groq_llm")

    def test_gemini_quota_falls_back_to_groq(self):
        def quota_error(*_):
            raise RuntimeError("429 quota")

        gemini = type("Gemini", (), {"draft_answer": staticmethod(quota_error)})
        groq = type(
            "Groq", (), {"draft_answer": staticmethod(lambda *_: "fallback")}
        )
        providers = {"gemini": gemini, "groq_llm": groq}
        with patch.dict(
            os.environ,
            {"GEMINI_API_KEY": "test", "GROQ_API_KEY": "test"},
            clear=True,
        ), patch("common.llm._provider", side_effect=providers.get), patch.object(
            llm.Path, "exists", return_value=False
        ):
            self.assertEqual(llm.draft_answer("question", {}), "fallback")


class ProfilePrivacyTests(unittest.TestCase):
    def test_llm_context_excludes_salary_and_resume(self):
        profile = Profile(
            {
                "skills_primary": ["Python"],
                "work_history_narrative": "Built data systems.",
                "current_ctc_lpa": 10,
                "expected_ctc_lpa": 15,
                "resume_file_name": "private.pdf",
            }
        )
        context = profile.llm_context()
        self.assertEqual(context["skills_primary"], ["Python"])
        self.assertNotIn("current_ctc_lpa", context)
        self.assertNotIn("expected_ctc_lpa", context)
        self.assertNotIn("resume_file_name", context)


class BulkRunSafetyTests(unittest.TestCase):
    def test_role_keyword_matching_uses_word_boundaries(self):
        self.assertTrue(naukri_apply._title_has_keyword("Graduate Trainee - Java", "java"))
        self.assertFalse(naukri_apply._title_has_keyword("JavaScript Developer", "java"))

    def test_resume_path_resolves_existing_relative_file(self):
        with TemporaryDirectory() as directory:
            resume = Path(directory) / "resume.pdf"
            resume.write_bytes(b"resume")
            profile = Profile({"resume_file_name": str(resume)})

            self.assertEqual(naukri_apply._resume_path(profile), resume)

    def test_location_filter_allows_bengaluru_or_remote(self):
        profile = Profile(
            {
                "role_required_keywords": {"Software Developer": ["software developer"]},
                "company_exclude": [],
                "company_include_only": [],
                "current_city": "Bengaluru",
                "relocate_cities": [],
                "work_mode": "flexible",
                "seniority_floor_years": 0,
                "seniority_ceiling_years": 2,
            }
        )
        base = {
            "title": "Software Developer",
            "company": "Example",
            "exp": "0-2 Yrs",
        }
        self.assertFalse(
            naukri_apply.passes_filters(
                {**base, "location": "Mumbai"}, profile, "Software Developer"
            )[0]
        )
        self.assertTrue(
            naukri_apply.passes_filters(
                {**base, "location": "Bengaluru"}, profile, "Software Developer"
            )[0]
        )
        self.assertTrue(
            naukri_apply.passes_filters(
                {**base, "location": "Remote"}, profile, "Software Developer"
            )[0]
        )

    def test_applied_jobs_and_daily_count_are_loaded_from_log(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "applications.csv"
            path.write_text(
                "timestamp,source,title,company,status,reason\n"
                "2026-09-14 09:00:00,naukri,Software Developer,Aimsoft,applied,\n"
                "2026-09-14 10:00:00,naukri,Python Developer,Example,uncertain,x\n"
            )
            self.assertIn(
                ("software developer", "aimsoft"),
                naukri_apply.load_applied_job_keys(str(path)),
            )
            self.assertEqual(
                naukri_apply.count_applications_today(
                    str(path), today=date(2026, 9, 14)
                ),
                1,
            )

    def test_randomized_application_delay_stays_within_bounds(self):
        profile = Profile(
            {
                "min_delay_seconds_between_applications": 75,
                "max_delay_seconds_between_applications": 135,
            }
        )
        with patch("naukri_apply.random.uniform", return_value=90) as choose, patch(
            "naukri_apply.time.sleep"
        ) as sleep:
            self.assertEqual(naukri_apply.wait_before_next_application(profile), 90)
            choose.assert_called_once_with(75.0, 135.0)
            sleep.assert_called_once_with(90)

    def test_remote_only_location_filter(self):
        profile = Profile(
            {
                "role_required_keywords": {"Software Developer": ["software developer"]},
                "company_exclude": [],
                "company_include_only": [],
                "current_city": "Bengaluru",
                "relocate_cities": [],
                "work_mode": "remote_only",
                "seniority_floor_years": 0,
                "seniority_ceiling_years": 5,
            }
        )
        base = {
            "title": "Software Developer",
            "company": "Example",
            "exp": "0-2 Yrs",
        }
        # On-site in Bengaluru should be rejected under remote_only
        self.assertFalse(
            naukri_apply.passes_filters(
                {**base, "location": "Bengaluru"}, profile, "Software Developer"
            )[0]
        )
        # Remote should be accepted
        self.assertTrue(
            naukri_apply.passes_filters(
                {**base, "location": "Remote"}, profile, "Software Developer"
            )[0]
        )
        # Work from home should be accepted
        self.assertTrue(
            naukri_apply.passes_filters(
                {**base, "location": "Anywhere in India (Work from Home)"}, profile, "Software Developer"
            )[0]
        )

    def test_linkedin_search_url_remote_worldwide(self):
        url = linkedin_apply.build_linkedin_search_url(
            role="Full Stack Developer",
            location="Worldwide",
            freshness_seconds=86400,
            start_offset=25,
            remote_only=True,
            under_10_applicants=True,
        )
        self.assertIn("keywords=Full+Stack+Developer", url)
        self.assertIn("location=Worldwide", url)
        self.assertIn("f_WT=2", url)
        self.assertIn("sortBy=DD", url)
        self.assertIn("f_AL=true", url)
        self.assertIn("f_EA=true", url)
        self.assertIn("f_TPR=r86400", url)
        self.assertIn("start=25", url)

    def test_applicant_count_limit_filter(self):
        profile = Profile(
            {
                "role_required_keywords": {"Software Developer": ["software developer"]},
                "company_exclude": [],
                "company_include_only": [],
                "current_city": "Bengaluru",
                "relocate_cities": [],
                "work_mode": "remote_only",
                "under_10_applicants_only": True,
                "max_applicants": 10,
                "seniority_floor_years": 0,
                "seniority_ceiling_years": 5,
            }
        )
        base = {
            "title": "Software Developer",
            "company": "Example",
            "location": "Remote",
            "exp": "0-2 Yrs",
        }
        # 5 applicants should pass
        self.assertTrue(
            naukri_apply.passes_filters(
                {**base, "applicants": "5 applicants"}, profile, "Software Developer"
            )[0]
        )
        # 25 applicants should fail
        ok, reason = naukri_apply.passes_filters(
            {**base, "applicants": "25 applicants"}, profile, "Software Developer"
        )
        self.assertFalse(ok)
        self.assertIn("too many applicants", reason)

    def test_naukri_search_url_remote(self):
        profile = Profile(
            {
                "target_roles": ["Full Stack Developer"],
                "total_experience_years": 3,
                "notice_period_days": 30,
                "current_ctc_lpa": 10,
                "expected_ctc_lpa": 15,
                "resume_file_name": "resume.pdf",
                "work_mode": "remote_only",
                "job_freshness_days": 1,
            }
        )
        url = naukri_apply.build_naukri_search_url("Full Stack Developer", 2, profile)
        self.assertIn("full-stack-developer-remote-jobs-2", url)
        self.assertIn("wfhType=0", url)
        self.assertIn("wfhType=2", url)
        self.assertIn("sort=f", url)
        self.assertIn("jobAge=1", url)


if __name__ == "__main__":
    unittest.main()
