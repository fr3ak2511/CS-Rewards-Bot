# CS Rewards Bot — Portal Claim Accountability Audit

## Purpose

Provide the owner with evidence when a reward is already shown as Claimed/on cooldown **before the bot's first click attempt**. This supports manual review of accounts that may be bypassing the clan's instruction to report issues instead of claiming directly on the portal.

The feature is observational only. It does not change reward eligibility, make/skip a click differently, trigger retries, or remove IDs from `players.csv`.

## Evidence and attribution rules

- Only a portal Claimed/cooldown state observed before the bot attempts that reward can create a new review record.
- A bot-confirmed claim already recorded in the current 05:30 IST reset suppresses a new pre-existing-claim record.
- An unresolved bot attempt in the current reset also suppresses a new record: a later Claimed state could be the delayed effect of the bot's own earlier click.
- A portal state that changes after the bot click is not classified as pre-existing.
- Initial dynamic-promotion cards already marked Claimed are recorded once per player/campaign; a card that changes to Claimed after this run's click is treated as a bot claim.
- The same standard reward is deduplicated per player/reward/reset date. Repeated scans or backup runs on the same reset do not create extra incidents.
- The portal never supplies the actor identity. The email calls these **Pre-existing Portal Claims — Review**, not proven manual claims.

## Evidence retained

`bot_meta.json` maintains `player_display_names` as the current-IGN compatibility cache and `player_display_name_history` as the timestamped alias history keyed by immutable Player ID. Existing cached names are migrated with unknown historical timestamps rather than inventing dates. A later rename adds a new alias while retaining previous names; a return to an earlier IGN reuses that alias entry. The audit event preserves the IGN captured at first observation and stores the latest observed IGN separately.

`portal_claim_audit.json` stores:
- Full Player ID, IGN captured at the time of the event, and the latest observed IGN when that audit record is seen again.
- Reward category, reward key and visible reward name.
- Classification and portal-observation source.
- Reset date and first/last observed IST timestamps.
- Run label and GitHub Actions run ID when available.
- Observation count and reset-day recurrence, derived from distinct recorded reset dates.

The record is separate from `claim_history.json`, which remains the authoritative bot claim/cooldown state. The primary workflow commits the audit file with the other runtime state. Corrupt/unreadable audit state is not overwritten; new audit writes are skipped and a warning is logged.

## Email presentation

- A separate warning-style section lists each newly detected pre-existing portal claim in the current run.
- Every account-specific issue row uses **IGN · full Player ID**. If the IGN could not be extracted after login, the report says `IGN unavailable`.
- Repeat observations show the number of distinct reset dates on which that account was flagged.
- This section does not turn an otherwise successful claim run into a claim failure and does not alter the operational `Need attention` count.
- The section explains that the portal cannot prove who performed the claim.

## Owner workflow

Use the full Player ID and IGN in the email to check the account. Compare its observations across reset dates before deciding whether the behaviour is repeated. Any removal from `players.csv` is a deliberate owner decision; the bot does not perform disciplinary actions.

