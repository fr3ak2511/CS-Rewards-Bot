# Review notes — v3.3.1

Targeted changes on top of the locally integrated v3.3.0:
1. Preserve confirmed event claims in an append-only `claim_ledger`, keyed by configured cycle and tier threshold.
2. Revalidate the live Claim button immediately before clicking (visibility, enabled state, exact action label, and exact card threshold).
3. Make the event points label configurable (`points_label`); Halloween is `Lost Souls`.
4. Wait briefly after Trick & Treat claims on promotional-only runs before checking the event balance.
5. Hide Halloween-specific counters/title once the event expires; retain the Trick & Treat email subsection.
6. Add two offline tests for the pre-click button guard.

No live claims were executed. No repository, workflow, roster, or metadata files were modified.
