import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WorkflowBranchSafetyTests(unittest.TestCase):
    def test_claimer_is_guarded_at_preflight_and_job_level(self):
        workflow = (ROOT / ".github" / "workflows" / "schedule.yml").read_text(encoding="utf-8")
        self.assertIn("selectedBranch !== 'prod'", workflow)
        self.assertIn("github.ref_name == 'prod'", workflow)
        self.assertIn("SELECTED_BRANCH: ${{ github.ref_name }}", workflow)
        self.assertIn("selected_branch != 'prod'", workflow)

    def test_regression_workflow_covers_stable_and_short_lived_branches(self):
        workflow = (ROOT / ".github" / "workflows" / "event-tests.yml").read_text(encoding="utf-8")
        for branch in ("prod", "dev", "test", "'feature/**'", "'fix/**'", "'chore/**'"):
            self.assertIn(branch, workflow)


if __name__ == "__main__":
    unittest.main()
