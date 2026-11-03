import unittest

from event_closeout import (
    build_closeout_snapshot,
    eligible_unredeemed_tiers,
    finalize_expired_history,
    load_program,
    record_closeout_result,
)


class EventCloseoutPureTests(unittest.TestCase):
    def setUp(self):
        self.program = {
            "enabled": True,
            "name": "Lost Soul Rewards",
            "slug": "halloween-2026",
            "program_id": "pgp_test_halloween",
            "start_at": 1792598400,
            "end_at": 1793826000,
        }

    def test_load_program_requires_one_enabled_halloween_program(self):
        loaded = load_program({"enabled": True, "programs": [self.program]})
        self.assertEqual(loaded["program_id"], "pgp_test_halloween")
        self.assertEqual(loaded["end_at_epoch"], 1793826000)

    def test_load_program_fails_closed_when_disabled_or_ambiguous(self):
        with self.assertRaises(ValueError):
            load_program({"enabled": False, "programs": [self.program]})
        with self.assertRaises(ValueError):
            load_program({"enabled": True, "programs": [self.program, self.program]})

    def test_eligible_unredeemed_requires_threshold_met_and_not_delivered(self):
        tiers = [
            {"threshold": 400, "status": "delivered"},
            {"threshold": 900, "status": "available"},
            {"threshold": 1300, "status": "locked"},
        ]
        self.assertEqual(
            eligible_unredeemed_tiers(tiers, 1000),
            [{"threshold": 900, "status": "available", "reward": []}],
        )
        self.assertEqual(eligible_unredeemed_tiers(tiers, None), [])

    def test_build_snapshot_captures_final_balance_and_unredeemed_tiers(self):
        event = {
            "balance": 1300,
            "next_threshold": 2000,
            "tiers_total": 3,
            "tiers_delivered": 2,
            "tiers": [
                {"threshold": 400, "status": "delivered"},
                {"threshold": 900, "status": "delivered"},
                {"threshold": 1300, "status": "available", "reward": ["Reward ×1"]},
            ],
            "claims_this_run": [{"threshold": 900, "status": "claimed_this_run"}],
            "retry_required": False,
            "status": "checked",
        }
        snapshot = build_closeout_snapshot(event, "2026-11-05T01:15:00+05:30", 2000)
        self.assertEqual(snapshot["balance"], 1300)
        self.assertEqual(snapshot["tiers_delivered"], 2)
        self.assertEqual(snapshot["eligible_unredeemed_tiers"][0]["threshold"], 1300)

    def test_record_closeout_result_preserves_existing_claim_ledger(self):
        history = {
            "PLAYER0001": {
                "event_rewards": {
                    "pgp_test_halloween": {
                        "claim_ledger": [{"cycle": 0, "threshold": 400, "verification": "delivered"}]
                    }
                }
            }
        }
        program = {**self.program, "end_at_epoch": 1793826000}
        result = {
            "status": "Promotional Check Complete",
            "promotions_observed": [],
            "event_rewards": [{
                "program_id": "pgp_test_halloween",
                "name": "Lost Soul Rewards",
                "balance": 900,
                "next_threshold": 1300,
                "tiers_total": 3,
                "tiers_delivered": 2,
                "tiers": [
                    {"threshold": 400, "status": "delivered"},
                    {"threshold": 900, "status": "delivered"},
                    {"threshold": 1300, "status": "locked"},
                ],
                "claims_this_run": [],
                "retry_required": False,
                "status": "checked",
            }],
        }
        record_closeout_result(history, "PLAYER0001", program, result, "2026-11-05T01:15:00+05:30")
        entry = history["PLAYER0001"]["event_rewards"]["pgp_test_halloween"]
        self.assertEqual(len(entry["claim_ledger"]), 1)
        self.assertEqual(entry["closeout_snapshot"]["balance"], 900)
        self.assertEqual(len(entry["closeout_attempts"]), 1)

    def test_popup_blocked_store_promotion_marks_closeout_attention(self):
        history = {}
        program = {**self.program, "end_at_epoch": 1793826000}
        result = {
            "promotions_observed": [{
                "title": "Store promotion popup",
                "status": "popup_blocked",
                "error": "unknown_fullscreen_overlay_blocks_page",
            }],
            "event_rewards": [{
                "program_id": "pgp_test_halloween",
                "name": "Lost Soul Rewards",
                "balance": 900,
                "next_threshold": 1300,
                "tiers_total": 2,
                "tiers_delivered": 2,
                "tiers": [
                    {"threshold": 400, "status": "delivered"},
                    {"threshold": 900, "status": "delivered"},
                ],
                "claims_this_run": [],
                "retry_required": False,
                "status": "checked",
            }],
        }

        attempt = record_closeout_result(
            history, "PLAYER0001", program, result, "2026-11-04T23:15:00+05:30"
        )
        entry = history["PLAYER0001"]["event_rewards"]["pgp_test_halloween"]

        self.assertEqual(attempt["status"], "attention_required")
        self.assertEqual(attempt["unconfirmed_promotions"], 1)
        self.assertEqual(
            entry["closeout_snapshot"]["unconfirmed_promotions"][0]["status"],
            "popup_blocked",
        )
    
    def test_finalize_marks_expired_and_preserves_ledger(self):
        history = {
            "PLAYER0001": {
                "event_rewards": {
                    "pgp_test_halloween": {
                        "status": "checked",
                        "claim_ledger": [{"cycle": 0, "threshold": 400, "verification": "delivered"}],
                        "closeout_snapshot": {
                            "checked_at": "2026-11-05T01:15:00+05:30",
                            "balance": 900,
                            "tiers_total": 3,
                            "tiers_delivered": 2,
                            "tiers": [
                                {"threshold": 400, "status": "delivered"},
                                {"threshold": 900, "status": "delivered"},
                                {"threshold": 1300, "status": "locked"},
                            ],
                            "retry_required": False,
                        },
                    }
                }
            },
            "PLAYER0002": {},
        }
        program = {**self.program, "end_at_epoch": 1793826000}
        summary = finalize_expired_history(
            history,
            ["PLAYER0001", "PLAYER0002"],
            program,
            "2026-11-05T02:45:00+05:30",
            1793826900,
        )
        first = history["PLAYER0001"]["event_rewards"]["pgp_test_halloween"]
        second = history["PLAYER0002"]["event_rewards"]["pgp_test_halloween"]
        self.assertEqual(first["status"], "expired")
        self.assertEqual(first["closeout_status"], "completed")
        self.assertEqual(len(first["claim_ledger"]), 1)
        self.assertEqual(second["status"], "expired")
        self.assertEqual(second["closeout_status"], "closeout_missing")
        self.assertEqual(summary["closeout_snapshots_found"], 1)
        self.assertEqual(summary["closeout_snapshots_missing"], 1)

    def test_finalize_refuses_to_run_before_expiry(self):
        with self.assertRaises(ValueError):
            finalize_expired_history({}, ["PLAYER0001"], {**self.program, "end_at_epoch": 1793826000}, "2026-11-05T02:00:00+05:30", 1793824200)


    def test_finalize_does_not_hide_a_failed_last_sweep_behind_an_older_snapshot(self):
        history = {
            "PLAYER0001": {
                "event_rewards": {
                    "pgp_test_halloween": {
                        "closeout_snapshot": {
                            "checked_at": "2026-11-05T01:15:00+05:30",
                            "balance": 900,
                            "tiers_total": 2,
                            "tiers_delivered": 2,
                            "tiers": [
                                {"threshold": 400, "status": "delivered"},
                                {"threshold": 900, "status": "delivered"},
                            ],
                            "retry_required": False,
                        },
                        "closeout_attempts": [
                            {"checked_at": "2026-11-05T01:15:00+05:30", "status": "checked"},
                            {"checked_at": "2026-11-05T02:00:00+05:30", "status": "attention_required", "error": "login failed"},
                        ],
                    }
                }
            }
        }
        summary = finalize_expired_history(
            history,
            ["PLAYER0001"],
            {**self.program, "end_at_epoch": 1793826000},
            "2026-11-05T02:45:00+05:30",
            1793826900,
        )
        entry = history["PLAYER0001"]["event_rewards"]["pgp_test_halloween"]
        self.assertEqual(entry["closeout_status"], "attention_required")
        self.assertEqual(summary["accounts_requiring_attention"], 1)

    def test_snapshot_at_or_after_expiry_is_not_accepted(self):
        history = {
            "PLAYER0001": {
                "event_rewards": {
                    "pgp_test_halloween": {
                        "closeout_snapshot": {
                            "checked_at": "2026-11-05T02:30:00+05:30",
                            "balance": 900,
                            "tiers_total": 2,
                            "tiers_delivered": 2,
                            "tiers": [],
                        }
                    }
                }
            }
        }
        summary = finalize_expired_history(
            history,
            ["PLAYER0001"],
            {**self.program, "end_at_epoch": 1793826000},
            "2026-11-05T02:45:00+05:30",
            1793826900,
        )
        entry = history["PLAYER0001"]["event_rewards"]["pgp_test_halloween"]
        self.assertEqual(entry["closeout_status"], "closeout_missing")
        self.assertEqual(summary["closeout_snapshots_found"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
