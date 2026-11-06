# Repository Branching Strategy

## Permanent branches

- `prod` — production source of truth and repository default branch. Scheduled reward claiming and automatic recovery are allowed only here.
- `dev` — integration branch. Feature and fix pull requests are integrated here before release validation.
- `test` — QA/release-candidate branch. Run regression tests and safe diagnostics here; the production claimer must not execute.

## Short-lived branches

- `feature/<short-description>` — new functionality, for example `feature/email-v2`.
- `fix/<short-description>` — defect corrections.
- `chore/<short-description>` — repository maintenance.

Create feature/fix branches from `dev`. Keep each branch focused and delete it after its pull request merges, unless it is intentionally retained for active work.

## Release flow

1. Feature/fix branch → pull request into `dev`.
2. Run the full regression suite on the pull request and integration branch.
3. Promote the reviewed `dev` state to `test`.
4. Validate the release candidate on `test` using regression tests and safe diagnostics.
5. Open a release pull request from `test` into `prod`.
6. Merge into `prod` only after review and successful tests.

## Production safety

- `.github/workflows/schedule.yml` is production-only. Its preflight, job condition and Python run-mode gate require the selected branch to be `prod`.
- Never run the full-roster claimer or live reward-claim workflow from `dev`, `test` or a short-lived feature branch.
- Regression tests are safe to run on development branches and pull requests.
- `players.csv`, `bot_meta.json` and `claim_history.json` are runtime-critical. Do not replace them with stale copies from another branch.
- Production automation commits runtime state back to `prod`. Any future branch protection must explicitly account for those GitHub Actions commits.

## Retired event workflow

The one-off Halloween 2026 closeout workflow has expired. Its workflow definition is retained under `docs/archive/` for reference, outside the active GitHub Actions workflow directory. Reusable event-closeout code and tests remain in the repository.
