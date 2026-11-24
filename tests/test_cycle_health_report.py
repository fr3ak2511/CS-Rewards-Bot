import unittest
from datetime import date

from cycle_health_report import (
    build_cycle_health_overview,
    render_cycle_health_overview,
)


class CycleHealthReportTests(unittest.TestCase):
    def setUp(self):
        self.roster = [f"PLAYER{i:04d}" for i in range(1, 37)]

    def test_full_overview_keeps_core_grenade_and_progression_scopes_separate(self):
        results = [
            {
                "pid": pid,
                "grenade_balance": {
                    "status": "pass",
                    "current_count": 72,
                    "expected_minimum": 72,
                },
            }
            for pid in self.roster[:11]
        ]
        queue = {}
        slots = ["Primary Run", "Immediate Progression Recovery", "Promotional Check #1"]
        for index, pid in enumerate(self.roster[11:]):
            queue[pid] = {
                "status": "deferred",
                "last_reason": (
                    "Popup/overlay safety guard blocked inspection"
                    if index == 24 else
                    "Queued Progression inspection found no currently visible claimable reward"
                ),
                "attempted_slots": slots,
                "last_attempt_slot": "Promotional Check #1",
            }

        snapshot = build_cycle_health_overview(
            results,
            configured_player_ids=self.roster,
            reset_date="2026-11-24",
            grenade_state={"schema_version": 1, "cycles": {}},
            daily_cycle_summary={
                "reset_date": "2026-11-24",
                "configured_player_count": 36,
                "accounts_complete": 36,
                "accounts_pending": 0,
                "primary_run_label": "Primary Run",
                "primary_run_timestamp": "2026-11-24T05:35:38+05:30",
            },
            progression_queue=queue,
        )

        self.assertEqual(snapshot["core_cycle"]["complete"], 36)
        self.assertEqual(snapshot["core_cycle"]["pending"], 0)
        self.assertEqual(snapshot["grenade"]["passed_today"], 11)
        self.assertEqual(snapshot["grenade"]["not_checked_today"], 25)
        self.assertEqual(snapshot["progression"]["pending"], 25)
        self.assertEqual(snapshot["progression"]["no_visible_claimable_card"], 24)
        self.assertEqual(snapshot["progression"]["blocked_by_overlay"], 1)
        self.assertEqual(snapshot["progression"]["most_common_slots_used"], 3)

    def test_old_historical_pass_is_not_counted_as_pass_today(self):
        state = {
            "schema_version": 1,
            "cycles": {
                "2026-11": {
                    "players": {
                        "PLAYER0001": {
                            "baseline_status": "active",
                            "baseline_count": 0,
                            "tracking_start_date": "2026-11-01",
                            "last_observed_date": "2026-11-22",
                            "last_observed_count": 66,
                            "last_observed_status": "pass",
                        }
                    }
                }
            },
        }
        snapshot = build_cycle_health_overview(
            [],
            configured_player_ids=["PLAYER0001"],
            reset_date="2026-11-24",
            grenade_state=state,
            daily_cycle_summary=None,
            progression_queue={},
        )
        self.assertEqual(snapshot["grenade"]["passed_today"], 0)
        self.assertEqual(snapshot["grenade"]["not_checked_today"], 1)

    def test_current_below_minimum_and_regression_are_not_hidden(self):
        results = [
            {"pid": "PLAYER0001", "grenade_balance": {"status": "below_minimum", "current_count": 20}},
            {"pid": "PLAYER0002", "grenade_balance": {"status": "counter_regressed", "current_count": 30}},
        ]
        snapshot = build_cycle_health_overview(
            results,
            configured_player_ids=["PLAYER0001", "PLAYER0002"],
            reset_date="2026-11-24",
            grenade_state={"schema_version": 1, "cycles": {}},
            daily_cycle_summary=None,
            progression_queue={},
        )
        self.assertEqual(snapshot["grenade"]["below_minimum_today"], 1)
        self.assertEqual(snapshot["grenade"]["counter_regressed_today"], 1)
        self.assertEqual(snapshot["grenade"]["not_checked_today"], 0)

    def test_rendered_overview_explains_empty_scan_is_not_completion(self):
        html = render_cycle_health_overview({
            "reset_date": "2026-11-24",
            "configured": 36,
            "core_cycle": {
                "available": True, "complete": 36, "pending": 0,
                "source_run_label": "Primary Run",
                "source_timestamp": "2026-11-24T05:35:38+05:30",
            },
            "grenade": {
                "observed_today": 11, "passed_today": 11,
                "below_minimum_today": 0, "counter_regressed_today": 0,
                "other_status_today": 0, "not_checked_today": 25,
            },
            "progression": {
                "pending": 25, "no_visible_claimable_card": 24,
                "blocked_by_overlay": 1, "other_pending": 0,
                "most_common_slots_used": 3,
                "accounts_at_most_common_slots_used": 25,
                "max_slots_per_reset": 4,
                "latest_attempt_slot": "Promotional Check #1",
            },
        })
        self.assertIn("36/36 accounts complete", html)
        self.assertIn("11 PASS today", html)
        self.assertIn("0 other / baseline-pending", html)
        self.assertIn("25 not checked today", html)
        self.assertIn("25/36 inspections pending", html)
        self.assertIn("24 with no visible claimable card", html)
        self.assertIn("1 blocked by overlay", html)
        self.assertIn("not treated as proof", html)


if __name__ == "__main__":
    unittest.main()
