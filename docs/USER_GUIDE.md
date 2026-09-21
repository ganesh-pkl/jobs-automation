# Naukri Automation: complete user guide

## What you receive

The share ZIP contains reusable Python source, generic configuration templates, tests, and this documentation. Each person supplies their own profile, resume, session, and optional API keys. There is no shared account or hosted service. You can use a terminal directly or ask an AI coding assistant to operate the same commands.

## First-time setup, step by step

1. Install Python 3.10+ and extract the ZIP into a folder you control.
2. Open a terminal in the extracted folder containing `naukri.py`.
3. Create a virtual environment and install the packages and browser using the platform-specific commands in the next section.
4. Run `python naukri.py init`. Existing local configuration is preserved.
5. Edit `profile.yaml` in a text editor. Replace the example roles, skills, qualifications, and work narrative with actual facts. Keep YAML indentation; use spaces instead of tabs.
6. Copy your resume into this folder and set its filename in the profile. Ensure your Naukri account also has the intended resume.
7. Optionally edit `.env` with your own provider key. Do not paste keys into an AI conversation. Leave a key blank if unused. Use one assignment per line; quoted values are accepted, inline comments after values are not.
8. Run `python naukri.py login`. Complete login yourself in the visible browser. Once logged in, return to the terminal and press Enter. Do not give the assistant your password or OTP.
9. Run `python naukri.py doctor --browser-smoke`. Correct each FAIL. A missing-key warning is acceptable if you want manual free-text answers.
10. Run `python naukri.py preview`. Confirm the search results suit you before using the real apply command.
11. For a first live run, set both application and attempt limits to 1, keep the browser visible, and run `python naukri.py apply --confirm`.
12. Inspect the result on Naukri and in the local application log. Increase limits only after the results and screening answers look correct.

## Installation commands

### macOS / Linux

```bash
python3 --version
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m playwright install chromium
python naukri.py init
```

For future terminal sessions, return to the folder and run `source .venv/bin/activate`. Alternatively use `.venv/bin/python` wherever the guide says `python`. Some Linux distributions require a separate Python venv package. Missing browser libraries can be installed with `python -m playwright install --with-deps chromium`; that may require administrator privileges.

### Windows PowerShell

```powershell
py -3 --version
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe naukri.py init
.\.venv\Scripts\python.exe naukri.py login
.\.venv\Scripts\python.exe naukri.py doctor --browser-smoke
.\.venv\Scripts\python.exe naukri.py preview
```

Use `.\.venv\Scripts\python.exe` for all later Python commands. This avoids PowerShell activation restrictions. Quote folder paths containing spaces when using `cd`.

## Profile field reference

| Field(s) | Meaning and valid input |
|---|---|
| `target_roles` | Non-empty YAML list of desired job titles. |
| `role_required_keywords` | Mapping from exact target role to acceptable phrases in a job title. Update when changing roles. |
| `current_city`, `relocate_cities` | Your current city and list of places you actually accept relocating to. |
| `work_mode` | `onsite_or_hybrid`, `remote_only`, or `flexible`. Remote detection uses listing text and is imperfect. |
| `night_shift_ok`, `weekend_ok` | Actual booleans `true` or `false`, without quotes. |
| `total_experience_years` | Non-negative number, decimals allowed. Search URLs use its integer portion. |
| `current_employer` | Current employer; use an empty string if unemployed. |
| `current_title_official`, `current_title_functional` | Real official title and plain description of your work. |
| `notice_period_days`, `immediately_available` | Real notice and availability. Keep these consistent. |
| `current_ctc_lpa`, `expected_ctc_lpa` | Required non-negative numbers in lakhs per annum. Null examples must be replaced. |
| `ctc_disclosure_policy` | `negotiable` produces discussion-oriented text; `real_numbers` uses supplied figures. Numeric-only forms may still need intervention. |
| `highest_qualification`, `certifications` | Real education text and list of certifications. |
| `skills_primary`, `skills_adjacent` | Lists of real skills, separating strong and lighter experience. |
| `work_history_narrative` | Two to four factual sentences, with concrete projects and duties. |
| `company_exclude`, `company_include_only` | Company text filters; empty include list allows all companies. |
| `seniority_floor_years`, `seniority_ceiling_years` | Accepted experience-range overlap, not a claim about your personal experience. |
| `job_freshness_days` | Positive whole number for the site's age filter. |
| `salary_floor_lpa` | Optional numeric salary threshold; null disables it. Unknown salary may still pass. |
| `stop_after_n_applications` | Maximum confirmed applications per run. |
| `stop_after_n_attempts` | Maximum counted Apply clicks per run. Keep low initially. |
| `daily_application_limit` | Confirmed applications allowed per local calendar day based on the log. |
| `min_delay_seconds_between_applications`, `max_delay_seconds_between_applications` | Non-negative delay bounds; minimum must not exceed maximum. Preserve the example pacing initially. |
| `human_input_timeout_seconds` | Positive whole seconds to wait for a manual answer. No answer skips the listing. |
| `max_pages_per_role` | Positive whole number; use 1 for initial preview. |
| `browser_mode` | `visible` recommended; `minimized` is OS dependent; `headless` has no interactive browser window. |
| `resume_file_name` | Existing resume path. Relative paths resolve from the project folder through the main CLI. |

