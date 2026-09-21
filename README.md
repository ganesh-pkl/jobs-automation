# Naukri Automation — shareable edition

Search Naukri jobs using your own profile and browser session, preview matching listings, and optionally submit applications within configured limits. Runs as a normal Python project; no particular AI editor is required.

**Download:** [Complete PDF guide](output/pdf/Naukri_Automation_Complete_Guide.pdf) - setup, usage, troubleshooting, and all 10 AI prompts.

**Start here:** [complete user guide](docs/USER_GUIDE.md) · [copy-and-paste AI prompts](docs/AI_PROMPTS.md) · [test results and limitations](docs/VALIDATION.md).

## Requirements

- Python 3.10+ and a desktop terminal on macOS, Windows, or Linux supported by [Playwright](https://playwright.dev/python/docs/intro).
- Your own Naukri account, manual login, and a local resume.
- Optional Gemini or Groq API key for free-text drafting. Without a key, unsupported questions request terminal input. Groq and Grok are different products.
- Internet access for installation and live use. Keep a terminal available for screening questions.

Automation can trigger platform restrictions. Review the platform's current terms before using it. Stop on login challenges, CAPTCHA, or access restrictions; this project does not bypass them.

## Get the code

```bash
git clone https://github.com/alamuruharsha24/naukri-automation.git
cd naukri-automation
```

Or download the ZIP from the repository’s **Code → Download ZIP** menu.

## 1. Extract and install

Extract the shared ZIP and open a terminal in the extracted `naukri-automation` folder. Do not run inside the ZIP viewer.

macOS / Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
python naukri.py init
```

Windows PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe naukri.py init
```

On Windows, replace `python` in the following commands with `.\.venv\Scripts\python.exe`. Activation is optional; no execution-policy change is necessary. On Linux, missing system libraries may require `python -m playwright install --with-deps chromium` with administrator access.

## 2. Configure your own profile

`init` creates `profile.yaml` and `.env` without overwriting existing files. Edit `profile.yaml` locally using the comments in `profile.example.yaml` and the [field reference](docs/USER_GUIDE.md#profile-field-reference).

Replace all example text, select roles and locations, enter real experience/salary/notice values, and set `resume_file_name` to your own file. Salary is in **lakhs per annum**, not rupees per month. Both CTC fields must be numbers; use zero only when truthful. Null example values deliberately prevent a real run.

For your first real run use `stop_after_n_applications: 1`, `stop_after_n_attempts: 1`, `max_pages_per_role: 1`, and `browser_mode: "visible"`.

Optional: enter `GEMINI_API_KEY` and/or `GROQ_API_KEY` in `.env`. Model names are configurable with `GEMINI_MODEL` / `GROQ_MODEL`. See the current [Gemini model catalog](https://ai.google.dev/gemini-api/docs/models) and [Groq model catalog](https://console.groq.com/docs/models) for availability. Pricing and quotas are provider controlled; this project does not promise free access.

## 3. Log in, check, and preview

```bash
python naukri.py login
python naukri.py doctor --browser-smoke
python naukri.py preview
```

Log in manually in the browser, then press Enter in the terminal. The session is saved locally. Doctor validates local configuration, files, dependencies, and Chromium; it does not verify API credentials or live login validity. Preview opens Naukri search pages, shows MATCH/SKIP results, and never clicks Apply or writes the application log.

## 4. Submit only when ready

```bash
python naukri.py apply --confirm
```

This submits real applications. Watch the browser and answer terminal questions. Ctrl+C stops the process. Check any interrupted or uncertain application manually before retrying.

The example limits are five confirmed applications / seven attempts per run and eight confirmed applications per local calendar day, with 75–135 seconds between attempts. Limits count confirmed submissions in `applications_log.csv`; uncertain submissions may still have gone through. Run only one instance per project/account. Duplicate matching uses title + company and can also skip distinct openings sharing those values.

The older `python naukri_apply.py` command still immediately starts a real run for backward compatibility. Prefer the new command interface.

## Use with Codex, Claude, Grok, or Antigravity

Open this folder in an assistant with local file/terminal access and ask it to read `AGENTS.md`, `README.md`, and `docs/AI_PROMPTS.md`. `CLAUDE.md` points to the same instructions. Any other assistant can follow those files when explicitly asked; automatic instruction discovery varies by product.

A chat-only assistant can explain commands and help edit redacted configuration, but cannot operate your local browser without execution tools. The runtime AI answer providers are Gemini/Groq; using a different coding assistant does not change them.

## Test and share

```bash
python -m unittest discover -s tests -v
python scripts/package_share.py
```

Share **only** `dist/naukri-automation-share.zip`. The builder includes an explicit list of source, template, test, and documentation files. It excludes `.git`, `.env`, `profile.yaml`, cookies, resumes, logs, screenshots, and virtual environments. Do not zip the entire working directory. Review changes to public templates before packaging; an allowlist cannot detect secrets someone manually adds to source.

## Other scripts and limitations

`linkedin_apply.py` is experimental and outside the main supported workflow. `naukri_refresh_resume.py` deletes and re-uploads a resume; a failed upload can leave no resume attached. It is a legacy utility, not part of setup, preview, or normal applying. Do not schedule either as part of first-time setup.

AI-generated answers are not guaranteed factual. Only allowlisted professional profile fields are sent to the configured provider, together with question and title/company context. Common questions use profile rules; sensitive questions require fresh terminal input. Review your profile and remembered answers regularly.

Selectors can break when Naukri changes its pages. Local tests and browser checks are documented in [VALIDATION.md](docs/VALIDATION.md); they do not prove live submissions work on every account or operating system.

## License

MIT. Original attribution is preserved in [LICENSE](LICENSE). No warranty.

## Attribution

Based on [Hemanth-kumar-N-arya/naukri-job-apply-ai](https://github.com/Hemanth-kumar-N-arya/naukri-job-apply-ai), with portability, setup validation, preview, tests, and sharing documentation added in this edition.

To rebuild the PDF after editing the Markdown guides, install the optional `reportlab` package in a separate environment and run `python scripts/build_pdf_guide.py`. It is not needed to run the automation.
