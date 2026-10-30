# CS Rewards Bot — Halloween Event Rewards integration (v2)

This package is based on the user's locally integrated `master_claimer.py` v3.3.0 and the reviewed upstream v3.2.0 source. It keeps the permanent grenade-based Progression Program function unchanged and adds a separate configurable point-programme module.

## Email reporting

The existing email section combines Store Trick & Treat observations with active limited-time event rewards. For each active event/account it reports:

- current event points and the configured points label;
- next reward threshold and delivered tier count;
- each reward tier confirmed as claimed during that run;
- any event claim or verification failures.

Confirmed event tiers are included in overall claim totals and run-summary JSON. Event claim failures are marked for recovery. When a configured event passes its expiry timestamp, its page is no longer checked, its event-specific rows/counters disappear from future emails, and the remaining section is labelled **Trick & Treat Rewards**. The event's historical claim ledger is retained in `claim_history.json`.

## Claim safeguards

- The module reads the account-specific event page after login.
- A tier is eligible only when the visible balance meets its threshold and the portal exposes an enabled Claim/Claim all control.
- Immediately before clicking, the module rechecks that the exact button is visible, enabled, labelled Claim/Claim all, and belongs to the expected point-threshold card.
- A click is counted only after the portal displays Delivered for that tier.
- An unconfirmed click is not blindly repeated in the same run.
- Confirmed tiers are appended to a durable per-player/per-program/per-cycle claim ledger. A later progress check cannot overwrite earlier confirmed claims.

## Files

- `master_claimer.py` — updated integration (v3.3.1).
- `event_rewards.py` — reusable UI-driven event tier checker/claimer.
- `event_rewards_config.json` — current Halloween 2026 event configuration.
- `tests/test_event_rewards.py` — offline tests using a simulated driver; no real account is logged in.

## Halloween 2026 configuration

- Page: `https://hub.vertigogames.co/halloween-2026`
- Programme ID: `pgp_eLiQHDMchen`
- Points label: `Lost Souls`
- Expiry: `1793826000` Unix seconds = **5 November 2026, 2:30 AM IST**
- Maximum claims per account/pass: 20 (the inspected ladder contains 14 tiers)

To configure a future event, update `event_rewards_config.json` with its name, slug, programme ID, points label, start/end timestamps and cycle. Set the programme `enabled` flag to false to suppress it without deleting the reusable module.

## Install and verify

Place the files at repository root (the test file under `tests/`). Do not overwrite `players.csv`, `bot_meta.json`, or `.github/workflows/schedule.yml`.

Run:

```bash
python -m py_compile master_claimer.py event_rewards.py
python -m unittest discover -s tests -v
```

The tests are offline-only. Passing them does not constitute a live portal test. The first live run must be monitored before treating the integration as production-proven.
