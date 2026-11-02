# CS Rewards Fix — PP popup and verification patch

## Target branch

**`fix/halloween-popup-guard` only.** Do not upload these files to `main` or `feature/halloween-event-rewards`.

## Files in this patch

Replace these four existing files with the complete files in this ZIP:

1. `reward_popup_guard.py`
2. `progression_claim_safety.py`
3. `tests/test_reward_popup_guard.py`
4. `tests/test_progression_claim_safety.py`

The two root-level Python files must stay at the repository root. The two test files must stay inside the existing `tests/` directory.

## What the patch changes

- Allows the exact `Continue` control only when it is the sole non-close action inside a clearly labelled `Your rewards! xN` success dialog. A generic Continue button, purchase/claim/confirm controls, an ambiguous dialog, or a dialog with another action remains blocked.
- Prefers a normal explicit close control over the reward Continue action when both exist.
- Makes PP card identity comparison ignore the changing `Claim` / `Delivered` state label, while requiring one unique delivered-card identity match. A different or ambiguous delivered card cannot confirm the claim.
- Preserves the existing no-double-click rule: a dispatched but unconfirmed claim is never clicked a second time in the same visit.

## Manual update steps in GitHub Web

1. Open the repository: `https://github.com/fr3ak2511/CS-Rewards-Bot`.
2. Switch the branch dropdown to **`fix/halloween-popup-guard`**.
3. Open each of the four paths listed above.
4. Use the pencil **Edit this file** control. Replace the entire file with the matching extracted ZIP file contents. Do not paste only a code fragment.
5. Commit the changes to **`fix/halloween-popup-guard`**. Suggested commit message:

   `fix: dismiss reward success popup and confirm PP card transition`

6. After all four files are committed, open Actions and verify that **Halloween and reward regression tests** passes on `fix/halloween-popup-guard`.

Do not manually run a real one-player claim test immediately after this upload. The previous test ID `5899855DAA167B36` has already had its 6 x20 PP and Halloween 1,300-point rewards delivered. First inspect the regression-test result; then choose an ID that still has a genuinely claimable PP reward for the next live test.

## Do not change in this patch

Leave `master_claimer.py`, `players.csv`, `claim_history.json`, `bot_meta.json`, `.github/workflows/schedule.yml`, and `.github/workflows/recovery.yml` untouched. In particular, keep the current roster order and all runtime-state files intact.

## Known separate reporting issue

The previous live test exposed a reporting inconsistency: `master_claimer.py` can print `CLAIM_TEST_RESULT=SUCCESS` while `CLAIM_TEST_STATUS=Partial` if at least one other reward was claimed. This bundle intentionally does not replace the large central runner file. Correcting that summary marker (and adding a dedicated PP screenshot to the test artifact) remains a separate, reporting-only patch before production promotion.

## Local verification performed for this ZIP

Command:

```bash
python -m unittest tests.test_reward_popup_guard tests.test_progression_claim_safety -v
```

Result: **15 tests passed**.

This is targeted unit-test coverage for the changed modules, not a claim that the full repository suite or a live portal test has already passed with these new files. Do not merge to `main` until the branch regression workflow and a fresh live PP claim/portal-confirmation test both pass.
