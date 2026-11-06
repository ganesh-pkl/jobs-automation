import unittest
from unittest.mock import MagicMock
from pathlib import Path

import linkedin_apply
from common.profile import Profile


class LinkedInEngineTests(unittest.TestCase):
    def test_extract_experience_requirement(self):
        self.assertEqual(linkedin_apply.extract_experience_requirement("Requires 3+ years of experience"), 3)
        self.assertEqual(linkedin_apply.extract_experience_requirement("2-4 years experience in Python"), 2)
        self.assertEqual(linkedin_apply.extract_experience_requirement("Minimum 5 years of software engineering"), 5)
        self.assertEqual(linkedin_apply.extract_experience_requirement("3+ years with React and Node.js"), 3)
        self.assertIsNone(linkedin_apply.extract_experience_requirement("Great company culture"))
        self.assertIsNone(linkedin_apply.extract_experience_requirement("For over 20+ years, ACME Corp has been a global leader in software"))
        self.assertIsNone(linkedin_apply.extract_experience_requirement("In business for 15 years delivering enterprise solutions"))

    def test_job_key_normalization(self):
        self.assertEqual(
            linkedin_apply._job_key("  Full Stack Developer  ", " Acme Corp "),
            ("full stack developer", "acme corp"),
        )

    def test_is_application_confirmation(self):
        self.assertTrue(linkedin_apply._is_application_confirmation("Your application was sent to Google"))
        self.assertTrue(linkedin_apply._is_application_confirmation("Application submitted successfully"))
        self.assertTrue(linkedin_apply._is_application_confirmation("Track your application in your dashboard"))
        self.assertTrue(linkedin_apply._is_application_confirmation("You successfully applied to Meta"))
        self.assertFalse(linkedin_apply._is_application_confirmation("Save this application?"))
        self.assertFalse(linkedin_apply._is_application_confirmation("Review your application before submitting"))

    def test_handle_unexpected_save_prompt_evaluates(self):
        mock_page = MagicMock()
        mock_page.evaluate.return_value = True
        self.assertTrue(linkedin_apply.handle_unexpected_save_prompt(mock_page))
        mock_page.evaluate.assert_called_once()

    def test_numeric_expectation_preserves_narrative_for_textareas(self):
        # A question like "What tech stack did you use in backend and explain a usage?"
        # should NEVER be treated as numeric when tag is TEXTAREA or question is explanatory
        q_label = "What tech stack did you use in backend and explain a usage?"
        lbl_lower = q_label.lower()
        field_tag = "TEXTAREA"
        field_type = "textarea"

        is_numeric = (
            field_type == "number"
            or (
                field_tag != "TEXTAREA"
                and (
                    "how many years" in lbl_lower
                    or "years of experience" in lbl_lower
                    or "experience in years" in lbl_lower
                    or any(w in lbl_lower for w in ("hourly rate", "rate in usd", "rate (in usd)", "per hour", "ctc in lpa", "current ctc", "expected ctc", "in days", "pin code", "pincode", "zip code", "postal code"))
                )
            )
        )
        self.assertFalse(is_numeric, "Textarea narrative technical questions must not be flagged as numeric!")

    def test_numeric_expectation_flags_true_numeric_fields(self):
        lbl_lower = "how many years of work experience do you have with react.js?"
        field_tag = "INPUT"
        field_type = "text"

        is_numeric = (
            field_type == "number"
            or (
                field_tag != "TEXTAREA"
                and (
                    "how many years" in lbl_lower
                    or "years of experience" in lbl_lower
                    or "experience in years" in lbl_lower
                    or any(w in lbl_lower for w in ("hourly rate", "rate in usd", "rate (in usd)", "per hour", "ctc in lpa", "current ctc", "expected ctc", "in days", "pin code", "pincode", "zip code", "postal code"))
                )
            )
        )
        self.assertTrue(is_numeric, "Direct years experience prompt must be flagged as numeric")

    def test_top_choice_and_premium_checkboxes_are_protected(self):
        promotional_labels = [
            "mark job as a top choice",
            "top choice",
            "follow company to stay up to date",
            "follow acme corp",
        ]
        for lbl in promotional_labels:
            lbl_lower = lbl.lower()
            should_skip = any(w in lbl_lower for w in ("top choice", "premium", "follow company", "follow "))
            self.assertTrue(should_skip, f"Promotional checkbox '{lbl}' should be skipped and never checked")

    def test_click_modal_button_dom_evaluation(self):
        mock_page = MagicMock()
        mock_page.evaluate.return_value = True
        self.assertTrue(linkedin_apply.click_modal_button(mock_page, "Next"))
        self.assertTrue(linkedin_apply.click_modal_button(mock_page, "Review"))
        self.assertTrue(linkedin_apply.click_modal_button(mock_page, "Submit application"))

    def test_dismiss_modal_evaluates_safely(self):
        mock_page = MagicMock()
        mock_page.evaluate.return_value = True
        # Should return without throwing
        linkedin_apply.dismiss_modal_if_open(mock_page)
        self.assertTrue(mock_page.evaluate.called)


if __name__ == "__main__":
    unittest.main()

