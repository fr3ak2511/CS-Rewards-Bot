import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from master_claimer import (
    _current_reset_date,
    _full_claim_recovery_candidate,
    _result_needs_recovery,
    _issue_identity_label,
    _portal_claim_reset_day_count,
    build_preexisting_portal_claim_section,
    build_standard_attention_section,
    build_deferred_checks_section,
    record_preexisting_portal_claim,
    record_player_display_name,
    _clear_promotion_unconfirmed_attempt,
    _record_promotion_history,
    _player_claim_total,
    _promotion_detail_worthy,
    _record_full_claim_coverage,
    _recovery_targets_from_result,
    _mark_daily_recovery_slot_attempt,
    _preserve_queued_progression_state,
    _run_queued_progression_inspection,
    process_player_progression_only,
    _summary_counts,
    build_email,
    build_event_rewards_section,
    build_promotional_section,
    get_ist_time,
    write_run_summary,
)


class ClaimCountAndEmailReportingTests(unittest.TestCase):
    def test_first_deferred_reinspection_uses_existing_generic_scan_without_target_keyword(self):
        driver = type("ProgressionDriver", (), {})()

        def inspect(
            target_driver, player_id, *, max_claims_per_visit,
            require_unique_claimable_card, defer_if_more_claimable
        ):
            self.assertEqual(max_claims_per_visit, 1)
            self.assertTrue(require_unique_claimable_card)
            self.assertTrue(defer_if_more_claimable)
            target_driver._progression_unconfirmed = False
            target_driver._progression_unconfirmed_card_signature = None
            target_driver._progression_deferred = False
            target_driver._progression_deferred_reason = None
            target_driver._progression_claim_attempted = False
            target_driver._progression_checked = True
            return 0

        stats = {"progression": 0}
        with patch("master_claimer.claim_progression_program_rewards", side_effect=inspect) as claim:
            _run_queued_progression_inspection(
                driver, "PLAYER0001", stats,
                {"status": "deferred", "requires_exact_target": False, "target_card_signature": None},
            )
        claim.assert_called_once_with(
            driver, "PLAYER0001", max_claims_per_visit=1,
            require_unique_claimable_card=True, defer_if_more_claimable=True,
        )
        self.assertTrue(stats["progression_reinspection_attempted"])
        self.assertTrue(stats["progression_checked"])
        self.assertFalse(stats["progression_deferred"])

    def test_prior_unconfirmed_reinspection_passes_exact_target_signature(self):
        driver = type("ProgressionDriver", (), {})()

        def inspect(target_driver, player_id, *, target_card_signature, max_claims_per_visit):
            self.assertEqual(max_claims_per_visit, 1)
            self.assertEqual(player_id, "PLAYER0001")
            self.assertEqual(target_card_signature, "Grenade reward 3")
            target_driver._progression_unconfirmed = False
            target_driver._progression_unconfirmed_card_signature = None
            target_driver._progression_deferred = False
            target_driver._progression_deferred_reason = None
            target_driver._progression_claim_attempted = False
            target_driver._progression_checked = True
            return 1

        stats = {"progression": 0}
        with patch("master_claimer.claim_progression_program_rewards", side_effect=inspect) as claim:
            _run_queued_progression_inspection(
                driver, "PLAYER0001", stats,
                {"status": "claim_unconfirmed", "requires_exact_target": True,
                 "target_card_signature": "Grenade reward 3"},
            )
        claim.assert_called_once_with(
            driver, "PLAYER0001", target_card_signature="Grenade reward 3",
            max_claims_per_visit=1,
        )
        self.assertEqual(stats["progression"], 1)
        self.assertTrue(stats["progression_checked"])

    def test_preclick_exception_preserves_prior_exact_target_as_unconfirmed(self):
        driver = type("ProgressionDriver", (), {})()
        stats = {"progression": 0}
        with patch("master_claimer.claim_progression_program_rewards", side_effect=RuntimeError("inspection failed")):
            _run_queued_progression_inspection(
                driver, "PLAYER0001", stats,
                {"status": "claim_unconfirmed", "requires_exact_target": True,
                 "target_card_signature": "Grenade reward 3"},
            )
        self.assertTrue(stats["progression_deferred"])
        self.assertTrue(stats["progression_unconfirmed"])
        self.assertEqual(stats["progression_unconfirmed_card_signature"], "Grenade reward 3")
        self.assertFalse(stats["progression_claim_attempted"])
        self.assertFalse(stats["progression_checked"])

    def test_prior_unconfirmed_without_signature_fails_closed_without_claim_call(self):
        driver = type("ProgressionDriver", (), {})()
        stats = {"progression": 0}
        with patch("master_claimer.claim_progression_program_rewards") as claim:
            _run_queued_progression_inspection(
                driver, "PLAYER0001", stats,
                {"status": "claim_unconfirmed", "requires_exact_target": True,
                 "target_card_signature": None},
            )
        claim.assert_not_called()
        self.assertTrue(stats["progression_deferred"])
        self.assertTrue(stats["progression_unconfirmed"])
        self.assertFalse(stats["progression_checked"])
        self.assertFalse(stats["progression_claim_attempted"])


    def test_deferred_unconfirmed_progression_stays_out_of_broad_recovery_scope(self):
        deferred = {
            "processing_mode": "full_claim",
            "status": "Partial",
            "progression_deferred": True,
            "progression_unconfirmed": True,
            "progression_unconfirmed_card_signature": "Exact queued card",
        }
        self.assertFalse(_full_claim_recovery_candidate(deferred))
        self.assertTrue(_full_claim_recovery_candidate({
            **deferred, "daily_unconfirmed": True,
        }))

    def test_deferred_exact_progression_target_is_not_serialized_into_general_retry(self):
        targets = _recovery_targets_from_result({
            "status": "Partial",
            "daily_unconfirmed": True,
            "store_unconfirmed_indices": [2],
            "progression_deferred": True,
            "progression_unconfirmed": True,
            "progression_unconfirmed_card_signature": "Exact queued card",
            "event_rewards": [],
        })
        self.assertTrue(targets["daily_unconfirmed"])
        self.assertEqual(targets["store_unconfirmed_indices"], [2])
        self.assertFalse(targets["progression_unconfirmed"])
        self.assertIsNone(targets["progression_card_signature"])

    def test_queued_progression_click_unconfirmed_does_not_become_general_retry(self):
        result = {
            "processing_mode": "full_claim",
            "status": "Partial",
            "progression_deferred": False,
            "progression_reinspection_attempted": True,
            "progression_unconfirmed": True,
            "progression_unconfirmed_card_signature": "Exact queued card",
        }
        self.assertFalse(_full_claim_recovery_candidate(result))
        targets = _recovery_targets_from_result(result)
        self.assertFalse(targets["progression_unconfirmed"])
        self.assertIsNone(targets["progression_card_signature"])

    def test_daily_only_or_other_non_inspection_run_preserves_queue(self):
        meta = {
            "progression_deferred_inspections": {
                "PLAYER0001": {
                    "status": "claim_unconfirmed",
                    "requires_exact_target": True,
                    "target_card_signature": "Exact queued card",
                }
            }
        }
        daily_result = {
            "status": "Success",
            "progression_checked": True,
            "progression_deferred": False,
            "progression_unconfirmed": False,
        }
        preserved = _preserve_queued_progression_state(
            meta, "PLAYER0001", daily_result,
            "Daily-only recovery does not inspect Progression.",
        )
        self.assertFalse(preserved["progression_checked"])
        self.assertTrue(preserved["progression_deferred"])
        self.assertTrue(preserved["progression_unconfirmed"])
        self.assertEqual(
            preserved["progression_unconfirmed_card_signature"],
            "Exact queued card",
        )
        self.assertFalse(preserved["progression_claim_attempted"])
        self.assertIn("Daily-only", preserved["progression_deferred_reason"])

    def test_promotional_only_popup_does_not_qualify_for_full_claim_recovery(self):
        result = {
            "pid": "PROMO0001",
            "processing_mode": "promotional_only",
            "status": "Partial",
            "retry_required": True,
            "promotion_popup_blocked": True,
            "promotions_observed": [{"status": "popup_blocked"}],
            "event_rewards_retry_required": False,
        }
        self.assertFalse(_full_claim_recovery_candidate(result))

    def test_full_claim_with_only_promotion_detection_failure_is_not_full_claim_retry(self):
        result = {
            "pid": "FULLPROMO0001",
            "processing_mode": "full_claim",
            "status": "Partial",
            "retry_required": True,
            "promotion_detection_failed": True,
            "promotion_popup_blocked": True,
            "daily_unconfirmed": False,
            "store_unconfirmed_indices": [],
            "progression_unconfirmed": False,
            "event_rewards_retry_required": False,
            "event_rewards": [],
        }
        self.assertFalse(_full_claim_recovery_candidate(result))

    def test_full_claim_standard_daily_failure_remains_recoverable(self):
        result = {
            "pid": "FULLDAILY0001",
            "processing_mode": "full_claim",
            "status": "Partial",
            "daily_unconfirmed": True,
            "store_unconfirmed_indices": [],
            "progression_unconfirmed": False,
            "event_rewards_retry_required": False,
            "event_rewards": [],
        }
        self.assertTrue(_full_claim_recovery_candidate(result))

    def test_backup_reconciliation_standard_store_failure_remains_recoverable(self):
        result = {
            "pid": "BACKUPSTORE0001",
            "processing_mode": "backup_reconciliation",
            "status": "Partial",
            "daily_unconfirmed": False,
            "store_unconfirmed_indices": [1],
            "progression_unconfirmed": False,
            "event_rewards_retry_required": False,
            "event_rewards": [],
        }
        self.assertTrue(_full_claim_recovery_candidate(result))

    def test_event_reward_verification_failure_remains_recoverable(self):
        result = {
            "pid": "EVENT0001",
            "processing_mode": "full_claim",
            "status": "Partial",
            "event_rewards_retry_required": True,
            "event_rewards": [],
        }
        self.assertTrue(_full_claim_recovery_candidate(result))

    def test_promotional_only_result_does_not_overwrite_full_claim_coverage(self):
        meta = {
            "full_claim_coverage": {
                "PROMO0002": {
                    "reset_date": "2026-11-07",
                    "status": "complete",
                    "retry_required": False,
                    "standard_rewards_audited_reset": "2026-11-07",
                }
            }
        }
        original = dict(meta["full_claim_coverage"]["PROMO0002"])
        result = {
            "processing_mode": "promotional_only",
            "status": "Partial",
            "retry_required": True,
            "promotion_popup_blocked": True,
        }
        self.assertFalse(_record_full_claim_coverage(meta, "PROMO0002", result, "Promotional Check #1"))
        self.assertEqual(meta["full_claim_coverage"]["PROMO0002"], original)


    def test_daily_recovery_slot_marker_is_durable_and_at_most_once(self):
        reset_date = _current_reset_date()
        meta = {
            "full_claim_coverage": {
                "PENDINGDAILY001": {
                    "reset_date": reset_date,
                    "status": "pending",
                    "retry_required": True,
                }
            }
        }
        with patch("master_claimer.save_bot_meta") as save:
            self.assertTrue(_mark_daily_recovery_slot_attempt(
                meta, "PENDINGDAILY001", "Promotional Check #2"
            ))
            self.assertFalse(_mark_daily_recovery_slot_attempt(
                meta, "PENDINGDAILY001", "Promotional Check #2"
            ))
            self.assertTrue(_mark_daily_recovery_slot_attempt(
                meta, "PENDINGDAILY001", "Promotional Check #3"
            ))
        entry = meta["full_claim_coverage"]["PENDINGDAILY001"]
        self.assertEqual(
            entry["daily_recovery_slots_attempted"],
            ["Promotional Check #2", "Promotional Check #3"],
        )
        self.assertEqual(entry["daily_recovery_attempts"], 2)
        self.assertEqual(save.call_count, 2)

    def test_daily_recovery_slot_markers_survive_coverage_refresh(self):
        reset_date = _current_reset_date()
        meta = {
            "full_claim_coverage": {
                "PENDINGDAILY002": {
                    "reset_date": reset_date,
                    "status": "pending",
                    "retry_required": True,
                    "daily_recovery_slots_attempted": ["Promotional Check #2"],
                    "daily_recovery_attempts": 1,
                    "daily_recovery_last_slot": "Promotional Check #2",
                    "daily_recovery_last_attempt_at": "2026-11-13T13:35:10",
                }
            }
        }
        result = {
            "processing_mode": "full_claim",
            "status": "Partial",
            "daily_unconfirmed": True,
            "store_unconfirmed_indices": [],
            "progression_unconfirmed": False,
            "event_rewards_retry_required": False,
            "event_rewards": [],
        }
        self.assertTrue(_record_full_claim_coverage(
            meta, "PENDINGDAILY002", result, "Promotional Check #2"
        ))
        entry = meta["full_claim_coverage"]["PENDINGDAILY002"]
        self.assertEqual(entry["status"], "pending")
        self.assertEqual(entry["daily_recovery_slots_attempted"], ["Promotional Check #2"])
        self.assertEqual(entry["daily_recovery_attempts"], 1)
        self.assertEqual(entry["daily_recovery_last_slot"], "Promotional Check #2")


    def test_full_claim_promotion_failure_keeps_clean_standard_coverage(self):
        meta = {}
        result = {
            "processing_mode": "full_claim",
            "status": "Partial",
            "retry_required": True,
            "promotion_detection_failed": True,
            "daily_unconfirmed": False,
            "store_unconfirmed_indices": [],
            "standard_rewards_checked": True,
            "progression_unconfirmed": False,
            "event_rewards_retry_required": False,
            "event_rewards": [],
        }
        self.assertTrue(_record_full_claim_coverage(meta, "FULLPROMO0002", result, "Primary Run"))
        entry = meta["full_claim_coverage"]["FULLPROMO0002"]
        self.assertEqual(entry["status"], "complete")
        self.assertFalse(entry["retry_required"])
        self.assertEqual(entry["standard_rewards_audited_reset"], entry["reset_date"])

    @staticmethod
    def event_results():
        return [{
            "program_id": "pgp_eLiQHDMchen",
            "name": "Lost Soul Rewards",
            "slug": "halloween-2026",
            "points_label": "Lost Souls",
            "balance": 1100,
            "next_threshold": 1300,
            "tiers_total": 14,
            "tiers_delivered": 2,
            "claims_this_run": [
                {"threshold": 400, "reward": ["Reward A"], "status": "claimed_this_run"},
                {"threshold": 900, "reward": ["Reward B"], "status": "claimed_this_run"},
            ],
            "retry_required": False,
            "error": None,
        }]

    @classmethod
    def sample_player(cls):
        return {
            "pid": "TESTPLAYER0001",
            "display_name": "Halloween Test Player",
            "processing_mode": "full_claim",
            "standard_rewards_checked": True,
            "progression_checked": True,
            "status": "Claims Made",
            "daily": 1,
            "store": 2,
            "store_temp": 1,
            "progression": 1,
            "loyalty": 0,
            "daily_skipped": False,
            "store_skipped": [False, False, False],
            "has_loyalty": False,
            "loyalty_skipped": False,
            "promotions_observed": [{
                "title": "Free Weekly Reward",
                "status": "claimed_this_run",
                "claim_count": 1,
                "button_text": "Free",
                "card_text": "Free Weekly Reward · Free",
            }],
            "promotion_detection_failed": False,
            "retry_required": False,
            "event_rewards": cls.event_results(),
        }

    def test_event_claims_are_counted_once_in_player_total(self):
        result = {
            "daily": 0, "store": 0, "store_temp": 0,
            "progression": 0, "loyalty": 0,
            "event_rewards": self.event_results(),
        }
        self.assertEqual(_player_claim_total(result), 2)

    def test_player_total_combines_all_reward_categories_once(self):
        result = {
            "daily": 1, "store": 2, "store_temp": 1,
            "progression": 3, "loyalty": 1,
            "event_rewards": self.event_results(),
        }
        self.assertEqual(_player_claim_total(result), 10)

    def test_paid_or_discounted_promotion_is_excluded_from_email(self):
        paid = {
            "status": "claimed_this_run",
            "button_text": "Claim",
            "card_text": "Premium bundle ₹499 · 30% off",
        }
        self.assertFalse(_promotion_detail_worthy(paid))
        section = build_promotional_section([{
            "pid": "TESTPLAYER0001",
            "display_name": "Test Player",
            "promotions_observed": [{
                **paid, "title": "Premium Bundle", "claim_count": 1,
            }],
        }])
        self.assertEqual(section, "")

    def test_free_claimed_promotion_appears_under_store_promotions(self):
        section = build_promotional_section([self.sample_player()])
        self.assertIn("Promotional Offers", section)
        self.assertIn("Free Weekly Reward", section)
        self.assertNotIn("Trick &amp; Treat", section)

    def test_event_rewards_are_separate_and_only_render_when_present(self):
        player = self.sample_player()
        section = build_event_rewards_section([player])
        self.assertIn("Active Event Rewards", section)
        self.assertIn("Lost Soul Rewards", section)
        self.assertIn("1,100 Lost Souls", section)
        self.assertIn("Next tier: 1,300", section)
        self.assertIn("2/14 delivered", section)
        self.assertIn("400 points", section)
        self.assertNotIn("Active Event Rewards", build_event_rewards_section([{"pid": "NONE", "event_rewards": []}]))

    def test_summary_counts_exclude_paid_promotion_from_promotional_claim_total(self):
        player = self.sample_player()
        # Simulate two confirmed Store promotion claims in run telemetry: one free
        # and reportable, one paid and intentionally hidden from the email.
        player["store_temp"] = 2
        player["promotions_observed"].append({
            "title": "Paid Pack", "status": "claimed_this_run", "claim_count": 1,
            "button_text": "Claim", "card_text": "Paid pack ₹250",
        })
        counts = _summary_counts([player])
        self.assertEqual(counts["promotional_claims_made"], 1)
        # Internal run telemetry still reflects the actual confirmed count.
        self.assertEqual(counts["claims_made"], 8)

        report = build_email(
            [player], "Manual Run", 1,
            get_ist_time() - timedelta(seconds=30), {},
        )
        # The core headline is Daily + standard Store only; other claims stay separate.
        self.assertIn("<span>🎁 Core claims</span><strong>3/4</strong>", report)
        self.assertIn("Progression Program", report)
        self.assertIn("1 reward confirmed", report)
        self.assertIn("Other Confirmed Rewards", report)
        self.assertNotIn("Paid Pack", report)

    def test_free_bundle_is_not_mistaken_for_a_paid_offer(self):
        free_bundle = {
            "title": "Free Bonus Bundle", "status": "claimed_this_run",
            "claim_count": 1, "button_text": "Free",
            "card_text": "Free Bonus Bundle · Free",
        }
        self.assertTrue(_promotion_detail_worthy(free_bundle))
        section = build_promotional_section([{
            "pid": "TESTPLAYER0001", "display_name": "Test Player",
            "promotions_observed": [free_bundle],
        }])
        self.assertIn("Free Bonus Bundle", section)

    def test_currency_code_after_price_and_discount_language_are_filtered(self):
        self.assertFalse(_promotion_detail_worthy({
            "status": "available", "button_text": "Claim",
            "card_text": "Premium reward · 4.99 USD",
        }))
        self.assertFalse(_promotion_detail_worthy({
            "status": "available", "button_text": "Claim",
            "card_text": "Premium reward · Save 20% today",
        }))

    def test_email_includes_obvious_link_to_browser_copy_report(self):
        player = self.sample_player()
        with patch.dict("os.environ", {
            "GITHUB_RUN_ID": "12345",
            "GITHUB_REPOSITORY": "fr3ak2511/CS-Rewards-Bot",
        }):
            report = build_email(
                [player], "Manual Run", 1,
                get_ist_time() - timedelta(minutes=2), {},
            )
        self.assertIn("Open Copyable Report", report)
        self.assertIn(
            "href='https://github.com/fr3ak2511/CS-Rewards-Bot/actions/runs/12345#artifacts'",
            report,
        )
        self.assertIn("Email apps block clipboard scripts.", report)
        self.assertIn("use its Copy Report button", report)

    def test_email_is_compact_and_does_not_surface_streak_or_schedule(self):
        player = self.sample_player()
        player["event_rewards"] = []
        html = build_email(
            [player], "Manual Run", 1,
            get_ist_time() - timedelta(minutes=2),
            {"streak": {"current": 99, "best": 100}},
        )
        self.assertIn("<h1>Rewards Run Report</h1>", html)
        self.assertIn("Completed", html)
        self.assertIn("Core claims", html)
        self.assertIn("Accounts processed", html)
        self.assertIn("Need attention", html)
        self.assertIn("Daily", html)
        self.assertIn("Store Rewards", html)
        self.assertNotIn("Day Streak", html)
        self.assertNotIn("Best streak", html)
        self.assertNotIn("Scheduled Runs", html)
        self.assertNotIn("Slowest", html)
        self.assertNotIn("Avg", html)

    def test_email_marks_unresolved_accounts_as_attention_required(self):
        player = self.sample_player()
        player["status"] = "Partial"
        player["retry_required"] = True
        player["fail_reason"] = "Portal confirmation pending"
        html = build_email(
            [player], "Manual Run", 1,
            get_ist_time() - timedelta(seconds=30),
            {},
        )
        self.assertIn("Attention Required", html)
        self.assertIn("Portal confirmation pending", html)
        self.assertIn(".v2-warn{color:#92400e;background-color:#fef3c7}", html)
        self.assertIn(".v2-exceptions{background-color:#fffbeb;border-color:#fcd34d}", html)



    def test_run_summary_writes_the_separate_copyable_html_artifact(self):
        started = get_ist_time() - timedelta(minutes=1)
        finished = get_ist_time()
        with tempfile.TemporaryDirectory() as folder:
            with patch("master_claimer.RUN_ARTIFACTS_DIR", folder):
                summary_path = write_run_summary(
                    [], "Manual Run", started, finished, True,
                    configured_player_count=0,
                )
            self.assertTrue(Path(summary_path).is_file())
            report_path = Path(folder) / "report.html"
            self.assertTrue(report_path.is_file())
            report = report_path.read_text(encoding="utf-8")
            self.assertIn("Copy Report", report)
            self.assertIn("navigator.clipboard.write", report)
            saved_summary = json.loads(Path(summary_path).read_text(encoding="utf-8"))
            self.assertEqual(saved_summary["totals"]["all_claims_this_run"], 0)
            self.assertEqual(saved_summary["reconciliation"]["status"], "matched")

    def test_hidden_paid_promotion_failure_does_not_trigger_email_attention(self):
        player = {
            "pid": "PAIDPROMO0001",
            "display_name": "Paid Promo Player",
            "processing_mode": "full_claim",
            "standard_rewards_checked": True,
            "progression_checked": True,
            "status": "Partial",
            "retry_required": True,
            "fail_reason": "At least one observed promotional claim was not confirmed by the portal",
            "daily": 0, "store": 0, "progression": 0, "loyalty": 0,
            "has_loyalty": False,
            "promotions_observed": [{
                "title": "Premium Pack", "status": "claim_unconfirmed",
                "button_text": "Claim", "card_text": "Premium Pack · ₹499 · 30% off",
            }],
            "event_rewards": [],
        }
        report = build_email(
            [player], "Manual Run", 1,
            get_ist_time() - timedelta(seconds=30), {},
        )
        self.assertIn("Completed", report)
        self.assertIn("<span>⚠️ Need attention</span><strong>0</strong>", report)
        self.assertNotIn("Premium Pack", report)
        self.assertNotIn("At least one observed promotional claim", report)

    def test_free_unconfirmed_promotion_still_triggers_email_attention(self):
        player = {
            "pid": "FREEPROMO0001",
            "display_name": "Free Promo Player",
            "processing_mode": "full_claim",
            "standard_rewards_checked": True,
            "progression_checked": True,
            "status": "Partial",
            "retry_required": True,
            "fail_reason": "At least one observed promotional claim was not confirmed by the portal",
            "daily": 0, "store": 0, "progression": 0, "loyalty": 0,
            "has_loyalty": False,
            "promotions_observed": [{
                "title": "Free Weekly Reward", "status": "claim_unconfirmed",
                "button_text": "Claim", "card_text": "Free Weekly Reward · Free",
            }],
            "event_rewards": [],
        }
        report = build_email(
            [player], "Manual Run", 1,
            get_ist_time() - timedelta(seconds=30), {},
        )
        self.assertIn("Attention Required", report)
        self.assertIn("<span>⚠️ Need attention</span><strong>1</strong>", report)
        self.assertIn("Free Weekly Reward", report)

    def test_promotional_only_run_marks_unchecked_reward_categories_as_not_checked(self):
        player = {
            "pid": "PROMOONLY0001", "display_name": "Promo Only Player",
            "processing_mode": "promotional_only", "status": "Promotional Check Complete",
            "daily": 0, "store": 0, "progression": 0, "loyalty": 0,
            "has_loyalty": False, "promotions_observed": [], "event_rewards": [],
        }
        report = build_email(
            [player], "Promo Backup", 1,
            get_ist_time() - timedelta(seconds=30), {},
        )
        self.assertNotIn("Core Daily Claims", report)
        self.assertIn("<span>🎁 Core claims</span><strong>—</strong>", report)
        self.assertIn("Not checked in this run", report)
        self.assertNotIn("<span>Daily</span><strong>0</strong>", report)

    def test_mixed_run_category_totals_only_include_checked_accounts(self):
        full = {
            "pid": "FULL0001", "display_name": "Full Player",
            "processing_mode": "full_claim", "standard_rewards_checked": True,
            "progression_checked": True, "has_loyalty": True,
            "status": "Claims Made", "daily": 1, "store": 2,
            "progression": 3, "loyalty": 4, "event_rewards": [],
        }
        backup_not_reopened = {
            "pid": "BACKUP0001", "display_name": "Backup Player",
            "processing_mode": "backup_reconciliation", "standard_rewards_checked": True,
            "progression_checked": False, "has_loyalty": False,
            "status": "Backup Reconciliation Complete", "daily": 5, "store": 6,
            "progression": 99, "loyalty": 99, "event_rewards": [],
        }
        backup_reopened = {
            "pid": "BACKUP0002", "display_name": "Reopened Backup Player",
            "processing_mode": "backup_reconciliation", "standard_rewards_checked": True,
            "progression_checked": True, "has_loyalty": False,
            "status": "Claims Made", "daily": 7, "store": 8,
            "progression": 9, "loyalty": 99, "event_rewards": [],
        }
        promo_only = {
            "pid": "PROMO0001", "display_name": "Promo Player",
            "processing_mode": "promotional_only", "status": "Promotional Check Complete",
            "daily": 50, "store": 50, "progression": 50, "loyalty": 50,
            "has_loyalty": False, "event_rewards": [],
        }
        report = build_email(
            [full, backup_not_reopened, backup_reopened, promo_only],
            "Mixed Run", 1, get_ist_time() - timedelta(seconds=30), {},
        )
        self.assertIn("<span>🎁 Core claims</span><strong>29</strong>", report)
        self.assertIn("<span>Daily Rewards</span><strong>13</strong><small>3 accounts checked</small>", report)
        self.assertIn("<span>Standard Store Rewards</span><strong>16</strong><small>3 accounts checked</small>", report)
        self.assertIn("12 rewards confirmed", report)
        self.assertIn("Other Confirmed Rewards", report)
        self.assertIn("Loyalty", report)
        self.assertNotIn("<span>Progression Rewards</span>", report)


    def test_deferred_progression_is_not_counted_as_checked_or_attention(self):
        checked = [
            {
                "pid": f"CHECKED{i:04d}",
                "display_name": f"Checked Player {i}",
                "processing_mode": "full_claim",
                "status": "Success",
                "standard_rewards_checked": True,
                "progression_checked": True,
                "progression_unconfirmed": False,
                "progression_deferred": False,
                "daily": 1, "store": 3, "progression": 0, "loyalty": 0,
                "has_loyalty": False, "promotions_observed": [], "event_rewards": [],
            }
            for i in range(34)
        ]
        deferred_reason = (
            "popup/overlay safety guard blocked inspection "
            "(unknown_fullscreen_overlay_blocks_page; dialogs=0; overlays=1; "
            "top_dialog='PRIVATE-POPUP-CONTEXT'; popup_context={\"url\":\"https://internal.invalid\"})"
        )
        deferred = {
            "pid": "5429611A24A8F01A",
            "display_name": "hgkme1",
            "processing_mode": "full_claim",
            "status": "Success",
            "standard_rewards_checked": True,
            "progression_checked": False,
            "progression_unconfirmed": False,
            "progression_deferred": True,
            "progression_deferred_reason": deferred_reason,
            "progression_claim_attempted": False,
            "daily": 1, "store": 3, "progression": 0, "loyalty": 0,
            "has_loyalty": False, "promotions_observed": [], "event_rewards": [],
        }
        report = build_email(
            checked + [deferred], "Primary Run", 1,
            get_ist_time() - timedelta(minutes=2), {}, configured_player_count=35,
        )
        self.assertNotIn("<span>Progression Rewards</span>", report)
        self.assertIn("Progression — checks still open", report)
        self.assertIn("1 account remains open", report)
        self.assertIn("Blocked by an on-screen popup or overlay", report)
        self.assertIn("1 account remains open", report)
        self.assertIn("<span>⚠️ Need attention</span><strong>0</strong>", report)
        self.assertNotIn("Attention Required", report)
        self.assertNotIn("unknown_fullscreen_overlay_blocks_page", report)
        self.assertNotIn("hgkme1", report)
        self.assertNotIn("PRIVATE-POPUP-CONTEXT", report)
        self.assertNotIn("popup_context", report)
        self.assertNotIn("unconfirmed standard reward", report.lower())
        self.assertNotIn("<span>Progression Rewards</span>", report)

    def test_queue_only_partial_retry_does_not_raise_attention_but_unconfirmed_claim_does(self):
        player = self.sample_player()
        reason = (
            "Queued Progression inspection found no currently visible claimable reward; "
            "keeping the queue open for a later pass because an empty snapshot is not authoritative proof."
        )
        player.update({
            "processing_mode": "progression_queue_only",
            "status": "Partial",
            "retry_required": True,
            "fail_reason": reason,
            "progression_deferred": True,
            "progression_deferred_reason": reason,
            "progression_checked": False,
            "progression_unconfirmed": False,
            "progression_claim_attempted": False,
            "daily": 0, "store": 0, "progression": 0,
            "standard_rewards_checked": False,
            "daily_unconfirmed": False,
            "store_unconfirmed_indices": [],
            "grenade_balance": None,
            "promotions_observed": [],
            "event_rewards": [],
        })
        report = build_email(
            [player], "Immediate Progression Recovery", 1,
            get_ist_time() - timedelta(minutes=2), {}, configured_player_count=36,
        )
        self.assertIn("<span>⚠️ Need attention</span><strong>0</strong>", report)
        self.assertIn("Progression — checks still open", report)
        self.assertIn("No claimable reward card visible", report)
        self.assertNotIn("Attention Required", report)

        player["progression_unconfirmed"] = True
        report = build_email(
            [player], "Immediate Progression Recovery", 1,
            get_ist_time() - timedelta(minutes=2), {}, configured_player_count=36,
        )
        self.assertIn("<span>⚠️ Need attention</span><strong>1</strong>", report)
        self.assertIn("Attention Required", report)

    def test_unexpected_progression_inspection_exception_remains_attention(self):
        player = self.sample_player()
        reason = "Progression-only recovery raised RuntimeError; inspection remains queued."
        player.update({
            "processing_mode": "progression_queue_only",
            "status": "Partial",
            "retry_required": True,
            "fail_reason": reason,
            "progression_deferred": True,
            "progression_deferred_reason": reason,
            "progression_checked": False,
            "progression_unconfirmed": False,
            "progression_claim_attempted": False,
            "daily": 0, "store": 0, "progression": 0,
            "standard_rewards_checked": False,
            "daily_unconfirmed": False,
            "store_unconfirmed_indices": [],
            "grenade_balance": None,
            "promotions_observed": [],
            "event_rewards": [],
        })
        report = build_email(
            [player], "Immediate Progression Recovery", 1,
            get_ist_time() - timedelta(minutes=2), {}, configured_player_count=36,
        )
        self.assertIn("<span>⚠️ Need attention</span><strong>1</strong>", report)
        self.assertIn("Attention Required", report)
        self.assertIn("RuntimeError", report)

    def test_deferred_progression_email_includes_read_only_grenade_carry_forward(self):
        player = self.sample_player()
        deferred_reason = (
            "Queued Progression inspection found no currently visible claimable reward; "
            "keeping the queue open for a later pass."
        )
        player.update({
            "processing_mode": "progression_queue_only",
            "status": "Partial",
            "retry_required": True,
            "fail_reason": deferred_reason,
            "progression_deferred": True,
            "progression_deferred_reason": deferred_reason,
            "progression_checked": False,
            "progression_unconfirmed": False,
            "progression_claim_attempted": False,
            "daily": 0, "store": 0, "progression": 0,
            "standard_rewards_checked": False,
            "daily_unconfirmed": False,
            "store_unconfirmed_indices": [],
            "grenade_balance": None,
            "promotions_observed": [],
            "event_rewards": [],
        })
        saved_state = {
            "schema_version": 1,
            "cycles": {
                "2026-11": {
                    "players": {
                        "TESTPLAYER0001": {
                            "baseline_status": "active",
                            "baseline_count": 0,
                            "baseline_date": "2026-11-01",
                            "tracking_start_date": "2026-11-01",
                            "baseline_source": "roster_present_before_cycle_start",
                            "last_observed_count": 66,
                            "last_observed_date": "2026-11-22",
                            "last_observed_at_ist": "2026-11-22T05:36:58+05:30",
                            "last_observed_expected_minimum": 66,
                            "last_observed_status": "pass",
                            "last_observed_next_milestone": 123,
                            "last_observed_remaining_to_next_milestone": 57,
                        }
                    }
                }
            },
        }
        fixed_now = datetime(2026, 11, 23, 12, 30)
        with patch("master_claimer.load_grenade_balance_state", return_value=saved_state), \
             patch("master_claimer.get_ist_time", return_value=fixed_now):
            report = build_email(
                [player], "Promotional Check #1", 1,
                fixed_now - timedelta(minutes=2), {}, configured_player_count=36,
            )
        self.assertIn("<span>⚠️ Need attention</span><strong>0</strong>", report)
        self.assertIn("Grenade Balance — last saved readings", report)
        self.assertIn("66 Grenades recorded", report)
        self.assertIn("66 expected at last check", report)
        self.assertIn("69 expected by 2026-11-23", report)
        self.assertIn("Next milestone 123 · 57 to go at last check", report)
        self.assertIn("not fresh balances from this run", report.lower())
        self.assertNotIn("Attention Required", report)

        saved_state["cycles"]["2026-11"]["players"]["TESTPLAYER0001"].update({
            "last_observed_count": 60,
            "last_observed_expected_minimum": 66,
            "last_observed_status": "below_minimum",
        })
        with patch("master_claimer.load_grenade_balance_state", return_value=saved_state), \
             patch("master_claimer.get_ist_time", return_value=fixed_now):
            deficit_report = build_email(
                [player], "Promotional Check #1", 1,
                fixed_now - timedelta(minutes=2), {}, configured_player_count=36,
            )
        self.assertIn("<span>⚠️ Need attention</span><strong>1</strong>", deficit_report)
        self.assertIn("Below minimum at last check", deficit_report)
        self.assertIn("Attention Required", deficit_report)

        # Legacy observations without a saved status must not be presented as a pass.
        legacy_entry = saved_state["cycles"]["2026-11"]["players"]["TESTPLAYER0001"]
        legacy_entry.update({"last_observed_count": 66})
        legacy_entry.pop("last_observed_status", None)
        with patch("master_claimer.load_grenade_balance_state", return_value=saved_state), \
             patch("master_claimer.get_ist_time", return_value=fixed_now):
            legacy_report = build_email(
                [player], "Promotional Check #1", 1,
                fixed_now - timedelta(minutes=2), {}, configured_player_count=36,
            )
        self.assertIn("Previous validation result not saved", legacy_report)
        self.assertIn("1 previous result unavailable", legacy_report)

    def test_queue_reinspection_is_counted_and_grouped_in_compact_email(self):
        reason = (
            "Queued Progression inspection found no currently visible claimable reward; "
            "keeping the queue open for a later pass because an empty snapshot is not authoritative proof."
        )
        players = []
        for index in range(3):
            players.append({
                "pid": f"QUEUEPLAYER{index:04d}",
                "display_name": f"Queued Player {index}",
                "processing_mode": "progression_queue_only",
                "status": "Partial",
                "retry_required": True,
                "fail_reason": reason,
                "progression_deferred": True,
                "progression_deferred_reason": reason,
                "progression_reinspection_attempted": True,
                "progression_checked": False,
                "progression_unconfirmed": False,
                "progression_claim_attempted": False,
                "daily": 0, "store": 0, "progression": 0, "loyalty": 0,
                "standard_rewards_checked": False,
                "daily_unconfirmed": False,
                "store_unconfirmed_indices": [],
                "grenade_balance": None,
                "promotions_observed": [],
                "event_rewards": [],
            })
        report = build_email(
            players, "Promotional Check #2", 1,
            get_ist_time() - timedelta(minutes=2), {}, configured_player_count=36,
        )
        self.assertIn("Progression pending</span><strong>3</strong>", report)
        self.assertIn("3 queued reinspection attempts made.", report)
        self.assertIn("0 Progression rewards confirmed; 3 accounts remain open", report)
        self.assertNotIn("<span>Progression Checks</span>", report)
        self.assertIn("0 Progression rewards confirmed; 3 accounts remain open", report)
        self.assertIn("No claimable reward card visible", report)
        self.assertEqual(report.count("No claimable reward card visible"), 1)
        self.assertNotIn("QUEUEPLAYER0000", report)
        self.assertIn("<span>⚠️ Need attention</span><strong>0</strong>", report)
        self.assertNotIn("Attention Required", report)

    def test_identical_saved_grenade_readings_are_grouped(self):
        reason = (
            "Queued Progression inspection found no currently visible claimable reward; "
            "keeping the queue open for a later pass."
        )
        player1 = self.sample_player()
        player1.update({
            "pid": "GROUPPLAYER0001", "display_name": "Group Player One",
            "processing_mode": "progression_queue_only", "status": "Partial",
            "retry_required": True, "fail_reason": reason,
            "progression_deferred": True, "progression_deferred_reason": reason,
            "progression_reinspection_attempted": True,
            "progression_checked": False, "progression_unconfirmed": False,
            "progression_claim_attempted": False,
            "daily": 0, "store": 0, "progression": 0, "loyalty": 0,
            "standard_rewards_checked": False, "has_loyalty": False,
            "grenade_balance": None, "promotions_observed": [], "event_rewards": [],
        })
        player2 = dict(player1)
        player2.update({"pid": "GROUPPLAYER0002", "display_name": "Group Player Two"})
        saved_player = {
            "baseline_status": "active", "baseline_count": 0,
            "baseline_date": "2026-11-01", "tracking_start_date": "2026-11-01",
            "baseline_source": "roster_present_before_cycle_start",
            "last_observed_count": 66, "last_observed_date": "2026-11-22",
            "last_observed_at_ist": "2026-11-22T05:36:58+05:30",
            "last_observed_expected_minimum": 66, "last_observed_status": "pass",
            "last_observed_next_milestone": 123,
            "last_observed_remaining_to_next_milestone": 57,
        }
        saved_state = {
            "schema_version": 1,
            "cycles": {"2026-11": {"players": {
                "GROUPPLAYER0001": dict(saved_player),
                "GROUPPLAYER0002": dict(saved_player),
            }}},
        }
        fixed_now = datetime(2026, 11, 23, 12, 30)
        with patch("master_claimer.load_grenade_balance_state", return_value=saved_state), \
             patch("master_claimer.get_ist_time", return_value=fixed_now):
            report = build_email(
                [player1, player2], "Promotional Check #1", 1,
                fixed_now - timedelta(minutes=2), {}, configured_player_count=36,
            )
        self.assertIn("2 accounts · Last checked 2026-11-22", report)
        self.assertIn("66 Grenades recorded", report)
        self.assertEqual(report.count("66 Grenades recorded"), 1)
        self.assertNotIn("GROUPPLAYER0001", report)
        self.assertNotIn("GROUPPLAYER0002", report)


    def test_generic_popup_blocker_remains_reportable(self):
        generic = {
            "key": "store-popup-blocked",
            "title": "Store promotion popup",
            "status": "popup_blocked",
            "claim_count": 0,
        }
        self.assertTrue(_promotion_detail_worthy(generic))
        section = build_promotional_section([{
            "pid": "TESTPLAYER0001",
            "display_name": "Test Player",
            "promotions_observed": [generic],
        }])
        self.assertIn("Promotional Offers", section)
        self.assertIn("Store promotion popup", section)

    def test_free_identified_popup_blocked_offer_remains_reportable(self):
        free = {
            "key": "free-weekly-reward",
            "title": "Free Weekly Reward",
            "status": "popup_blocked",
            "claim_count": 0,
            "button_text": "Claim",
            "card_text": "Free Weekly Reward · Free",
        }
        self.assertTrue(_promotion_detail_worthy(free))
        section = build_promotional_section([{
            "pid": "TESTPLAYER0001",
            "display_name": "Test Player",
            "promotions_observed": [free],
        }])
        self.assertIn("Free Weekly Reward", section)

    def test_paid_popup_blocked_offer_is_excluded_from_store_promotions(self):
        paid = {
            "key": "premium-pack",
            "title": "Premium Pack",
            "status": "popup_blocked",
            "claim_count": 0,
            "button_text": "Claim",
            "card_text": "Premium Pack · ₹499 · 30% off",
        }
        self.assertFalse(_promotion_detail_worthy(paid))
        section = build_promotional_section([{
            "pid": "TESTPLAYER0001",
            "display_name": "Test Player",
            "promotions_observed": [paid],
        }])
        self.assertEqual(section, "")

    def test_paid_popup_blocked_offer_does_not_create_email_attention_warning(self):
        paid = {
            "key": "premium-pack",
            "title": "Premium Pack",
            "status": "popup_blocked",
            "claim_count": 0,
            "button_text": "Claim",
            "card_text": "Premium Pack · ₹499 · 30% off",
        }
        player = {
            "pid": "PAIDPROMO0001",
            "display_name": "Paid Promo Player",
            "processing_mode": "full_claim",
            "standard_rewards_checked": True,
            "progression_checked": True,
            "status": "Partial",
            "retry_required": True,
            "fail_reason": "At least one observed promotional claim was not confirmed by the portal",
            "daily": 0,
            "store": 0,
            "progression": 0,
            "loyalty": 0,
            "has_loyalty": False,
            "promotions_observed": [paid],
            "event_rewards": [],
        }
        report = build_email(
            [player], "Manual Run", 1,
            get_ist_time() - timedelta(seconds=30), {},
        )
        self.assertIn("Completed", report)
        self.assertIn("<span>⚠️ Need attention</span><strong>0</strong>", report)
        self.assertNotIn("Premium Pack", report)
        self.assertNotIn("At least one observed promotional claim", report)

    def test_free_popup_blocked_offer_still_creates_email_attention(self):
        free = {
            "key": "free-weekly-reward",
            "title": "Free Weekly Reward",
            "status": "popup_blocked",
            "claim_count": 0,
            "button_text": "Claim",
            "card_text": "Free Weekly Reward · Free",
        }
        player = {
            "pid": "FREEPROMO0001",
            "display_name": "Free Promo Player",
            "processing_mode": "full_claim",
            "standard_rewards_checked": True,
            "progression_checked": True,
            "status": "Partial",
            "retry_required": True,
            "fail_reason": "At least one observed promotional claim was not confirmed by the portal",
            "daily": 0,
            "store": 0,
            "progression": 0,
            "loyalty": 0,
            "has_loyalty": False,
            "promotions_observed": [free],
            "event_rewards": [],
        }
        report = build_email(
            [player], "Manual Run", 1,
            get_ist_time() - timedelta(seconds=30), {},
        )
        self.assertIn("Attention Required", report)
        self.assertIn("<span>⚠️ Need attention</span><strong>1</strong>", report)
        self.assertIn("Free Weekly Reward", report)

    def test_identified_popup_blocked_offer_without_card_text_is_not_assumed_free(self):
        incomplete = {
            "key": "unknown-specific-offer",
            "title": "Unknown Offer",
            "status": "popup_blocked",
            "button_text": "Claim",
        }
        self.assertFalse(_promotion_detail_worthy(incomplete))


    def test_email_shows_processed_accounts_against_configured_roster_and_uses_game_store_name(self):
        player = self.sample_player()
        player["promotions_observed"] = []
        player["event_rewards"] = []
        report = build_email(
            [player], "Manual Run", 1,
            get_ist_time() - timedelta(seconds=30), {},
            configured_player_count=35,
        )
        self.assertIn("<span>👥 Accounts processed</span><strong>1 / 35</strong>", report)
        self.assertIn("<span>Standard Store Rewards</span>", report)

    def test_email_includes_compact_grenade_balance_result_and_flags_shortfall(self):
        player = self.sample_player()
        player["promotions_observed"] = []
        player["event_rewards"] = []
        player["grenade_balance"] = {
            "status": "below_minimum",
            "current_count": 27,
            "expected_minimum": 30,
            "deficit": 3,
            "next_milestone": 56,
            "remaining_to_next_milestone": 29,
        }
        report = build_email(
            [player], "Primary Run", 1,
            get_ist_time() - timedelta(seconds=30), {},
            configured_player_count=35,
        )
        self.assertIn("Grenade Balance Check", report)
        self.assertIn("27 / 30 · short 3 · next 56 (in 29 Grenades)", report)
        self.assertIn("Need attention", report)
        self.assertIn("<strong>1</strong>", report)

    def test_email_reports_surplus_and_next_milestone_without_attention(self):
        player = self.sample_player()
        player["promotions_observed"] = []
        player["event_rewards"] = []
        player["grenade_balance"] = {
            "status": "pass", "current_count": 34, "expected_minimum": 24,
            "surplus": 10, "next_milestone": 56,
            "remaining_to_next_milestone": 22,
        }
        report = build_email(
            [player], "Primary Run", 1,
            get_ist_time() - timedelta(seconds=30), {},
            configured_player_count=1,
        )
        self.assertIn("Above minimum", report)
        self.assertIn("34 / 24 · surplus 10 · next 56 (in 22 Grenades)", report)
        self.assertIn("Above minimum", report)
        self.assertNotIn("Tracking Incomplete", report)

    def test_pending_baseline_is_informational_not_attention(self):
        player = self.sample_player()
        player["promotions_observed"] = []
        player["event_rewards"] = []
        player["grenade_balance"] = {
            "status": "pending_baseline", "current_count": 34,
            "expected_minimum": None, "next_milestone": 56,
        }
        report = build_email(
            [player], "Primary Run", 1,
            get_ist_time() - timedelta(seconds=30), {},
            configured_player_count=1,
        )
        self.assertIn("Tracking pending", report)
        self.assertIn("Tracking Incomplete", report)
        self.assertIn("Need attention</span><strong>0</strong>", report)

    def test_grenade_balance_email_uses_singular_account_count(self):
        player = self.sample_player()
        player["promotions_observed"] = []
        player["event_rewards"] = []
        player["grenade_balance"] = {
            "status": "pass",
            "current_count": 30,
            "expected_minimum": 30,
            "next_milestone": 56,
        }
        report = build_email(
            [player], "Recovery Run", 1,
            get_ist_time() - timedelta(seconds=30), {},
            configured_player_count=35,
        )
        self.assertIn("1 account checked · 1 balance captured · 1 passed", report)

    def test_recovery_email_shows_combined_cycle_totals_and_completion(self):
        player = {
            "pid": "RECOVERY0001",
            "display_name": "Recovered Player",
            "processing_mode": "full_claim",
            "standard_rewards_checked": True,
            "progression_checked": True,
            "status": "Claims Made",
            "daily": 0,
            "store": 3,
            "progression": 0,
            "loyalty": 0,
            "has_loyalty": False,
            "promotions_observed": [],
            "event_rewards": [],
            "grenade_balance": {
                "status": "pass",
                "current_count": 30,
                "expected_minimum": 30,
                "next_milestone": 56,
            },
        }
        meta = {
            "daily_cycle_email_summary": {
                "reset_date": _current_reset_date(),
                "configured_player_count": 3,
                "accounts_complete": 3,
                "accounts_pending": 0,
                "claim_totals": {
                    "daily": 3, "store": 9, "promotions": 0,
                    "progression": 0, "loyalty": 0, "events": 0,
                },
                "total_claims": 12,
                "primary_claim_totals": {
                    "daily": 3, "store": 6, "promotions": 0,
                    "progression": 0, "loyalty": 0, "events": 0,
                },
                "recovery_claim_totals": {
                    "daily": 0, "store": 3, "promotions": 0,
                    "progression": 0, "loyalty": 0, "events": 0,
                },
                "recovery_claims_added": 3,
                "recovery_runs_count": 1,
                "recovery_accounts_count": 1,
                "pending_player_last4": [],
                "cycle_complete": True,
            }
        }
        report = build_email(
            [player], "Recovery Run", 1,
            get_ist_time() - timedelta(seconds=30), meta,
            configured_player_count=3,
        )
        self.assertIn("<span>🎁 Core claims</span><strong>12</strong>", report)
        self.assertIn("<span>👥 Accounts complete</span><strong>3 / 3</strong>", report)
        self.assertIn("Today&#x27;s Reward Cycle", report)
        self.assertIn("Recovery checked 1 account and confirmed 3 additional claims.", report)
        self.assertIn("<span>Standard Store Rewards</span><strong>9</strong>", report)
        self.assertIn("Core Daily Claims", report)
        self.assertNotIn("<strong>3</strong><small>3 accounts checked</small>", report)



    def test_recovery_email_ignores_cycle_summary_from_previous_reset(self):
        player = self.sample_player()
        player["promotions_observed"] = []
        player["event_rewards"] = []
        previous_reset = (
            datetime.strptime(_current_reset_date(), "%Y-%m-%d") - timedelta(days=1)
        ).strftime("%Y-%m-%d")
        stale_meta = {
            "daily_cycle_email_summary": {
                "reset_date": previous_reset,
                "configured_player_count": 3,
                "accounts_complete": 3,
                "accounts_pending": 0,
                "claim_totals": {
                    "daily": 3, "store": 9, "promotions": 0,
                    "progression": 0, "loyalty": 0, "events": 0,
                },
                "total_claims": 12,
                "recovery_claims_added": 3,
                "recovery_accounts_count": 1,
                "pending_player_last4": [],
                "cycle_complete": True,
            }
        }
        report = build_email(
            [player], "Recovery Run", 1,
            get_ist_time() - timedelta(seconds=30), stale_meta,
            configured_player_count=3,
        )
        self.assertNotIn("Today&#x27;s Reward Cycle", report)
        self.assertNotIn("12 total confirmed claims", report)

    def test_recovery_email_does_not_double_count_pending_account_in_current_results(self):
        player = self.sample_player()
        player["pid"] = "RECOVERY0001"
        player["status"] = "Partial"
        player["daily_unconfirmed"] = True
        player["promotions_observed"] = []
        player["event_rewards"] = []
        meta = {
            "daily_cycle_email_summary": {
                "reset_date": _current_reset_date(),
                "configured_player_count": 3,
                "accounts_complete": 2,
                "accounts_pending": 1,
                "claim_totals": {
                    "daily": 3, "store": 8, "promotions": 0,
                    "progression": 0, "loyalty": 0, "events": 0,
                },
                "total_claims": 11,
                "recovery_claims_added": 2,
                "recovery_accounts_count": 1,
                "pending_player_last4": ["0001"],
                "cycle_complete": False,
            }
        }
        report = build_email(
            [player], "Recovery Run", 1,
            get_ist_time() - timedelta(seconds=30), meta,
            configured_player_count=3,
        )
        self.assertIn("<span>⚠️ Need attention</span><strong>1</strong>", report)
        self.assertIn("2 / 3 accounts complete", report)

    def test_email_dashboard_distinguishes_processing_scope_and_unchecked_categories(self):
        players = [
            {
                "pid": f"PROMOONLY{i:04d}", "display_name": f"Promo Player {i}",
                "processing_mode": "promotional_only", "status": "Promotional Check Complete",
                "daily": 0, "store": 0, "progression": 0, "loyalty": 0,
                "has_loyalty": False, "promotion_scan_completed": True,
                "promotions_observed": [], "event_rewards": [],
            }
            for i in range(35)
        ]
        report = build_email(
            players, "Promotional Check #2", 2,
            get_ist_time() - timedelta(minutes=20), {},
            configured_player_count=35,
        )
        self.assertIn("<span>👥 Accounts processed</span><strong>35 / 35</strong>", report)
        self.assertIn("<span>🎁 Core claims</span><strong>—</strong>", report)
        self.assertIn("<span>🔄 In-run recovery</span><strong>0</strong>", report)
        self.assertIn("<span>⚠️ Need attention</span><strong>0</strong>", report)
        self.assertNotIn("Full Claim Path", report)
        self.assertIn("Promotional-only", report)
        self.assertIn("<strong class='v2-mode-count'>35</strong>", report)
        self.assertIn("Not performed for this promotional-only run", report)
        self.assertIn("Run Reconciliation", report)
        self.assertIn("Matched", report)
        self.assertIn("Partial / mixed run", report)
        self.assertIn("Not checked in this run", report)
        self.assertIn("Completed", report)
        self.assertNotIn("Healthy", report)
        self.assertNotIn("Next Run", report)

    def test_grenade_balance_summary_is_separate_from_heading_on_desktop(self):
        player = self.sample_player()
        player["grenade_balance"] = {
            "status": "below_minimum",
            "current_count": 27,
            "expected_minimum": 30,
            "deficit": 3,
        }
        report = build_email(
            [player], "Primary Run", 1,
            get_ist_time() - timedelta(seconds=30), {},
            configured_player_count=35,
        )
        self.assertIn("<div class='v2-section-head v2-grenade-head'><h2>Grenade Balance Check</h2>", report)
        self.assertIn("<div class='v2-grenade-summary'>", report)
        self.assertIn("27 / 30 · short 3", report)
        self.assertIn(".v2-grenade-summary{display:block", report)
        self.assertIn("class='v2-section-head v2-grenade-head'", report)
        self.assertNotIn("display:flex", report)
        self.assertNotIn("grid-template-columns", report)

    def test_promotional_offer_summary_groups_free_unavailable_offers(self):
        players = [
            {
                "pid": "PROMO0001", "display_name": "Promo Player",
                "promotion_scan_completed": True,
                "promotions_observed": [{
                    "title": "First Purchaser Pack", "status": "unavailable",
                    "button_text": "free", "card_text": "First Purchaser Pack x1 Free",
                }],
            },
            {
                "pid": "PROMO0002", "display_name": "Second Player",
                "promotion_scan_completed": True,
                "promotions_observed": [{
                    "title": "First Purchaser Pack", "status": "unavailable",
                    "button_text": "free", "card_text": "First Purchaser Pack x1 Free",
                }],
            },
        ]
        section = build_promotional_section(players)
        self.assertIn("Promotional Offers", section)
        self.assertIn("Seen on 2 accounts", section)
        self.assertIn("2 account scans · 1 offer type", section)
        self.assertIn("2 unavailable", section)
        self.assertNotIn("PROMO0001", section)
        self.assertNotIn("PROMO0002", section)

    def test_promotional_offer_type_summary_pluralizes_multiple_types(self):
        players = [
            {
                "pid": "PROMO0001", "promotion_scan_completed": True,
                "promotions_observed": [{
                    "title": "Free Pack A", "status": "unavailable",
                    "button_text": "free", "card_text": "Free Pack A Free",
                }],
            },
            {
                "pid": "PROMO0002", "promotion_scan_completed": True,
                "promotions_observed": [{
                    "title": "Free Pack B", "status": "unavailable",
                    "button_text": "free", "card_text": "Free Pack B Free",
                }],
            },
        ]
        section = build_promotional_section(players)
        self.assertIn("2 account scans · 2 offer types", section)

    def test_promo_summary_does_not_surface_paid_unavailable_offer(self):
        section = build_promotional_section([{
            "pid": "PAID0001", "promotion_scan_completed": True,
            "promotions_observed": [{
                "title": "Premium Pack", "status": "unavailable",
                "button_text": "unavailable", "card_text": "Premium Pack ₹499",
            }],
        }])
        self.assertNotIn("Premium Pack", section)
        self.assertIn("No reportable free promotional offers", section)


    def test_run_level_reconciliation_warning_marks_execution_attention(self):
        player = self.sample_player()
        player["promotions_observed"] = []
        player["event_rewards"] = []
        reconciliation = {
            "status": "attention_required",
            "scope": "partial_or_mixed_run",
            "configured_player_count": 1,
            "accounts_processed": 1,
            "checks": {"processing_modes_match_processed_accounts": False},
            "warnings": ["processing_modes_match_processed_accounts"],
        }
        with patch("master_claimer.build_run_reconciliation", return_value=reconciliation):
            report = build_email(
                [player], "Manual Run", 1,
                get_ist_time() - timedelta(seconds=30), {},
                configured_player_count=1,
            )
        self.assertIn("<span class='v2-health v2-warn'>Attention Required</span>", report)
        self.assertIn("Run Reconciliation", report)
        self.assertIn("processing modes match processed accounts", report)
        self.assertIn("<span>⚠️ Need attention</span><strong>0</strong>", report)

    def test_confirmed_promotional_claims_are_aggregated_not_repeated_per_account(self):
        players = []
        for index in range(3):
            players.append({
                "pid": f"CLAIMEDPROMO{index:04d}",
                "display_name": f"Promo Player {index}",
                "promotion_scan_completed": True,
                "promotions_observed": [{
                    "title": "Free Weekly Reward", "status": "claimed_this_run",
                    "claim_count": 1, "button_text": "free",
                    "card_text": "Free Weekly Reward · Free",
                }],
            })
        section = build_promotional_section(players)
        self.assertIn("Seen on 3 accounts", section)
        self.assertIn("3 claimed", section)
        self.assertNotIn("Promo Player 0", section)
        self.assertNotIn("Promo Player 1", section)
        self.assertNotIn("Promo Player 2", section)

    def test_clean_dashboard_omits_redundant_attention_section(self):
        players = [
            {
                "pid": f"CLEAN{i:04d}", "display_name": f"Clean Player {i}",
                "processing_mode": "promotional_only", "status": "Promotional Check Complete",
                "daily": 0, "store": 0, "progression": 0, "loyalty": 0,
                "has_loyalty": False, "promotion_scan_completed": True,
                "promotions_observed": [], "event_rewards": [],
            }
            for i in range(2)
        ]
        report = build_email(
            players, "Promotional Check #2", 2,
            get_ist_time() - timedelta(minutes=3), {},
            configured_player_count=2,
        )
        self.assertIn("<span>⚠️ Need attention</span><strong>0</strong>", report)
        self.assertNotIn("Attention &amp; Exceptions", report)

    def test_recovery_rollup_kpi_reports_actual_recovery_accounts(self):
        players = [self.sample_player()]
        meta = {
            "daily_cycle_email_summary": {
                "reset_date": "2026-11-11",
                "configured_player_count": 1,
                "accounts_complete": 1,
                "accounts_pending": 0,
                "total_claims": 7,
                "recovery_accounts_count": 1,
                "recovery_claims_added": 2,
                "pending_player_last4": [],
                "claim_totals": {
                    "daily": 1, "store": 2, "progression": 1,
                    "loyalty": 0, "promotions": 1, "events": 2,
                },
            }
        }
        with patch("master_claimer._current_reset_date", return_value="2026-11-11"):
            report = build_email(
                players, "Recovery Run", 2,
                get_ist_time() - timedelta(minutes=1), meta,
                configured_player_count=1,
            )
        self.assertIn("<span>🔄 Recovery accounts</span><strong>1</strong>", report)
        self.assertIn("2 additional claims confirmed", report)
        self.assertNotIn("<span>🔄 In-run recovery</span>", report)

    def test_email_uses_gmail_safe_table_layout_and_color_coded_cards(self):
        player = self.sample_player()
        report = build_email(
            [player], "Primary Run", 1,
            get_ist_time() - timedelta(seconds=30), {},
            configured_player_count=1,
        )
        self.assertIn("class='v2-kpi-table'", report)
        self.assertIn("class='v2-category-table'", report)
        self.assertIn("v2-kpi-accounts", report)
        self.assertIn("v2-kpi-claims", report)
        self.assertIn("v2-kpi-recovery", report)
        self.assertIn("v2-kpi-attention", report)
        self.assertIn("v2-category-daily", report)
        self.assertIn("v2-category-store", report)
        self.assertIn("@media screen and (max-width:640px)", report)
        self.assertNotIn("display:grid", report)
        self.assertNotIn("display:flex", report)
        self.assertNotIn("grid-template-columns", report)
        self.assertNotIn("justify-content", report)

    def test_recovery_email_labels_subset_scope_and_cycle_totals_correctly(self):
        player = self.sample_player()
        player["immediate_recovery"] = {
            "attempted": True,
            "status": "resolved",
            "categories": ["Daily Reward"],
            "resolved_categories": ["Daily Reward"],
            "unresolved_categories": [],
            "recovered_claims": 1,
        }
        meta = {
            "daily_cycle_email_summary": {
                "reset_date": "2026-11-12",
                "configured_player_count": 35,
                "accounts_complete": 35,
                "accounts_pending": 0,
                "total_claims": 140,
                "recovery_accounts_count": 1,
                "recovery_claims_added": 1,
                "pending_player_last4": [],
                "claim_totals": {
                    "daily": 35, "store": 105, "progression": 0,
                    "loyalty": 0, "promotions": 0, "events": 0,
                },
            }
        }
        with patch("master_claimer._current_reset_date", return_value="2026-11-12"):
            report = build_email(
                [player], "Recovery Run", 2,
                get_ist_time() - timedelta(minutes=1), meta,
                configured_player_count=35,
            )
        self.assertIn("Targeted Recovery", report)
        self.assertIn("Replayed exact saved target(s): Daily Reward", report)
        self.assertIn("Core Daily Claims", report)
        self.assertIn("Cumulative Daily + standard Store claims in today&#x27;s cycle", report)
        self.assertIn("Recovery Run Consistency", report)
        self.assertIn("1 targeted account(s) represented", report)
        self.assertNotIn("Full Claim Path", report)
        self.assertNotIn("Reward Claims (This Run)", report)
        self.assertIn("Post-run Recovery", report)
        self.assertIn("35 / 35 accounts complete", report)
        self.assertIn("140 total confirmed claims", report)


    def test_preexisting_portal_claim_audit_records_full_id_and_ign_once_per_reset(self):
        player_id = "1234567890123456"
        with tempfile.TemporaryDirectory() as temp_dir:
            audit_path = Path(temp_dir) / "portal_claim_audit.json"
            history_path = Path(temp_dir) / "claim_history.json"
            driver = type("AuditDriver", (), {})()
            driver._portal_claim_audit_enabled = True
            driver._portal_claim_display_name = "FREAKv20"
            driver._portal_claim_run_label = "Primary Run"
            driver._preexisting_portal_claims = []

            with patch("master_claimer.PORTAL_CLAIM_AUDIT_FILE", str(audit_path)), \
                 patch("master_claimer.HISTORY_FILE", str(history_path)), \
                 patch("master_claimer.get_ist_time", return_value=datetime(2026, 11, 12, 5, 42, 0)), \
                 patch("master_claimer.get_last_daily_reset", return_value=datetime(2026, 11, 12, 5, 30, 0)):
                first = record_preexisting_portal_claim(
                    driver, player_id, "daily", "daily", "Daily Reward",
                    "daily_initial_portal_state",
                )
                second = record_preexisting_portal_claim(
                    driver, player_id, "daily", "daily", "Daily Reward",
                    "daily_initial_portal_state",
                )
                audit = json.loads(audit_path.read_text(encoding="utf-8"))
                self.assertIsNotNone(first)
                self.assertIsNone(second)
                self.assertEqual(len(audit["observations"]), 1)
                saved = next(iter(audit["observations"].values()))
                self.assertEqual(saved["player_id"], player_id)
                self.assertEqual(saved["display_name"], "FREAKv20")
                self.assertEqual(saved["classification"], "pre_existing_portal_claim")
                self.assertEqual(saved["actor"], "unknown_portal_actor")
                self.assertEqual(saved["observation_count"], 2)
                self.assertEqual(len(driver._preexisting_portal_claims), 1)

    def test_preexisting_portal_claim_is_not_recorded_after_same_reset_bot_attempt(self):
        player_id = "1234567890123456"
        with tempfile.TemporaryDirectory() as temp_dir:
            audit_path = Path(temp_dir) / "portal_claim_audit.json"
            history_path = Path(temp_dir) / "claim_history.json"
            history_path.write_text(json.dumps({
                player_id: {
                    "daily": {
                        "status": "unconfirmed",
                        "last_attempt_at": "2026-11-12T05:38:00",
                        "last_claim": None,
                    }
                }
            }), encoding="utf-8")
            driver = type("AuditDriver", (), {
                "_portal_claim_audit_enabled": True,
                "_portal_claim_display_name": "FREAKv20",
                "_portal_claim_run_label": "Backup Run #1",
                "_preexisting_portal_claims": [],
            })()
            with patch("master_claimer.PORTAL_CLAIM_AUDIT_FILE", str(audit_path)), \
                 patch("master_claimer.HISTORY_FILE", str(history_path)), \
                 patch("master_claimer.get_ist_time", return_value=datetime(2026, 11, 12, 6, 0, 0)), \
                 patch("master_claimer.get_last_daily_reset", return_value=datetime(2026, 11, 12, 5, 30, 0)):
                event = record_preexisting_portal_claim(
                    driver, player_id, "daily", "daily", "Daily Reward",
                    "daily_initial_portal_state",
                )
                self.assertIsNone(event)
                self.assertFalse(audit_path.exists())

    def test_preexisting_portal_claim_repeat_count_uses_distinct_reset_days(self):
        player_id = "1234567890123456"
        with tempfile.TemporaryDirectory() as temp_dir:
            audit_path = Path(temp_dir) / "portal_claim_audit.json"
            history_path = Path(temp_dir) / "claim_history.json"
            driver = type("AuditDriver", (), {
                "_portal_claim_audit_enabled": True,
                "_portal_claim_display_name": "FREAKv20",
                "_portal_claim_run_label": "Primary Run",
                "_preexisting_portal_claims": [],
            })()
            with patch("master_claimer.PORTAL_CLAIM_AUDIT_FILE", str(audit_path)), \
                 patch("master_claimer.HISTORY_FILE", str(history_path)):
                for day in (12, 13):
                    current = datetime(2026, 11, day, 5, 42, 0)
                    reset = datetime(2026, 11, day, 5, 30, 0)
                    with patch("master_claimer.get_ist_time", return_value=current), \
                         patch("master_claimer.get_last_daily_reset", return_value=reset):
                        event = record_preexisting_portal_claim(
                            driver, player_id, "daily", "daily", "Daily Reward",
                            "daily_initial_portal_state",
                        )
                        self.assertIsNotNone(event)
                audit = json.loads(audit_path.read_text(encoding="utf-8"))
                self.assertEqual(_portal_claim_reset_day_count(player_id, audit), 2)

    def test_preexisting_portal_email_section_shows_full_id_ign_and_recurrence(self):
        player_id = "1234567890123456"
        with tempfile.TemporaryDirectory() as temp_dir:
            audit_path = Path(temp_dir) / "portal_claim_audit.json"
            history_path = Path(temp_dir) / "claim_history.json"
            driver = type("AuditDriver", (), {
                "_portal_claim_audit_enabled": True,
                "_portal_claim_display_name": "FREAKv20",
                "_portal_claim_run_label": "Primary Run",
                "_preexisting_portal_claims": [],
            })()
            events = []
            with patch("master_claimer.PORTAL_CLAIM_AUDIT_FILE", str(audit_path)), \
                 patch("master_claimer.HISTORY_FILE", str(history_path)):
                for day in (12, 13):
                    with patch("master_claimer.get_ist_time", return_value=datetime(2026, 11, day, 5, 42, 0)), \
                         patch("master_claimer.get_last_daily_reset", return_value=datetime(2026, 11, day, 5, 30, 0)):
                        event = record_preexisting_portal_claim(
                            driver, player_id, "daily", "daily", "Daily Reward",
                            "daily_initial_portal_state",
                        )
                        events.append(event)
            result = {
                "pid": player_id,
                "display_name": "FREAKv20",
                "preexisting_portal_claims": [events[-1]],
            }
            with patch("master_claimer.PORTAL_CLAIM_AUDIT_FILE", str(audit_path)):
                section = build_preexisting_portal_claim_section([result])
            self.assertIn("FREAKv20", section)
            self.assertIn(player_id, section)
            self.assertIn("Observed on 2 separate reset days", section)
            self.assertIn("portal does not identify who made the claim", section)

    def test_issue_rows_include_full_player_id_and_captured_ign(self):
        result = {
            "pid": "1234567890123456",
            "display_name": "FREAKv20",
            "daily_unconfirmed": True,
            "store_unconfirmed_indices": [],
            "progression_unconfirmed": False,
        }
        row = build_standard_attention_section([result])
        label = _issue_identity_label(result)
        self.assertEqual(label, "FREAKv20 · Player ID 1234567890123456")
        self.assertIn("FREAKv20", row)
        self.assertIn("1234567890123456", row)
        self.assertNotIn("…3456", row)


    def test_email_counts_confirmed_claim_when_progression_inspection_remains_deferred(self):
        result = {
            "pid": "F251D3D226496061",
            "display_name": "Gvk9916",
            "processing_mode": "full_claim",
            "status": "Success",
            "retry_required": False,
            "progression": 1,
            "progression_checked": False,
            "progression_deferred": True,
            "progression_claim_attempted": True,
            "progression_unconfirmed": False,
            "progression_deferred_reason": "additional claimable cards remain",
            "daily": 0, "store": 0, "loyalty": 0,
            "has_loyalty": False, "promotions_observed": [], "event_rewards": [],
        }
        report = build_email(
            [result], "Promotional Check #2", 2,
            get_ist_time() - timedelta(minutes=1), {}, configured_player_count=35,
        )
        self.assertNotIn("<span>Progression Rewards</span>", report)
        self.assertIn("Progression — checks still open", report)
        self.assertIn("1 Progression reward confirmed; 1 account remains open", report)

    def test_popup_blocked_progression_inspection_is_deferred_not_a_failed_claim(self):
        result = {
            "pid": "5429611A24A8F01A",
            "display_name": "hgkme1",
            "processing_mode": "full_claim",
            "status": "Success",
            "retry_required": False,
            "progression_unconfirmed": False,
            "progression_deferred": True,
            "progression_deferred_reason": "popup/overlay safety guard blocked inspection",
            "progression_claim_attempted": False,
        }
        self.assertFalse(_result_needs_recovery(result))
        self.assertFalse(_full_claim_recovery_candidate(result))
        self.assertEqual(build_standard_attention_section([result]), "")
        deferred = build_deferred_checks_section([result])
        self.assertIn("Progression — checks still open", deferred)
        self.assertNotIn("hgkme1", deferred)
        self.assertIn("No claim button was clicked in this run", deferred)
        self.assertNotIn("Attention Required", deferred)


    def test_email_deferred_checks_explain_partial_progression_confirmation(self):
        result = {
            "pid": "F251D3D226496061",
            "display_name": "Gvk9916",
            "progression": 1,
            "progression_deferred": True,
            "progression_claim_attempted": True,
            "progression_unconfirmed": False,
            "progression_deferred_reason": (
                "One Progression reward was portal-confirmed, but additional claimable cards remain"
            ),
        }
        section = build_deferred_checks_section([result])
        self.assertIn("1 Progression reward confirmed", section)
        self.assertIn("1 account remains open", section)
        self.assertNotIn("No claim button was clicked", section)

    def test_deferred_recheck_keeps_a_real_prior_progression_failure_visible(self):
        result = {
            "pid": "5429611A24A8F01A",
            "display_name": "hgkme1",
            "progression_unconfirmed": True,
            "progression_deferred": True,
            "progression_claim_attempted": False,
        }
        attention = build_standard_attention_section([result])
        deferred = build_deferred_checks_section([result])
        self.assertIn("previously attempted Progression claim remains unconfirmed", attention)
        self.assertIn("No claim button was clicked in this run", deferred)

    def test_unconfirmed_promotion_click_is_not_reclassified_as_preexisting_claimed(self):
        player_id = "1234567890123456"
        campaign = {"key": "campaign:weekly-free", "title": "Weekly Free Reward"}
        with tempfile.TemporaryDirectory() as temp_dir:
            audit_path = Path(temp_dir) / "portal_claim_audit.json"
            history_path = Path(temp_dir) / "claim_history.json"
            history_path.write_text(json.dumps({
                player_id: {
                    "promotions": {
                        campaign["key"]: {
                            "title": campaign["title"],
                            "status": "claim_unconfirmed",
                            "last_seen_at": "2026-11-12T05:40:00",
                        }
                    }
                }
            }), encoding="utf-8")
            driver = type("AuditDriver", (), {
                "_portal_claim_audit_enabled": True,
                "_portal_claim_display_name": "FREAKv20",
                "_portal_claim_run_label": "Backup Run #1",
                "_preexisting_portal_claims": [],
            })()
            with patch("master_claimer.PORTAL_CLAIM_AUDIT_FILE", str(audit_path)), \
                 patch("master_claimer.HISTORY_FILE", str(history_path)), \
                 patch("master_claimer.get_ist_time", return_value=datetime(2026, 11, 12, 6, 0, 0)), \
                 patch("master_claimer.get_last_daily_reset", return_value=datetime(2026, 11, 12, 5, 30, 0)):
                _record_promotion_history(player_id, campaign, "portal_claimed")
                event = record_preexisting_portal_claim(
                    driver, player_id, "promotion", campaign["key"], campaign["title"],
                    "promotion_initial_portal_state",
                )
                self.assertIsNone(event)
                self.assertFalse(audit_path.exists())
                saved = json.loads(history_path.read_text(encoding="utf-8"))[player_id]["promotions"][campaign["key"]]
                self.assertEqual(saved["last_unconfirmed_attempt_at"], "2026-11-12T05:40:00")

    def test_live_available_promotion_clears_old_ambiguous_attempt_before_manual_review(self):
        player_id = "1234567890123456"
        campaign = {"key": "campaign:weekly-free", "title": "Weekly Free Reward"}
        with tempfile.TemporaryDirectory() as temp_dir:
            audit_path = Path(temp_dir) / "portal_claim_audit.json"
            history_path = Path(temp_dir) / "claim_history.json"
            history_path.write_text(json.dumps({
                player_id: {
                    "promotions": {
                        campaign["key"]: {
                            "title": campaign["title"],
                            "status": "claim_unconfirmed",
                            "last_seen_at": "2026-11-12T05:40:00",
                        }
                    }
                }
            }), encoding="utf-8")
            driver = type("AuditDriver", (), {
                "_portal_claim_audit_enabled": True,
                "_portal_claim_display_name": "FREAKv20",
                "_portal_claim_run_label": "Primary Run",
                "_preexisting_portal_claims": [],
            })()
            with patch("master_claimer.PORTAL_CLAIM_AUDIT_FILE", str(audit_path)), \
                 patch("master_claimer.HISTORY_FILE", str(history_path)), \
                 patch("master_claimer.get_ist_time", return_value=datetime(2026, 11, 13, 5, 42, 0)), \
                 patch("master_claimer.get_last_daily_reset", return_value=datetime(2026, 11, 13, 5, 30, 0)):
                cleared = _clear_promotion_unconfirmed_attempt(player_id, campaign)
                event = record_preexisting_portal_claim(
                    driver, player_id, "promotion", campaign["key"], campaign["title"],
                    "promotion_initial_portal_state",
                )
            self.assertTrue(cleared)
            self.assertIsNotNone(event)
            self.assertEqual(event["reset_date"], "2026-11-13")


    def test_player_display_name_history_tracks_renames_without_changing_player_id(self):
        player_id = "1234567890123456"
        meta = {"player_display_names": {player_id: "FREAKv20"}}
        first = record_player_display_name(
            meta, player_id, "FREAKv2.0",
            observed_at=datetime(2026, 11, 13, 5, 36, 12),
        )
        self.assertTrue(first["changed"])
        self.assertEqual(first["previous_name"], "FREAKv20")
        self.assertEqual(meta["player_display_names"][player_id], "FREAKv2.0")
        names = meta["player_display_name_history"][player_id]["names"]
        self.assertEqual([item["name"] for item in names], ["FREAKv20", "FREAKv2.0"])
        legacy = names[0]
        self.assertIsNone(legacy["first_seen_at_ist"])
        self.assertIsNone(legacy["last_seen_at_ist"])
        current = names[1]
        self.assertEqual(current["first_seen_at_ist"], "2026-11-13T05:36:12")
        self.assertEqual(current["last_seen_at_ist"], "2026-11-13T05:36:12")

    def test_player_display_name_history_updates_last_seen_without_duplicate_alias(self):
        player_id = "1234567890123456"
        meta = {}
        first = record_player_display_name(
            meta, player_id, "FREAKv20",
            observed_at=datetime(2026, 11, 12, 5, 36, 12),
        )
        second = record_player_display_name(
            meta, player_id, "FREAKv20",
            observed_at=datetime(2026, 11, 13, 5, 36, 12),
        )
        names = meta["player_display_name_history"][player_id]["names"]
        self.assertFalse(first["changed"])
        self.assertFalse(second["changed"])
        self.assertEqual(len(names), 1)
        self.assertEqual(names[0]["observation_count"], 2)
        self.assertEqual(names[0]["first_seen_at_ist"], "2026-11-12T05:36:12")
        self.assertEqual(names[0]["last_seen_at_ist"], "2026-11-13T05:36:12")

    def test_player_display_name_history_reuses_alias_when_name_changes_back(self):
        player_id = "1234567890123456"
        meta = {}
        record_player_display_name(meta, player_id, "FREAKv20", datetime(2026, 11, 12, 5, 36))
        record_player_display_name(meta, player_id, "FREAKv2.0", datetime(2026, 11, 13, 5, 36))
        result = record_player_display_name(meta, player_id, "FREAKv20", datetime(2026, 11, 14, 5, 36))
        history = meta["player_display_name_history"][player_id]
        self.assertTrue(result["changed"])
        self.assertEqual(result["previous_name"], "FREAKv2.0")
        self.assertEqual(history["current_name"], "FREAKv20")
        self.assertEqual([item["name"] for item in history["names"]], ["FREAKv20", "FREAKv2.0"])
        self.assertEqual(history["names"][0]["observation_count"], 2)

    def test_portal_claim_audit_preserves_name_at_first_observation_after_rename(self):
        player_id = "1234567890123456"
        with tempfile.TemporaryDirectory() as temp_dir:
            audit_path = Path(temp_dir) / "portal_claim_audit.json"
            history_path = Path(temp_dir) / "claim_history.json"
            driver = type("AuditDriver", (), {})()
            driver._portal_claim_audit_enabled = True
            driver._portal_claim_display_name = "FREAKv20"
            driver._portal_claim_run_label = "Primary Run"
            driver._preexisting_portal_claims = []
            with patch("master_claimer.PORTAL_CLAIM_AUDIT_FILE", str(audit_path)), \
                 patch("master_claimer.HISTORY_FILE", str(history_path)), \
                 patch("master_claimer.get_ist_time", return_value=datetime(2026, 11, 12, 5, 42, 0)), \
                 patch("master_claimer.get_last_daily_reset", return_value=datetime(2026, 11, 12, 5, 30, 0)):
                first = record_preexisting_portal_claim(
                    driver, player_id, "daily", "daily", "Daily Reward",
                    "daily_initial_portal_state",
                )
                driver._portal_claim_display_name = "FREAKv2.0"
                second = record_preexisting_portal_claim(
                    driver, player_id, "daily", "daily", "Daily Reward",
                    "daily_initial_portal_state",
                )
                audit = json.loads(audit_path.read_text(encoding="utf-8"))
                saved = next(iter(audit["observations"].values()))
            self.assertIsNotNone(first)
            self.assertIsNone(second)
            self.assertEqual(saved["display_name"], "FREAKv20")
            self.assertEqual(saved["display_name_at_first_observation"], "FREAKv20")
            self.assertEqual(saved["last_observed_display_name"], "FREAKv2.0")


    def test_progression_queue_only_processor_never_touches_other_reward_categories(self):
        driver = type("ProgressionDriver", (), {"quit": lambda self: None})()
        entry = {
            "status": "deferred",
            "requires_exact_target": False,
            "target_card_signature": None,
        }

        def inspect(target_driver, player_id, stats, queue_entry):
            self.assertIs(target_driver, driver)
            self.assertEqual(player_id, "PLAYER0001")
            self.assertEqual(queue_entry, entry)
            stats.update({
                "progression": 1,
                "progression_checked": True,
                "progression_unconfirmed": False,
                "progression_unconfirmed_card_signature": None,
                "progression_deferred": False,
                "progression_deferred_reason": None,
                "progression_claim_attempted": True,
            })

        with patch("master_claimer.init_player_history") as init_history, \
             patch("master_claimer.create_driver", return_value=driver), \
             patch("master_claimer.login_to_hub", return_value=True) as login, \
             patch("master_claimer.capture_display_name", return_value="Test IGN"), \
             patch("master_claimer._run_queued_progression_inspection", side_effect=inspect) as inspect_queue, \
             patch("master_claimer.claim_daily_rewards") as daily, \
             patch("master_claimer.claim_store_rewards") as store, \
             patch("master_claimer.run_event_reward_checks") as events, \
             patch("master_claimer._record_grenade_balance_for_player") as grenade:
            result = process_player_progression_only(
                "PLAYER0001", False, False, entry, "Immediate Progression Recovery"
            )

        init_history.assert_called_once_with("PLAYER0001")
        login.assert_called_once_with(driver, "PLAYER0001")
        inspect_queue.assert_called_once_with(
            driver, "PLAYER0001", inspect_queue.call_args.args[2], entry
        )
        daily.assert_not_called()
        store.assert_not_called()
        events.assert_not_called()
        grenade.assert_not_called()
        self.assertEqual(result["processing_mode"], "progression_queue_only")
        self.assertEqual(result["progression"], 1)
        self.assertEqual(result["status"], "Claims Made")
        self.assertTrue(result["progression_checked"])
        self.assertTrue(result["progression_claim_attempted"])
        self.assertEqual(result["daily"], 0)
        self.assertEqual(result["store"], 0)
        self.assertEqual(result["loyalty"], 0)
        self.assertEqual(result["event_rewards"], [])
        self.assertTrue(result["daily_skipped"])
        self.assertEqual(result["store_skipped"], [True, True, True])

    def test_progression_queue_only_login_failure_leaves_the_queue_deferred_without_claiming(self):
        driver = type("ProgressionDriver", (), {"quit": lambda self: None})()
        with patch("master_claimer.init_player_history"), \
             patch("master_claimer.create_driver", return_value=driver), \
             patch("master_claimer.login_to_hub", return_value=False), \
             patch("master_claimer._run_queued_progression_inspection") as inspect_queue:
            result = process_player_progression_only(
                "PLAYER0001", False, False,
                {
                    "status": "claim_unconfirmed",
                    "requires_exact_target": True,
                    "target_card_signature": "Milestone 62 | x20",
                },
                "Immediate Progression Recovery",
            )
        inspect_queue.assert_not_called()
        self.assertEqual(result["status"], "Login Failed")
        self.assertTrue(result["progression_deferred"])
        self.assertTrue(result["progression_unconfirmed"])
        self.assertEqual(
            result["progression_unconfirmed_card_signature"],
            "Milestone 62 | x20",
        )
        self.assertFalse(result["progression_claim_attempted"])
        self.assertEqual(result["progression"], 0)



    def test_progression_queue_only_exception_after_click_keeps_exact_target_unconfirmed(self):
        driver = type("ProgressionDriver", (), {"quit": lambda self: None})()
        target = "Milestone 62 | x20"

        def fail_after_click(target_driver, _player_id, _stats, _entry):
            target_driver._progression_claim_attempted = True
            target_driver._progression_unconfirmed = True
            target_driver._progression_unconfirmed_card_signature = target
            raise RuntimeError("portal verification interrupted")

        with patch("master_claimer.init_player_history"), \
             patch("master_claimer.create_driver", return_value=driver), \
             patch("master_claimer.login_to_hub", return_value=True), \
             patch("master_claimer.capture_display_name", return_value="Test IGN"), \
             patch("master_claimer._run_queued_progression_inspection", side_effect=fail_after_click):
            result = process_player_progression_only(
                "PLAYER0001", False, False,
                {
                    "status": "deferred",
                    "requires_exact_target": False,
                    "target_card_signature": None,
                },
                "Immediate Progression Recovery",
            )

        self.assertTrue(result["progression_claim_attempted"])
        self.assertTrue(result["progression_unconfirmed"])
        self.assertFalse(result["progression_deferred"])
        self.assertEqual(result["progression_unconfirmed_card_signature"], target)
        self.assertEqual(result["status"], "Partial")



    def test_primary_core_claim_headline_is_daily_plus_three_standard_store_claims(self):
        players = []
        for index in range(36):
            player = self.sample_player()
            player.update({
                "pid": f"COREPLAYER{index:04d}",
                "display_name": f"Core Player {index}",
                "daily": 1,
                "store": 3,
                "progression": 1 if index == 0 else 0,
                "loyalty": 0,
                "has_loyalty": False,
                "promotions_observed": [],
                "event_rewards": [],
                "progression_deferred": False,
                "progression_checked": True,
            })
            players.append(player)
        report = build_email(
            players, "Primary Run", 1,
            get_ist_time() - timedelta(minutes=2), {},
            configured_player_count=36,
        )
        self.assertIn("<span>🎁 Core claims</span><strong>144/144</strong>", report)
        self.assertIn("<span>Daily Rewards</span><strong>36</strong>", report)
        self.assertIn("<span>Standard Store Rewards</span><strong>108</strong>", report)
        self.assertIn("Progression Program", report)
        self.assertIn("1 reward confirmed", report)
        self.assertNotIn("<span>🎁 Core claims</span><strong>145</strong>", report)

    def test_progression_only_email_has_its_own_scope_and_omits_core_and_grenade_sections(self):
        player = self.sample_player()
        player.update({
            "processing_mode": "progression_queue_only",
            "status": "Partial",
            "daily": 0, "store": 0, "progression": 0, "loyalty": 0,
            "standard_rewards_checked": False,
            "progression_checked": False,
            "progression_deferred": True,
            "progression_reinspection_attempted": True,
            "progression_deferred_reason": (
                "Queued Progression inspection found no currently visible claimable reward."
            ),
            "promotions_observed": [],
            "event_rewards": [],
            "grenade_balance": None,
        })
        report = build_email(
            [player], "Immediate Progression Recovery", 1,
            get_ist_time() - timedelta(minutes=2), {},
            configured_player_count=36,
        )
        self.assertIn("<strong class='v2-mode-title'>Progression-only</strong>", report)
        self.assertIn("<strong class='v2-mode-count'>1</strong>", report)
        self.assertIn("<span>🎁 Core claims</span><strong>—</strong>", report)
        self.assertIn("Not checked in this run", report)
        self.assertNotIn("Core Daily Claims", report)
        self.assertNotIn("Full Claim Path", report)
        self.assertNotIn("Grenade Balance Check", report)
        self.assertNotIn("Grenade Balance History", report)
        self.assertIn("1 queued reinspection attempt made.", report)
        self.assertIn("0 Progression rewards confirmed in this run", report)

if __name__ == "__main__":
    unittest.main()
