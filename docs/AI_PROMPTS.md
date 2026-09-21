# Naukri Automation: AI assistant prompt guide

Use these prompts with Codex, Claude, Grok, Antigravity, or another assistant. Open the extracted project folder first. For any assistant that does not automatically read project instructions, explicitly provide `AGENTS.md`, `README.md`, and this guide. Local execution requires file and terminal tools; a chat-only assistant can guide you through commands instead.

Never send API keys, passwords, OTPs, session JSON, or unredacted personal documents in prompts. Replace bracketed placeholders below before use. Grok is an assistant; Groq is a separate runtime API provider supported by this project.

## 1. Full setup without submissions

```text
Set up this Naukri automation project on my computer. Read AGENTS.md,
README.md, and docs/USER_GUIDE.md first. Detect my operating system and use
an isolated Python virtual environment. Preserve all existing local files.
Install requirements and Chromium, run python naukri.py init, and help me
fill the profile with my real facts. Ask for missing facts instead of guessing.
I will enter secrets locally and complete browser login myself.
Run the offline tests, doctor --browser-smoke, and preview once setup is ready.
Do not submit applications or run resume refresh. Report what passed and
what still needs manual verification. If you cannot execute locally, give
me the exact commands for my operating system, one stage at a time.
```

## 2. Build my profile accurately

```text
Read profile.example.yaml and explain the fields I need to fill.
My target roles are [roles], city is [city], experience is [years],
and acceptable work locations are [locations]. Ask me for the remaining
required facts. Help update my local profile.yaml without inventing
experience, salary, availability, qualifications, or skills.
Keep initial run and attempt limits at 1, max_pages_per_role at 1,
and browser_mode visible. Keep example pacing. Do not print my complete
profile or resume in the conversation. Do not apply to jobs.
```

## 3. Validate installation

```text
Read AGENTS.md. Run the project tests and python naukri.py doctor
--browser-smoke using this project's virtual environment. Fix code or
installation issues where possible without changing my personal facts.
Do not submit applications, refresh my resume, or expose secrets.
Separate local validation from live website/API validation in your report.
```

## 4. Preview matching jobs

```text
Run python naukri.py preview and summarize which listings match my
configured roles and filters. Do not click Apply. If login has expired,
help me launch manual login. Explain excluded results and suggest profile
filter changes for my review without inventing qualifications.
```

## 5. First real application

```text
I authorize one real Naukri application in this run. Read AGENTS.md.
Set stop_after_n_applications and stop_after_n_attempts to 1, preserve
my other profile facts, and keep the browser visible. Check the local
setup, then run python naukri.py apply --confirm. I will answer manual
screening questions in the terminal. Stop on restrictions or challenges.
Report confirmed versus uncertain results, and do not refresh my resume.
```

Only use this prompt when you intend to send a real job application. Your saved profile and generated answers affect what is submitted.

## 6. Later limited run

```text
I authorize a real run of up to [N] confirmed applications and [M]
attempts using my existing profile. Keep my existing daily ceiling and
pacing, and do not run another instance concurrently. Check that the
limits are valid, then run python naukri.py apply --confirm.
Use my actual facts and stop on platform restrictions. Summarize
confirmed, skipped, and uncertain outcomes without exposing personal data.
```

## 7. Fix an error

```text
Read AGENTS.md and docs/VALIDATION.md. Diagnose this redacted error:
[error text with secrets and personal information removed]
Reproduce with an offline test or local HTML fixture where possible.
Preserve current user edits and keep preview free of submission actions.
Do not disable stop signals, fabricate success, or retry live applications
as a test. Explain the change and run the relevant tests.
```

## 8. Use without an AI API key

```text
Help me use the project with manual terminal answers for free-text
questions. I do not want to configure an API key. Explain that common
profile answers still work and unsupported questions pause for input.
Check whether my terminal is interactive. Do not print or remove existing
keys automatically; explain how I can leave the key fields blank locally
and unset any inherited provider-key environment variables myself.
Run checks and preview only, with no submissions.
```

## 9. Package my code for sharing

```text
Review only public code/templates for accidental personal information.
Run the offline tests, then python scripts/package_share.py. Inspect the
ZIP manifest and verify that it excludes .env, profile.yaml, session files,
resumes, logs, screenshots, virtual environments, and Git history.
Check that README.md, AGENTS.md, and both user/prompt guides are included.
Give me the final ZIP path and validation results. Do not publish or send
it to anyone, and do not zip the full working folder.
```

## 10. Get help from a chat-only assistant

```text
I have the Naukri Automation share ZIP and use [Windows/macOS/Linux].
You do not have access to my computer. Use the attached README and user
guide to walk me through installation, profile setup, manual login,
validation, and preview. Give commands I can run myself and explain
expected output. Do not ask for secrets. Do not assume you ran a command.
Wait until I explicitly request real applications before giving that stage.
```

## What a good completion report contains

Ask for the commands actually run, test results, the archive path if created, and any missing verification. A local smoke test is not proof that a job was submitted. A script must detect explicit confirmation before counting success; uncertain results need manual inspection. Compatibility here means common files and commands an assistant can follow, not a tested native plugin for every product.
