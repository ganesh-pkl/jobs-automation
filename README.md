# Multi-Platform Daily Job Application Pipeline

An automated, intelligent daily job application pipeline that searches, matches, auto-fills screening questionnaires via AI, and applies across **Naukri**, **LinkedIn**, **Hirist**, **Foundit** (Monster), **Wellfound** (AngelList), **Uplers**, and **Instahyre**.

Designed to run **once every morning** as an autonomous daily workflow with persistent duplicate prevention and an actionable external application queue.

---

## Key Features & Architecture

```
                                      DAILY MORNING RUN
                                 (python daily_pipeline.py)
                                              │
                    ┌─────────────────────────┴─────────────────────────┐
                    ▼                                                   ▼
       applications_log.csv                                    external_jobs.csv
    (Permanent Application History)                       (Today's Working Manual Queue)
    • Never reset across days                             • Reset to 0 rows at start of every run
    • Stores all successful submissions                   • Incrementally populated during run
    • Blocks reapplying to same jobs                      • ONLY external ATS/careers links
    • Tracks historical counts                            • No duplicates / No applied jobs
```

### 1. 7-in-1 Platform Support
* **Naukri**: Searches fresh jobs, applies directly, handles multi-turn chatbot screening drawers, and extracts external links.
* **LinkedIn**: Scans Easy Apply postings, navigates multi-step modals (`Contact Info` ➔ `Screening Questions` ➔ `Review` ➔ `Submit`), and collects external ATS openings.
* **Hirist.tech**: Evaluates fresh tech openings, navigates `/screening` questionnaires, auto-selects radio choices, and logs company careers links.
* **Foundit (Monster)**: Searches targeted keyword/role queries, evaluates experience and freshness filters, handles Quick Apply / Apply Now modals, and logs external careers links.
* **Wellfound (AngelList)**: Filters direct 1-click startup roles, evaluates match criteria, crafts tailored recruiter pitch notes via LLM, and submits applications directly.
* **Uplers**: Evaluates opportunities in the talent dashboard, auto-fills application dialogs, and submits applications.
* **Instahyre**: Searches matched tech roles & keywords on Instahyre candidate dashboard, auto-fills screening notes, and submits applications.

### 2. AI Screening & Form Auto-Fill (`common/answers.py`)
* **Direct Candidate Facts**: Instantly fills known values (Full Name, Notice Period, Current/Expected CTC, Total Experience, City, Relocation).
* **Groq AI Dynamic Answers**: Powered by `qwen/qwen3.8-27b` via Groq to dynamically answer technical screening questions, role fit, and experience summaries.
* **Learned Memory**: Caches responses locally in `common/learned_answers.py` for consistent, fast answering.
* **Sensitive Field Protection**: Halts and prompts the terminal for sensitive PII (PAN, Aadhaar, Passport, Bank details).

### 3. Isolated Persistent History vs. Daily Working CSV
* **`applications_log.csv` (Permanent)**: Accumulates all successful submissions (`status == 'applied'`). Prevents reapplying to any job already applied on previous days.
* **`external_jobs.csv` (Today's Queue)**: Automatically cleared to 0 rows at the start of every run. Only holds genuine external company ATS opportunities discovered during today's run.

---

## Setup & Prerequisites

### 1. Installation

```bash
# 1. Clone repository
git clone git@github.com:ganesh-pkl/jobs-automation.git
cd jobs-automation

# 2. Setup Python environment
python3 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies & Chromium
pip install -r requirements.txt
playwright install chromium
```

### 2. Configure Credentials & Profile

1. **Environment Variables (`.env`)**:
   ```env
   GROQ_API_KEY=your_groq_api_key_here
   GROQ_MODEL=qwen/qwen-2.5-32b-instruct
   ```

2. **Candidate Profile (`profile.yaml`)**:
   Edit `profile.yaml` with your actual professional facts, target roles, experience ceiling/floor, and job freshness window (e.g. `1` to `7` days).

3. **Capture Platform Logins**:
   Run login capture for the platforms you want to automate:
   ```bash
   python login_capture.py naukri
   python login_capture.py linkedin
   python login_capture.py hirist
   python login_capture.py foundit
   python login_capture.py wellfound
   python login_capture.py uplers
   python login_capture.py instahyre
   ```
   Log into each platform in the opened browser, then press **Enter** in the terminal to save the session JSON.

---

## Running the Automation

### Daily Morning Run (Recommended)

Run the full 7-platform pipeline every morning with a single command:

```bash
python daily_pipeline.py
```
*or*
```bash
python run_all.py
```

### Running Individual Platforms

You can also run any platform independently:

```bash
python wellfound_apply.py   # Run Wellfound automation
python hirist_apply.py      # Run Hirist automation
python naukri_apply.py      # Run Naukri automation
python linkedin_apply.py    # Run LinkedIn Easy Apply
python foundit_apply.py     # Run Foundit automation
python uplers_apply.py      # Run Uplers automation
python instahyre_apply.py   # Run Instahyre automation
```

---

## Daily Summary Report

At the end of every morning run, the pipeline prints a complete summary:

```text
=================================================================
Date: 2026-09-29

Fresh jobs discovered: 95
Previously applied jobs skipped: 18
Duplicate jobs skipped: 7

Naukri:
  Applied: 18

LinkedIn:
  Applied: 8

Hirist:
  Applied: 12

Foundit:
  Applied: 10

Wellfound:
  Applied: 8

Uplers:
  Applied: 4

Instahyre:
  Applied: 6

Total successful applications today: 66

Total successful applications historically: 154

New external/manual jobs added to CSV: 16

Total external jobs currently in CSV: 16
=================================================================

--- HIGH-MATCH EXTERNAL OPPORTUNITIES ADDED TODAY ---
1. [LinkedIn] Senior Full Stack Developer @ Acme Corp (Hyderabad / Remote)
   Skills: React.js, Node.js, REST APIs, System Design
   Careers Link: https://boards.greenhouse.io/acmecorp/jobs/123456
```

---

## Testing & Verification

Run the test suite to verify question answering, candidate fact extraction, and safety rules:

```bash
python -m unittest discover -s tests -v
```

Run browser smoke validation:
```bash
python naukri.py doctor --browser-smoke
```

---

## License

MIT License.
