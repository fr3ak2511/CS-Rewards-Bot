import unittest
from datetime import datetime, timedelta

from recovery_policy import (
    full_claim_retry_ids,
    has_complete_full_roster_coverage,
    is_recent_full_claim_recovery,
    daily_only_pending_targets,
    eligible_daily_recovery_ids,
    manual_daily_recovery_targets,
)


class RecoveryPolicyTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 7, 7, 30, 0)
        self.reset_at = datetime(2026, 11, 7, 5, 30, 0)
        self.last_end = datetime(2026, 11, 7, 7, 20, 0)
        self.last_start = datetime(2026, 11, 7, 7, 10, 0)
        self.last_run = {
            "run_label": "Primary Run",
            "timestamp": self.last_end.isoformat(),
            "duration_seconds": 600,
            "retry_candidate_scope": "full_claim_only",
            "retry_player_ids": ["PLAYER-A"],
            "retry_player_count": 1,
            "full_claim_accounts": 1,
            "backup_reconciliation_accounts": 0,
            "promotional_only_accounts": 0,
        }

    def test_old_metadata_without_scope_marker_is_rejected(self):
        legacy = dict(self.last_run)
        legacy.pop("retry_candidate_scope")
        self.assertEqual(full_claim_retry_ids(legacy), [])
        self.assertFalse(self.is_recent(legacy))

    def test_promotional_only_run_cannot_trigger_full_claim_recovery(self):
        promotional_only = dict(self.last_run)
        promotional_only.update({
            "retry_player_ids": ["PROMO-ONLY-ID"],
            "retry_player_count": 1,
            "full_claim_accounts": 0,
            "backup_reconciliation_accounts": 0,
            "promotional_only_accounts": 35,
        })
        self.assertEqual(full_claim_retry_ids(promotional_only), [])
        self.assertFalse(self.is_recent(promotional_only))

    def test_empty_candidate_list_is_rejected(self):
        empty = dict(self.last_run)
        empty.update({"retry_player_ids": [], "retry_player_count": 0})
        self.assertEqual(full_claim_retry_ids(empty), [])
        self.assertFalse(self.is_recent(empty))

    def test_candidate_count_mismatch_is_rejected(self):
        mismatch = dict(self.last_run)
        mismatch.update({"retry_player_ids": ["PLAYER-A", "PLAYER-B"], "retry_player_count": 1})
        self.assertEqual(full_claim_retry_ids(mismatch), [])
        self.assertFalse(self.is_recent(mismatch))

    def test_matching_full_claim_candidates_are_eligible(self):
        self.assertEqual(full_claim_retry_ids(self.last_run), ["PLAYER-A"])
        self.assertTrue(self.is_recent(self.last_run))

    def test_backup_reconciliation_candidates_remain_eligible(self):
        backup = dict(self.last_run)
        backup.update({
            "full_claim_accounts": 0,
            "backup_reconciliation_accounts": 1,
        })
        self.assertEqual(full_claim_retry_ids(backup), ["PLAYER-A"])
        self.assertTrue(self.is_recent(backup))

    def test_stale_run_is_not_eligible_for_scheduled_safety_net(self):
        stale_end = self.last_end - timedelta(hours=5)
        stale = dict(self.last_run)
        stale["timestamp"] = stale_end.isoformat()
        self.assertFalse(is_recent_full_claim_recovery(
            stale,
            last_end=stale_end,
            last_start=stale_end - timedelta(minutes=10),
            now=self.now,
            reset_at=self.reset_at,
        ))

    def test_run_started_before_reset_is_not_eligible(self):
        self.assertFalse(is_recent_full_claim_recovery(
            self.last_run,
            last_end=self.last_end,
            last_start=self.reset_at - timedelta(seconds=1),
            now=self.now,
            reset_at=self.reset_at,
        ))

    def test_complete_full_roster_cycle_after_reset_is_covered(self):
        full_run = {
            "run_label": "Primary Run",
            "player_count": 35,
            "full_claim_accounts": 35,
        }
        self.assertTrue(has_complete_full_roster_coverage(
            full_run,
            player_count=35,
            full_end=datetime(2026, 11, 7, 7, 20, 0),
            full_start=datetime(2026, 11, 7, 5, 40, 0),
            now=self.now,
            reset_at=self.reset_at,
        ))

    def test_manual_full_roster_cycle_after_reset_is_covered(self):
        full_run = {
            "run_label": "Manual Run",
            "player_count": 35,
            "full_claim_accounts": 35,
        }
        self.assertTrue(has_complete_full_roster_coverage(
            full_run,
            player_count=35,
            full_end=datetime(2026, 11, 7, 7, 20, 0),
            full_start=datetime(2026, 11, 7, 5, 40, 0),
            now=self.now,
            reset_at=self.reset_at,
        ))

    def test_full_roster_cycle_started_before_reset_is_not_covered(self):
        full_run = {
            "run_label": "Primary Run",
            "player_count": 35,
            "full_claim_accounts": 35,
        }
        self.assertFalse(has_complete_full_roster_coverage(
            full_run,
            player_count=35,
            full_end=datetime(2026, 11, 7, 7, 20, 0),
            full_start=self.reset_at - timedelta(seconds=1),
            now=self.now,
            reset_at=self.reset_at,
        ))

    def test_incomplete_roster_or_claim_path_is_not_covered(self):
        full_run = {
            "run_label": "Primary Run",
            "player_count": 34,
            "full_claim_accounts": 35,
        }
        self.assertFalse(has_complete_full_roster_coverage(
            full_run,
            player_count=35,
            full_end=datetime(2026, 11, 7, 7, 20, 0),
            full_start=datetime(2026, 11, 7, 5, 40, 0),
            now=self.now,
            reset_at=self.reset_at,
        ))
        full_run["player_count"] = 35
        full_run["full_claim_accounts"] = 34
        self.assertFalse(has_complete_full_roster_coverage(
            full_run,
            player_count=35,
            full_end=datetime(2026, 11, 7, 7, 20, 0),
            full_start=datetime(2026, 11, 7, 5, 40, 0),
            now=self.now,
            reset_at=self.reset_at,
        ))

    def is_recent(self, last_run):
        return is_recent_full_claim_recovery(
            last_run,
            last_end=self.last_end,
            last_start=self.last_start,
            now=self.now,
            reset_at=self.reset_at,
        )


