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
        queue["REMOVED_PLAYER"] = {
            "status": "deferred",
            "last_reason": "Queued Progression inspection found no currently visible claimable reward",
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
        self.assertEqual(snapshot["progression"]["claim_unconfirmed"], 0)
        self.assertEqual(snapshot["progression"]["other_pending"], 0)
        self.assertEqual(snapshot["progression"]["outside_current_roster"], 1)
        self.assertEqual(snapshot["progression"]["most_common_slots_used"], 3)

    def test_register_lists_every_id_uses_observation_date_and_keeps_delivery_separate(self):
        roster = [f"PLAYER{i:04d}" for i in range(1, 6)]
        results = [{
            "pid": "PLAYER0001",
            "grenade_balance": {
                "status": "pass",
                "current_count": 72,
                "expected_minimum": 72,
                "next_milestone": 123,
            },
        }]
        state = {
            "schema_version": 1,
            "cycles": {
                "2026-11": {
                    "players": {
                        "PLAYER0002": {
                            "baseline_status": "active",
                            "baseline_count": 0,
                            "tracking_start_date": "2026-11-01",
                            "last_observed_count": 63,
                            "last_observed_date": "2026-11-22",
                            "last_observed_at_ist": "2026-11-22T06:00:00+05:30",
                            "last_observed_expected_minimum": 66,
                            "last_observed_status": "below_minimum",
                            "last_observed_next_milestone": 65,
                            "last_observed_remaining_to_next_milestone": 2,
                        },
                        "PLAYER0003": {
                            "baseline_status": "active",
                            "baseline_count": 0,
                            "tracking_start_date": "2026-11-01",
                            "last_observed_count": 88,
                            "last_observed_date": "2026-11-23",
                            "last_observed_expected_minimum": 69,
                            "last_observed_status": "status_not_retained",
                            "last_observed_next_milestone": 123,
                        },
                        "PLAYER0004": {
                            "baseline_status": "active",
                            "baseline_count": 0,
                            "tracking_start_date": "2026-11-01",
                        },
                        "PLAYER0005": {
                            "baseline_status": "pending_baseline",
                            "baseline_source": "first_observation_after_rollout",
                        },
                    }
                }
            },
        }
        snapshot = build_cycle_health_overview(
            results,
            configured_player_ids=roster,
            reset_date="2026-11-24",
            grenade_state=state,
            daily_cycle_summary=None,
            progression_queue={},
            player_display_names={
                "PLAYER0001": "One",
                "PLAYER0002": "Two",
                "PLAYER0003": "Three",
                "PLAYER0004": "Four",
                "PLAYER0005": "Five",
            },
        )
        accounts = snapshot["grenade"]["accounts"]
        self.assertEqual(len(accounts), 5)
        by_id = {item["player_id"]: item for item in accounts}
        self.assertEqual(by_id["PLAYER0001"]["comparison"], "at_expected")
        self.assertEqual(by_id["PLAYER0001"]["milestone_count_reached_by_balance"], 9)
        self.assertEqual(by_id["PLAYER0001"]["next_milestone_from_reference"], 123)
        self.assertEqual(by_id["PLAYER0001"]["delivery_status"], "not_verified_by_balance")
        self.assertEqual(by_id["PLAYER0002"]["comparison"], "below_expected")
        self.assertEqual(by_id["PLAYER0002"]["expected_minimum_at_observation"], 66)
        self.assertEqual(by_id["PLAYER0002"]["expected_minimum_as_of"], 72)
        self.assertEqual(by_id["PLAYER0002"]["observation_age_days"], 2)
        self.assertFalse(by_id["PLAYER0002"]["fresh_today"])
        self.assertEqual(by_id["PLAYER0003"]["comparison"], "above_expected")
        self.assertEqual(by_id["PLAYER0003"]["difference_at_observation"], 19)
        self.assertEqual(by_id["PLAYER0003"]["validation_status"], "status_not_retained")
        self.assertEqual(by_id["PLAYER0004"]["comparison"], "not_observed")
        self.assertEqual(by_id["PLAYER0004"]["expected_minimum_as_of"], 72)
        self.assertEqual(by_id["PLAYER0005"]["comparison"], "baseline_pending")
        self.assertEqual(snapshot["grenade"]["accounts_at_expected"], 1)
        self.assertEqual(snapshot["grenade"]["accounts_below_expected"], 1)
        self.assertEqual(snapshot["grenade"]["accounts_above_expected"], 1)
        self.assertEqual(snapshot["grenade"]["accounts_without_observation"], 1)
        self.assertEqual(snapshot["grenade"]["accounts_baseline_pending"], 1)
        self.assertEqual(snapshot["milestone_thresholds"], [
            3, 6, 12, 15, 18, 56, 59, 62, 65, 123, 126, 129, 141, 147,
            186, 189, 192, 195, 400, 600, 680, 750, 880, 950, 1000,
        ])

        rendered = render_cycle_health_overview(snapshot)
        self.assertIn("Permanent PP milestone reference", rendered)
        self.assertIn("All-account Grenade balance register", rendered)
        self.assertIn("Player ID: PLAYER0001", rendered)
        self.assertIn("Player ID: PLAYER0005", rendered)
        self.assertIn("Expected today: 72", rendered)
        self.assertIn("2 day(s) old", rendered)
        self.assertIn("Reward delivery: not established by balance", rendered)
        self.assertEqual(rendered.count("Player ID: PLAYER000"), 5)

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
                "blocked_by_overlay": 1, "claim_unconfirmed": 0,
                "other_pending": 0, "outside_current_roster": 0,
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
        self.assertIn("0 with claim outcome unconfirmed", html)
        self.assertIn("0 other pending", html)
        self.assertIn("not treated as proof that no Progression reward exists", html)

    def test_unconfirmed_queue_status_is_separate_and_latest_slot_is_chronological(self):
        queue = {
            self.roster[0]: {
                "status": "claim_unconfirmed",
                "last_reason": "A previously attempted Progression card has no safe saved identity",
                "attempted_slots": ["Primary Run"],
                "last_attempt_at": "2026-11-24T10:00:00+05:30",
                "last_attempt_slot": "Primary Run",
            },
            self.roster[1]: {
                "status": "deferred",
                "last_reason": "Popup/overlay safety guard blocked Progression inspection",
                "attempted_slots": ["Primary Run", "Promotional Check #1"],
                "last_attempt_at": "2026-11-24T12:00:00+05:30",
                "last_attempt_slot": "Primary Run",
            },
            self.roster[2]: {
                "status": "deferred",
                "last_reason": "Unexpected Progression page state; inspection was deferred",
                "attempted_slots": ["Primary Run"],
                "last_attempt_at": "2026-11-24T13:00:00+05:30",
                "last_attempt_slot": "Promotional Check #1",
            },
            "REMOVED_PLAYER": {
                "status": "deferred",
                "last_reason": "Queued Progression inspection found no currently visible claimable reward",
                "attempted_slots": ["Primary Run"],
                "last_attempt_at": "2026-11-24T14:00:00+05:30",
                "last_attempt_slot": "Manual Run",
            },
        }
        snapshot = build_cycle_health_overview(
            [],
            configured_player_ids=self.roster,
            reset_date="2026-11-24",
            grenade_state={"schema_version": 1, "cycles": {}},
            daily_cycle_summary=None,
            progression_queue=queue,
        )
        progression = snapshot["progression"]
        self.assertEqual(progression["pending"], 3)
        self.assertEqual(progression["claim_unconfirmed"], 1)
        self.assertEqual(progression["blocked_by_overlay"], 1)
        self.assertEqual(progression["other_pending"], 1)
        self.assertEqual(progression["no_visible_claimable_card"], 0)
        self.assertEqual(progression["outside_current_roster"], 1)
        self.assertEqual(progression["latest_attempt_slot"], "Promotional Check #1")
        self.assertEqual(progression["latest_attempt_at"], "2026-11-24T13:00:00+05:30")

    def test_current_login_failure_overrides_stale_empty_snapshot_reason(self):
        pid = self.roster[0]
        queue = {
            pid: {
                "status": "deferred",
                "last_reason": "Queued Progression inspection found no currently visible claimable reward",
                "last_outcome_category": "empty_scan",
                "same_outcome_count": 1,
                "attempted_slots": ["Primary Run", "Immediate Progression Recovery"],
                "last_attempt_slot": "Immediate Progression Recovery",
                "last_attempt_at": "2026-11-24T06:00:00+05:30",
            }
        }
        result = {
            "pid": pid,
            "status": "Login Failed",
            "progression_reinspection_attempted": True,
            "progression_deferred": True,
            "progression_checked": False,
        }
        snapshot = build_cycle_health_overview(
            [result], configured_player_ids=[pid], reset_date="2026-11-24",
            grenade_state={"schema_version": 1, "cycles": {}},
            daily_cycle_summary=None, progression_queue=queue,
        )
        self.assertEqual(snapshot["progression"]["login_failed"], 1)
        self.assertEqual(snapshot["progression"]["no_visible_claimable_card"], 0)

    def test_manual_attention_and_exhausted_retry_budget_are_reported(self):
        queue = {
            self.roster[0]: {
                "status": "deferred",
                "last_outcome_category": "overlay_blocked",
                "same_outcome_count": 3,
                "manual_attention_required": True,
                "attempted_slots": ["Primary Run", "Immediate Progression Recovery", "Promotional Check #1", "Promotional Check #2"],
                "last_attempt_slot": "Promotional Check #2",
                "last_attempt_at": "2026-11-24T18:05:00+05:30",
            }
        }
        snapshot = build_cycle_health_overview(
            [], configured_player_ids=[self.roster[0]], reset_date="2026-11-24",
            grenade_state={"schema_version": 1, "cycles": {}},
            daily_cycle_summary=None, progression_queue=queue,
        )
        pp = snapshot["progression"]
        self.assertEqual(pp["manual_attention_required"], 1)
        self.assertEqual(pp["retry_exhausted"], 1)
        self.assertEqual(pp["repeated_outcome_count"], 1)
        html = render_cycle_health_overview(snapshot)
        self.assertIn("1 account(s) require manual attention", html)
        self.assertIn("Primary Run of the next reset", html)

    def test_inconsistent_cycle_counts_are_not_marked_available(self):
        snapshot = build_cycle_health_overview(
            [],
            configured_player_ids=self.roster,
            reset_date="2026-11-24",
            grenade_state={"schema_version": 1, "cycles": {}},
            daily_cycle_summary={
                "reset_date": "2026-11-24",
                "configured_player_count": 36,
                "accounts_complete": 37,
                "accounts_pending": 0,
                "primary_run_label": "Primary Run",
            },
            progression_queue={},
        )
        self.assertFalse(snapshot["core_cycle"]["available"])
        self.assertEqual(snapshot["core_cycle"]["complete"], 0)
        self.assertEqual(snapshot["core_cycle"]["pending"], 36)

    def test_raw_cycle_record_rebuilds_counts_from_saved_coverage(self):
        roster = self.roster[:3]
        coverage = {
            roster[0]: {"reset_date": "2026-11-24", "status": "complete", "retry_required": False},
            roster[1]: {"reset_date": "2026-11-24", "status": "complete", "retry_required": True},
            roster[2]: {"reset_date": "2026-11-23", "status": "complete", "retry_required": False},
        }
        snapshot = build_cycle_health_overview(
            [],
            configured_player_ids=roster,
            reset_date="2026-11-24",
            grenade_state={"schema_version": 1, "cycles": {}},
            daily_cycle_summary={
                "reset_date": "2026-11-24",
                "configured_player_count": 3,
                "primary_run_label": "Primary Run",
                "primary_run_timestamp": "2026-11-24T05:35:00+05:30",
            },
            progression_queue={},
            full_claim_coverage=coverage,
        )
        self.assertTrue(snapshot["core_cycle"]["available"])
        self.assertEqual(snapshot["core_cycle"]["complete"], 1)
        self.assertEqual(snapshot["core_cycle"]["pending"], 2)
        self.assertTrue(snapshot["core_cycle"]["coverage_derived"])
        html = render_cycle_health_overview(snapshot)
        self.assertIn("1/3 accounts complete", html)
        self.assertIn("re-derived from saved full-claim coverage records", html)

    def test_string_cycle_counts_are_not_accepted_as_verified_evidence(self):
        snapshot = build_cycle_health_overview(
            [],
            configured_player_ids=self.roster,
            reset_date="2026-11-24",
            grenade_state={"schema_version": 1, "cycles": {}},
            daily_cycle_summary={
                "reset_date": "2026-11-24",
                "configured_player_count": 36,
                "accounts_complete": "36",
                "accounts_pending": 0,
                "primary_run_label": "Primary Run",
            },
            progression_queue={},
        )
        self.assertFalse(snapshot["core_cycle"]["available"])
        self.assertEqual(snapshot["core_cycle"]["complete"], 0)
        self.assertEqual(snapshot["core_cycle"]["pending"], 36)

    def test_rendered_overview_explains_unconfirmed_and_stale_queue_entries(self):
        html = render_cycle_health_overview({
            "reset_date": "2026-11-24",
            "configured": 36,
            "core_cycle": {"available": False},
            "grenade": {
                "observed_today": 0, "passed_today": 0,
                "below_minimum_today": 0, "counter_regressed_today": 0,
                "other_status_today": 0, "not_checked_today": 36,
            },
            "progression": {
                "pending": 1, "no_visible_claimable_card": 0,
                "blocked_by_overlay": 0, "claim_unconfirmed": 1,
                "other_pending": 0, "outside_current_roster": 2,
            },
        })
        self.assertIn("1/36 inspections pending", html)
        self.assertIn("1 with claim outcome unconfirmed", html)
        self.assertIn("2 queued record(s) outside the current roster are excluded", html)

if __name__ == "__main__":
    unittest.main()