The template is a starting point, not a valid applicant. Review every placeholder. For a fresher, zero professional experience and no current employer may be appropriate only if accurate. Do not claim that total experience equals experience in every skill.

## AI keys and answer behavior

Get keys from [Google AI Studio](https://aistudio.google.com/apikey) or [Groq Console](https://console.groq.com/keys). The API provider is separate from whichever assistant helps you set up the code. An assistant subscription does not automatically configure API access for this script.

Gemini is tried first when configured; quota/unavailability errors can fall back to Groq. Model IDs can be overridden in `.env`; consult the [Gemini catalog](https://ai.google.dev/gemini-api/docs/models) and [Groq catalog](https://console.groq.com/docs/models). Costs, access, and limits may change.

Common answers are supplied from your profile. The provider receives only allowlisted professional fields plus screening question and job title/company context. AI output can still be wrong. Unsupported questions or provider failures request manual input in an interactive terminal; noninteractive terminals skip. Certain human answers are remembered locally in `learned_answers.json`; sensitive fields use fresh input. Review or remove stale remembered answers when your circumstances change, especially salary and notice period.

## Daily operation and stopping

Run doctor if configuration changed, preview as needed, then apply explicitly. Keep only one instance running for an account. Do not delete the application log to evade the daily cap. Duplicate matching is title/company based; it may merge distinct openings with the same labels.

Ctrl+C interrupts the current operation. An interrupted click or an `uncertain` log entry may represent a real application. Check Naukri manually before re-running. A page challenge stops the run; do not repeatedly retry it.

`applications_log.csv` records timestamp, source, title, company, status, and reason. `applied` means explicit confirmation was detected; `skipped` means the workflow did not complete normally; `uncertain` means no reliable confirmation; `stopped` means a run-level stop signal. These are automation observations, not authoritative server records. Screenshots saved for uncertain cases can contain personal information.

## Troubleshooting

| Problem | Action |
|---|---|
| Python or `py` not found | Install Python, reopen the terminal, and check the installation's PATH setting. |
| Module not found | Install requirements using the same virtual-environment Python used to run the script. |
| Chromium executable missing | Run `python -m playwright install chromium` in that environment. |
| Browser launch denied inside an AI sandbox | Use an approved local execution environment or run the command yourself in a normal terminal. |
| Invalid YAML / profile FAIL | Check indentation, unquoted numeric values, booleans, required salaries, and bounds. |
| Resume missing | Correct the filename/path; do not point at someone else's resume. |
| Session missing or login page appears | Run login again, complete login, and press Enter only afterward. Doctor checks structure, not live authentication. |
| Provider/model unavailable | Verify the key locally and choose an available model from official docs, or use manual answers. |
| No matching jobs | Inspect preview reasons; check roles, keywords, city and experience filters. Zero results can also mean changed selectors or a blocked page. |
| No input accepted by assistant terminal | Run the live workflow in an interactive local terminal. |
| Application uncertain | Check Naukri history manually before retrying. |
| Browser selectors stopped working | Request a code fix with a redacted error and minimal redacted HTML. Do not upload raw sessions, resumes, or screenshots publicly. |
| Daily limit reached | Wait until the next local calendar day; review the log rather than deleting it. |

## Updating and sharing

Keep a private backup of your local profile and resume. Preserve them while updating source. Reinstall requirements and Chromium after dependency updates, run tests, then doctor and preview.

To share your customized code, keep public templates generic, run tests, and run `python scripts/package_share.py`. Send only the resulting ZIP. Each recipient repeats setup with their own account. The archive contains no Git history. A file allowlist prevents accidental inclusion of local account files, but you must still avoid putting personal data into public source or templates.

## Scope and verification

The supported entry point is `naukri.py`. Legacy LinkedIn and resume-refresh scripts are included for source completeness but are not part of the validated onboarding workflow. Resume refresh deletes before re-uploading and should be requested and supervised separately.

See [VALIDATION.md](VALIDATION.md) for what was actually tested. No claim is made that live submissions work for every account or that every named AI assistant was exercised. See [AI_PROMPTS.md](AI_PROMPTS.md) for ready-to-use instructions.
