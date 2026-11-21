# Deferred Progression Inspection Queue

## Purpose

A Progression inspection can be deferred by the popup/overlay safety guard before any claim click is dispatched. That is not a failed claim and must not be written into `claim_history.json` as `unconfirmed`, or used to make `full_claim_coverage` pending.

This queue records the separate obligation to inspect Progression again. It does not itself authorise a click.

## Runtime state

The queue is stored inside `bot_meta.json` under `progression_deferred_inspections`, which is already persisted by the normal runtime-state branch workflow.

Each entry is keyed by exact Player ID and records:

- status: `deferred` or `claim_unconfirmed`;
- first and latest defer timestamps, source run label and run ID;
- a concise, popup-context-free reason;
- optional exact `target_card_signature` plus `requires_exact_target`;
- per-reset attempted backup slots and a cumulative attempt count.

Resolved entries are moved to `progression_deferred_inspection_history`; the archive is capped at 50 entries. This is separate from claim history, full-claim coverage, retry candidates and failed-player IDs.

## Lifecycle

1. Every normal full-claim result is checked for `progression_deferred`. A deferred inspection is queued automatically, including its source run ID.
2. On a promotional backup slot, the claimer checks for a queue entry only after confirming the account has complete full-claim coverage for the current reset.
3. The queued Progression inspection is executed inside the account's existing authenticated browser session. Normal promotional discovery and event checks still run; no second browser login is added.
4. A pure deferred inspection with no earlier click target may use the existing card-bound Progression claim routine. A previously dispatched but unconfirmed click must carry its exact saved card signature. The routine will inspect only that exact target.
5. If the popup guard still blocks the inspection, the queue remains. If the exact card is missing or ambiguous, the routine refuses to click another card. A confirmed same-card Delivered state resolves the target. A fully completed inspection with no claimable reward also resolves a pure deferred item.
6. A login or processing failure does not resolve the queue.

## Retry limits and isolation

- Each account can consume a given backup slot once per reset date.
- The queue permits at most four distinct backup-slot attempts per reset date. Attempt markers are written to `bot_meta.json` before browser processing, so a repeated slot cannot cause another Progression click.
- If a previously dispatched claim is still unconfirmed, it remains an exact-target queue item. A queued Progression target is not immediately retried a second time in the same slot. Immediate recovery for unrelated Daily/Store/Event targets remains available.
- The queue does not change `full_claim_coverage`, `claim_history`, daily streak, full-claim retry IDs, or the normal recovery observer.
- A dedicated Daily-only recovery remains strictly Daily-only and does not service the Progression queue. The next eligible promotional backup slot can service the queue.
- The existing popup/overlay guard and same-card Delivered confirmation remain mandatory. There is no JavaScript click fallback or broad click when target identity is uncertain.

## Reporting

When an inspection remains deferred, the result carries `progression_deferred` and the existing email/Copy Report warning added in PR #51 surfaces it as review work. If a click was dispatched but not confirmed, the normal unconfirmed-claim signal remains visible while the separate queue retains its exact target.

## Regression coverage

`tests/test_progression_inspection_queue.py` covers state separation, sanitised reasons, slot deduplication, per-reset caps, exact-target retention, resolution/archive behaviour and non-resolution after login failure.

`tests/test_workflow_branch_safety.py` asserts that the queue is integrated into backup processing without adding it to the broad full-claim recovery predicate.
