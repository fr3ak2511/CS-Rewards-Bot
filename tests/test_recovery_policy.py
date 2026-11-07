import unittest
from datetime import datetime, timedelta

from recovery_policy import full_claim_retry_ids, is_recent_full_claim_recovery


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

    def is_recent(self, last_run):
        return is_recent_full_claim_recovery(
            last_run,
            last_end=self.last_end,
            last_start=self.last_start,
            now=self.now,
            reset_at=self.reset_at,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
