import unittest

from email_cycle_summary import update_daily_cycle_summary


class DailyCycleEmailSummaryTests(unittest.TestCase):
    def test_full_roster_run_starts_cycle_with_coverage_and_category_totals(self):
        meta = {
            "full_claim_coverage": {
                "PLAYER0001": {
                    "reset_date": "2026-11-10", "status": "complete",
                    "retry_required": False,
                },
                "PLAYER0002": {
                    "reset_date": "2026-11-10", "status": "pending",
                    "retry_required": True,
                },
            }
        }
        view = update_daily_cycle_summary(
            meta,
            reset_date="2026-11-10",
            run_label="Primary Run",
            configured_player_ids=["PLAYER0001", "PLAYER0002"],
            claim_totals={
                "daily": 2, "store": 5, "promotions": 1,
                "progression": 0, "loyalty": 0, "events": 0,
            },
            processed_player_ids=["PLAYER0001", "PLAYER0002"],
            run_key="run-1:1",
            run_timestamp="2026-11-10T06:30:00",
            is_full_roster_run=True,
        )
        self.assertIsNotNone(view)
        self.assertEqual(view["accounts_complete"], 1)
        self.assertEqual(view["accounts_pending"], 1)
        self.assertEqual(view["total_claims"], 8)
        self.assertEqual(view["claim_totals"]["store"], 5)
        self.assertEqual(meta["daily_cycle_email_summary"]["reset_date"], "2026-11-10")

    def test_recovery_claims_are_added_once_per_actions_attempt(self):
        meta = {
            "daily_cycle_email_summary": {
                "schema_version": 1,
                "reset_date": "2026-11-10",
                "configured_player_count": 1,
                "primary_run_label": "Primary Run",
                "primary_run_timestamp": "2026-11-10T06:00:00",
                "primary_claim_totals": {
                    "daily": 1, "store": 0, "promotions": 0,
                    "progression": 0, "loyalty": 0, "events": 0,
                },
                "recovery_claim_totals": {
                    "daily": 0, "store": 0, "promotions": 0,
                    "progression": 0, "loyalty": 0, "events": 0,
                },
                "recovery_run_keys": [],
                "recovered_player_last4": [],
            },
            "full_claim_coverage": {
                "PLAYER0001": {
                    "reset_date": "2026-11-10", "status": "complete",
                    "retry_required": False,
                }
            },
        }
        kwargs = {
            "meta": meta,
            "reset_date": "2026-11-10",
            "run_label": "Recovery Run",
            "configured_player_ids": ["PLAYER0001"],
            "claim_totals": {
                "daily": 0, "store": 3, "promotions": 0,
                "progression": 0, "loyalty": 0, "events": 0,
            },
            "processed_player_ids": ["PLAYER0001"],
            "run_key": "run-2:1",
            "run_timestamp": "2026-11-10T06:45:00",
        }
        first = update_daily_cycle_summary(**kwargs)
        second = update_daily_cycle_summary(**kwargs)
        self.assertEqual(first["total_claims"], 4)
        self.assertEqual(first["recovery_claims_added"], 3)
        self.assertEqual(second["total_claims"], 4)
        self.assertEqual(second["recovery_runs_count"], 1)
        self.assertEqual(second["recovered_player_last4"], ["0001"])
        self.assertTrue(second["cycle_complete"])

    def test_recovery_can_seed_from_same_day_full_run_but_not_previous_reset(self):
        base_meta = {
            "last_full_run": {
                "timestamp": "2026-11-10T06:30:00",
                "run_label": "Primary Run",
                "configured_player_count": 1,
                "accounts_processed": 1,
                "full_claim_accounts": 1,
                "per_type": {
                    "daily": 1, "store_daily": 3, "progression": 0,
                    "loyalty": 0, "event_rewards": 0,
                },
                "promotional_claims_made": 0,
            },
            "full_claim_coverage": {
                "PLAYER0001": {
                    "reset_date": "2026-11-10", "status": "complete",
                    "retry_required": False,
                }
            },
        }
        kwargs = {
            "reset_date": "2026-11-10",
            "run_label": "Recovery Run",
            "configured_player_ids": ["PLAYER0001"],
            "claim_totals": {
                "daily": 0, "store": 1, "promotions": 0,
                "progression": 0, "loyalty": 0, "events": 0,
            },
            "processed_player_ids": ["PLAYER0001"],
            "run_key": "run-3:1",
            "run_timestamp": "2026-11-10T06:50:00",
        }
        view = update_daily_cycle_summary(meta=base_meta, **kwargs)
        self.assertEqual(view["claim_totals"]["store"], 4)
        self.assertEqual(view["total_claims"], 5)

        prior_day_meta = {
            "daily_cycle_email_summary": {
                "reset_date": "2026-11-09",
                "configured_player_count": 1,
                "primary_claim_totals": {"daily": 1, "store": 3},
            },
            "last_full_run": {
                **base_meta["last_full_run"],
                "timestamp": "2026-11-09T06:30:00",
            }
        }
        prior_day_view = update_daily_cycle_summary(meta=prior_day_meta, **kwargs)
        self.assertIsNone(prior_day_view)
        self.assertNotIn("daily_cycle_email_summary", prior_day_meta)


if __name__ == "__main__":
    unittest.main()
