import csv
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import master_claimer


class ProgressionDiagnosticOnlyModeTests(unittest.TestCase):
    def setUp(self):
        master_claimer._PROGRESSION_INSPECTION_DIAGNOSTICS_BY_PLAYER.clear()

    def tearDown(self):
        master_claimer._PROGRESSION_INSPECTION_DIAGNOSTICS_BY_PLAYER.clear()

    def _write_roster(self, path, player_ids):
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["player_id", "has_loyalty"])
            for player_id in player_ids:
                writer.writerow([player_id, "true"])

    def _diagnostic_snapshot(self):
        return {
            "page_path": "/progression-program",
            "ready_state": "complete",
            "viewport": {"width": 1440, "height": 900},
            "body_text_length": 1200,
            "page_valid": True,
            "visible_claimable_count": 1,
            "visible_delivered_control_count": 2,
            "candidate_container_count": 1,
            "carousel_containers": [{
                "index": 0,
                "tag": "div",
                "class_name": "progression-rail",
                "rect": {"x": 0, "y": 100, "width": 1200, "height": 300},
                "client_width": 1200,
                "scroll_width": 2400,
                "scroll_left": 0,
                "max_scroll_left": 1200,
                "at_start": True,
                "at_end": False,
                "direct_child_count": 25,
                "visible_child_count": 25,
                "captured_cards": [{
                    "tag": "div",
                    "class_name": "reward-card",
                    "text_snippet": "Milestone 62 x20 Claim",
                    "button_labels": ["Claim"],
                    "exact_status_text_hints": ["Claim"],
                    "milestone_number_hints": [62],
                    "rect": {"x": 10, "y": 120, "width": 200, "height": 180},
                }],
            }],
        }

    def test_read_only_mode_emits_artifact_without_claim_or_state_mutation(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            roster_path = root_path / "players.csv"
            output_dir = root_path / "artifacts"
            self._write_roster(roster_path, ["PLAYER0001", "PLAYER0002"])
            drivers = [MagicMock(), MagicMock()]

            def snapshot(driver):
                value = {
                    "valid_page": True,
                    "claimable_count": 1,
                    "delivered_count": 2,
                    "inspection_diagnostics": self._diagnostic_snapshot(),
                }
                master_claimer._record_progression_inspection_snapshot(driver, value)
                return value

            with (
                patch.object(master_claimer, "PLAYER_ID_FILE", str(roster_path)),
                patch.object(master_claimer, "RUN_ARTIFACTS_DIR", str(output_dir)),
                patch.object(master_claimer, "create_driver", side_effect=drivers),
                patch.object(master_claimer, "login_to_hub", return_value=True),
                patch.object(master_claimer, "bypass_cloudflare"),
                patch.object(master_claimer, "_progression_claim_snapshot", side_effect=snapshot),
                patch.object(master_claimer, "_scroll_progression_carousel", return_value=False),
                patch.object(master_claimer, "claim_progression_program_rewards", side_effect=AssertionError("claim path called")) as claim,
                patch.object(master_claimer, "update_claim_history", side_effect=AssertionError("history path called")) as history,
                patch.object(master_claimer, "load_bot_meta", side_effect=AssertionError("runtime state loaded")) as load_meta,
                patch.object(master_claimer, "time") as mocked_time,
                patch.object(sys, "argv", ["master_claimer.py", "--progression-diagnostic-only"]),
            ):
                mocked_time.sleep = lambda _seconds: None
                result = master_claimer.run_progression_diagnostic_only()

            self.assertEqual(result, 0)
            claim.assert_not_called()
            history.assert_not_called()
            load_meta.assert_not_called()
            self.assertTrue(all(driver.quit.called for driver in drivers))

            artifact = json.loads((output_dir / "progression_diagnostic_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(artifact["run_mode"], "PROGRESSION_DIAGNOSTIC_ONLY")
            self.assertEqual(artifact["configured_player_count"], 2)
            self.assertEqual(artifact["authenticated_progression_pages_observed"], 2)
            self.assertFalse(artifact["claim_actions_permitted"])
            self.assertEqual(artifact["claim_clicks_dispatched"], 0)
            self.assertFalse(artifact["claim_history_modified"])
            self.assertFalse(artifact["progression_queue_modified"])
            self.assertFalse(artifact["runtime_state_persisted"])
            self.assertFalse(artifact["completeness_established"])
            self.assertFalse(artifact["delivery_status_established"])
            self.assertEqual(artifact["snapshot_count"], 2)
            self.assertNotIn("PLAYER0001", str(artifact))
            self.assertNotIn("PLAYER0002", str(artifact))

    def test_login_failure_is_reported_without_opening_progression_page(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            roster_path = root_path / "players.csv"
            output_dir = root_path / "artifacts"
            self._write_roster(roster_path, ["PLAYER0001"])
            driver = MagicMock()

            with (
                patch.object(master_claimer, "PLAYER_ID_FILE", str(roster_path)),
                patch.object(master_claimer, "RUN_ARTIFACTS_DIR", str(output_dir)),
                patch.object(master_claimer, "create_driver", return_value=driver),
                patch.object(master_claimer, "login_to_hub", return_value=False),
                patch.object(master_claimer, "_progression_claim_snapshot") as snapshot,
                patch.object(master_claimer, "time") as mocked_time,
                patch.object(sys, "argv", ["master_claimer.py", "--progression-diagnostic-only"]),
            ):
                mocked_time.sleep = lambda _seconds: None
                result = master_claimer.run_progression_diagnostic_only()

            self.assertEqual(result, 0)
            snapshot.assert_not_called()
            driver.get.assert_not_called()
            artifact = json.loads((output_dir / "progression_diagnostic_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(artifact["account_results"][0]["login_status"], "login_failed")
            self.assertEqual(artifact["account_results"][0]["page_status"], "not_reached")

    def test_diagnostic_cli_dispatches_before_claim_reset_guard(self):
        with (
            patch.object(sys, "argv", ["master_claimer.py", "--progression-diagnostic-only"]),
            patch.object(master_claimer, "run_progression_diagnostic_only", return_value=0) as diagnostic,
            patch.object(master_claimer, "enforce_claim_reset_guard", side_effect=AssertionError("claim reset guard should not run")),
        ):
            self.assertEqual(master_claimer.main(), 0)
        diagnostic.assert_called_once_with()

    def test_claim_mode_environment_flag_rejects_diagnostic_before_browser_start(self):
        with (
            patch.dict(os.environ, {"MANUAL_PROGRESSION_RECOVERY": "true"}),
            patch.object(master_claimer, "create_driver") as create_driver,
            patch.object(sys, "argv", ["master_claimer.py", "--progression-diagnostic-only"]),
        ):
            self.assertEqual(master_claimer.run_progression_diagnostic_only(), 2)
        create_driver.assert_not_called()

    def test_duplicate_roster_is_rejected_before_browser_start(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            roster_path = root_path / "players.csv"
            output_dir = root_path / "artifacts"
            self._write_roster(roster_path, ["PLAYER0001", "PLAYER0001"])
            with (
                patch.object(master_claimer, "PLAYER_ID_FILE", str(roster_path)),
                patch.object(master_claimer, "RUN_ARTIFACTS_DIR", str(output_dir)),
                patch.object(master_claimer, "create_driver") as create_driver,
                patch.object(sys, "argv", ["master_claimer.py", "--progression-diagnostic-only"]),
            ):
                self.assertEqual(master_claimer.run_progression_diagnostic_only(), 2)
            create_driver.assert_not_called()


if __name__ == "__main__":
    unittest.main()
