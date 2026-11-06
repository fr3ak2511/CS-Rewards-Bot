#!/usr/bin/env python3
"""Apply the safe branch/workflow standardization patch to chore/branch-standardization.

Run from the root of a clean local clone of CS-Rewards-Bot.
This script deliberately does not commit, push, or touch runtime state.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

EXPECTED_BRANCH = "chore/branch-standardization"
ROOT = Path.cwd()


def run_git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, text=True, capture_output=True, check=False
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def replace_exact(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Safety check failed for {label}: expected 1 match, found {count}")
    return text.replace(old, new, 1)


def main() -> int:
    if not (ROOT / ".git").exists():
        raise RuntimeError("Run this script from the root of your local Git clone.")
    branch = run_git("branch", "--show-current")
    if branch != EXPECTED_BRANCH:
        raise RuntimeError(
            f"Wrong branch: currently on '{branch}'. Switch to '{EXPECTED_BRANCH}' first."
        )
    status = run_git("status", "--porcelain")
    if status:
        raise RuntimeError(
            "Working tree is not clean. Commit or stash your existing local changes, "
            "then run this script again. No files were changed."
        )

    schedule_path = ROOT / ".github/workflows/schedule.yml"
    tests_path = ROOT / ".github/workflows/event-tests.yml"
    closeout_path = ROOT / ".github/workflows/halloween-closeout.yml"
    sync_path = ROOT / ".github/workflows/sync-runtime-state.yml"
    archive_path = ROOT / "docs/archive/halloween-closeout-2026.yml"
    guide_path = ROOT / "BRANCHING.md"
    guard_test_path = ROOT / "tests/test_workflow_branch_safety.py"

    required = [schedule_path, tests_path, closeout_path, sync_path]
    missing = [str(p.relative_to(ROOT)) for p in required if not p.is_file()]
    if missing:
        raise RuntimeError("Expected source files are missing: " + ", ".join(missing))
    if archive_path.exists() or guide_path.exists() or guard_test_path.exists():
        raise RuntimeError(
            "One or more destination files already exist. Review them manually; "
            "the script will not overwrite them. No files were changed."
        )

    schedule = schedule_path.read_text(encoding="utf-8")
    schedule = replace_exact(
        schedule,
        """            if (context.eventName !== 'schedule') {
              out(true, `${context.eventName} request`);
              return;
            }
