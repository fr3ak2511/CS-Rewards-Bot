# CS Rewards Bot

Automated CS Hub rewards claimer using Python, Selenium, and GitHub Actions.

**Release:** v3.3.3 · **Configured roster:** loaded dynamically from `players.csv` (current repository snapshot contains 35 IDs).

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

Temporary point-event rewards currently use the reusable, configuration-driven event engine; automatic discovery of future event programmes is not yet enabled. The portal's authenticated event registry/page metadata has not been verified, and the current tier-card adapter is based on the Halloween 2026 layout. Unknown event routes or layouts must not be guessed. See [the event auto-discovery acceptance contract](docs/event-reward-auto-discovery.md).

## Existing/manual claims and history migration

The bot inspects the live portal before every eligible Daily and standard Store claim. If a reward is already Claimed/on cooldown before the bot's first click attempt, the portal state is recorded separately from a claim made by the bot. The dynamic promotional scanner similarly distinguishes a card that was already claimed on the initial page snapshot from a claim confirmed after this run's click.

A separate `portal_claim_audit.json` stores review evidence for pre-existing portal claims. Daily and standard Store observations are deduplicated per player, reward and 05:30 IST reset date; dynamic promotional cards are deduplicated per player/campaign. Repeat observations are summarised across distinct reset dates. The audit retains the full Player ID, captured in-game display name (IGN), reward name/category, first-observed IST timestamp, run label/ID and evidence source. A same-reset bot-confirmed claim or unresolved bot attempt suppresses a new manual-review record because the later portal state is ambiguous.

**The portal does not identify who made a claim.** The email therefore labels these as pre-existing portal claims / review evidence, not proven manual claims. This section does not change claim eligibility, retry policy, or remove players from `players.csv`. Any roster removal remains a manual owner decision. Legacy `store.reward_4` state is preserved for compatibility.

The existing `claim_history.json` is migrated lazily per player; do not replace it with a fresh empty file. `bot_meta.json` is also preserved and updated by the workflow. Existing Store entries `reward_1`–`reward_3` remain reset-based and legacy `reward_4` history is preserved. Current promotional cards are discovered and reported independently; an old `reward_4` marker does not suppress a newly appearing offer.

## Email and report artifacts

Every normal run builds and sends the HTML email after processing the selected accounts. The email and JSON summary distinguish accounts processed, confirmed claims made, unavailable/on-cooldown rewards, partial or unresolved results, unconfirmed promotional claims, dynamic-scan failures, and actual login/script failures. Deltas are suppressed when the current and previous run have different full-claim scope. Email sending retries up to three times; if delivery still fails, the workflow is marked failed and retains `debug_email.html` plus `run_artifacts/run_summary.json` for review.

The HTML report includes all processed account rows, per-account processing mode, confirmed claims, visible unavailable offers, partial or unresolved results, unconfirmed promotions, dynamic-scan failures, actual failures, efficiency for full-claim accounts, run timing, and portal-observed statuses. Account-specific issue sections show the full Player ID together with the captured IGN so an account can be checked without opening `players.csv`. The separate pre-existing portal-claim section includes full IDs and IGNs and flags accounts observed across multiple reset dates. Promotional-only rows explicitly mark daily Store, progression, and loyalty rewards as not checked; aggregate daily/store denominators exclude those rows. The general run-summary player table remains privacy-minimised; the separately retained portal-claim audit holds the full identifiers needed for follow-up.

Each normal run also creates `run_artifacts/report.html`, a self-contained browser report with a **Copy Report** action. It first attempts to copy the report as a PNG image (with formatted HTML and plain text included), then falls back to formatted HTML and plain text, and finally plain text if image or rich clipboard access is unavailable. Open this artifact in a browser; clipboard access may require browser permission. The emailed report remains script-free for email-client compatibility and is sent as HTML only, without a PNG attachment.

The JSON summary also contains a read-only reconciliation record. It independently compares per-account reward sums, category totals, the recorded claim counter, processing-mode counts, and configured-roster coverage for a primary run. Mismatches are recorded as attention items in the browser report; the reconciler never changes claim history, repeats a click, or guesses an expected Progression/Grenade count.

