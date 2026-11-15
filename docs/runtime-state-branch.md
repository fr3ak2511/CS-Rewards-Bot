# Runtime state branch

## Purpose

Keep production source and the bot's durable state on separate refs so the
`prod` branch can require pull requests for human changes without relying on a
GitHub Actions bypass actor. Production code and roster remain on `prod`.
The four mutable runtime JSON files are authoritative on `runtime-state`.

## Authoritative files

- `claim_history.json` — per-player reward claim and cooldown history.
- `bot_meta.json` — last-run/full-roster coverage, streak continuity, display
  name cache/history, promotional follow-ups and operational metadata.
- `grenade_balance_baselines.json` — account/cycle progression balance baselines.
- `portal_claim_audit.json` — evidence of pre-existing portal claims.

These files must move as one state set. Never initialize them as empty files,
copy development-branch versions over them, or reconstruct them from report
summaries.

## Branch responsibilities

- `prod`: source of truth for production code, workflow definitions,
  `players.csv`, configuration and regression-tested releases.
- `runtime-state`: authoritative durable JSON state only for the claimer.
  The workflow updates exactly the four files listed above. Do not develop
  application code on this branch.
- `fix/*`, `chore/*`, `feature/*`: short-lived PR branches. Production
  workflow changes must be reviewed and merged into `prod` through a PR.

The `runtime-state` branch was initially seeded from production HEAD
`105ffe5f5549970591ce84b7b9eb6299211cbb58` on 15 November 2026. That was a
starting copy, not the final cutover snapshot.

## Workflow behaviour after the cutover

1. The production workflow checks out code and `players.csv` from `prod`.
2. Before preflight coverage decisions or the Python claimer run, it fetches
   `runtime-state` and overlays the four authoritative JSON files into the
   workspace. All four files must exist and parse as JSON; unavailable or
   malformed state stops the claim path rather than silently starting fresh.
3. The native primary wait reads the latest `bot_meta.json` from
   `runtime-state` and the current roster from `prod`.
4. Immediate recovery reads the completed run's metadata from
   `runtime-state`, not the source run's code branch.
5. After processing, the workflow creates a temporary Git worktree at the
   latest `runtime-state` head, copies only the four updated JSON files into
   it, commits only those paths and pushes a normal fast-forward update to
   `runtime-state`. It never pushes runtime commits to `prod`.
6. The existing `rewards-claimer` concurrency group remains the single-writer
   guard. No parallel claim owner is introduced.

The save helper retries a non-fast-forward race by fetching and rebasing. It
never force-pushes. A conflict or failed push remains a visible failed step.
The temporary worktree is removed on exit.

## One-time cutover — mandatory pre-merge gate

The branch's initial snapshot becomes stale as long as the existing production
workflow continues to commit runtime state to `prod`. Before merging the
runtime-state separation PR:

1. Wait until the latest primary, promotional and recovery activity has
   finished. Confirm there is no active claimer job.
2. Read the four current runtime files from the latest `prod` head and
   synchronize their exact contents to the same four paths on `runtime-state`.
   Preserve every byte of the current JSON data; do not rebuild or normalize
   claim history.
3. Re-read all four files on `runtime-state` and compare their blob SHAs or
   exact contents against the source snapshot from `prod`.
4. Confirm `prod` has not advanced its runtime files during that synchronization.
   If it has, repeat the snapshot from the newer production head.
5. Only then merge the PR. From that point onward, the workflow reads and writes
   runtime state exclusively on `runtime-state`.

Do not merge the PR while the state branch is behind the latest production
runtime snapshot. Do not manually start a claim-capable workflow during the
cutover. The scheduled primary, promotional dispatches and immediate recovery
must all be idle during the snapshot/merge boundary.

## Validation after merge

- Confirm the workflow logs show the four JSON files loaded from
  `runtime-state` before preflight/run-mode logic.
- Confirm the `Commit runtime state safely` step reports a commit to
  `runtime-state` (or that no state changes were needed).
- Verify `prod` did not receive a runtime-state commit.
- Compare all four files on `runtime-state` after the run and confirm they
  contain the latest recorded run.
- Confirm the immediate recovery workflow reads the same branch.
- Only after those checks should the `Protect production` ruleset be activated.

## Protection model

The production ruleset should target only `prod`, require a PR and the
`Reward regression tests` status check, block force pushes and deletion, and
remain free of a broad human bypass. It is currently saved but Disabled.

Because the Actions app is not offered in the repository's bypass-actor picker,
this design avoids needing an Actions bypass on `prod`. After migration, consider
a separate ruleset for `runtime-state` that blocks deletion and force pushes
but does not require PRs or restrict ordinary fast-forward updates. That would
protect the state ref without blocking the bot's normal commits.

## Rollback

If the first post-cutover run cannot load or save state, stop further claim runs.
Do not create empty files or switch the workflow back to writing stale state on
`prod`. Diagnose the branch contents and workflow logs first. A rollback PR
can restore the previous workflow only after the latest state is reconciled
between the refs and the source of truth is explicitly selected.
