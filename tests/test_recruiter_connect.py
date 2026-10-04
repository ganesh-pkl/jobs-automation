import unittest
from datetime import date
from common.profile import Profile
from common.recruiter_connect import (
    build_recruiter_connection_note,
    MAX_NOTE_LENGTH,
    count_connections_today,
    load_contacted_recruiters,
)


class RecruiterConnectTests(unittest.TestCase):
    def setUp(self):
        self.profile = Profile({
            "first_name": "Ganesh",
            "last_name": "Pirikirala",
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
            "max_connection_requests_per_day": 10,
        })

    def test_note_length_strictly_under_200_chars(self):
        cases = [
            ("Sankalp Sharma", "Full Stack Developer", "Google"),
            ("Dr. Jane Doe, Ph.D.", "Senior Lead Full-Stack Software Engineer (React / Node)", "Microsoft Technologies Corporation India Pvt Ltd"),
            ("Alex", "Backend Engineer", "Stripe"),
            ("Priya R.", "Java Spring Boot Developer (AI / ML Integrations)", "Amazon Web Services India"),
            ("Michael van der Berg", "Software Engineer - Full Stack & Distributed Systems", "Meta Platforms"),
        ]

        for name, role, company in cases:
            note = build_recruiter_connection_note(name, role, company, self.profile)
            self.assertLessEqual(len(note), MAX_NOTE_LENGTH, f"Note exceeded 200 chars ({len(note)} chars): {note}")
            self.assertTrue(note.startswith("Hi "), f"Note should start with greeting: {note}")
            self.assertIn("Full-Stack", note)
            self.assertIn("Ganesh", note)

    def test_daily_counter_returns_integer(self):
        count = count_connections_today(date.today())
        self.assertIsInstance(count, int)
        self.assertGreaterEqual(count, 0)

    def test_contacted_recruiters_returns_set(self):
        contacted = load_contacted_recruiters()
        self.assertIsInstance(contacted, set)


if __name__ == "__main__":
    unittest.main()
