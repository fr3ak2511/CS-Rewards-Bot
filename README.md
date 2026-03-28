# CS-Rewards-Bot

[![CS Hub Rewards Claimer](https://github.com/fr3ak2511/CS-Rewards-Bot/actions/workflows/schedule.yml/badge.svg)](https://github.com/fr3ak2511/CS-Rewards-Bot/actions/workflows/schedule.yml)
[![Delete Old Workflow Runs](https://github.com/fr3ak2511/CS-Rewards-Bot/actions/workflows/cleanup.yml/badge.svg)](https://github.com/fr3ak2511/CS-Rewards-Bot/actions/workflows/cleanup.yml)

Automated reward claimer for CS Hub using **GitHub Actions** and **Python**.

Runs 8 times per day (every 3 hours), claims all available rewards across 25 player IDs, and sends a premium dark-themed HTML dashboard email after every run.

---

## 🗓️ Run Schedule (IST)

| Run | Time (IST) | Purpose |
|-----|-----------|---------|
| Primary | 05:35 AM | Main daily claim — all reward types |
| Backup #1 | 08:35 AM | Catch any IDs missed at 05:35 |
| Backup #2 | 11:35 AM | Mid-day retry + progression/loyalty |
| Backup #3 | 02:35 PM | Afternoon retry |
| Backup #4 | 05:35 PM | Evening retry |
| Backup #5 | 08:35 PM | Night retry |
| Backup #6 | 11:35 PM | Late-night retry |
| Backup #7 | 02:35 AM | Pre-dawn retry |

Backup runs **smart-skip** any ID where all rewards are already on cooldown — no wasted browser time.

---

## 🎮 Rewards Claimed

| Reward | Reset | Notes |
|--------|-------|-------|
| 🎁 Daily | 5:30 AM IST daily | 1 per ID |
| 🏪 Store (Gold, Cash, Luckyloon) | 5:30 AM IST daily | 3 per ID |
| 🎯 Progression Program | Monthly | Depends on grenades/bullets from Store |
| 🏆 Loyalty Program | Rolling 24h | Depends on LP from purchases |

---

## 📦 Repository Structure

| File | Purpose |
|------|---------|
| `master_claimer.py` | Core bot logic v3.0.0 |
| `players.csv` | Player ID database with loyalty flags |
| `claim_history.json` | Per-player claim state (auto-committed by bot) |
| `bot_meta.json` | Streak, efficiency delta, new-ID tracking (auto-committed) |
| `requirements.txt` | Python dependencies |
| `.github/workflows/schedule.yml` | 3-hourly run schedule with commit-back |
| `.github/workflows/cleanup.yml` | Deletes old workflow run logs every 3 days |

---

## 📧 Email Report Features

- **Dark-themed HTML dashboard** — readable on desktop and mobile
- **Run badge** — Primary / Backup #N / Manual Run
- **Hero section** — total claimed, efficiency %, day streak 🔥
- **4 KPI cards** — Daily, Store, Progression, Loyalty with progress bars and vs-last-run deltas
- **Run strip** — total time, avg per player, slowest ID, best streak
- **Full player table** — one row per ID, all reward columns, colour-coded by status
- **Detail cards** — expanded info shown only for failed or partial IDs
- **Scheduled runs footer** — all 8 daily run times at a glance
- **🆕 badge** — highlights new IDs on their first run

---

## ⚙️ GitHub Secrets Required

| Secret | Description |
|--------|-------------|
| `SENDER_EMAIL` / `SMTP_FROM` | Gmail address to send from |
| `GMAIL_APP_PASSWORD` / `SMTP_PASSWORD` | Gmail App Password |
| `RECIPIENT_EMAIL` / `SMTP_TO` | Address to receive reports |
| `SMTP_SERVER` | *(optional)* Defaults to `smtp.gmail.com` |
| `SMTP_PORT` | *(optional)* Defaults to `465` (SSL) |

---

## 🗑️ Files Safe to Delete

These legacy files are no longer referenced and can be removed from the repo:

- `send_email_with_log.py` — references a script that no longer exists
- `store_claims_log.csv` — superseded by `claim_history.json`
