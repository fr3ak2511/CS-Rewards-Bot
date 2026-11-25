import copy
import unittest

from progression_v5_queue_resolution_policy import (
    build_progression_v5_queue_resolution_proposal,
)

PLAYER_ID = "0000000000000001"
SOURCE_RUN_ID = "45677010790"
GENERATED_AT = "2026-11-26T00:13:35.282478"
THRESHOLDS = [3, 6]


def _raw_card(threshold, status):
    return {
        "tag": "div",
        "class_name": "reward-card",
        "test_id": "",
        "text_snippet": f"{threshold}x20{status}",
        "milestone_number_hints": [threshold],
    }


def _selected_rail(scroll_left, at_end):
    cards = [_raw_card(3, "Delivered"), _raw_card(6, "Locked")]
    return {
        "index": 0,
        "tag": "div",
        "class_name": "progression-rail",
        "client_width": 100,
        "scroll_width": 500,
        "scroll_left": scroll_left,
        "max_scroll_left": 400,
        "at_start": scroll_left <= 2,
        "at_end": at_end,
        "overflow_x": "auto",
        "scroll_snap_type": "x mandatory",
        "is_semantic_carousel_root": False,
        "card_capture_source": "repeated_reward_siblings",
        "explicit_slide_count": 0,
        "captured_card_count": len(cards),
        "captured_cards": cards,
    }


def _wrapper_candidate():
    cards = [_raw_card(3, "Delivered"), _raw_card(6, "Locked")]
    return {
        "index": 1,
        "tag": "div",
        "class_name": "reward-card-wrapper",
        "client_width": 100,
        "scroll_width": 124,
        "scroll_left": 0,
        "max_scroll_left": 24,
        "at_start": True,
        "at_end": False,
        "overflow_x": "visible",
        "scroll_snap_type": "none",
        "is_semantic_carousel_root": False,
        "card_capture_source": "repeated_reward_siblings",
        "explicit_slide_count": 0,
        "captured_card_count": len(cards),
        "captured_cards": cards,
    }


def _snapshot(observed_at, scroll_left, at_end):
    return {
        "observed_at_ist": observed_at,
        "page_path": "/progression-program",
        "carousel_containers": [
            _selected_rail(scroll_left, at_end),
            _wrapper_candidate(),
        ],
    }


def _account():
    cards = [
        {
            "threshold": 3,
            "classification": "Delivered",
            "card_signature": "3|reward|3x20",
            "evidence_sources": ["card_text_suffix"],
            "observations": 2,
        },
        {
            "threshold": 6,
            "classification": "Locked",
            "card_signature": "6|reward|6x20",
            "evidence_sources": ["icon_semantic_name"],
            "observations": 2,
        },
    ]
    return {
        "player_id_last4": "0001",
        "snapshot_count": 2,
        "snapshots": [
            _snapshot("2026-11-26T00:07:45.000000", 100, False),
            _snapshot("2026-11-26T00:08:10.000000", 400, True),
        ],
        "normalized_progression_rail": {
            "captured_rail_scan_completed": True,
            "rail_scan_reason": "authoritative_rail_reached_end_with_stable_unique_milestone_identities",
            "selected_rail": {
                "container_index": 0,
                "client_width": 100,
                "scroll_width": 500,
                "max_scroll_left": 400,
                "captured_card_count": 2,
                "at_end_observed": True,
            },
            "rail_snapshots_normalized": 2,
            "rail_snapshots_expected": 2,
            "inventory_stable_across_snapshots": True,
            "milestone_identity_valid_across_snapshots": True,
            "card_signatures_stable_across_snapshots": True,
            "observed_milestone_thresholds": THRESHOLDS.copy(),
            "milestone_cards": cards,
            "card_status_totals": {
                "Delivered": 1,
                "Claimable": 0,
                "Locked": 1,
                "Unknown": 0,
            },
            "inventory_completeness_established": False,
        },
    }


