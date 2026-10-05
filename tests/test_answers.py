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
        self.assertEqual(direct_profile_answer("Experience in Years*", answers), "3")
        self.assertEqual(direct_profile_answer("What is your school / university name?", answers), "Chaitanya Bharathi Institute of Technology")
        self.assertEqual(direct_profile_answer("Graduation Year*", answers), "2023")
        self.assertEqual(direct_profile_answer("Passout Year", answers), "2023")
        self.assertEqual(direct_profile_answer("Year of graduation", answers), "2023")
        self.assertEqual(direct_profile_answer("Start Year", answers), "2019")
        self.assertEqual(direct_profile_answer("State/Province", answers), "Telangana")
        self.assertEqual(direct_profile_answer("Country", answers), "India")
        self.assertEqual(direct_profile_answer("DOB ( Date of Birth )", answers), "15/08/2001")
        self.assertEqual(direct_profile_answer("First name*", answers), "Ganesh")
        self.assertEqual(direct_profile_answer("Last name*", answers), "Pirikirala")
        self.assertEqual(direct_profile_answer("Email address*", answers), "ganesh.pkl08@gmail.com")
        self.assertEqual(direct_profile_answer("Major / Field of study", answers), "Electronics and Communication Engineering")
        self.assertEqual(direct_profile_answer("Highest Qualification Held*", answers), "Bachelor of Technology")
        self.assertEqual(direct_profile_answer("Backend engineering ? *", answers), "3")
        self.assertEqual(direct_profile_answer("Led a team or project ?*", answers), "3")
        self.assertEqual(direct_profile_answer("Data engineering ?", answers), "3")
        self.assertEqual(direct_profile_answer("How many years of work experience do you have with Back-End Web Development?*", answers), "3")
        self.assertEqual(direct_profile_answer("How many years of work experience do you have with IPython?*", answers), "3")
        self.assertEqual(direct_profile_answer("How many years of work experience do you have with .NET Core?", answers), "0")

    def test_sensitive_field_detection(self):
        self.assertTrue(is_sensitive_field("Please share your PAN card number"))
        self.assertTrue(is_sensitive_field("Enter date of birth"))
        self.assertFalse(is_sensitive_field("What is your current CTC?"))

    def test_screening_answer_numeric_competencies(self):
        self.assertEqual(get_screening_answer("Backend engineering ? *", self.profile), "3")
        self.assertEqual(get_screening_answer("Led a team or project ?*", self.profile), "3")
        self.assertEqual(get_screening_answer("Data engineering ?", self.profile), "3")

    @patch("common.learned_answers.get_answer", return_value=None)
    @patch("common.llm.draft_answer", return_value="I have 3 years of experience in React and Node.js.")
    def test_llm_dynamic_answer_used(self, mock_llm, mock_learned):
        ans = get_screening_answer("Describe your experience with React.js", self.profile, "React Developer")
        self.assertEqual(ans, "I have 3 years of experience in React and Node.js.")
        mock_llm.assert_called_once()


if __name__ == "__main__":
    unittest.main()
