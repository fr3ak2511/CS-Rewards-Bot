# Repository Branching Strategy

## Permanent branches

- `prod` — production source of truth and repository default branch. Scheduled reward claiming, immediate recovery and runtime-state commits are allowed only here.
- `dev` — the single development and validation branch. Feature work, integration, automated regression tests and safe diagnostics happen here.

The legacy `test` branch is no longer part of the ongoing development or release flow. Retire it only after the two-branch transition has been completed and verified.

## Short-lived branches

- `feature/<short-description>` — new functionality.
- `fix/<short-description>` — defect corrections.
- `chore/<short-description>` — repository maintenance.

Create short-lived branches from `dev`. Keep each change focused, run regression tests and review the change before integrating it into `dev`. Delete the working branch after its pull request merges.

## Development and release flow

1. Develop and integrate source changes on `dev`.
2. Run the complete regression suite on the pull request and on `dev`; perform safe diagnostics and review the resulting reports.
3. When the release is approved, open one release pull request from `dev` into `prod`.
4. Review the release diff. It must not replace `players.csv`, `bot_meta.json` or `claim_history.json` with development copies.
5. Merge into `prod` only after regression checks pass and preservation of current production runtime state is confirmed.

Use Squash and merge for short-lived feature/fix pull requests into `dev`. Use Create a merge commit for the permanent `dev` to `prod` release so the two permanent branch histories retain their ancestry.

## Production safety

- `.github/workflows/schedule.yml` and `.github/workflows/recovery.yml` must remain production-only. Their branch, event, timestamp and recovery-candidate gates must fail closed.
- Never run the full-roster claimer or live reward-claim workflow from `dev` or a short-lived feature branch.
- Regression tests are safe on development branches and pull requests.
- `players.csv`, `bot_meta.json` and `claim_history.json` are runtime-critical. Do not copy stale versions between branches.
- Production automation commits runtime state back to `prod`. Any future branch protection must explicitly account for those GitHub Actions commits.

## Retired event workflow

The one-off Halloween 2026 closeout workflow has expired. Its workflow definition is retained under `docs/archive/` outside the active GitHub Actions workflow directory. Reusable event-closeout code and tests remain in the repository.
