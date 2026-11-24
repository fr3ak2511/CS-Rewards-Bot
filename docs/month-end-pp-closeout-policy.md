# Month-End Progression Closeout Policy

## Operating calendar (IST)

- 05:35: normal full-roster Primary Run (existing production schedule).
- After Primary and Immediate Recovery complete: wait until 07:00 for full month-end closeout.
- 07:00: full 36-account balance and reached-milestone audit.
- 09:00: targeted retry of unresolved accounts and exact milestone targets.
- 11:00: final targeted retry before the publication deadline.
- 12:30: mandatory final closeout report, whether COMPLETE or ATTENTION_REQUIRED.

The policy uses local calendar arithmetic and handles 28/29/30/31-day months.
This PR adds no cron trigger, workflow dispatch, browser run or claim action.

## Completion rules

A month-end closeout is COMPLETE only when:
1. The configured roster matches the expected 36 unique IDs.
2. Every account has a fresh balance dated on the closeout date.
3. Every milestone at or below each account's observed balance is present in
   the per-cycle milestone ledger.
4. Every such milestone is confirmed Delivered by a same-card portal confirmation.

A Claimable milestone is actionable but not complete. Locked, missing, ambiguous,
stale or Unverified evidence requires attention. Grenade balance alone never proves
a reward was delivered. The closeout policy evaluates evidence supplied by the
authenticated account adapter; it never clicks or mutates state.
