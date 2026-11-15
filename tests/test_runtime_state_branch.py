import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / ".github" / "scripts" / "runtime_state_branch.sh"
STATE_FILES = ("claim_history.json", "bot_meta.json", "grenade_balance_baselines.json", "portal_claim_audit.json")


def git(repo: Path, *args: str, env=None) -> str:
    result = subprocess.run(["git", *args], cwd=repo, env=env, text=True,
                            capture_output=True, check=False)
    if result.returncode:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stdout}\n{result.stderr}")
    return result.stdout.strip()


def run_helper(workspace: Path, mode: str, env=None, check=True):
    return subprocess.run(
        ["bash", str(workspace / ".github" / "scripts" / "runtime_state_branch.sh"), mode],
        cwd=workspace, env=env, text=True, capture_output=True, check=check,
    )


class RuntimeStateBranchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.remote = self.root / "remote.git"
        self.source = self.root / "source"
        self.workspace = self.root / "workspace"
        git(self.root, "init", "--bare", str(self.remote))
        self.source.mkdir()
        git(self.source, "init")
        git(self.source, "config", "user.name", "Test Owner")
        git(self.source, "config", "user.email", "test@example.invalid")
        (self.source / ".github" / "scripts").mkdir(parents=True)
        shutil.copy2(HELPER, self.source / ".github" / "scripts" / HELPER.name)
        initial = {
            "claim_history.json": {"origin": "prod", "records": [1]},
            "bot_meta.json": {"origin": "prod", "last_full_run": {"accounts_processed": 35}},
            "grenade_balance_baselines.json": {"origin": "prod", "baselines": {"A": 3}},
            "portal_claim_audit.json": {"origin": "prod", "events": []},
        }
        for name, value in initial.items():
            (self.source / name).write_text(json.dumps(value), encoding="utf-8")
        (self.source / "players.csv").write_text("player_id\nA\n", encoding="utf-8")
        (self.source / "README.md").write_text("production source\n", encoding="utf-8")
        git(self.source, "add", ".")
        git(self.source, "commit", "-m", "seed fixture")
        git(self.source, "branch", "-M", "prod")
        git(self.source, "remote", "add", "origin", str(self.remote))
        git(self.source, "push", "-u", "origin", "prod")
        git(self.source, "checkout", "-b", "runtime-state")
        git(self.source, "push", "-u", "origin", "runtime-state")
        git(self.source, "checkout", "prod")
        git(self.root, "clone", "--branch", "prod", str(self.remote), str(self.workspace))

    def tearDown(self):
        self.temp.cleanup()

    def test_load_and_save_use_only_runtime_state_and_preserve_prod(self):
        (self.workspace / "bot_meta.json").write_text(
            json.dumps({"origin": "stale-prod"}), encoding="utf-8")
        result = run_helper(self.workspace, "load")
        self.assertEqual(result.returncode, 0, result.stderr)
        loaded = json.loads((self.workspace / "bot_meta.json").read_text(encoding="utf-8"))
        self.assertEqual(loaded["origin"], "prod")

        for index, name in enumerate(STATE_FILES):
            updated = {"origin": "updated-runtime-state", "sequence": index + 1}
            (self.workspace / name).write_text(json.dumps(updated), encoding="utf-8")

        env = os.environ.copy()
        env["GITHUB_WORKSPACE"] = str(self.workspace)
        env["RUNNER_TEMP"] = str(self.root / "runner-temp")
        env["GITHUB_RUN_ID"] = "12345"
        result = run_helper(self.workspace, "save", env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        for name in STATE_FILES:
            state_text = git(self.root, "--git-dir", str(self.remote), "show", f"refs/heads/runtime-state:{name}")
            self.assertEqual(json.loads(state_text)["origin"], "updated-runtime-state")
            prod_text = git(self.root, "--git-dir", str(self.remote), "show", f"refs/heads/prod:{name}")
            self.assertEqual(json.loads(prod_text)["origin"], "prod")

        changed = set(git(self.root, "--git-dir", str(self.remote), "diff-tree",
                          "--no-commit-id", "--name-only", "-r",
                          "refs/heads/runtime-state").splitlines())
        self.assertEqual(changed, set(STATE_FILES))

    def test_load_fails_closed_when_runtime_state_branch_is_missing(self):
        git(self.root, "--git-dir", str(self.remote), "update-ref", "-d", "refs/heads/runtime-state")
        result = run_helper(self.workspace, "load", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("runtime-state", result.stderr)
        meta = json.loads((self.workspace / "bot_meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["origin"], "prod")


if __name__ == "__main__":
    unittest.main()
