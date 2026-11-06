CS REWARDS BOT — BRANCH STANDARDIZATION PATCH

TARGET BRANCH: chore/branch-standardization
BASE: prod

IMPORTANT
- Apply only to chore/branch-standardization.
- Use a clean local clone. The script refuses to run if there are uncommitted changes.
- The script does not commit or push anything.
- It does not modify players.csv, bot_meta.json, claim_history.json, or production schedule timings.

HOW TO APPLY (WINDOWS)
1. Download and extract this ZIP somewhere outside your local repository folder, e.g. Downloads.
2. Open GitHub Desktop and select the CS-Rewards-Bot repository.
3. Checkout the branch chore/branch-standardization.
4. Make sure GitHub Desktop shows no local changes.
5. Double-click Apply_Branch_Standardization.bat from the extracted folder.
6. When prompted, paste the full path to your local CS-Rewards-Bot clone folder.
7. Review the listed changes.
8. Run: python -m unittest discover -s tests -v
9. Run: git diff --check
10. Review `git status --short` and the diff. Commit to chore/branch-standardization only after tests pass.

WHAT IT CHANGES
- Makes .github/workflows/schedule.yml production-only on prod using multiple fail-closed checks.
- Updates the regression test workflow to run on prod/dev/test and feature/fix/chore branches.
- Adds BRANCHING.md and a static workflow-safety regression test.
- Archives the expired one-off Halloween closeout workflow outside .github/workflows.
- Removes the obsolete runtime-state sync workflow that targeted the retired popup-guard branch.

No GitHub branch rename, deletion, merge, or push is performed by this package.
