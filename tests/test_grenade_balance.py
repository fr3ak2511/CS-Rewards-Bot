import unittest
from datetime import date, datetime, timedelta

from grenade_balance import (
    daily_reset_date,
    eligible_cycle_days,
    evaluate_progression_balance,
    parse_progression_balance_panel,
)


class GrenadeBalanceTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 8, 6, 0)
        self.snapshot = {
            "valid_page": True,
            "cycle_id": "2026-11",
            "cycle_title": "November Reward Chain",
            "cycle_start_date": "2026-11-01",
            "current_count": 34,
            "next_milestone": 56,
            "observed_at_ist": "2026-11-08T06:00:00+05:30",
        }

    def test_parse_uses_reward_chain_fraction_not_profile_lp_fraction(self):
        panel = (
            "November Reward Chain "
            "CLAIM YOUR GRENADES AND GET YOUR PROGRESSION REWARDS! "
            "22 days 12 hours 10 minutes "
            "Make purchases, earn loyalty points "
            "34 / 56"
        )
        parsed = parse_progression_balance_panel(panel, observed_at=self.now)
        self.assertEqual(parsed["cycle_id"], "2026-11")
        self.assertEqual(parsed["current_count"], 34)
        self.assertEqual(parsed["next_milestone"], 56)

    def test_parser_rejects_unscoped_or_missing_chain_counter(self):
        self.assertIsNone(parse_progression_balance_panel(
            "Iron LP 20 / 40", observed_at=self.now
        ))
        self.assertIsNone(parse_progression_balance_panel(
            "November Reward Chain CLAIM YOUR GRENADES — loading",
            observed_at=self.now,
        ))

    def test_month_rollover_uses_previous_year_for_previous_month_chain(self):
        now = datetime(2027, 1, 2, 6, 0)
        parsed = parse_progression_balance_panel(
            "December Reward Chain 34 / 56", observed_at=now
        )
        self.assertEqual(parsed["cycle_id"], "2026-12")

    def test_daily_reset_date_uses_0530_ist_boundary(self):
        before = datetime(2026, 11, 8, 5, 29)
        after = datetime(2026, 11, 8, 5, 31)
        self.assertEqual(daily_reset_date(before), date(2026, 11, 7))
        self.assertEqual(daily_reset_date(after), date(2026, 11, 8))

    def test_existing_account_minimum_is_three_per_cycle_day(self):
        state = {
            "schema_version": 1,
            "cycles": {
                "2026-11": {
                    "players": {
                        "ACCOUNT_A": {
                            "baseline_status": "active",
                            "baseline_count": 0,
                            "baseline_date": "2026-11-01",
                            "tracking_start_date": "2026-11-01",
                            "baseline_source": "cycle_start_existing_account",
                        }
                    }
                }
            },
        }
        _, result = evaluate_progression_balance(
            self.snapshot, state, "ACCOUNT_A",
            reset_date=date(2026, 11, 8),
            is_new_account=False,
            store_rewards_fully_checked=True,
        )
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["eligible_days"], 8)
        self.assertEqual(result["expected_minimum"], 24)
        self.assertEqual(result["surplus"], 10)
        self.assertEqual(result["next_milestone"], 56)
        self.assertEqual(result["remaining_to_next_milestone"], 22)

    def test_existing_account_below_minimum_is_flagged(self):
        snapshot = {**self.snapshot, "current_count": 22}
        state = {
            "schema_version": 1,
            "cycles": {
                "2026-11": {
                    "players": {
                        "ACCOUNT_A": {
                            "baseline_status": "active",
                            "baseline_count": 0,
                            "tracking_start_date": "2026-11-01",
                            "baseline_source": "cycle_start_existing_account",
                        }
                    }
                }
            },
        }
        _, result = evaluate_progression_balance(
            snapshot, state, "ACCOUNT_A",
            reset_date=date(2026, 11, 8),
            is_new_account=False,
            store_rewards_fully_checked=True,
        )
        self.assertEqual(result["status"], "below_minimum")
        self.assertEqual(result["expected_minimum"], 24)
        self.assertEqual(result["deficit"], 2)

    def test_join_day_baseline_starts_tracking_from_the_following_day(self):
        state = {"schema_version": 1, "cycles": {}}
        join_snapshot = {**self.snapshot, "current_count": 100}
        updated, first = evaluate_progression_balance(
            join_snapshot, state, "NEW_ACCOUNT",
            reset_date=date(2026, 11, 16),
            is_new_account=True,
            store_rewards_fully_checked=True,
        )
        self.assertEqual(first["status"], "pass")
        self.assertEqual(first["baseline_count"], 100)
        self.assertEqual(first["tracking_start_date"], "2026-11-17")
        # A large pre-joining balance is simply the observed baseline. It is not
        # reconstructed or attributed to bot activity before the join date.
        next_day_snapshot = {
            **self.snapshot,
            "current_count": 103,
            "observed_at_ist": "2026-11-17T06:00:00+05:30",
        }
        _, next_day = evaluate_progression_balance(
            next_day_snapshot, updated, "NEW_ACCOUNT",
            reset_date=date(2026, 11, 17),
            is_new_account=False,
            store_rewards_fully_checked=True,
        )
        self.assertEqual(next_day["expected_minimum"], 103)
        self.assertEqual(next_day["eligible_days"], 1)
        self.assertEqual(next_day["status"], "pass")

    def test_new_account_baseline_waits_for_all_three_store_rewards(self):
        updated, result = evaluate_progression_balance(
            self.snapshot,
            {"schema_version": 1, "cycles": {}},
            "NEW_ACCOUNT",
            reset_date=date(2026, 11, 8),
            is_new_account=True,
            store_rewards_fully_checked=False,
        )
        self.assertEqual(result["status"], "pending_baseline")
        self.assertIsNone(result.get("expected_minimum"))
        entry = updated["cycles"]["2026-11"]["players"]["NEW_ACCOUNT"]
        self.assertEqual(entry["baseline_status"], "pending_baseline")

    def test_late_rollout_baseline_does_not_backfill_joining_days(self):
        state = {
            "schema_version": 1,
            "cycles": {
                "2026-11": {
                    "players": {
                        "LATE_ACCOUNT": {
                            "baseline_status": "pending_baseline",
                            "baseline_source": "late_joined_before_tracker_rollout",
                            "join_date": "2026-11-02",
                            "first_seen_date": "2026-11-02",
                        }
                    }
                }
            },
        }
        updated, result = evaluate_progression_balance(
            {**self.snapshot, "current_count": 34}, state, "LATE_ACCOUNT",
            reset_date=date(2026, 11, 9),
            is_new_account=False,
            store_rewards_fully_checked=True,
        )
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["baseline_count"], 34)
        self.assertEqual(result["tracking_start_date"], "2026-11-10")
        self.assertTrue(result["historical_gap"])
        self.assertEqual(
            updated["cycles"]["2026-11"]["players"]["LATE_ACCOUNT"]["baseline_date"],
            "2026-11-09",
        )

    def test_counter_decrease_within_same_chain_is_flagged(self):
        state = {
            "schema_version": 1,
            "cycles": {
                "2026-11": {
                    "players": {
                        "ACCOUNT_A": {
                            "baseline_status": "active",
                            "baseline_count": 20,
                            "tracking_start_date": "2026-11-08",
                            "baseline_source": "test",
                            "last_observed_count": 34,
                        }
                    }
                }
            },
        }
        _, result = evaluate_progression_balance(
            {**self.snapshot, "current_count": 33}, state, "ACCOUNT_A",
            reset_date=date(2026, 11, 8),
            is_new_account=False,
            store_rewards_fully_checked=True,
        )
        self.assertEqual(result["status"], "counter_regressed")

    def test_accounts_present_in_prior_cycle_start_new_cycle_from_zero(self):
        state = {
            "schema_version": 1,
            "cycles": {
                "2026-10": {
                    "players": {
                        "ACCOUNT_A": {
                            "baseline_status": "active",
                            "baseline_count": 0,
                            "tracking_start_date": "2026-10-01",
                            "baseline_source": "cycle_start_existing_account",
                        }
                    }
                }
            },
        }
        updated, result = evaluate_progression_balance(
            {**self.snapshot, "cycle_id": "2026-11", "cycle_start_date": "2026-11-01"},
            state, "ACCOUNT_A",
            reset_date=date(2026, 11, 8),
            is_new_account=False,
            store_rewards_fully_checked=True,
        )
        self.assertEqual(result["baseline_source"], "existing_account_at_cycle_start")
        self.assertEqual(result["expected_minimum"], 24)
        self.assertIn("2026-11", updated["cycles"])

    def test_invalid_page_does_not_change_baseline_state(self):
        state = {"schema_version": 1, "cycles": {}}
        updated, result = evaluate_progression_balance(
            {"valid_page": False}, state, "ACCOUNT_A",
            reset_date=date(2026, 11, 8),
            is_new_account=False,
            store_rewards_fully_checked=True,
        )
        self.assertEqual(result["status"], "not_validated")
        self.assertEqual(updated["cycles"], {})

    def test_eligible_cycle_days_is_inclusive_and_handles_reversed_ranges(self):
        self.assertEqual(
            eligible_cycle_days("2026-11-17", "2026-11-20"),
            4,
        )
        self.assertEqual(
            eligible_cycle_days("2026-11-21", "2026-11-20"),
            0,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