""",
        """            const selectedBranch = context.ref.replace(/^refs\\/heads\\//, '');
            if (selectedBranch !== 'prod') {
              out(false, `production-only claimer; selected branch '${selectedBranch}' is not 'prod'`);
              return;
            }

            if (context.eventName !== 'schedule') {
              out(true, `${context.eventName} request on prod`);
              return;
            }
""",
        "preflight production-only gate",
    )
    schedule = replace_exact(
        schedule,
        "    if: github.event_name == 'schedule' && github.event.schedule == '30 21 * * *' && needs.preflight.outputs.should_run == 'true'\n",
        "    if: github.ref_name == 'prod' && github.event_name == 'schedule' && github.event.schedule == '30 21 * * *' && needs.preflight.outputs.should_run == 'true'\n",
        "primary dispatcher job guard",
    )
    schedule = replace_exact(
        schedule,
        "    if: >-\n      always() &&\n      needs.preflight.outputs.should_run == 'true' &&\n",
        "    if: >-\n      github.ref_name == 'prod' &&\n      always() &&\n      needs.preflight.outputs.should_run == 'true' &&\n",
        "claimer job guard",
    )
    schedule = replace_exact(
        schedule,
        "          DISPATCH_TYPE: ${{ github.event.action }}\n",
        "          DISPATCH_TYPE: ${{ github.event.action }}\n          SELECTED_BRANCH: ${{ github.ref_name }}\n",
        "selected branch environment",
    )
    schedule = replace_exact(
        schedule,
        "          dispatch_type = os.environ.get('DISPATCH_TYPE', '')\n          out_path = os.environ['GITHUB_OUTPUT']\n",
        "          dispatch_type = os.environ.get('DISPATCH_TYPE', '')\n          selected_branch = os.environ.get('SELECTED_BRANCH', '')\n          out_path = os.environ['GITHUB_OUTPUT']\n",
        "selected branch in Python run-mode gate",
    )
    schedule = replace_exact(
        schedule,
        "          if event == 'workflow_dispatch':\n              emit(True, False, 'manual workflow_dispatch request; run the selected manual mode')\n",
        """          if selected_branch != 'prod':
              emit(False, False, f"production-only claimer; selected branch '{selected_branch or 'unknown'}' is not 'prod'")
              raise SystemExit(0)

          if event == 'workflow_dispatch':
              emit(True, False, 'manual workflow_dispatch request on prod; run the selected manual mode')
""",
        "Python fail-closed production-only gate",
    )

    tests_workflow = """name: Reward regression tests

on:
  push:
    branches:
      - prod
      - dev
      - test
      - 'feature/**'
      - 'fix/**'
      - 'chore/**'
    paths:
      - 'event_closeout.py'
      - 'event_rewards.py'
      - 'event_rewards_config.json'
      - 'master_claimer.py'
      - 'progression_claim_safety.py'
      - 'reward_popup_guard.py'
      - 'requirements.txt'
      - 'tests/**'
      - '.github/workflows/event-tests.yml'
      - '.github/workflows/schedule.yml'

  pull_request:
    branches:
      - prod
      - dev
      - test
    paths:
      - 'event_closeout.py'
      - 'event_rewards.py'
      - 'event_rewards_config.json'
      - 'master_claimer.py'
      - 'progression_claim_safety.py'
      - 'reward_popup_guard.py'
      - 'requirements.txt'
      - 'tests/**'
      - '.github/workflows/event-tests.yml'
      - '.github/workflows/schedule.yml'

  workflow_dispatch:

permissions:
  contents: read

jobs:
  reward-regression-tests:
    name: Reward regression tests
    runs-on: ubuntu-latest
    timeout-minutes: 10

    steps:
      - name: Check out repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: pip
          cache-dependency-path: requirements.txt

      - name: Install project dependencies
        run: python -m pip install -r requirements.txt

      - name: Run complete repository test suite
        run: python -m unittest discover -s tests -v
"""

    branch_guide = """# Repository Branching Strategy

## Stable branches

- `prod` — production source of truth and repository default branch. Scheduled reward claiming and automatic recovery are allowed only here.
- `dev` — integration branch. Features and fixes are integrated here before release validation.
- `test` — QA/release-candidate branch. Regression tests and explicitly safe diagnostics belong here; the production claimer must not execute.

## Short-lived working branches

Use descriptive prefixes:

- `feature/<short-description>` for new features, e.g. `feature/email-v2`.
- `fix/<short-description>` for defect corrections.
- `chore/<short-description>` for repository maintenance.

Keep each branch focused and delete it after its pull request has merged, unless it is intentionally retained for active work.

## Change flow

1. Create a feature/fix branch from `dev`.
2. Open a pull request into `dev`; run the full regression suite.
3. Promote the reviewed integration state from `dev` to `test`.
4. Validate on `test`; use regression tests and read-only diagnostics.
5. Open the release pull request from `test` into `prod`.
6. Merge into `prod` only after successful tests and review.

## Production safety

- `.github/workflows/schedule.yml` is production-only. Its preflight, job condition, and Python run-mode gate require the selected branch to be `prod`.
- Never run the full-roster claimer or live reward-claim workflow from `dev`, `test`, or a short-lived feature branch.
- Regression tests are safe to run on development branches and pull requests.
- `players.csv`, `bot_meta.json`, and `claim_history.json` are runtime-critical. Do not replace them with stale copies from another branch.
- Production automation commits runtime state back to `prod`. Any future branch protection must explicitly account for those GitHub Actions commits.

## Workflow retirement

The one-off Halloween 2026 closeout automation has expired. Its reusable closeout code and tests remain in the repository; the one-off workflow is archived under `docs/archive/` and is no longer an active GitHub Actions workflow.
"""

    guard_test = """import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WorkflowBranchSafetyTests(unittest.TestCase):
    def test_claimer_preflight_is_production_only(self):
        workflow = (ROOT / ".github" / "workflows" / "schedule.yml").read_text(encoding="utf-8")
        self.assertIn("selectedBranch !== 'prod'", workflow)
        self.assertIn("github.ref_name == 'prod'", workflow)
        self.assertIn("SELECTED_BRANCH: ${{ github.ref_name }}", workflow)
        self.assertIn("selected_branch != 'prod'", workflow)

    def test_regression_workflow_covers_the_stable_and_short_lived_branches(self):
        workflow = (ROOT / ".github" / "workflows" / "event-tests.yml").read_text(encoding="utf-8")
        for branch in ("prod", "dev", "test", "'feature/**'", "'fix/**'", "'chore/**'"):
            self.assertIn(branch, workflow)


if __name__ == "__main__":
    unittest.main()
"""

    # Prepare all output in memory first. No file is written until every source
    # match and every destination check has passed.
    archived_workflow = closeout_path.read_text(encoding="utf-8")
    planned = {
        schedule_path: schedule,
        tests_path: tests_workflow,
        archive_path: archived_workflow,
        guide_path: branch_guide,
        guard_test_path: guard_test,
    }

    for path, content in planned.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")

    closeout_path.unlink()
    sync_path.unlink()

    changed = [
        ".github/workflows/schedule.yml",
        ".github/workflows/event-tests.yml",
        "BRANCHING.md",
        "tests/test_workflow_branch_safety.py",
        "docs/archive/halloween-closeout-2026.yml",
        "D .github/workflows/halloween-closeout.yml",
        "D .github/workflows/sync-runtime-state.yml",
    ]
    print("Branch-standardization patch applied locally.")
    print("No commit or push was performed.")
    print("Runtime state files were not touched.")
    print("\nReview these changes:")
    for item in changed:
        print(f"  {item}")
    print("\nNext: python -m unittest discover -s tests -v")
    print("Then review 'git diff' and commit to chore/branch-standardization.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print("No automatic commit or push was attempted.", file=sys.stderr)
        raise SystemExit(2)
