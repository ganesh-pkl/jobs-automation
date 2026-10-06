"""
Unit tests for common/stealth.py anti-detection and human emulation engine.
"""
import unittest
from unittest.mock import MagicMock
from common.stealth import (
    get_launch_kwargs,
    get_context_options,
    apply_stealth,
    check_linkedin_restrictions,
    _bezier_curve,
)


class StealthEngineTests(unittest.TestCase):
    def test_get_launch_kwargs(self):
        kwargs = get_launch_kwargs(headless=False, slow_mo=60)
        self.assertFalse(kwargs["headless"])
        self.assertEqual(kwargs["slow_mo"], 60)
        self.assertIn("--disable-blink-features=AutomationControlled", kwargs["args"])
        self.assertIn("--no-sandbox", kwargs["args"])

    def test_get_context_options(self):
        opts = get_context_options(user_agent="CustomUA/1.0")
        self.assertEqual(opts["user_agent"], "CustomUA/1.0")
        self.assertEqual(opts["viewport"]["width"], 1440)
        self.assertEqual(opts["viewport"]["height"], 900)
        self.assertEqual(opts["locale"], "en-US")
        self.assertEqual(opts["timezone_id"], "Asia/Kolkata")
        self.assertIn("notifications", opts["permissions"])

    def test_apply_stealth_adds_init_script(self):
        mock_context = MagicMock()
        apply_stealth(mock_context)
        mock_context.add_init_script.assert_called_once()
        script = mock_context.add_init_script.call_args[0][0]
        self.assertIn("navigator.webdriver", script)
        self.assertIn("window.chrome", script)
        self.assertIn("PDF Viewer", script)

    def test_check_linkedin_restrictions_detection(self):
        # 1. Checkpoint in URL
        mock_page = MagicMock()
        mock_page.url = "https://www.linkedin.com/checkpoint/challenge/12345"
        is_res, reason = check_linkedin_restrictions(mock_page)
        self.assertTrue(is_res)
        self.assertIn("security checkpoint", reason.lower())

        # 2. Weekly invitation limit text
        mock_page2 = MagicMock()
        mock_page2.url = "https://www.linkedin.com/in/recruiter"
        mock_page2.evaluate.return_value = "You've reached the weekly invitation limit. Try again next week."
        is_res2, reason2 = check_linkedin_restrictions(mock_page2)
        self.assertTrue(is_res2)
        self.assertIn("weekly invitation limit", reason2.lower())

        # 3. Clean page
        mock_page3 = MagicMock()
        mock_page3.url = "https://www.linkedin.com/in/recruiter"
        mock_page3.evaluate.return_value = "Software Engineer at TechCorp. Connect with Ganesh."
        is_res3, reason3 = check_linkedin_restrictions(mock_page3)
        self.assertFalse(is_res3)
        self.assertEqual(reason3, "")

    def test_bezier_curve_interpolation(self):
        p0 = (0.0, 0.0)
        p1 = (10.0, 20.0)
        p2 = (30.0, 40.0)
        p3 = (50.0, 50.0)
        # At t=0, point should be p0
        x0, y0 = _bezier_curve(p0, p1, p2, p3, 0.0)
        self.assertAlmostEqual(x0, 0.0)
        self.assertAlmostEqual(y0, 0.0)
        # At t=1, point should be p3
        x1, y1 = _bezier_curve(p0, p1, p2, p3, 1.0)
        self.assertAlmostEqual(x1, 50.0)
        self.assertAlmostEqual(y1, 50.0)
        # At t=0.5, point should be strictly between 0 and 50
        xm, ym = _bezier_curve(p0, p1, p2, p3, 0.5)
        self.assertTrue(0.0 < xm < 50.0)
        self.assertTrue(0.0 < ym < 50.0)


if __name__ == "__main__":
    unittest.main()
