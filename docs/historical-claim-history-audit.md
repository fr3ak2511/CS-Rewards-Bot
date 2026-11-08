# Historical claim-history audit

## Purpose

This is a one-time, read-only reconstruction of the claim activity the bot recorded in Git history. It is separate from the run-level arithmetic checks in run_reconciliation.py.

The utility reads the first-parent Git history of claim_history.json and bot_meta.json plus the current players.csv roster. It does not write to, repair, reformat, or replace any of those files.

## Evidence types

claim_history.json is a mutable state file. For most reward categories it stores only the latest last_claim timestamp, not a complete append-only event list. The audit compares successive committed snapshots and records each distinct timestamp transition visible in Git.

The output separates:
- **Bot-confirmed transitions**: a new last_claim value whose record status is claimed.
- **Portal observations**: portal_observed_at values, reported separately and never counted as bot-made claims.
- **Event-program ledger deliveries**: entries in event_rewards[*].claim_ledger, classified as delivered only when ledger verification says delivered.
- **Unconfirmed or uncertain records**: retained as review evidence, not promoted to successful claims.

The first committed snapshot is a baseline. A timestamp already present there is reportable, but the audit cannot establish when that state first entered Git before the baseline. Activity never represented in a committed snapshot cannot be reconstructed.

## Run locally

From a full Git clone with production history fetched:

    python historical_claim_audit.py --ref origin/prod --output-dir historical-audit-output

The Git ref is read-only. The output directory receives:
- historical_claim_audit.md — human-readable summary.
- historical_claim_audit.csv — event-level rows for filtering.
- historical_claim_audit.json — complete structured report, including run comparisons and review flags.

Player IDs are masked to their final four characters in all report formats.

## Run in GitHub Actions

1. Open the repository's **Actions** tab.
2. Select **Historical Claim History Audit**.
3. Select **Run workflow**.
4. Select branch audit/historical-claim-history (or a later branch containing this workflow).
5. Run the workflow. It reads origin/prod and performs no browser login or reward action.
6. Download the generated historical-claim-audit artifact.

The workflow uses fetch-depth: 0 so the production commit history is available to the Git-based reader. Its only output is the uploaded audit artifact.

## Interpreting run-counter comparisons

For each historical bot_meta.json last-run record, the audit derives the run window from its timestamp and duration, then compares claim-history transitions inside that window with available per_type counters.

A mismatch is a review flag, not automatic proof of a missing or duplicate reward. Differences can result from incomplete historical snapshots, legacy counter naming, or data recorded outside the metadata run window. Review the underlying commits and run records before drawing conclusions.

## Safety and acceptance criteria

- Never modify production claim_history.json, bot_meta.json, or players.csv during this audit.
- Preserve all historical accounts, including accounts no longer present in the configured roster.
- Do not infer missing claims or expected balances.
- Do not treat portal-observed cooldown as a bot-made claim.
- Do not treat progression.last_count as a lifetime Grenade balance; it is a count associated with the recorded visit.
- Treat malformed snapshots, invalid timestamps, cleared claims, timestamp regressions, and run-counter mismatches as review items.
- The tool must fail rather than silently use an invalid Git ref or invalid current roster.