def _diagnostic():
    return {
        "diagnostic_version": 5,
        "read_only": True,
        "configured_accounts": 1,
        "accounts_with_diagnostics": 1,
        "accounts_with_completed_rail_scan": 1,
        "inventory_consistency_across_accounts": True,
        "common_observed_milestone_thresholds": THRESHOLDS.copy(),
        "card_status_totals": {
            "Delivered": 1,
            "Claimable": 0,
            "Locked": 1,
            "Unknown": 0,
        },
        "completeness_established": False,
        "delivery_status_established": False,
        "accounts": [_account()],
        "snapshot_count": 2,
        "run_mode": "PROGRESSION_DIAGNOSTIC_ONLY",
        "generated_at_ist": GENERATED_AT,
        "run_scope": "all_configured_accounts",
        "configured_player_count": 1,
        "authenticated_progression_pages_observed": 1,
        "accounts_with_login_failure": 0,
        "accounts_with_diagnostic_error": 0,
        "claim_actions_permitted": False,
        "claim_clicks_dispatched": 0,
        "claim_history_modified": False,
        "progression_queue_modified": False,
        "runtime_state_loaded": False,
        "runtime_state_persisted": False,
        "account_results": [
            {
                "player_id_last4": "0001",
                "login_status": "authenticated",
                "page_status": "authenticated_progression_page_observed",
                "snapshot_count": 2,
                "scroll_steps": 1,
                "scroll_stop_reason": "no_further_horizontal_movement_observed",
                "claimable_control_counts_by_snapshot": [0, 0],
                "delivered_control_counts_by_snapshot": [0, 1],
            }
        ],
    }


def _queue_entry():
    return {
        "status": "deferred",
        "last_outcome_category": "empty_scan",
        "last_attempt_at": "2026-11-25T18:10:00.000000",
        "last_deferred_at": "2026-11-25T18:10:10.000000",
        "manual_attention_required": False,
        "requires_exact_target": False,
        "target_card_signature": None,
        "total_attempts": 4,
    }