The streak remains in `bot_meta.json` as an internal continuity metric: it increments only when every configured player has a Daily claim and each of the three standard Store rewards recorded since the 05:30 IST reset, whether recorded by this bot or observed already claimed in the portal. Loyalty and separately tracked promotional rewards are excluded. It is intentionally omitted from the email because it is operational state, not an additional reward or claim count.

## Schedule and recovery

The main schedule has one full-claim primary cycle and four promotional-check backups:

| Window | Main trigger (IST) | Per-account behaviour |
|---|---:|---|
| Primary | 05:35–06:00 (date-seeded randomized target) | Full claim path for every configured ID |
| Promotional Check #1 | 09:35 | Existing IDs with complete same-day full-claim coverage: promotional-only; new/uncovered/pending IDs: full claim path |
| Promotional Check #2 | 13:35 | Same per-ID coverage rule |
| Promotional Check #3 | 17:35 | Same per-ID coverage rule |
| Promotional Check #4 | 21:35 | Same per-ID coverage rule |

The 03:00 IST dispatcher retains its randomized 05:35–06:00 target, but it no longer sleeps blindly. During the wait it checks for another queued/running claimer and exits if one takes ownership; after the target it refreshes committed production state and skips if a complete full-roster cycle already finished. The native dispatcher and five-minute 05:30–06:00 fallback remain available when the external primary has not completed a cycle. If a state/API inspection fails, the native path fails open and the in-job Python state gate remains authoritative. A central Python guard also refuses all claim-capable entry points before 05:30 IST; read-only login diagnostics and login-only tests are exempt. Scheduled events are best-effort and may still be delayed or dropped by GitHub Actions.

Per-ID full-claim coverage is stored separately from `new_ids_seen`. A backup uses the full claim path whenever the ID has no clean full-claim coverage for the current reset date or still has unresolved claims. A login failure, partial result, or unconfirmed claim remains pending and eligible for recovery; marking an ID as seen is not proof of successful processing.

After each full-claim account completes, the claimer performs one immediate, sequential recovery pass in a fresh browser session for that account's unresolved targets before moving to the next ID. Daily and standard Store targets are re-read live and receive at most one additional click; Progression recovery is restricted to the captured failed card identity; temporary-event recovery is restricted to the captured programme and tier thresholds. A failed initial login may receive one fresh full-account pass because the initial attempt did not reach reward pages. Promotional-only checks never trigger full-claim recovery, and recovery-only runs do not recursively invoke immediate recovery. The primary workflow remains the sole browser-claim owner; no parallel claimant or second concurrency group is introduced. The existing post-run Failed-ID Recovery observer remains the backstop for unresolved targets. Each source run persists exact retry targets in `bot_meta.json`; the post-run worker replays only those Daily, Store, Progression-card, and Event-tier targets in a fresh session. Missing target metadata is deferred without falling back to a full-account claim pass. Only an initial login failure may receive one full-account retry because no reward page was reached. The email and run_artifacts/run_summary.json report immediate attempts, recovered targets, deferred targets, and remaining pending targets. Overlapping runs remain serialized by the existing rewards-claimer concurrency group.

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
| `master_claimer.py` | Core full/promotional-only claim paths, portal-state reconciliation, coverage tracking, reports and guarded test modes (v3.3.3) |
| `.github/workflows/schedule.yml` | Randomized primary wait, four unique promotional-check slots, preflight/run gates, state commit-back and artifacts |
| `players.csv` | Current configured player IDs; unchanged |
| `requirements.txt` | Pinned Selenium / undetected-chromedriver versions for reproducible installation |
| `claim_history.json` | Per-player bot-claim and portal-state history; preserve existing data |
| `portal_claim_audit.json` | Reset-day-deduplicated pre-existing portal-claim evidence with full Player ID and captured IGN; persisted by the primary workflow |
| `bot_meta.json` | Streak, last run and email-delivery status; preserve existing data |
| `.github/workflows/cleanup.yml` | Existing old-run cleanup workflow; unchanged |

## Required Actions secrets

- `SENDER_EMAIL`
- `GMAIL_APP_PASSWORD`
- `RECIPIENT_EMAIL`

The workflow passes these as SMTP settings. Do not put credentials in the repository.
