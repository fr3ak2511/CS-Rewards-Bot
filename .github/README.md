# Auto Rewards Bot (GitHub Actions)

This repository runs your four Vertigo Hub reward scripts on a schedule using GitHub Actions and emails you the full console output after each run.

## Contents

- `hub_daily_rewards.py`
- `hub_store_rewards.py`
- `hub_merged_rewards.py`
- `2_hub_ProgressionProgram_rewards.py`
- `players.csv`
- `send_email_with_log.py`
- `.github/workflows/` (4 workflow files, one per script)

All scripts are already configured to use **headless Chrome** on Linux servers via Selenium.

## 1. Create a new GitHub repository

1. Create a new repo on GitHub (for example: `auto-rewards-bot`).
2. Clone it locally.
3. Copy all files from this folder into that repo (including the `.github` directory).
4. Commit and push.

## 2. Configure GitHub Secrets for email (Gmail)

In your GitHub repository:

1. Go to **Settings → Secrets and variables → Actions → New repository secret**.
2. Create these secrets with the following values:

- `SMTP_HOST`  → `smtp.gmail.com`
- `SMTP_PORT`  → `587`
- `SMTP_USER`  → `saurabh.mendiratta7@gmail.com`
- `SMTP_PASS`  → your **Gmail App Password** (16-character app password)
- `EMAIL_FROM` → `saurabh.mendiratta7@gmail.com`
- `EMAIL_TO`   → `saurabh.mendiratta7@gmail.com`

You must generate a Gmail app password from your Google account (Security → App passwords) and paste it as `SMTP_PASS`.

## 3. Schedules (IST → UTC)

Each script is scheduled to run **twice per day** at your requested IST times.

GitHub Actions uses **UTC**, so the workflows are set as:

- `hub_merged_rewards.yml`
  - 10:00 AM IST → `30 4 * * *` (UTC)
  - 5:00  PM IST → `30 11 * * *` (UTC)

- `hub_store_rewards.yml`
  - 11:00 AM IST → `30 5 * * *` (UTC)
  - 6:00  PM IST → `30 12 * * *` (UTC)

- `hub_daily_rewards.yml`
  - 11:30 AM IST → `0 6 * * *`  (UTC)
  - 6:30  PM IST → `0 13 * * *` (UTC)

- `hub_progression_program_rewards.yml`
  - 7:00  PM IST → `30 13 * * *` (UTC)
  - 10:30 PM IST → `0 17 * * *` (UTC)

You can change these cron expressions inside each `.yml` file later if needed.

## 4. What each workflow does

For each scheduled time (and for manual runs):

1. Checks out the repo.
2. Installs Python 3.11.
3. Installs `selenium` via pip.
4. Runs `send_email_with_log.py <script> "<Job Label>"`.

`send_email_with_log.py`:
- Executes the target script.
- Captures all console output (stdout + stderr).
- Prints it into the GitHub Actions log.
- Sends the same output in an email with subject:
  - `[<Job Label>] SUCCESS` or `[<Job Label>] FAILED`.

## 5. Testing manually (recommended)

After pushing to GitHub:

1. Go to the **Actions** tab.
2. Click a workflow (e.g. “Hub Daily Rewards”).
3. Click **Run workflow** (top-right) to trigger a manual run.
4. Open the job, watch logs live.
5. Check your email for the summary message.

If something fails (e.g. login, selectors, site change), you will see the traceback in both:
- GitHub logs
- The email body

## 6. Local notes

- All scripts now use headless Chrome via Selenium Manager (no explicit `chromedriver.exe` path).
- `players.csv` must stay in the same folder as the Python scripts.
- No cookies are persisted between runs; scripts log in fresh each time (as requested).

You can now use this project as a starting point and adjust scripts/workflows as needed.
