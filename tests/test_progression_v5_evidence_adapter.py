import copy
import unittest

from progression_v5_evidence_adapter import build_progression_v5_evidence_reconciliation_report

PLAYER_A = "0000000000000001"
PLAYER_B = "0000000000000002"
THRESHOLDS = [3, 6]


def _account(suffix, second_status="Locked"):
    cards = [
        {"threshold": 3, "classification": "Delivered", "card_signature": f"3|card-{suffix}-3", "evidence_sources": ["explicit_delivered_cta"], "observations": 2},
        {"threshold": 6, "classification": second_status, "card_signature": f"6|card-{suffix}-6", "evidence_sources": ["inline_lock_signature" if second_status == "Locked" else "explicit_status"], "observations": 2},
    ]
    counts = {"Delivered": 1, "Claimable": 0, "Locked": 0, "Unknown": 0}
    counts[second_status] += 1
    return {
        "player_id_last4": suffix,
        "snapshot_count": 2,
        "snapshots": [{}, {}],
        "normalized_progression_rail": {
            "captured_rail_scan_completed": True,
            "selected_rail": {"at_end_observed": True},
            "rail_snapshots_normalized": 2,
            "rail_snapshots_expected": 2,
            "inventory_stable_across_snapshots": True,
            "milestone_identity_valid_across_snapshots": True,
            "card_signatures_stable_across_snapshots": True,
            "observed_milestone_thresholds": THRESHOLDS.copy(),
            "milestone_cards": cards,
            "card_status_totals": counts,
            "inventory_completeness_established": False,
        },
    }


def _diagnostic():
    return {
        "diagnostic_version": 5,
        "read_only": True,
        "configured_accounts": 2,
        "accounts_with_diagnostics": 2,
        "accounts_with_completed_rail_scan": 2,
        "inventory_consistency_across_accounts": True,
        "common_observed_milestone_thresholds": THRESHOLDS.copy(),
        "card_status_totals": {"Delivered": 2, "Claimable": 0, "Locked": 2, "Unknown": 0},
        "completeness_established": False,
        "delivery_status_established": False,
        "run_mode": "PROGRESSION_DIAGNOSTIC_ONLY",
        "generated_at_ist": "2026-11-26T00:13:35.282478",
        "run_scope": "all_configured_accounts",
        "configured_player_count": 2,
        "authenticated_progression_pages_observed": 2,
        "accounts_with_login_failure": 0,
        "accounts_with_diagnostic_error": 0,
        "claim_actions_permitted": False,
        "claim_clicks_dispatched": 0,
        "claim_history_modified": False,
        "progression_queue_modified": False,
        "runtime_state_loaded": False,
        "runtime_state_persisted": False,
        "accounts": [_account("0001"), _account("0002")],
        "account_results": [
            {"player_id_last4": "0001", "login_status": "authenticated", "page_status": "authenticated_progression_page_observed"},
            {"player_id_last4": "0002", "login_status": "authenticated", "page_status": "authenticated_progression_page_observed"},
        ],
    }


def _queue_entry(**changes):
    result = {"status": "deferred", "last_outcome_category": "empty_scan", "total_attempts": 4, "manual_attention_required": False, "requires_exact_target": False, "target_card_signature": None}
    result.update(changes)
    return result


