# Validation report

Prepared on 2026-09-21 for the shareable edition.

## Verified on this computer

- 25 offline unittest cases passed on macOS, covering existing answer rules, sensitive-field routing, confirmation detection, profile privacy, provider fallback, filters, deduplication, daily counting, and delay bounds.
- New checks cover invalid configuration types/limits, preserving existing files during init, explicit CLI submission opt-in, quoted .env values, noninteractive input, and share-file exclusions.
- Mocked preview test confirms that a matching result does not visit a job detail page, click Apply, or write an application log, even when the daily submission ceiling has already been reached.
- `python naukri.py doctor --browser-smoke` passed: dependencies, existing local profile, resume presence, session structure, installed Chromium, and local browser DOM interaction. No credentials or profile contents are included in this report.
- Chromium initially failed under the execution sandbox's macOS process restrictions; the approved local run passed outside that sandbox.

## Release checks

The release ZIP is built from an explicit allowlist. The final preparation checks extract it into a fresh temporary folder, run all tests and CLI help there, and inspect the archive for required public files and excluded private files. The clean ZIP intentionally has no configured profile, session, resume, keys, or virtual environment: recipients must run setup.

## Not verified

- No live application, resume refresh, or LinkedIn action was performed during this preparation.
- The saved login was checked structurally, not against live Naukri; API keys were checked for presence, not provider authentication or billing.
- Windows and Linux execution have not been run locally. A GitHub Actions matrix is provided for Python 3.10/3.12 on Windows/macOS/Linux; it has not been executed on a remote repository as part of this delivery.
- Named AI assistant products were not individually tested. They can use the same Markdown instructions and Python commands if they have the required tools.
- Site selectors, network restrictions, provider model availability, AI answer accuracy, and account-specific screening remain live dependencies.

## Reproduce

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python naukri.py --help
python naukri.py doctor --browser-smoke
python scripts/package_share.py
```

Doctor requires your own configured profile, resume, and captured session to pass fully. Its browser smoke uses local HTML and sends no application. Run `python naukri.py preview` to check live search/login after setup. A real submission requires a separately intended live run.
