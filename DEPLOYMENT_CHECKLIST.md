# v3.2.0 Deployment and Monitoring Checklist

## Files included in this proposed change

Replace only these files from the reviewed package:

- `master_claimer.py`
- `.github/workflows/schedule.yml`
- `.github/workflows/recovery.yml`
- `README.md`
- `DEPLOYMENT_CHECKLIST.md`

Keep these files unchanged:

- `players.csv` — the current snapshot contains 36 configured IDs; the script reads this file dynamically.
- `claim_history.json` — preserve the existing per-account state; do not replace it with an empty file.
- `bot_meta.json` — preserve streak and previous-run state. The new `full_claim_coverage` map is added by the script as it records real full-path outcomes.
- `requirements.txt` and `.gitignore` — no changes are required for this proposal.
- `.github/workflows/cleanup.yml` — unchanged and not activated or otherwise modified by this proposal.

## Before committing

1. Save a copy of the current repository state.
2. Compare the five proposed files against the live `main` versions. Do not upload `players.csv`, `claim_history.json`, `bot_meta.json`, or `.github/workflows/cleanup.yml` from this package.
3. Keep the existing primary `03:00 IST` dispatcher, randomized sleep target (`05:35–06:00 IST`), and five-minute fallback entries (`05:30–06:00 IST`). The proposed schedule keeps that primary timing logic intact.
4. Use the distinct backup cron entries for `09:35`, `13:35`, `17:35`, and `21:35 IST`; they determine the slot label independently of the actual start time.

## First run after deployment

- Do not run a manual full-claim run or one-player claim test before `05:30 IST`. The Python entry-point guard will refuse claim-capable runs before reset. Login diagnostics and login-only tests remain exempt.
- If the new version runs as the morning primary, it records per-ID full-claim coverage after each account's real processing outcome.
- If the primary already ran under an older version before deployment, the first promotional-check backup will send IDs without complete current-reset coverage through the full claim path. This is intentional; `new_ids_seen` is not accepted as proof of coverage.
- A backup uses the dynamic live promotional-card detector for IDs with complete same-reset full-claim coverage. Uncovered or pending IDs use the existing full claim path.
- A successful click alone does not count a dynamic promotional card as claimed; the portal's claimed state must confirm it. Login failures, partial outcomes, dynamic scan failures, and unconfirmed promotional claims remain identifiable and retryable.

## Verify each cycle

1. In the job log, confirm the run label corresponds to the triggering cron slot, not merely the start hour.
2. For the primary, verify the Python run context starts after `05:30 IST`; the dispatcher can start earlier while waiting.
3. In the email and `run_artifacts/run_summary.json`, review: accounts processed, processing mode, claims made, unavailable/on-cooldown rewards, partial or unresolved results, unconfirmed promotional claims, promotional scan failures, and actual login/script failures.
4. Confirm promotional-only rows mark daily Store, progression, and loyalty rewards as not checked, rather than implying those rewards were skipped or unavailable.
5. Check `retry_player_ids` and the immediate recovery workflow for login failures, unresolved claim attempts, or dynamic promotional scan failures. Recovery should be verified from logs and resulting state, not from the green workflow check alone.
6. Review at least one complete primary and several backup cycles before making further schedule or cleanup changes.

## Schedule limitation

GitHub Actions scheduled events are best-effort and may be delayed or dropped. The randomized primary wait plus five-minute fallbacks reduce timing risk but cannot guarantee exact-time execution if GitHub does not deliver any trigger. Leave the existing workflow schedule in place during the monitoring phase; do not activate or alter the separate cleanup workflow as part of this change.
