# Progression / Grenade Balance Validator

## Purpose

Capture the cumulative Grenade counter from the authenticated Progression Program page and compare it with a conservative account-specific minimum. This is a read-only observation after the existing reward workflow; it does not click Progression Claim buttons or modify claim history.

## What the portal counter means

The Reward Chain banner shows **collected Grenades / next milestone**. For example, \`34 / 56\` means 34 Grenades have been collected in this chain and the next milestone is 56. Claiming a delivered milestone reward does not subtract from the cumulative count.

The account's visible current count is the authoritative observed value. The validator does not infer it by adding up reward cards.

## Minimum rule

Each completed 05:30 IST daily reset cycle earns a minimum of three Grenades from the three established daily Store rewards.

For an active baseline:

    expected minimum = baseline count + (3 × eligible reset dates)

Purchase bonuses and other extra Grenade gifts increase the observed count but are not included in the minimum. They appear as surplus.

Reset dates are counted inclusively from the tracking start date through the run's current 05:30 IST reset date.

## Account onboarding

1. The normal claim path checks all three daily Store cards.
2. An available card is claimed and counted only after portal confirmation. A card already shown as Claimed/on cooldown is also treated as confirmed for the daily Store check.
3. After all three cards are confirmed, the bot captures the Progression counter.
4. That observed count becomes the account's baseline for the joining day.
5. Minimum tracking starts on the following 05:30 IST reset date.

If the Store check is inconclusive, the baseline remains pending. It is not fabricated from an expected daily total.

## Initial November 2026 seed

Git history shows 34 accounts were already on the configured roster before the November Reward Chain began. They are seeded with baseline 0 from 1 November 2026, so the minimum is 3 per completed reset date in the chain.

One account was added on 2 November 2026, after that day's 05:30 reset. No authenticated Progression counter snapshot exists from its joining-day Store check. It remains pending until a fully verified Store check and live Progression capture after rollout; that observed value becomes its baseline and tracking starts the following reset date. The unobserved 2–8 November period is explicitly not backfilled.

## Monthly chain rollover

A new Reward Chain month starts a new cycle. Accounts with a baseline in a prior cycle are treated as already present at the new cycle start and begin from zero on day 1. A newly added account in the new cycle uses the joining-day baseline procedure above.

## Statuses

- **pass** — observed balance is at or above the minimum.
- **below_minimum** — observed balance is lower than the minimum; reported as an attention item.
- **counter_regressed** — the cumulative counter decreased compared with the previous observation in the same Reward Chain; reported as an attention item.
- **pending_baseline** — all three daily Store cards have not yet been portal-confirmed for baseline creation.
- **not_validated** — the authenticated page or its Reward Chain counter could not be read reliably.

A parser failure is never interpreted as zero Grenades.

## Runtime state and safety

- Separate persistent state: \`grenade_balance_baselines.json\`.
- The scheduler runtime commit stages this file alongside the existing bot state so the baseline survives future runs.
- \`claim_history.json\`, \`bot_meta.json\`, and \`players.csv\` are not modified by the balance validator.
- No Grenade amount is inferred from \`progression.last_count\`; that remains a per-visit reward-claim count.
- The Progression snapshot reads the banner text only. It does not click controls.
- Full account IDs are stored only as keys in the private runtime state; run reports display masked account suffixes.

## Validation coverage

Regression tests cover:
- Parsing the Reward Chain fraction without confusing the profile's separate LP fraction.
- 05:30 IST reset-day boundaries.
- Three-Grenade minimum calculations and 30/31-day calendar arithmetic.
- Joining-day baseline and next-day tracking.
- Pending baselines when Store evidence is incomplete.
- Late first observation without backfilling unobserved dates.
- Counter regression and monthly rollover.
- Attention propagation into run reconciliation and the mobile-first Copy Report.
