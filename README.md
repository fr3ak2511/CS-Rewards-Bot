# CS Rewards Bot

Automated CS Hub rewards claimer using Python, Selenium, and GitHub Actions.

**Release:** v3.2.0 · **Configured roster:** loaded dynamically from `players.csv` (current repository snapshot contains 36 IDs).

## Reward model

| Reward | State and availability handling |
|---|---|
| Daily reward | Daily reset at 05:30 IST. A live cooldown/timer is reconciled into history. |
| Gold (Daily) | Daily reset at 05:30 IST; detected on its named card. |
| Cash (Daily) | Daily reset at 05:30 IST; detected on its named card. |
| Luckyloon (Daily) | Daily reset at 05:30 IST; detected on its named card. |
| Promotional Store cards | Discovered dynamically from the live Store DOM. Visible non-daily cards with explicit claim, already-claimed, or unavailable states are tracked; only enabled claim buttons are clicked. The portal must confirm a claim before it is counted. |
| Legacy Hub First Year reward | Existing `store.reward_4` history is preserved but does not suppress dynamic discovery of current or future offers. |
| Progression Program | Claimable items depend on the portal's progression state and account thresholds. |
| Loyalty Program | Rolling 24-hour cooldown, subject to LP eligibility. |

Promotional cards are **not** attached to the three daily Store indices and are **not** a daily-streak requirement. The live scanner records claimable, already-claimed, and unavailable card states without relying on a fixed offer title. If a claim cannot be confirmed, the report distinguishes that state and the account remains eligible for recovery. A dispatched click is never enough to count a promotion as claimed.

## Existing/manual claims and history migration

On the first run after deployment, each ID is inspected live. If Daily or any of the three standard Store cards shows a timer or a `Claimed` state, the bot records a **portal-observed claim/cooldown** with an observation timestamp and suppresses another attempt until the daily reset. It does not falsify that event as a claim made by the bot. Legacy `store.reward_4` state is preserved for compatibility, while the dynamic promotional scanner records each live offer in the separate per-promotion history and records a portal-confirmed already-claimed state as `portal_claimed`.

The existing `claim_history.json` is migrated lazily per player; do not replace it with a fresh empty file. `bot_meta.json` is also preserved and updated by the workflow. Existing Store entries `reward_1`–`reward_3` remain reset-based and legacy `reward_4` history is preserved. Current promotional cards are discovered and reported independently; an old `reward_4` marker does not suppress a newly appearing offer.

## Email and report artifacts

Every normal run builds and sends the HTML email after processing the selected accounts. The email and JSON summary distinguish accounts processed, confirmed claims made, unavailable/on-cooldown rewards, partial or unresolved results, unconfirmed promotional claims, dynamic-scan failures, and actual login/script failures. Deltas are suppressed when the current and previous run have different full-claim scope. Email sending retries up to three times; if delivery still fails, the workflow is marked failed and retains `debug_email.html` plus `run_artifacts/run_summary.json` for review.

The HTML report includes all processed account rows, per-account processing mode, confirmed claims, visible unavailable offers, partial or unresolved results, unconfirmed promotions, dynamic-scan failures, actual failures, efficiency for full-claim accounts, run timing, and portal-observed statuses. Promotional-only rows explicitly mark daily Store, progression, and loyalty rewards as not checked; aggregate daily/store denominators exclude those rows. The uploaded JSON summary masks player IDs to their last four characters.

## Schedule and recovery

The main schedule has one full-claim primary cycle and four promotional-check backups:

| Window | Main trigger (IST) | Per-account behaviour |
|---|---:|---|
| Primary | 05:35–06:00 (date-seeded randomized target) | Full claim path for every configured ID |
| Promotional Check #1 | 09:35 | Existing IDs with complete same-day full-claim coverage: promotional-only; new/uncovered/pending IDs: full claim path |
| Promotional Check #2 | 13:35 | Same per-ID coverage rule |
| Promotional Check #3 | 17:35 | Same per-ID coverage rule |
| Promotional Check #4 | 21:35 | Same per-ID coverage rule |

The 03:00 IST dispatcher retains its sleep until the shared primary target, and five-minute fallback triggers in the 05:30–06:00 window check the same target/state guards. A central Python guard also refuses all claim-capable entry points before 05:30 IST; read-only login diagnostics and login-only tests are exempt. Scheduled events are best-effort and may still be delayed or dropped by GitHub Actions.

Per-ID full-claim coverage is stored separately from `new_ids_seen`. A backup uses the full claim path whenever the ID has no clean full-claim coverage for the current reset date or still has unresolved claims. A login failure, partial result, or unconfirmed claim remains pending and eligible for recovery; marking an ID as seen is not proof of successful processing. The immediate recovery workflow can retry unresolved IDs, while live portal state/history helps prevent redundant claims. Overlapping runs are serialized by a workflow concurrency group.

**Important platform limit:** GitHub documents scheduled workflows as best-effort: high load can delay or drop scheduled events. These offset triggers reduce the likelihood that a single missed event causes a missed run, but they cannot guarantee exact-time execution or recover if GitHub drops every trigger in that window. Scheduled workflows must remain enabled and the workflow file must be on the repository's default branch. If runs still do not appear at all, verify the Actions workflow is enabled and the account that last modified the cron remains active. For stricter timing, an external scheduler that dispatches this workflow is needed.

## Manual workflow options

Open **Actions → CS Hub Rewards Claimer → Run workflow**. Select only one mode:

- `diagnose_login`: read-only login UI inspection; no ID submission, no claims.
- `test_login_only`: submits the first configured ID and checks the login state; no claims.
- `test_claims_one_player`: performs real claims for the first configured ID only; saves claim-test artifacts and persists the resulting history. It is blocked before 05:30 IST.
- Leave all three false for a normal manual full-roster run; it is also blocked before 05:30 IST.

The scheduled primary wait logic is retained. Login diagnostics and login-only tests remain available before reset because they do not claim rewards.

## Repository files

| File | Purpose |
|---|---|
| `master_claimer.py` | Core full/promotional-only claim paths, portal-state reconciliation, coverage tracking, reports and guarded test modes (v3.2.0) |
| `.github/workflows/schedule.yml` | Randomized primary wait, four unique promotional-check slots, preflight/run gates, state commit-back and artifacts |
| `players.csv` | Current configured player IDs; unchanged |
| `requirements.txt` | Pinned Selenium / undetected-chromedriver versions for reproducible installation |
| `claim_history.json` | Per-player claim and portal-observation state; preserve existing data |
| `bot_meta.json` | Streak, last run and email-delivery status; preserve existing data |
| `.github/workflows/cleanup.yml` | Existing old-run cleanup workflow; unchanged |

## Required Actions secrets

- `SENDER_EMAIL`
- `GMAIL_APP_PASSWORD`
- `RECIPIENT_EMAIL`

The workflow passes these as SMTP settings. Do not put credentials in the repository.
