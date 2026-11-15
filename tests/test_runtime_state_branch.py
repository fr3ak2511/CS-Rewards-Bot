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
        load_output = self.root / "load-output.txt"
        load_env = os.environ.copy()
        load_env["GITHUB_OUTPUT"] = str(load_output)
        result = run_helper(self.workspace, "load", env=load_env)
        self.assertEqual(result.returncode, 0, result.stderr)
        loaded = json.loads((self.workspace / "bot_meta.json").read_text(encoding="utf-8"))
        self.assertEqual(loaded["origin"], "prod")
        base_sha = load_output.read_text(encoding="utf-8").strip().split("=", 1)[1]
        self.assertRegex(base_sha, r"^[0-9a-f]{40}$")

        for index, name in enumerate(STATE_FILES):
            updated = {"origin": "updated-runtime-state", "sequence": index + 1}
            (self.workspace / name).write_text(json.dumps(updated), encoding="utf-8")

        env = os.environ.copy()
        env["GITHUB_WORKSPACE"] = str(self.workspace)
        env["RUNNER_TEMP"] = str(self.root / "runner-temp")
        env["GITHUB_RUN_ID"] = "12345"
        env["EXPECTED_RUNTIME_STATE_SHA"] = base_sha
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


    def test_save_refuses_a_stale_runtime_state_snapshot(self):
        load_output = self.root / "stale-load-output.txt"
        load_env = os.environ.copy()
        load_env["GITHUB_OUTPUT"] = str(load_output)
        result = run_helper(self.workspace, "load", env=load_env)
        self.assertEqual(result.returncode, 0, result.stderr)
        base_sha = load_output.read_text(encoding="utf-8").strip().split("=", 1)[1]

        # Simulate another writer advancing runtime-state after this job loaded it.
        git(self.source, "checkout", "runtime-state")
        (self.source / "bot_meta.json").write_text(
            json.dumps({"origin": "concurrent-writer"}), encoding="utf-8")
        git(self.source, "add", "bot_meta.json")
        git(self.source, "commit", "-m", "concurrent runtime-state update")
        git(self.source, "push", "origin", "runtime-state")
        git(self.source, "checkout", "prod")

        env = os.environ.copy()
        env["GITHUB_WORKSPACE"] = str(self.workspace)
        env["RUNNER_TEMP"] = str(self.root / "stale-runner-temp")
        env["GITHUB_RUN_ID"] = "12346"
        env["EXPECTED_RUNTIME_STATE_SHA"] = base_sha
        result = run_helper(self.workspace, "save", env=env, check=False)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("refusing a stale overwrite", result.stderr)
        remote_meta = git(self.root, "--git-dir", str(self.remote), "show",
                          "refs/heads/runtime-state:bot_meta.json")
        self.assertEqual(json.loads(remote_meta)["origin"], "concurrent-writer")
        prod_meta = git(self.root, "--git-dir", str(self.remote), "show",
                        "refs/heads/prod:bot_meta.json")
        self.assertEqual(json.loads(prod_meta)["origin"], "prod")

    def test_save_refuses_invalid_json_without_advancing_runtime_state(self):
        load_output = self.root / "invalid-load-output.txt"
        load_env = os.environ.copy()
        load_env["GITHUB_OUTPUT"] = str(load_output)
        result = run_helper(self.workspace, "load", env=load_env)
        self.assertEqual(result.returncode, 0, result.stderr)
        base_sha = load_output.read_text(encoding="utf-8").strip().split("=", 1)[1]

        (self.workspace / "bot_meta.json").write_text("{ invalid json", encoding="utf-8")
        env = os.environ.copy()
        env["GITHUB_WORKSPACE"] = str(self.workspace)
        env["RUNNER_TEMP"] = str(self.root / "invalid-runner-temp")
        env["GITHUB_RUN_ID"] = "12347"
        env["EXPECTED_RUNTIME_STATE_SHA"] = base_sha
        result = run_helper(self.workspace, "save", env=env, check=False)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not valid JSON", result.stderr)
        current = git(self.root, "--git-dir", str(self.remote), "rev-parse",
                      "refs/heads/runtime-state")
        self.assertEqual(current, base_sha)

    def test_load_fails_closed_when_runtime_state_branch_is_missing(self):
        git(self.root, "--git-dir", str(self.remote), "update-ref", "-d", "refs/heads/runtime-state")
        result = run_helper(self.workspace, "load", check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("runtime-state", result.stderr)
        meta = json.loads((self.workspace / "bot_meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["origin"], "prod")


if __name__ == "__main__":
    unittest.main()
