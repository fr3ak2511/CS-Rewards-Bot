# CS Rewards Bot

Automated CS Hub rewards claimer using Python, Selenium, and GitHub Actions.

**Release:** v3.0.5 · **Configured roster:** 35 player IDs (loaded dynamically from `players.csv`).

## Reward model

| Reward | State and availability handling |
|---|---|
| Daily reward | Daily reset at 05:30 IST. A live cooldown/timer is reconciled into history. |
| Gold (Daily) | Daily reset at 05:30 IST; detected on its named card. |
| Cash (Daily) | Daily reset at 05:30 IST; detected on its named card. |
| Luckyloon (Daily) | Daily reset at 05:30 IST; detected on its named card. |
| **200 Gold – Hub First Year Reward** | Temporary one-time card tracked independently as `store.reward_4`. Claimed only after the portal card confirms the claimed state. |
| Progression Program | Claimable items depend on the portal's progression state and account thresholds. |
| Loyalty Program | Rolling 24-hour cooldown, subject to LP eligibility. |

The temporary card is **not** attached to the three daily Store indices and is **not** a daily-streak requirement. If the card is absent or its state is ambiguous, the bot reports it as unverified and checks it again later. It is never counted as claimed just because a click was dispatched.

## Existing/manual claims and history migration

On the first run after deployment, each ID is inspected live. If Daily or any of the three standard Store cards shows a timer or a `Claimed` state, the bot records a **portal-observed claim/cooldown** with an observation timestamp and suppresses another attempt until the daily reset. It does not falsify that event as a claim made by the bot. If the temporary reward card is already claimed, `store.reward_4.status` is recorded as `portal_claimed` and remains one-time across daily resets.

The existing `claim_history.json` is migrated lazily per player; do not replace it with a fresh empty file. `bot_meta.json` is also preserved and updated by the workflow. The four Store entries remain distinct: `reward_1`–`reward_3` are reset-based; `reward_4` is a one-time temporary card.

## Email and report artifacts

Every normal full-roster run builds and sends the HTML email after processing the entire configured player list. The subject and report use the actual count read from `players.csv` (currently 35), not a hard-coded 25. Email sending retries up to three times; if delivery still fails, the workflow is marked failed and retains `debug_email.html` plus `run_artifacts/run_summary.json` for review.

The HTML report includes all player rows, the fourth reward as a separate column/mobile row/detail, total daily Store claims versus temporary claims, efficiency, failure counts, run timing, and status observed from the portal. The uploaded JSON summary masks player IDs to their last four characters.

## Schedule and recovery

The main schedule retains eight daily claim windows:

| Window | Main trigger (IST) |
|---|---:|
| Primary | 05:35 |
| Backup #1 | 08:35 |
| Backup #2 | 11:35 |
| Backup #3 | 14:35 |
| Backup #4 | 17:35 |
| Backup #5 | 20:35 |
| Backup #6 | 23:35 |
| Backup #7 | 02:35 |

The workflow also has recovery triggers 12, 22 and 32 minutes after each main trigger. A guard reads the last committed `bot_meta.json`; it skips an offset retry only when a full run completed within the last 35 minutes **and** its email delivery was confirmed successful. If the prior run failed or email delivery was not confirmed, the recovery trigger retries the full workflow; live portal checks and claim history are used to avoid duplicating rewards. Overlapping runs are serialized by a workflow concurrency group.

**Important platform limit:** GitHub documents scheduled workflows as best-effort: high load can delay or drop scheduled events. These offset triggers reduce the likelihood that a single missed event causes a missed run, but they cannot guarantee exact-time execution or recover if GitHub drops every trigger in that window. Scheduled workflows must remain enabled and the workflow file must be on the repository's default branch. If runs still do not appear at all, verify the Actions workflow is enabled and the account that last modified the cron remains active. For stricter timing, an external scheduler that dispatches this workflow is needed.

## Manual workflow options

Open **Actions → CS Hub Rewards Claimer → Run workflow**. Select only one mode:

- `diagnose_login`: read-only login UI inspection; no ID submission, no claims.
- `test_login_only`: submits the first configured ID and checks the login state; no claims.
- `test_claims_one_player`: performs real claims for the first configured ID only; saves claim-test artifacts and persists the resulting history.
- Leave all three false for the normal full-roster run (all 35 configured IDs), including the email report.

For a one-player claim test, temporarily disable the scheduled cron entries first to prevent a test/scheduled overlap, then restore them after reviewing the artifact.

## Repository files

| File | Purpose |
|---|---|
| `master_claimer.py` | Core claiming, portal-state reconciliation, email generation, test modes (v3.0.5) |
| `.github/workflows/schedule.yml` | Main schedule, offset recovery triggers, de-dup guard, state commit-back, artifacts |
| `players.csv` | Current 35 player IDs; unchanged |
| `requirements.txt` | Pinned Selenium / undetected-chromedriver versions for reproducible installation |
| `claim_history.json` | Per-player claim and portal-observation state; preserve existing data |
| `bot_meta.json` | Streak, last run and email-delivery status; preserve existing data |
| `.github/workflows/cleanup.yml` | Existing old-run cleanup workflow; unchanged |

## Required Actions secrets

- `SENDER_EMAIL`
- `GMAIL_APP_PASSWORD`
- `RECIPIENT_EMAIL`

The workflow passes these as SMTP settings. Do not put credentials in the repository.