class ProgressionV5QueueResolutionPolicyTests(unittest.TestCase):
    def build(self, diagnostic=None, queue=None):
        return build_progression_v5_queue_resolution_proposal(
            diagnostic if diagnostic is not None else _diagnostic(),
            roster_player_ids=[PLAYER_ID],
            progression_deferred_inspections=queue if queue is not None else {PLAYER_ID: _queue_entry()},
            source_run_id=SOURCE_RUN_ID,
        )

    def test_valid_evidence_proposes_only_scoped_captured_rail_resolution(self):
        report = self.build()
        self.assertTrue(report["global_validation"]["valid"], report["global_validation"]["errors"])
        self.assertEqual(report["queue_summary"]["entries_proposed_for_scoped_resolution"], 1)
        self.assertEqual(report["queue_summary"]["entries_blocked"], 0)
        row = report["entries"][0]
        self.assertEqual(row["evidence_status"], "PROPOSED_NO_CLAIMABLE_IN_CAPTURED_RAIL")
        self.assertTrue(row["resolution_eligible"])
        self.assertEqual(row["proposed_resolution"], "no_claimable_in_captured_rail")
        self.assertFalse(report["resolution_policy"]["resolution_authorized"])
        self.assertFalse(report["resolution_policy"]["portal_inventory_completeness_established"])
        self.assertFalse(report["resolution_policy"]["queue_mutated"])

    def test_inputs_are_not_mutated(self):
        diagnostic = _diagnostic()
        queue = {PLAYER_ID: _queue_entry()}
        before = copy.deepcopy((diagnostic, queue))
        self.build(diagnostic, queue)
        self.assertEqual((diagnostic, queue), before)

    def test_claimable_control_in_any_snapshot_blocks_resolution(self):
        diagnostic = _diagnostic()
        diagnostic["account_results"][0]["claimable_control_counts_by_snapshot"] = [0, 1]
        row = self.build(diagnostic=diagnostic)["entries"][0]
        self.assertFalse(row["resolution_eligible"])
        self.assertIn(
            "account_capture_invalid:0001:claimable_control_count_not_zero_for_every_snapshot",
            row["validation_errors"],
        )

    def test_diagnostic_scroll_cap_or_failure_blocks_resolution(self):
        diagnostic = _diagnostic()
        diagnostic["account_results"][0]["scroll_stop_reason"] = "diagnostic_scroll_safety_cap_reached"
        row = self.build(diagnostic=diagnostic)["entries"][0]
        self.assertFalse(row["resolution_eligible"])
        self.assertIn(
            "account_capture_invalid:0001:diagnostic_did_not_observe_horizontal_scroll_end",
            row["validation_errors"],
        )

    def test_ambiguous_authoritative_rail_blocks_resolution(self):
        diagnostic = _diagnostic()
        duplicate = copy.deepcopy(diagnostic["accounts"][0]["snapshots"][0]["carousel_containers"][0])
        duplicate["index"] = 2
        diagnostic["accounts"][0]["snapshots"][0]["carousel_containers"].append(duplicate)
        row = self.build(diagnostic=diagnostic)["entries"][0]
        self.assertFalse(row["resolution_eligible"])
        self.assertIn(
            "account_capture_invalid:0001:snapshot_authoritative_rail_ambiguous:0",
            row["validation_errors"],
        )

    def test_invalid_card_capture_source_blocks_resolution(self):
        diagnostic = _diagnostic()
        diagnostic["accounts"][0]["snapshots"][1]["carousel_containers"][0]["card_capture_source"] = "no_card_group_found"
        row = self.build(diagnostic=diagnostic)["entries"][0]
        self.assertFalse(row["resolution_eligible"])
        self.assertIn(
            "account_capture_invalid:0001:snapshot_card_capture_source_invalid:1",
            row["validation_errors"],
        )

    def test_queue_change_after_diagnostic_blocks_resolution(self):
        queue = {PLAYER_ID: _queue_entry()}
        queue[PLAYER_ID]["last_deferred_at"] = "2026-11-26T00:14:00.000000"
        row = self.build(queue=queue)["entries"][0]
        self.assertFalse(row["resolution_eligible"])
        self.assertIn("queue_changed_after_diagnostic", row["validation_errors"])

    def test_manual_attention_and_exact_target_entries_are_excluded(self):
        for field, value in (
            ("manual_attention_required", True),
            ("requires_exact_target", True),
            ("target_card_signature", "a-prior-click-target"),
        ):
            queue = {PLAYER_ID: _queue_entry()}
            queue[PLAYER_ID][field] = value
            row = self.build(queue=queue)["entries"][0]
            self.assertFalse(row["resolution_eligible"], field)

    def test_stale_evidence_not_after_last_queue_attempt_is_excluded(self):
        queue = {PLAYER_ID: _queue_entry()}
        queue[PLAYER_ID]["last_attempt_at"] = "2026-11-26T00:07:50.000000"
        row = self.build(queue=queue)["entries"][0]
        self.assertFalse(row["resolution_eligible"])
        self.assertIn("diagnostic_does_not_postdate_last_queue_attempt", row["validation_errors"])

    def test_unclassified_card_and_portal_completeness_claim_are_rejected(self):
        diagnostic = _diagnostic()
        rail = diagnostic["accounts"][0]["normalized_progression_rail"]
        rail["milestone_cards"][1]["classification"] = "Unknown"
        rail["card_status_totals"] = {
            "Delivered": 1,
            "Claimable": 0,
            "Locked": 0,
            "Unknown": 1,
        }
        diagnostic["card_status_totals"] = dict(rail["card_status_totals"])
        row = self.build(diagnostic=diagnostic)["entries"][0]
        self.assertFalse(row["resolution_eligible"])
        self.assertIn(
            "diagnostic_account_invalid:0001:unknown_cards_observed_requires_live_review",
            row["validation_errors"],
        )

        diagnostic = _diagnostic()
        diagnostic["completeness_established"] = True
        row = self.build(diagnostic=diagnostic)["entries"][0]
        self.assertFalse(row["resolution_eligible"])

    def test_prior_generic_reopen_is_recorded_but_not_confused_with_new_resolution(self):
        queue = {PLAYER_ID: _queue_entry()}
        queue[PLAYER_ID]["reopened_from_resolution"] = "inspection_completed"
        queue[PLAYER_ID]["reopened_from_resolution_run_id"] = "44973856377"
        row = self.build(queue=queue)["entries"][0]
        self.assertTrue(row["resolution_eligible"])
        self.assertEqual(row["proposed_resolution"], "no_claimable_in_captured_rail")


if __name__ == "__main__":
    unittest.main()
