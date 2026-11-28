import re
import shutil
import subprocess
import unittest

from account_activity_report import (
    build_account_activity_records,
    render_account_activity_section,
    summarize_account_activity,
)
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
                "daily_claimed_this_run": 1,
                "standard_store_claimed_this_run": 3,
                "promotional_claims_this_run": 0,
                "event_reward_claims_this_run": 0,
                "progression_claimed_this_run": 0,
                "loyalty_claimed_this_run": 0,
                "all_claims_this_run": 4,
            },
            "player_results": [
                {
                    "player_id_last4": "AB12",
                    "display_name": "<Player One>",
                    "status": "Success",
                    "processing_mode": "full_claim",
                    "standard_rewards_checked": True,
                    "has_loyalty": False,
                    "daily_claimed_this_run": 1,
                    "daily_portal_status": "claimed",
                    "daily_unconfirmed": False,
                    "standard_store_claimed_this_run": 3,
                    "standard_store_claimed_indices": [1, 2, 3],
                    "standard_store_portal_status": ["claimed", "claimed", "claimed"],
                    "progression_checked": True,
                    "progression_claimed_this_run": 0,
                    "loyalty_claimed_this_run": 0,
                    "event_reward_claims_this_run": 0,
                    "retry_required": False,
                    "grenade_balance": {
                        "status": "pass", "current_count": 34,
                        "expected_minimum": 24, "next_milestone": 56,
                        "remaining_to_next_milestone": 22,
                    },
                    "promotion_scan_completed": True,
                    "promotions_observed": [],
                    "event_rewards": [],
                },
                {
                    "player_id_last4": "CD34",
                    "display_name": None,
                    "status": "Partial",
                    "processing_mode": "full_claim",
                    "standard_rewards_checked": True,
                    "daily_claimed_this_run": 0,
                    "daily_portal_status": "unknown",
                    "daily_unconfirmed": True,
                    "standard_store_claimed_this_run": 0,
                    "standard_store_unconfirmed_indices": [2],
                    "standard_store_portal_status": ["claimed", "available", "claimed"],
                    "progression_checked": False,
                    "progression_deferred": True,
                    "progression_deferred_reason": "No currently visible claimable reward.",
                    "progression_inspection_slots_used": 2,
                    "progression_claimed_this_run": 0,
                    "loyalty_claimed_this_run": 0,
                    "event_reward_claims_this_run": 0,
                    "retry_required": True,
                    "fail_reason": "Daily and Store confirmation pending",
                    "event_rewards": [],
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
            "Reward Summary",
        ):
            self.assertIn(expected, report)

    def test_report_escapes_untrusted_display_names_and_reasons(self):
        report = build_copyable_report(self.summary)
        self.assertNotIn("&lt;Player One&gt;", report)
        self.assertNotIn("<Player One>", report)
        self.assertIn("not confirmed by the portal", report)
        self.assertIn("Player …CD34", report)

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
        self.assertIn("<strong>0</strong>", report)
        self.assertIn("Need attention", report)

    def test_report_includes_per_account_progression_balance(self):
        report = build_copyable_report(self.summary)
        self.assertIn("Reward Summary", report)
        self.assertIn("1 balance observation(s)", report)
        self.assertIn("1 fresh", report)
        self.assertNotIn("34 Grenades", report)
        self.assertNotIn("Next milestone 56", report)
        self.assertNotIn("Progression / Grenade balance — grouped by milestone", report)

    def test_pending_grenade_baseline_remains_visible_as_account_attention(self):
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
            dict(self.summary["player_results"][0]),
            {
                "player_id_last4": "CD34",
                "display_name": "Player Two",
                "status": "Success",
                "processing_mode": "full_claim",
                "standard_rewards_checked": True,
                "progression_checked": True,
                "daily_claimed_this_run": 1,
                "daily_portal_status": "claimed",
                "standard_store_claimed_this_run": 3,
                "standard_store_portal_status": ["claimed", "claimed", "claimed"],
                "grenade_balance": {
                    "status": "pending_baseline", "current_count": 20,
                    "expected_minimum": None, "next_milestone": 56,
                },
                "event_rewards": [],
            },
        ]
        report = build_copyable_report(pending)
        self.assertIn("ATTENTION REQUIRED", report)
        self.assertIn("Pending baseline", report)
        self.assertNotIn("Player Two · …CD34", report)

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
        self.assertIn("Full-cycle coverage", report)
        self.assertIn("36/36 accounts complete", report)
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
        self.assertEqual(result.returncode, 0, msg=f"Generated report JavaScript is invalid:\n{result.stderr}")

    def test_deferred_progression_is_counted_and_overlay_is_explained_safely(self):
        deferred = dict(self.summary["player_results"][0])
        deferred.update({
            "progression_deferred": True,
            "progression_deferred_reason": (
                "popup/overlay safety guard blocked inspection "
                "(unknown_fullscreen_overlay_blocks_page; overlays=1; "
                "popup_context={\"url\":\"https://internal.invalid\"})"
            ),
            "progression_last_outcome_category": "overlay_blocked",
            "progression_unconfirmed": False,
            "progression_claim_attempted": False,
            "progression_inspection_slots_used": 4,
            "progression_inspection_attempts": 4,
            "progression_manual_attention_required": True,
        })
        summary = dict(self.summary)
        summary["player_results"] = [deferred]
        report = build_copyable_report(summary)
        self.assertIn(">ATTENTION REQUIRED</span>", report)
        self.assertIn("1 pending Progression inspection(s) remain queued", report)
        self.assertIn("Blocked by an on-screen popup or overlay", report)
        self.assertIn("all 4/4 safe inspection slots", report)
        self.assertIn("manual attention", report)
        self.assertNotIn("unknown_fullscreen_overlay_blocks_page", report)
        self.assertNotIn("internal.invalid", report)
        self.assertNotIn("popup_context", report)
        self.assertIn("Unconfirmed claims: <strong>0</strong>", report)

    def test_empty_scan_is_pending_not_no_rewards_and_uses_safe_next_action(self):
        deferred = dict(self.summary["player_results"][0])
        deferred.update({
            "progression_deferred": True,
            "progression_deferred_reason": (
                "Queued Progression inspection found no currently visible claimable reward; "
                "keeping the queue open for a later safe pass."
            ),
            "progression_last_outcome_category": "empty_scan",
            "progression_same_outcome_count": 4,
            "progression_inspection_slots_used": 4,
            "progression_inspection_attempts": 4,
        })
        summary = dict(self.summary)
        summary["player_results"] = [deferred]
        report = build_copyable_report(summary)
        self.assertIn(">HEALTHY</span>", report)
        self.assertIn("Progression reinspection queue: 1 account(s) pending", report)
        self.assertIn("<strong>0</strong>", report)
        self.assertNotIn("Pending Player", report)
        self.assertNotIn("An empty scan is not proof", report)

    def test_generic_claim_exception_remains_visible_with_deferred_progression(self):
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
        self.assertIn("Progression-only recovery raised RuntimeError", report)

    def test_unconfirmed_progression_claim_remains_attention_and_exact_target_is_preserved(self):
        deferred = dict(self.summary["player_results"][0])
        deferred.update({
            "progression_deferred": True,
            "progression_unconfirmed": True,
            "progression_deferred_reason": "Prior Progression claim remains unconfirmed.",
            "progression_unconfirmed_card_signature": "Grenade reward 3",
        })
        summary = dict(self.summary)
        summary["player_results"] = [deferred]
        report = build_copyable_report(summary)
        self.assertIn(">ATTENTION REQUIRED</span>", report)
        self.assertIn("Claim attempted · portal confirmation missing", report)
        self.assertIn("same card to show Delivered", report)

    def test_carry_forward_balance_is_explicitly_stale_and_keeps_date_minimum_context(self):
        deferred = dict(self.summary["player_results"][0])
        deferred.update({
            "progression_deferred": True,
            "progression_last_outcome_category": "empty_scan",
            "progression_deferred_reason": "No currently visible claimable reward.",
            "progression_unconfirmed": False,
            "grenade_balance": None,
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
        self.assertIn(">HEALTHY</span>", report)
        self.assertIn("1 balance observation(s) · 0 fresh · 1 carried forward · 0 fresh validation(s) passed", report)
        self.assertNotIn("66 / 66 at observation", report)

    def test_legacy_carried_balance_does_not_infer_a_pass(self):
        deferred = dict(self.summary["player_results"][0])
        deferred.update({
            "progression_deferred": True,
            "progression_last_outcome_category": "empty_scan",
            "progression_deferred_reason": "No currently visible claimable reward.",
            "progression_unconfirmed": False,
            "grenade_balance": None,
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
        self.assertIn("1 not validated", report)
        self.assertNotIn("Status not retained", report)
        self.assertIn(">ATTENTION REQUIRED</span>", report)

    def test_partially_confirmed_progression_keeps_inspection_pending(self):
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
        self.assertIn("1 confirmed · 1 pending", report)
        self.assertIn("another eligible safe inspection", report.lower())

    def test_saved_grenade_deficit_remains_attention_when_inspection_is_deferred(self):
        deferred = dict(self.summary["player_results"][0])
        deferred.update({
            "progression_deferred": True,
            "progression_last_outcome_category": "empty_scan",
            "progression_deferred_reason": "No currently visible claimable reward.",
            "progression_unconfirmed": False,
            "grenade_balance": None,
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
        self.assertIn("Below date minimum", report)
        self.assertIn("60 Grenades", report)

    def test_unknown_daily_portal_claim_provenance_is_not_credited_to_the_bot(self):
        players = [
            {
                "player_id_last4": "B91F", "display_name": "Umang1984",
                "status": "Success", "processing_mode": "full_claim",
                "standard_rewards_checked": True, "daily_claimed_this_run": 0,
                "daily_portal_status": "portal_claimed", "daily_unconfirmed": False,
                "preexisting_portal_claims": [],
            },
            {
                "player_id_last4": "62E4", "display_name": "annu411",
                "status": "Success", "processing_mode": "full_claim",
                "standard_rewards_checked": True, "daily_claimed_this_run": 0,
                "daily_portal_status": "portal_claimed", "daily_unconfirmed": False,
                "preexisting_portal_claims": [],
            },
        ]
        summary = dict(self.summary)
        summary["player_results"] = players
        summary["accounts_processed"] = 2
        report = build_copyable_report(summary)
        self.assertIn("Umang1984 · …B91F", report)
        self.assertIn("annu411 · …62E4", report)
        self.assertEqual(report.count("Portal says claimed"), 2)
        self.assertIn("Umang1984 · …B91F", report)
        self.assertIn("annu411 · …62E4", report)
        self.assertIn("actor unknown", report.lower())
        self.assertNotIn("Claimed by bot", report)

    def test_audited_preexisting_daily_state_is_not_attributed_to_bot(self):
        player = {
            "player_id_last4": "62E4", "display_name": "annu411",
            "status": "Success", "processing_mode": "full_claim",
            "standard_rewards_checked": True, "daily_claimed_this_run": 0,
            "daily_portal_status": "portal_claimed", "daily_unconfirmed": False,
            "preexisting_portal_claims": [{
                "reward_category": "daily", "reward_key": "daily",
                "actor": "unknown_portal_actor",
            }],
        }
        record = build_account_activity_records([player])[0]
        self.assertEqual(record["daily"]["state"], "portal_claimed")
        self.assertIn("before bot interaction", record["daily"]["detail"])
        self.assertNotIn("Claimed by bot", record["daily"]["label"])

    def test_popup_blocked_before_click_is_not_reported_as_failed_claim(self):
        player = {
            "pid": "Gvk9916ABC6061", "display_name": "Gvk9916",
            "status": "Partial", "processing_mode": "full_claim",
            "standard_rewards_checked": True,
            "daily_claimed_this_run": 1, "daily_portal_status": "claimed",
            "daily_unconfirmed": False,
            "standard_store_claimed_this_run": 3,
            "standard_store_claimed_indices": [1, 2, 3],
            "standard_store_portal_status": ["claimed", "claimed", "claimed"],
            "promotion_scan_completed": True, "promotion_popup_blocked": True,
            "promotions_observed": [{
                "title": "Store promotion",
                "status": "popup_blocked",
            }],
            "promotion_detection_failed": False,
            "progression_checked": True,
            "event_rewards": [],
        }
        record = build_account_activity_records([player])[0]
        self.assertEqual(record["daily"]["state"], "claimed_by_bot")
        self.assertEqual(record["store"]["count"], 3)
        self.assertIn("before any claim click", record["promotions"])
        self.assertIn("no claim was attempted", record["promotions"])
        self.assertTrue(record["attention_required"])
        self.assertNotIn("Daily retry", record["next_action"])

    def test_promotional_only_categories_show_not_checked_not_zero(self):
        player = {
            "pid": "PROMO0001", "display_name": "Promo Player",
            "status": "Promotional Check Complete", "processing_mode": "promotional_only",
            "standard_rewards_checked": False, "daily_portal_status": "not_checked",
            "daily_claimed_this_run": 0, "standard_store_claimed_this_run": 0,
            "progression_checked": False, "progression_deferred": False,
            "promotion_scan_completed": True, "promotions_observed": [],
            "event_rewards": [],
        }
        records = build_account_activity_records([player])
        categories = {row["label"]: row["value"] for row in summarize_account_activity(records)}
        self.assertEqual(categories["Daily"], "Not checked")
        self.assertEqual(categories["Standard Store"], "Not checked")
        self.assertEqual(categories["Progression"], "Not checked")
        self.assertIn("scanned", categories["Promotions"])
        report = build_copyable_report({
            **self.summary,
            "player_results": [player],
            "accounts_processed": 1,
            "configured_player_count": 36,
            "totals": {"all_claims_this_run": 0},
        })
        self.assertIn(">Daily</strong><span", report)
        self.assertIn(">Standard Store</strong><span", report)
        self.assertNotIn(">Daily</strong><span>0</span>", report)

    def test_identical_accounts_are_grouped_and_ids_remain_masked(self):
        players = [
            {
                "pid": "SENSITIVEPLAYER0001", "display_name": "Player One",
                "status": "Promotional Check Complete", "processing_mode": "promotional_only",
                "progression_checked": False,
            },
            {
                "pid": "SENSITIVEPLAYER0002", "display_name": "Player Two",
                "status": "Promotional Check Complete", "processing_mode": "promotional_only",
                "progression_checked": False,
            },
        ]
        records = build_account_activity_records(players)
        report = render_account_activity_section(records)
        self.assertIn("Reward Summary", report)
        self.assertNotIn("account-activity-card", report)
        self.assertNotIn("Player One", report)
        self.assertNotIn("Player Two", report)
        self.assertNotIn("SENSITIVEPLAYER0001", report)
        self.assertNotIn("SENSITIVEPLAYER0002", report)

    def test_copy_report_uses_the_same_grouped_account_renderer(self):
        players = [
            dict(self.summary["player_results"][0], player_id_last4="AB12", display_name="Player One"),
            dict(self.summary["player_results"][0], player_id_last4="CD34", display_name="Player Two"),
        ]
        summary = {**self.summary, "player_results": players, "accounts_processed": 2}
        report = build_copyable_report(summary)
        self.assertIn("Reward Summary", report)
        self.assertNotIn("account-activity-card", report)

    def test_no_milestone_grouping_or_multi_player_rows_remain(self):
        players = [
            {
                "player_id_last4": "0001", "display_name": "Claimed Player",
                "status": "Success", "processing_mode": "full_claim",
                "standard_rewards_checked": True, "daily_claimed_this_run": 1,
                "daily_portal_status": "claimed", "standard_store_claimed_this_run": 3,
                "standard_store_portal_status": ["claimed", "claimed", "claimed"],
                "progression_claimed_this_run": 1, "progression_checked": True,
                "grenade_balance": {
                    "status": "pass", "current_count": 18, "expected_minimum": 18,
                    "next_milestone": 56, "remaining_to_next_milestone": 38,
                },
                "event_rewards": [],
            },
            {
                "player_id_last4": "0002", "display_name": "Pending Player",
                "status": "Promotional Check Complete", "processing_mode": "promotional_only",
                "progression_deferred": True, "progression_checked": False,
                "progression_last_outcome_category": "empty_scan",
                "progression_inspection_slots_used": 4,
            },
        ]
        report = build_copyable_report({**self.summary, "player_results": players})
        self.assertIn("Reward Summary", report)
        self.assertNotIn("account-activity-card", report)
        self.assertNotIn("Claimed Player", report)
        self.assertNotIn("Pending Player", report)
        self.assertNotIn("grouped by milestone", report)
        self.assertNotIn("PP status and accounts", report)

    def test_invalid_summary_type_is_rejected(self):
        with self.assertRaises(TypeError):
            build_copyable_report([])


if __name__ == "__main__":
    unittest.main()
