"""Portable command line entry point. Run python naukri.py --help."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parent


def initialize():
    for source, target in (("profile.example.yaml", "profile.yaml"), (".env.example", ".env")):
        destination = ROOT / target
        if destination.exists():
            print(f"Kept existing {target}")
        else:
            shutil.copyfile(ROOT / source, destination)
            destination.chmod(0o600)
            print(f"Created {target}; edit it locally before continuing.")


def doctor(browser_smoke=False):
    failures = []
    for module in ("yaml", "playwright", "groq", "google.genai"):
        try:
            found = importlib.util.find_spec(module) is not None
        except ModuleNotFoundError:
            found = False
        print(f"{'OK' if found else 'FAIL'} dependency: {module}")
        if not found:
            failures.append(module)
    if failures:
        print("Install dependencies: python -m pip install -r requirements.txt")
        return 1
    from common.profile import Profile
    try:
        profile = Profile.load(str(ROOT / "profile.yaml"))
        print("OK profile types and limits")
        resume = Path(profile.resume_file_name).expanduser()
        if not resume.is_absolute():
            resume = ROOT / resume
        if not resume.is_file():
            raise ValueError("Resume file missing: check resume_file_name.")
        print("OK resume file exists")
    except (OSError, ValueError, TypeError) as exc:
        print(f"FAIL profile: {exc}")
        failures.append("profile")
    try:
        state = json.loads((ROOT / "session_naukri.json").read_text(encoding="utf-8"))
        if not isinstance(state, dict) or not isinstance(state.get("cookies"), list) or not state["cookies"]:
            raise ValueError("No saved cookies")
        print("OK session structure (expiry/login validity requires live preview)")
    except (OSError, ValueError) as exc:
        print("FAIL session: run python naukri.py login")
        failures.append("session")
    from common.llm import _configured_key
    if not any(_configured_key(k) for k in ("GEMINI_API_KEY", "GROQ_API_KEY")):
        print("WARN no AI key: unsupported free-text questions will need terminal input")
    else:
        print("OK provider key present (not an API connectivity test)")
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as playwright:
            if not Path(playwright.chromium.executable_path).is_file():
                raise RuntimeError("Run python -m playwright install chromium")
            print("OK Chromium installed")
            if browser_smoke:
                browser = playwright.chromium.launch(headless=True)
                try:
                    page = browser.new_page()
                    page.set_content('<h1 id="check">Naukri local smoke test</h1>')
                    assert page.locator("#check").inner_text() == "Naukri local smoke test"
                    print("OK local Chromium launch and DOM interaction (no website accessed)")
                finally:
                    browser.close()
    except Exception as exc:
        print(f"FAIL browser: {type(exc).__name__}: {exc}")
        failures.append("browser")
    return int(bool(failures))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Naukri automation: initialize, validate, preview, then apply.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Create local configuration without overwriting existing files")
    check = commands.add_parser("doctor", help="Validate local setup; never submit applications")
    check.add_argument("--browser-smoke", action="store_true", help="Also launch Chromium against a local page")
    commands.add_parser("login", help="Open a browser for manual Naukri login")
    commands.add_parser("preview", help="Read job search results without clicking Apply or writing application logs")
    apply = commands.add_parser("apply", help="Submit real applications within profile limits")
    apply.add_argument("--confirm", action="store_true", help="Confirm this run may submit real applications")
    args = parser.parse_args(argv)
    os.chdir(ROOT)
    if sys.version_info < (3, 10):
        parser.error("Python 3.10 or newer is required")
    if args.command == "init":
        initialize()
        return 0
    if args.command == "doctor":
        return doctor(args.browser_smoke)
    if args.command == "apply" and not args.confirm:
        parser.error("Real submissions require: python naukri.py apply --confirm")
    if args.command == "login":
        from login_capture import main as login
        sys.argv = ["login_capture.py", "naukri"]
        login()
    else:
        from naukri_apply import run
        run(preview=args.command == "preview")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nStopped by user. Check application history before retrying an interrupted submission.")
        raise SystemExit(130)
