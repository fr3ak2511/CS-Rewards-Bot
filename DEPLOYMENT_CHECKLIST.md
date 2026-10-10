# v3.0.5 Deployment and Full-Roster Test Checklist

## Files to replace in the repository root

- `master_claimer.py`
- `requirements.txt`
- `.gitignore`
- `README.md`
- `.github/workflows/schedule.yml`

Keep these existing files as they are:

- `players.csv` — it already contains the configured 35 IDs; the script reads the roster dynamically and fails if it is empty or has duplicates.
- `claim_history.json` — **do not replace it with an empty/new file**. The first run reconciles each account's live reward state and migrates each player's history in place.
- `bot_meta.json` — **do not reset or replace it**. It stores the existing streak and previous-run/email state.
- `.github/workflows/cleanup.yml` — unchanged.

`.gitignore` does not automatically untrack files that are already committed. The workflow still commits updates to the existing tracked state files.

## Recommended first full-roster test

1. Back up the current repository state.
2. To prevent an overlapping automatic run, temporarily comment out all four `schedule:` cron lines in `.github/workflows/schedule.yml` before your first deployment commit. Leave `workflow_dispatch` intact.
3. Commit the five replacement files listed above to the default branch (`main`). Scheduled and manual workflows use the workflow definition on the default branch.
4. Open **Actions → CS Hub Rewards Claimer → Run workflow**. Choose `main` and leave all three options (`diagnose_login`, `test_login_only`, `test_claims_one_player`) set to `false`. This runs all configured IDs; it does not run a separate one-player mode.
5. In the logs, confirm `Loaded 35 players`, review login/reward outcomes, and look for `EMAIL_DELIVERY_RESULT=SUCCESS`.
6. Confirm the email arrives. If it does not, inspect `EMAIL_DELIVERY_RESULT`, the final SMTP messages, and the uploaded `debug_email.html` / `run_artifacts/run_summary.json` artifact. A green check alone is not proof of mail delivery.
7. In the run artifact, check `configured_player_count` is 35, inspect failure counts, and confirm the temporary reward is reported separately from the three daily Store cards.
8. Restore the three cron entries and commit that change once the full test is reviewed.

## Existing/manual rewards

On a full run, the bot opens each account and checks the current portal UI. A visible Daily/Store cooldown or `Claimed` state is recorded as `portal_claimed` with an observation timestamp, rather than fabricating a bot-generated `last_claim` timestamp. The temporary `200 Gold - Hub First Year Reward` is tracked separately at `store.reward_4` and remains claimed across daily resets only when the portal confirms it. Ambiguous/missing state is left unverified and checked again on later runs.

The email distinguishes claims made during this run from states merely observed on the portal. The complete report is attempted after all 35 configured player IDs have been processed; SMTP delivery is retried up to three times. If delivery is still not confirmed, the workflow returns a failure and preserves report artifacts.

## Cron reliability

The primary schedule is at `:05` UTC (05:35 IST) and recovery triggers are at `:17`, `:27` and `:37` UTC (12, 22 and 32 minutes later). A recovery trigger skips a repeat only if a recent full run also has `email_sent: true`; otherwise it is allowed to retry. This is a best-effort improvement, **not an exact-time guarantee**: GitHub states scheduled events can be delayed or dropped during high load. Keep the workflow enabled and the file on `main`, and check that the account associated with the schedule remains active. If precise execution is essential, an external scheduler that dispatches the workflow is needed.
