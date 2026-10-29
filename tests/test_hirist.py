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

    def test_job_key(self):
        self.assertEqual(
            hirist_apply._job_key("  Full Stack  Developer  ", " Acme Corp "),
            ("full stack developer", "acme corp")
        )


if __name__ == "__main__":
    unittest.main()
