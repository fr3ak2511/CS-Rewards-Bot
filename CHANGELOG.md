# Patch changelog

## PP popup lifecycle and verification

- Added a tightly scoped safe-dismiss rule for the portal reward-success dialog's exact `Continue` button.
- Kept generic Continue, purchases, claims, confirmations and ambiguous dialogs blocked.
- Normalised changing PP action/status labels when comparing pre-click and post-click card identity.
- Required a unique delivered-card identity match when exact signatures differ.
- Added regression tests for reward popup dismissal, generic Continue blocking, additional action blocking, PP Claim-to-Delivered identity transition, wrong-card rejection, and ambiguous identity rejection.
