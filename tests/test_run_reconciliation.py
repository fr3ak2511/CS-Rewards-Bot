import unittest

from run_reconciliation import build_run_reconciliation


class RunReconciliationTests(unittest.TestCase):
    def _clean_row(self):
        return {
            "daily": 1,
            "store": 3,
            "store_temp": 1,
            "progression": 1,
            "loyalty": 0,
            "event_rewards": [{"claims_this_run": [{"threshold": 400}, {"threshold": 900}]}],
            "processing_mode": "full_claim",
            "pid": "PLAYER0001",
            "grenade_balance": {
                "status": "pass",
                "current_count": 34,
                "expected_minimum": 24,
                "next_milestone": 56,
                "surplus": 10,
                "deficit": 0,
            },
        }

    def _totals(self):
        return {
            "daily_claimed_this_run": 1,
            "standard_store_claimed_this_run": 3,
            "promotional_claims_this_run": 1,
            "event_reward_claims_this_run": 2,
            "progression_claimed_this_run": 1,
            "loyalty_claimed_this_run": 0,
            "all_claims_this_run": 8,
        }

    def _counters(self):
        return {
            "claims_made": 8,
            "full_claim_accounts": 1,
            "promotional_only_accounts": 0,
            "backup_reconciliation_accounts": 0,
        }

    def test_clean_primary_run_reconciles_all_account_and_category_counts(self):
        result = build_run_reconciliation(
            [self._clean_row()],
            run_label="Primary Run",
            configured_player_count=1,
            totals=self._totals(),
            counters=self._counters(),
        )
        self.assertEqual(result["status"], "matched")
        self.assertEqual(result["claim_counts"]["per_account_sum"], 8)
        self.assertTrue(result["checks"]["primary_processed_full_configured_roster"])
        self.assertIsNone(result["progression_validation"]["expected_count"])

    def test_grenade_balance_below_minimum_is_an_attention_warning(self):
        row = self._clean_row()
        row["grenade_balance"] = {
            "status": "below_minimum",
            "current_count": 22,
            "expected_minimum": 24,
            "deficit": 2,
        }
        result = build_run_reconciliation(
            [row], run_label="Primary Run", configured_player_count=1,
            totals=self._totals(), counters=self._counters(),
        )
        self.assertEqual(result["status"], "attention_required")
        self.assertIn("grenade_balance_minimum_not_met", result["warnings"])
        self.assertEqual(result["grenade_balance_validation"]["accounts_below_minimum"], 1)
        self.assertEqual(
            result["grenade_balance_validation"]["attention_accounts"][0]["player_id_last4"],
            "0001",
        )

    def test_pending_grenade_baseline_is_reported_as_partial_not_pass(self):
        row = self._clean_row()
        row["grenade_balance"] = {"status": "pending_baseline", "current_count": 34}
        result = build_run_reconciliation(
            [row], run_label="Primary Run", configured_player_count=1,
            totals=self._totals(), counters=self._counters(),
        )
        self.assertEqual(result["grenade_balance_validation"]["status"], "partial")
        self.assertIn("grenade_balance_validation_incomplete", result["warnings"])

    def test_claim_count_mismatch_is_reported_without_mutating_inputs(self):
        row = self._clean_row()
        totals = self._totals()
        counters = self._counters()
        totals["standard_store_claimed_this_run"] = 2
        result = build_run_reconciliation(
            [row], run_label="Primary Run", configured_player_count=1,
            totals=totals, counters=counters,
        )
        self.assertEqual(result["status"], "attention_required")
        self.assertIn("per_account_claims_match_category_totals", result["warnings"])
        self.assertEqual(row["store"], 3)

    def test_partial_or_mixed_run_is_not_mistaken_for_a_full_roster_failure(self):
        result = build_run_reconciliation(
            [self._clean_row()],
            run_label="Promotional Check #1",
            configured_player_count=35,
            totals=self._totals(),
            counters=self._counters(),
        )
        self.assertEqual(result["status"], "matched")
        self.assertIsNone(result["checks"]["primary_processed_full_configured_roster"])

    def test_primary_run_flags_missing_configured_accounts(self):
        counters = dict(self._counters())
        counters["full_claim_accounts"] = 0
        counters["promotional_only_accounts"] = 1
        totals = dict(self._totals())
        result = build_run_reconciliation(
            [self._clean_row()],
            run_label="Primary Run",
            configured_player_count=2,
            totals=totals,
            counters=counters,
        )
        self.assertEqual(result["status"], "attention_required")
        self.assertFalse(result["checks"]["primary_processed_full_configured_roster"])

    def test_mode_count_mismatch_is_reported(self):
        counters = dict(self._counters())
        counters["full_claim_accounts"] = 0
        result = build_run_reconciliation(
            [self._clean_row()],
            run_label="Manual Run",
            configured_player_count=1,
            totals=self._totals(),
            counters=counters,
        )
        self.assertIn("processing_modes_match_processed_accounts", result["warnings"])

    def test_claim_counter_mismatch_is_reported(self):
        counters = dict(self._counters())
        counters["claims_made"] = 7
        result = build_run_reconciliation(
            [self._clean_row()],
            run_label="Manual Run",
            configured_player_count=1,
            totals=self._totals(),
            counters=counters,
        )
        self.assertIn("category_totals_match_claim_counter", result["warnings"])


if __name__ == "__main__":
    unittest.main()
