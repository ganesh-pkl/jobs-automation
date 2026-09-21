import contextlib
import io
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch, MagicMock
import yaml
import naukri
import naukri_apply
from common.profile import Profile
from common.env import setting
from common.human_input import ask_user

ROOT = Path(__file__).resolve().parents[1]


def valid_profile():
    data = yaml.safe_load((ROOT / 'profile.example.yaml').read_text())
    data.update(current_ctc_lpa=0, expected_ctc_lpa=4)
    return data


class PortabilityTests(unittest.TestCase):
    def test_profile_rejects_invalid_types_and_limits(self):
        for key, value in [('target_roles', ['']), ('daily_application_limit', -1),
                           ('stop_after_n_attempts', '7'), ('current_ctc_lpa', float('nan')),
                           ('night_shift_ok', 'false'), ('resume_file_name', []),
                           ('min_delay_seconds_between_applications', 200)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                Profile._validate({**valid_profile(), key: value})

    def test_example_requires_real_salary_values(self):
        with self.assertRaises(ValueError):
            Profile.load(str(ROOT / 'profile.example.yaml'))
        Profile._validate(valid_profile())

    def test_init_preserves_existing_profile(self):
        with TemporaryDirectory() as directory, patch.object(naukri, 'ROOT', Path(directory)):
            root = Path(directory)
            (root / 'profile.example.yaml').write_text('template')
            (root / '.env.example').write_text('KEY=')
            (root / 'profile.yaml').write_text('my private settings')
            naukri.initialize()
            self.assertEqual((root / 'profile.yaml').read_text(), 'my private settings')
            self.assertEqual((root / '.env').read_text(), 'KEY=')

    def test_apply_without_flag_cannot_run(self):
        with patch('naukri_apply.run') as run, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                naukri.main(['apply'])
            self.assertEqual(error.exception.code, 2)
            run.assert_not_called()

    def test_quoted_environment_settings_and_precedence(self):
        with patch.dict(os.environ, {}, clear=True), patch('common.env.Path.exists', return_value=True), patch('common.env.Path.read_text', return_value='GROQ_API_KEY="example"\n'):
            self.assertEqual(setting('GROQ_API_KEY'), 'example')
            with patch.dict(os.environ, {'GROQ_API_KEY': 'override'}):
                self.assertEqual(setting('GROQ_API_KEY'), 'override')

    def test_noninteractive_input_skips_without_reader_thread(self):
        with patch('sys.stdin.isatty', return_value=False):
            self.assertIsNone(ask_user('Question?', 1))

    def test_preview_never_visits_detail_or_applies_or_logs(self):
        data = valid_profile()
        data.update(target_roles=['Data Engineer'], max_pages_per_role=1)
        page = MagicMock()
        playwright = MagicMock()
        playwright.chromium.launch.return_value.new_context.return_value.new_page.return_value = page
        with patch('naukri_apply.Profile.load', return_value=Profile(data)), patch('naukri_apply.Path.exists', return_value=True), patch('naukri_apply.count_applications_today', return_value=100), patch('naukri_apply.load_applied_job_keys', return_value=set()), patch('naukri_apply.sync_playwright') as sync, patch('naukri_apply.time.sleep'), patch('naukri_apply.page_has_stop_signal', return_value=None), patch('naukri_apply.enumerate_cards', return_value=[{'jobId': '1', 'title': 'Data Engineer', 'company': 'Example', 'href': 'https://example.test/detail'}]), patch('naukri_apply.passes_filters', return_value=(True, '')), patch('naukri_apply.click_native_apply') as click, patch('naukri_apply.log_row') as log:
            sync.return_value.__enter__.return_value = playwright
            naukri_apply.run(preview=True)
            click.assert_not_called()
            log.assert_not_called()
            self.assertEqual(page.goto.call_count, 1)
            self.assertNotIn('example.test', page.goto.call_args.args[0])

    def test_package_allowlist_excludes_private_files(self):
        from scripts.package_share import FILES
        for name in FILES:
            self.assertNotIn(name, {'.env', 'profile.yaml', 'session_naukri.json', 'applications_log.csv', 'learned_answers.json'})
            self.assertFalse(name.startswith(('.git/', '.venv/', 'debug_screenshots/')))
            if name != 'output/pdf/Naukri_Automation_Complete_Guide.pdf':
                self.assertFalse(name.endswith(('.pdf', '.docx')))


if __name__ == '__main__':
    unittest.main()
