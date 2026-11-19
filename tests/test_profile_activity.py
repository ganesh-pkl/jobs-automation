import unittest
from datetime import datetime
from unittest.mock import patch

from profile_activity_refresher import (
    NAUKRI_HEADLINE_VARIANTS,
    LINKEDIN_HEADLINE_VARIANTS,
    run_refresh_cycle,
)


class ProfileActivityRefresherTests(unittest.TestCase):
    def test_headline_variants_are_non_empty_and_valid(self):
        self.assertTrue(len(NAUKRI_HEADLINE_VARIANTS) >= 2)
        self.assertTrue(len(LINKEDIN_HEADLINE_VARIANTS) >= 2)
        for v in NAUKRI_HEADLINE_VARIANTS:
            self.assertIn("Immediate joiner", v)
            self.assertIn("Node.js", v)
        for v in LINKEDIN_HEADLINE_VARIANTS:
            self.assertIn("Immediate joiner", v)
            self.assertIn("Node.js", v)

    @patch("profile_activity_refresher.refresh_naukri_activity")
    @patch("profile_activity_refresher.refresh_linkedin_activity")
    def test_run_refresh_cycle_skips_outside_target_hours_without_force(self, mock_li, mock_naukri):
        # Hour 12 (2nd run) should skip
        fake_time = datetime(2026, 11, 19, 12, 0, 0)
        with patch("profile_activity_refresher.datetime") as mock_dt:
            mock_dt.now.return_value = fake_time
            run_refresh_cycle(force=False)
            mock_naukri.assert_not_called()
            mock_li.assert_not_called()

    @patch("profile_activity_refresher.refresh_naukri_activity")
    @patch("profile_activity_refresher.refresh_linkedin_activity")
    def test_run_refresh_cycle_executes_at_9am_and_3pm(self, mock_li, mock_naukri):
        # 9 AM (1st run)
        fake_time_9am = datetime(2026, 11, 19, 9, 0, 0)
        with patch("profile_activity_refresher.datetime") as mock_dt:
            mock_dt.now.return_value = fake_time_9am
            run_refresh_cycle(force=False)
            self.assertEqual(mock_naukri.call_count, 1)
            self.assertEqual(mock_li.call_count, 1)

        mock_naukri.reset_mock()
        mock_li.reset_mock()

        # 3 PM (3rd run)
        fake_time_3pm = datetime(2026, 11, 19, 15, 0, 0)
        with patch("profile_activity_refresher.datetime") as mock_dt:
            mock_dt.now.return_value = fake_time_3pm
            run_refresh_cycle(force=False)
            self.assertEqual(mock_naukri.call_count, 1)
            self.assertEqual(mock_li.call_count, 1)


if __name__ == "__main__":
    unittest.main()