class ProgressionV5EvidenceAdapterTests(unittest.TestCase):
    def build(self, diagnostic=None, queue=None, roster=None):
        return build_progression_v5_evidence_reconciliation_report(
            diagnostic if diagnostic is not None else _diagnostic(),
            roster_player_ids=roster if roster is not None else [PLAYER_A, PLAYER_B],
            progression_deferred_inspections=queue if queue is not None else {PLAYER_A: _queue_entry(), PLAYER_B: _queue_entry()},
            source_run_id="45677010790",
        )

    def test_valid_evidence_is_ready_for_policy_review_but_never_resolves(self):
        report = self.build()
        self.assertTrue(report["global_validation"]["valid"])
        self.assertEqual(report["queue_summary"]["entries_review_ready"], 2)
        self.assertEqual(report["queue_summary"]["ledger_records_generated"], 4)
        self.assertEqual(report["queue_summary"]["review_ready_card_status_totals"], {"Delivered": 2, "Claimable": 0, "Locked": 2, "Unknown": 0})
        self.assertTrue(all(row["evidence_status"] == "READY_FOR_POLICY_REVIEW" for row in report["entries"]))
        self.assertTrue(all(row["resolution_eligible"] is False for row in report["entries"]))
        self.assertFalse(report["resolution_policy"]["resolution_authorized"])
        self.assertEqual(report["resolution_policy"]["resolution_eligible_count"], 0)

    def test_inputs_are_not_mutated(self):
        diagnostic = _diagnostic()
        queue = {PLAYER_A: _queue_entry(), PLAYER_B: _queue_entry()}
        before = copy.deepcopy((diagnostic, queue))
        self.build(diagnostic, queue)
        self.assertEqual((diagnostic, queue), before)

    def test_unknown_and_claimable_cards_block_account(self):
        for state in ("Unknown", "Claimable"):
            diagnostic = _diagnostic()
            diagnostic["accounts"][0] = _account("0001", state)
            diagnostic["card_status_totals"] = {"Delivered": 2, "Claimable": int(state == "Claimable"), "Locked": int(state == "Locked"), "Unknown": int(state == "Unknown")}
            report = self.build(diagnostic=diagnostic)
            row = next(item for item in report["entries"] if item["player_id_last4"] == "0001")
            self.assertEqual(row["evidence_status"], "BLOCKED")
            self.assertEqual(row["card_ledger"], [])

    def test_incomplete_rail_or_unstable_signature_blocks_account(self):
        for field, value in (("captured_rail_scan_completed", False), ("card_signatures_stable_across_snapshots", False)):
            diagnostic = _diagnostic()
            diagnostic["accounts"][0]["normalized_progression_rail"][field] = value
            row = next(item for item in self.build(diagnostic=diagnostic)["entries"] if item["player_id_last4"] == "0001")
            self.assertEqual(row["evidence_status"], "BLOCKED")

    def test_missing_card_signature_blocks_account(self):
        diagnostic = _diagnostic()
        diagnostic["accounts"][0]["normalized_progression_rail"]["milestone_cards"][0]["card_signature"] = ""
        row = next(item for item in self.build(diagnostic=diagnostic)["entries"] if item["player_id_last4"] == "0001")
        self.assertIn("diagnostic_account_invalid:0001:milestone_card_signature_missing:3", row["validation_errors"])

    def test_v4_diagnostic_and_unsafe_flags_are_rejected(self):
        diagnostic = _diagnostic()
        diagnostic["diagnostic_version"] = 4
        report = self.build(diagnostic=diagnostic)
        self.assertFalse(report["global_validation"]["valid"])
        self.assertTrue(all(row["evidence_status"] == "BLOCKED" for row in report["entries"]))
        diagnostic = _diagnostic()
        diagnostic["runtime_state_persisted"] = True
        report = self.build(diagnostic=diagnostic)
        self.assertIn("diagnostic_field_mismatch:runtime_state_persisted", report["global_validation"]["errors"])

    def test_duplicate_roster_suffix_is_rejected(self):
        report = self.build(roster=[PLAYER_A, "FFFFFFFFFFFF0001"])
        self.assertIn("ambiguous_roster_suffix:0001", report["global_validation"]["errors"])
        self.assertTrue(all(row["evidence_status"] == "BLOCKED" for row in report["entries"]))

    def test_account_result_authentication_must_match(self):
        diagnostic = _diagnostic()
        diagnostic["account_results"][0]["login_status"] = "failed"
        report = self.build(diagnostic=diagnostic)
        self.assertIn("account_not_authenticated:0001", report["global_validation"]["errors"])

    def test_missing_queued_account_is_blocked(self):
        diagnostic = _diagnostic()
        diagnostic["accounts"] = diagnostic["accounts"][:1]
        diagnostic["account_results"] = diagnostic["account_results"][:1]
        for key in ("configured_accounts", "configured_player_count", "accounts_with_diagnostics", "authenticated_progression_pages_observed", "accounts_with_completed_rail_scan"):
            diagnostic[key] = 1
        report = self.build(diagnostic=diagnostic)
        row = next(item for item in report["entries"] if item["player_id_last4"] == "0002")
        self.assertIn("queued_account_missing_from_diagnostic", row["validation_errors"])

    def test_manual_attention_and_exact_target_are_never_review_ready(self):
        queue = {PLAYER_A: _queue_entry(manual_attention_required=True), PLAYER_B: _queue_entry(target_card_signature="milestone-6", requires_exact_target=True)}
        report = self.build(queue=queue)
        self.assertTrue(all(row["evidence_status"] == "BLOCKED" for row in report["entries"]))

    def test_wrong_queue_state_is_blocked(self):
        report = self.build(queue={PLAYER_A: _queue_entry(status="claim_unconfirmed"), PLAYER_B: _queue_entry()})
        row = next(item for item in report["entries"] if item["player_id_last4"] == "0001")
        self.assertIn("queue_status_not_deferred", row["validation_errors"])


if __name__ == "__main__":
    unittest.main()
