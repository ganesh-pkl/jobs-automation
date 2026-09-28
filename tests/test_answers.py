import unittest
from unittest.mock import patch
from common.profile import Profile
from common.answers import get_screening_answer, direct_profile_answer, is_sensitive_field


class AnswersModuleTests(unittest.TestCase):
    def setUp(self):
        self.profile = Profile({
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
        })

    def test_direct_profile_answers(self):
        answers = self.profile.answer_library()
        self.assertEqual(direct_profile_answer("What is your current CTC?", answers), "6 LPA")
        self.assertEqual(direct_profile_answer("What is your notice period?", answers), "Immediately available")
        self.assertEqual(direct_profile_answer("What is your current location?", answers), "Hyderabad")
        self.assertEqual(direct_profile_answer("What is your total experience?", answers), "3")

    def test_sensitive_field_detection(self):
        self.assertTrue(is_sensitive_field("Please share your PAN card number"))
        self.assertTrue(is_sensitive_field("Enter date of birth"))
        self.assertFalse(is_sensitive_field("What is your current CTC?"))

    @patch("common.learned_answers.get_answer", return_value=None)
    @patch("common.llm.draft_answer", return_value="I have 3 years of experience in React and Node.js.")
    def test_llm_dynamic_answer_used(self, mock_llm, mock_learned):
        ans = get_screening_answer("Describe your experience with React.js", self.profile, "React Developer")
        self.assertEqual(ans, "I have 3 years of experience in React and Node.js.")
        mock_llm.assert_called_once()


if __name__ == "__main__":
    unittest.main()
