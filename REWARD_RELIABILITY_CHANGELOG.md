# CS Rewards Bot v3.3.2 — Reward Reliability Patch

## Scope

This patch addresses false-success reporting for Daily and standard Store rewards, makes the first backup after deployment audit existing standard-reward state, and keeps unresolved claims eligible for recovery.

Base source: `master_claimer.py` from `dev/reward-reliability` at blob SHA `e0da3370de70656337ebd00d74f2d8177e3f5d9a`.

## Changes

### 1. Daily reward verification

- The bot reads the live Daily page instead of treating local same-day history as proof that the reward is already claimed.
- It counts a claim only after the live page shows a Claimed/already-claimed state or a Daily-page cooldown signal.
- If a click is not confirmed, the bot refreshes and allows at most one retry only when a fresh read still shows an enabled Claim button.
- An unresolved result is recorded as `unconfirmed`, not `claimed`.

### 2. Standard Store reward verification

- Gold (Daily), Cash (Daily), and Luckyloon (Daily) are inspected individually on every full claim and standard-reward audit.
- A visible enabled Free/Claim button is treated as available even when a Store Bonus badge shows its own `Next in` timer.
- Generic Store Bonus countdown text is not accepted as proof that the daily Store card was claimed.
- A claim is counted only after the corresponding card changes to Claimed/on-cooldown state.
- After an unconfirmed click, the bot refreshes before one permitted retry. If refresh fails or state is ambiguous, it stops and leaves the item retryable rather than clicking blindly.
- A same-reset history record is cleared when a live read proves the reward is still unclaimed.

### 3. Progression verification and follow-through

- Progression claims are counted only when the portal's Delivered/Claimed state increases after the click.
- A disappearing Claim button alone is not considered proof of delivery.
- When a backup recovers a standard Store reward, it reopens Progression so the newly credited Grenade can unlock a milestone reward.

### 4. Backup audit cadence

- A player whose existing full-claim coverage has no standard-reward audit marker is given one live Daily + three-card Store audit during a backup run.
- After a clean audit, `bot_meta.json` records `standard_rewards_audited_reset` for that player's current 05:30 IST reset date.
- Later backup slots on the same reset return to the lighter promotional/event-only check for that player.
- If any standard reward remains unconfirmed, the player's coverage becomes pending. Later backups use the full claim/recovery path for that player until the issue is resolved.
- A new reset date automatically requires a fresh audit.

### 5. History and email reporting

- Unconfirmed attempts are recorded separately from confirmed claims and remain retryable.
- The email gains a readable amber `Attention Required` section for unconfirmed Daily, Store, and Progression rewards.
- Run-summary artifacts include the standard-reward verification details and unconfirmed Store indices.

## Files in this patch

- `master_claimer.py` — complete replacement for the repository root file.
- `tests/test_reward_claim_verification.py` — new focused regression tests.

No changes are included for `players.csv`, `claim_history.json`, `bot_meta.json`, `event_rewards.py`, `event_rewards_config.json`, `schedule.yml`, or `recovery.yml`.

## Validation performed

- Python compilation passed for the replacement source and both reporting/reliability test files.
- 29 offline automated tests passed: 26 focused reliability tests and 3 existing claim-count/email-reporting tests.
- Browser and event dependencies were stubbed/mocked for the reliability tests. This is not a live CS Hub portal test.
- The existing `datetime.utcnow()` deprecation warning in `get_ist_time()` remains unchanged; it did not cause test failures.

## Live validation still required

After the replacement is uploaded to the default branch, the first scheduled backup for already-covered accounts should perform the one-time standard-reward audit. The still-Free Cash (Daily) reward for the first roster ID is the primary live recovery test. Confirm the Store card changes to Claimed and the Progression counter increments before considering that recovery successful.

The unclaimed 900-point Halloween reward for `63989CEF5D15F053` is not changed by this patch and remains a separate investigation.


### 6. Immediate in-run recovery

- After each full-claim or backup-reconciliation account completes its first pass, unresolved reward targets are evaluated before the roster loop proceeds to the next ID.
- A fresh browser session rechecks only the affected Daily reward, standard Store card indices, exact Progression card signature, or temporary-event programme/tier thresholds.
- Daily and standard Store recovery passes are limited to one additional click per target. The live portal is checked before clicking; already-claimed/cooldown states are recorded without a click.
- Progression recovery is restricted to the original failed card signature. If the signature is absent or ambiguous, the recovery pass refuses to click another card.
- Event recovery is restricted to the original programme IDs and tier thresholds. Unidentified event failures remain pending for the post-run observer.
- An initial login failure may receive one fresh full-account pass because no reward page was reached during the failed login.
- Promotional-only processing and recovery-only workflows do not recursively invoke immediate full-claim recovery.
- The primary workflow remains the only claim-session owner; the existing rewards-claimer concurrency group is unchanged. No parallel claimant or extra workflow concurrency group is introduced.
- The existing post-run Failed-ID Recovery observer remains in place as the second-line backstop.
- The run email and run_artifacts/run_summary.json include immediate recovery attempts, successful resolutions, and unresolved targets.

- The post-run Failed-ID Recovery worker now consumes `last_run.retry_targets_by_player` and uses the same targeted fresh-session recovery helper. Missing or malformed target metadata is deferred without calling the normal full-account claim routine; only a prior login failure may receive one full-account retry because the original session never reached reward pages.
- Target metadata is persisted with each source run, including the Progression card signature, event programme/tier thresholds, Store indices, and Daily unconfirmed flag. Deferred items are shown in the recovery email instead of being hidden as unattempted.
