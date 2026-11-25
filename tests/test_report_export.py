import re
import shutil
import subprocess
import unittest

from report_export import build_copyable_report


class CopyableReportTests(unittest.TestCase):
    def setUp(self):
        self.summary = {
            "version": "v3.3.3",
            "run_label": "Primary Run",
            "started_at_ist": "2026-11-08T05:35:30",
            "finished_at_ist": "2026-11-08T06:41:02",
            "configured_player_count": 2,
            "accounts_processed": 2,
            "claims_made": 8,
            "actual_failures": 0,
            "unconfirmed_claims": 0,
            "retry_candidate_count": 0,
            "full_claim_efficiency": 100,
            "email_sent": True,
            "reconciliation": {
                "status": "matched",
                "warnings": [],
                "grenade_balance_validation": {
                    "status": "matched",
                    "accounts_observed": 2,
                    "accounts_passed": 2,
                    "accounts_below_minimum": 0,
                    "accounts_pending_baseline": 0,
                    "accounts_not_validated": 0,
                },
            },
            "totals": {
                "daily_claimed_this_run": 2,
                "standard_store_claimed_this_run": 6,
                "promotional_claims_this_run": 0,
                "event_reward_claims_this_run": 0,
                "progression_claimed_this_run": 0,
                "loyalty_claimed_this_run": 0,
                "all_claims_this_run": 8,
            },
            "player_results": [
                {
                    "player_id_last4": "AB12",
                    "display_name": "<Player One>",
                    "status": "Success",
                    "daily_claimed_this_run": 1,
                    "standard_store_claimed_this_run": 3,
                    "progression_claimed_this_run": 0,
                    "loyalty_claimed_this_run": 0,
                    "event_reward_claims_this_run": 0,
                    "retry_required": False,
                    "grenade_balance": {
                        "status": "pass", "current_count": 34,
                        "expected_minimum": 24, "next_milestone": 56,
                    },
                },
                {
                    "player_id_last4": "CD34",
                    "display_name": None,
                    "status": "Partial",
                    "fail_reason": "Portal confirmation pending",
                    "daily_claimed_this_run": 1,
                    "standard_store_claimed_this_run": 2,
                    "progression_claimed_this_run": 0,
                    "loyalty_claimed_this_run": 0,
                    "event_reward_claims_this_run": 0,
                    "retry_required": True,
                    "grenade_balance": {
                        "status": "pass", "current_count": 28,
                        "expected_minimum": 24, "next_milestone": 56,
                    },
                },
            ],
        }

    def test_report_is_self_contained_mobile_first_and_copy_enabled(self):
        report = build_copyable_report(self.summary)
        for expected in (
            "Copy Report",
            "viewport",
            "@media(max-width:620px)",
            "navigator.clipboard.write",
            "'image/png'",
            "renderReportPng",
            "ClipboardItem",
            "document.execCommand('copy')",
            "text/html",
            "text/plain",
            "ATTENTION REQUIRED",
        ):
            self.assertIn(expected, report)

    def test_report_escapes_untrusted_display_names_and_reasons(self):
        report = build_copyable_report(self.summary)
        self.assertIn("&lt;Player One&gt;", report)
        self.assertNotIn("<Player One>", report)
        self.assertIn("Portal confirmation pending", report)

    def test_report_uses_masked_suffix_not_full_player_identifier(self):
        report = build_copyable_report(self.summary)
        self.assertIn("Player …CD34", report)
        self.assertNotIn("player_id", report)
        self.assertNotIn("AB12CD34", report)

    def test_healthy_summary_has_zero_attention_and_no_false_warning(self):
        healthy = dict(self.summary)
        healthy["player_results"] = [dict(self.summary["player_results"][0])]
        report = build_copyable_report(healthy)
        self.assertIn(">HEALTHY</span>", report)
        self.assertIn("<strong>0</strong><small>Accounts with claim / validation exceptions", report)

    def test_report_includes_masked_progression_balance_validation(self):
        report = build_copyable_report(self.summary)
        self.assertIn("Progression / Grenade balance", report)
        self.assertIn("<td>34</td><td>24</td><td>56</td>", report)
        self.assertIn("Grenade balance", report)
        self.assertNotIn("PLAYER0001", report)

    def test_pending_grenade_baseline_is_not_reported_as_healthy(self):
        pending = dict(self.summary)
        pending["reconciliation"] = {
            "status": "partial",
            "warnings": ["grenade_balance_validation_incomplete"],
            "grenade_balance_validation": {
                "status": "partial",
                "accounts_observed": 2,
                "accounts_passed": 1,
                "accounts_pending_baseline": 1,
                "accounts_below_minimum": 0,
                "accounts_not_validated": 0,
            },
        }
        pending["player_results"] = [
            {
                "player_id_last4": "AB12",
                "display_name": "Player A",
                "grenade_balance": {
                    "status": "pass", "current_count": 34,
                    "expected_minimum": 24, "next_milestone": 56,
                },
            },
            {
                "player_id_last4": "CD34",
                "display_name": "Player B",
                "grenade_balance": {
                    "status": "pending_baseline", "current_count": 20,
                    "expected_minimum": None, "next_milestone": 56,
                },
            },
        ]
        report = build_copyable_report(pending)
        self.assertIn("ATTENTION REQUIRED", report)
        self.assertIn("Pending baseline", report)

    def test_report_shows_full_cycle_health_separately_from_run_attention(self):
        summary = dict(self.summary)
        summary["cycle_health_overview"] = {
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
        }
        report = build_copyable_report(summary)
        self.assertIn("Overall reset-cycle health", report)
        self.assertIn("36/36 accounts complete", report)
        self.assertIn("11 PASS today", report)
        self.assertIn("25 not checked today", report)
        self.assertIn("24 with no visible claimable card", report)
        self.assertIn("1 blocked by overlay", report)
        self.assertIn("Need attention", report)
        self.assertIn("class='cycle-health-table'", report)
        self.assertIn(".cycle-health-table{min-width:0 !important", report)

    def test_generated_inline_copy_script_passes_node_syntax_check(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node.js must be available for generated report syntax checks.")

        report = build_copyable_report(self.summary)
        scripts = re.findall(r"<script>(.*?)</script>", report, flags=re.DOTALL)
        self.assertEqual(len(scripts), 1, "Expected exactly one inline report script.")

        result = subprocess.run(
            [node, "--check", "-"],
            input=scripts[0],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"Generated report JavaScript is invalid:\n{result.stderr}",
        )

    def test_deferred_progression_is_informational_and_keeps_overlay_visible(self):
        deferred = dict(self.summary["player_results"][0])
        deferred.update({
            "progression_deferred": True,
            "progression_deferred_reason": (
                "popup/overlay safety guard blocked inspection "
                "(unknown_fullscreen_overlay_blocks_page; dialogs=0; overlays=1; "
                "top_dialog='PRIVATE-POPUP-CONTEXT'; popup_context={\"url\":\"https://internal.invalid\"})"
            ),
            "progression_unconfirmed": False,
            "progression_claim_attempted": False,
        })
        summary = dict(self.summary)
        summary["player_results"] = [deferred]

        report = build_copyable_report(summary)
        self.assertIn(">COMPLETED — INSPECTIONS PENDING</span>", report)
        self.assertIn("<strong>0</strong><small>Claim / validation exceptions only", report)
        self.assertIn("Progression / Grenade balance — grouped by milestone", report)
        self.assertIn("Pending · Blocked by an on-screen popup or overlay", report)
        self.assertIn("&lt;Player One&gt;", report)
        self.assertIn(">Deferred check</span>", report)
        self.assertIn("needs another safe inspection", report)
        self.assertNotIn("unknown_fullscreen_overlay_blocks_page", report)
        self.assertNotIn("PRIVATE-POPUP-CONTEXT", report)
        self.assertNotIn("popup_context", report)
        self.assertIn("Actual failures: <strong>0</strong>", report)
        self.assertIn("Unconfirmed claims: <strong>0</strong>", report)
        self.assertIn("Progression</span><strong>0</strong>", report)

    def test_queue_only_partial_retry_is_not_counted_as_attention(self):
        deferred = dict(self.summary["player_results"][0])
        reason = (
            "Queued Progression inspection found no currently visible claimable reward; "
            "keeping the queue open for a later pass."
        )
        deferred.update({
            "status": "Partial",
            "retry_required": True,
            "progression_deferred": True,
            "progression_deferred_reason": reason,
            "fail_reason": reason,
            "progression_unconfirmed": False,
            "progression_claim_attempted": False,
        })
        summary = dict(self.summary)
        summary["player_results"] = [deferred]
        report = build_copyable_report(summary)
        self.assertIn(">COMPLETED — INSPECTIONS PENDING</span>", report)
        self.assertIn("<strong>0</strong><small>Claim / validation exceptions only", report)
        self.assertIn("Pending · no claimable card visible", report)
        self.assertIn(">Deferred check</span>", report)

    def test_promotion_detection_failure_is_not_hidden_by_deferred_progression(self):
        deferred = dict(self.summary["player_results"][0])
        reason = "No currently visible claimable reward."
        deferred.update({
            "status": "Partial",
            "retry_required": True,
            "progression_deferred": True,
            "progression_deferred_reason": reason,
            "fail_reason": reason,
            "promotion_detection_failed": True,
            "progression_unconfirmed": False,
        })
        summary = dict(self.summary)
        summary["player_results"] = [deferred]
        report = build_copyable_report(summary)
        self.assertIn(">ATTENTION REQUIRED</span>", report)
        self.assertIn("<strong>1</strong><small>Claim / validation exceptions only", report)

    def test_unconfirmed_progression_claim_remains_attention(self):
        deferred = dict(self.summary["player_results"][0])
        deferred.update({
            "progression_deferred": True,
            "progression_unconfirmed": True,
            "progression_deferred_reason": "Prior Progression claim remains unconfirmed.",
        })
        summary = dict(self.summary)
        summary["player_results"] = [deferred]
        report = build_copyable_report(summary)
        self.assertIn(">ATTENTION REQUIRED</span>", report)
        self.assertIn("<strong>1</strong><small>Claim / validation exceptions only", report)

    def test_carry_forward_balance_is_explicitly_not_live_and_does_not_raise_attention(self):
        deferred = dict(self.summary["player_results"][0])
        deferred.update({
            "progression_deferred": True,
            "progression_unconfirmed": False,
            "progression_deferred_reason": "No currently visible claimable reward.",
            "grenade_balance_carry_forward": {
                "source": "saved_portal_observation",
                "status_at_observation": "pass",
                "last_observed_count": 66,
                "last_observed_date": "2026-11-22",
                "expected_minimum_at_observation": 66,
                "expected_minimum_as_of": 69,
                "next_milestone_at_observation": 123,
                "remaining_to_milestone_at_observation": 57,
                "as_of_reset_date": "2026-11-23",
            },
        })
        summary = dict(self.summary)
        summary["player_results"] = [deferred]
        report = build_copyable_report(summary)
        self.assertIn("Progression / Grenade balance — grouped by milestone", report)
        self.assertIn("66", report)
        self.assertIn("69", report)
        self.assertIn("Next milestone 123", report)
        self.assertIn("57", report)
        self.assertIn("Last observed 2026-11-22", report)
        self.assertIn(">COMPLETED — INSPECTIONS PENDING</span>", report)
        self.assertNotIn(">ATTENTION REQUIRED</span>", report)


    def test_legacy_carry_forward_status_is_not_inferred_as_pass(self):
        deferred = dict(self.summary["player_results"][0])
        deferred.update({
            "progression_deferred": True,
            "progression_deferred_reason": "No currently visible claimable reward.",
            "progression_unconfirmed": False,
            "grenade_balance_carry_forward": {
                "status_at_observation": "status_not_retained",
                "last_observed_count": 66,
                "last_observed_date": "2026-11-22",
                "expected_minimum_at_observation": 66,
                "expected_minimum_as_of": 69,
                "next_milestone_at_observation": None,
                "remaining_to_milestone_at_observation": None,
                "as_of_reset_date": "2026-11-23",
            },
        })
        summary = dict(self.summary)
        summary["player_results"] = [deferred]
        report = build_copyable_report(summary)
        self.assertIn("Status not retained", report)
        self.assertIn("Last observed 2026-11-22", report)
        self.assertIn(">COMPLETED — INSPECTIONS PENDING</span>", report)
        self.assertNotIn(">ATTENTION REQUIRED</span>", report)

    def test_deferred_progression_reports_partial_confirmation_truthfully(self):
        deferred = dict(self.summary["player_results"][0])
        deferred.update({
            "progression": 1,
            "progression_deferred": True,
            "progression_claim_attempted": True,
            "progression_deferred_reason": (
                "One Progression reward was portal-confirmed, but additional claimable cards remain"
            ),
            "progression_unconfirmed": False,
        })
        summary = dict(self.summary)
        summary["player_results"] = [deferred]
        report = build_copyable_report(summary)
        self.assertIn("1 claim(s) confirmed · further inspection pending", report)
        self.assertIn("Pending accounts need another safe inspection", report)

    def test_unexpected_progression_inspection_exception_remains_attention(self):
        deferred = dict(self.summary["player_results"][0])
        reason = "Progression-only recovery raised RuntimeError; inspection remains queued."
        deferred.update({
            "status": "Partial",
            "retry_required": True,
            "progression_deferred": True,
            "progression_unconfirmed": False,
            "progression_deferred_reason": reason,
            "fail_reason": reason,
        })
        summary = dict(self.summary)
        summary["player_results"] = [deferred]
        report = build_copyable_report(summary)
        self.assertIn(">ATTENTION REQUIRED</span>", report)
        self.assertIn("<strong>1</strong><small>Claim / validation exceptions only", report)

    def test_saved_grenade_deficit_remains_attention_when_inspection_is_deferred(self):
        deferred = dict(self.summary["player_results"][0])
        deferred.update({
            "progression_deferred": True,
            "progression_unconfirmed": False,
            "progression_deferred_reason": "No currently visible claimable reward.",
            "grenade_balance_carry_forward": {
                "status_at_observation": "below_minimum",
                "last_observed_count": 60,
                "last_observed_date": "2026-11-22",
                "expected_minimum_at_observation": 66,
                "expected_minimum_as_of": 69,
                "as_of_reset_date": "2026-11-23",
            },
        })
        summary = dict(self.summary)
        summary["player_results"] = [deferred]
        report = build_copyable_report(summary)
        self.assertIn(">ATTENTION REQUIRED</span>", report)
        self.assertIn("<strong>1</strong><small>Claim / validation exceptions only", report)
        self.assertIn("Below date minimum", report)

    def test_invalid_summary_type_is_rejected(self):
        with self.assertRaises(TypeError):
            build_copyable_report([])



    def test_copy_report_groups_same_milestone_by_balance_and_claim_status(self):
        summary = dict(self.summary)
        summary["player_results"] = [
            {
                "player_id_last4": "0001", "display_name": "Claimed Player",
                "progression_claimed_this_run": 1, "progression_checked": True,
                "grenade_balance": {
                    "status": "pass", "current_count": 18, "expected_minimum": 18,
                    "next_milestone": 56, "remaining_to_next_milestone": 38,
                },
            },
            {
                "player_id_last4": "0002", "display_name": "Pending Player A",
                "progression_deferred": True,
                "progression_deferred_reason": "No currently visible claimable reward card.",
                "grenade_balance": {
                    "status": "pass", "current_count": 75, "expected_minimum": 75,
                    "next_milestone": 123, "remaining_to_next_milestone": 48,
                },
            },
            {
                "player_id_last4": "0003", "display_name": "Checked Player",
                "progression_checked": True,
                "grenade_balance": {
                    "status": "pass", "current_count": 75, "expected_minimum": 75,
                    "next_milestone": 123, "remaining_to_next_milestone": 48,
                },
            },
        ]
        report = build_copyable_report(summary)
        self.assertIn("Progression / Grenade balance — grouped by milestone", report)
        self.assertIn("Next milestone 56", report)
        self.assertIn("Next milestone 123", report)
        self.assertIn("Pending Player A (…0002)", report)
        self.assertIn("Checked Player (…0003)", report)
        self.assertIn("Pending · no claimable card visible · 1", report)
        self.assertIn("Checked · no reward claimed · 1", report)

if __name__ == "__main__":
    unittest.main()