class DailyOnlyBackupRecoveryTests(unittest.TestCase):
    RESET_DATE = "2026-11-13"
    PLAYER = "BB382E889AA37C4F"

    def setUp(self):
        self.meta = {
            "full_claim_coverage": {
                self.PLAYER: {
                    "reset_date": self.RESET_DATE,
                    "status": "pending",
                    "retry_required": True,
                },
                "COMPLETE-PLAYER": {
                    "reset_date": self.RESET_DATE,
                    "status": "complete",
                    "retry_required": False,
                },
                "STALE-PLAYER": {
                    "reset_date": "2026-11-12",
                    "status": "pending",
                    "retry_required": True,
                },
            },
            "last_run": {
                "run_label": "Recovery Run",
                "retry_targets_by_player": {
                    self.PLAYER: {
                        "login_retry": False,
                        "processing_error": False,
                        "daily_unconfirmed": True,
                        "store_unconfirmed_indices": [],
                        "progression_unconfirmed": False,
                        "event_retry_required": False,
                        "event_thresholds_by_program": {},
                        "event_rewards": [],
                    }
                },
            },
        }
        self.roster = [self.PLAYER, "COMPLETE-PLAYER", "STALE-PLAYER"]

    def test_recovery_run_label_does_not_strand_pending_daily_target(self):
        candidates = daily_only_pending_targets(
            self.meta, self.roster, reset_date=self.RESET_DATE
        )
        self.assertEqual(list(candidates), [self.PLAYER])
        self.assertTrue(candidates[self.PLAYER]["daily_unconfirmed"])

    def test_complete_stale_and_out_of_roster_accounts_are_excluded(self):
        candidates = daily_only_pending_targets(
            self.meta, [self.PLAYER, "COMPLETE-PLAYER", "UNKNOWN-PLAYER", "STALE-PLAYER"],
            reset_date=self.RESET_DATE,
        )
        self.assertEqual(list(candidates), [self.PLAYER])

    def test_mixed_reward_targets_are_not_sent_through_daily_only_path(self):
        self.meta["last_run"]["retry_targets_by_player"][self.PLAYER][
            "store_unconfirmed_indices"
        ] = [2]
        self.assertEqual(
            daily_only_pending_targets(self.meta, self.roster, reset_date=self.RESET_DATE),
            {},
        )

    def test_next_backup_slot_is_eligible_once(self):
        self.assertEqual(
            eligible_daily_recovery_ids(
                self.meta, self.roster, reset_date=self.RESET_DATE,
                slot_label="Promotional Check #2",
            ),
            [self.PLAYER],
        )
        self.meta["full_claim_coverage"][self.PLAYER][
            "daily_recovery_slots_attempted"
        ] = ["Promotional Check #2"]
        self.assertEqual(
            eligible_daily_recovery_ids(
                self.meta, self.roster, reset_date=self.RESET_DATE,
                slot_label="Promotional Check #2",
            ),
            [],
        )

    def test_later_backup_slot_remains_available_after_prior_slot(self):
        self.meta["full_claim_coverage"][self.PLAYER][
            "daily_recovery_slots_attempted"
        ] = ["Promotional Check #2"]
        self.assertEqual(
            eligible_daily_recovery_ids(
                self.meta, self.roster, reset_date=self.RESET_DATE,
                slot_label="Promotional Check #3",
            ),
            [self.PLAYER],
        )

    def test_four_attempt_cap_is_enforced_across_backup_slots(self):
        self.meta["full_claim_coverage"][self.PLAYER][
            "daily_recovery_slots_attempted"
        ] = [
            "Promotional Check #1",
            "Promotional Check #2",
            "Promotional Check #3",
            "Promotional Check #4",
        ]
        self.assertEqual(
            eligible_daily_recovery_ids(
                self.meta, self.roster, reset_date=self.RESET_DATE,
                slot_label="Promotional Check #4",
            ),
            [],
        )

    def test_manual_recovery_selects_only_the_exact_pending_daily_player(self):
        selected = manual_daily_recovery_targets(
            self.meta, self.roster, reset_date=self.RESET_DATE,
            player_id=self.PLAYER,
        )
        self.assertEqual(list(selected), [self.PLAYER])
        self.assertTrue(selected[self.PLAYER]["daily_unconfirmed"])

    def test_manual_recovery_rejects_blank_unknown_and_non_pending_ids(self):
        for player_id in ("", "UNKNOWN-PLAYER", "COMPLETE-PLAYER", "STALE-PLAYER"):
            with self.subTest(player_id=player_id):
                self.assertEqual(
                    manual_daily_recovery_targets(
                        self.meta, self.roster, reset_date=self.RESET_DATE,
                        player_id=player_id,
                    ),
                    {},
                )

    def test_manual_recovery_rejects_mixed_reward_targets(self):
        self.meta["last_run"]["retry_targets_by_player"][self.PLAYER][
            "store_unconfirmed_indices"
        ] = [2]
        self.assertEqual(
            manual_daily_recovery_targets(
                self.meta, self.roster, reset_date=self.RESET_DATE,
                player_id=self.PLAYER,
            ),
            {},
        )

    def test_manual_recovery_does_not_consume_a_scheduled_slot(self):
        selected = manual_daily_recovery_targets(
            self.meta, self.roster, reset_date=self.RESET_DATE,
            player_id=self.PLAYER,
        )
        self.assertEqual(selected, {self.PLAYER: self.meta["last_run"]["retry_targets_by_player"][self.PLAYER]})
        self.assertNotIn("daily_recovery_slots_attempted", self.meta["full_claim_coverage"][self.PLAYER])


    def test_unknown_slot_fails_closed(self):
        self.assertEqual(
            eligible_daily_recovery_ids(
                self.meta, self.roster, reset_date=self.RESET_DATE,
                slot_label="Manual Run",
            ),
            [],
        )



if __name__ == "__main__":
    unittest.main(verbosity=2)
