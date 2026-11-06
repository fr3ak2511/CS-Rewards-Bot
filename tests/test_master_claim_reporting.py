import unittest
from datetime import timedelta

from master_claimer import (
    _player_claim_total,
    _promotion_detail_worthy,
    _summary_counts,
    build_email,
    build_event_rewards_section,
    build_promotional_section,
    get_ist_time,
)


class ClaimCountAndEmailReportingTests(unittest.TestCase):
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
        self.assertIn("Store Promotions", section)
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
        # Daily 1 + Standard Store 2 + free promo 1 + Progression 1 + Event 2.
        self.assertIn("<span>Confirmed claims</span><strong>7</strong>", report)
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

    def test_email_is_compact_and_does_not_surface_streak_or_schedule(self):
        player = self.sample_player()
        player["event_rewards"] = []
        html = build_email(
            [player], "Manual Run", 1,
            get_ist_time() - timedelta(minutes=2),
            {"streak": {"current": 99, "best": 100}},
        )
        self.assertIn("Healthy", html)
        self.assertIn("Confirmed claims", html)
        self.assertIn("Accounts processed", html)
        self.assertIn("Need attention", html)
        self.assertIn("Daily", html)
        self.assertIn("Standard Store", html)
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
        self.assertIn("Healthy", report)
        self.assertIn("<span>Need attention</span><strong>0</strong>", report)
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
        self.assertIn("<span>Need attention</span><strong>1</strong>", report)
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
        for label in ("Daily", "Standard Store", "Progression", "Loyalty"):
            self.assertIn(
                f"<span>{label}</span><strong>—</strong><small>Not checked in this run</small>",
                report,
            )
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
        self.assertIn("<span>Confirmed claims</span><strong>45</strong>", report)
        self.assertIn("<span>Daily</span><strong>13</strong><small>confirmed · 3 checked</small>", report)
        self.assertIn("<span>Standard Store</span><strong>16</strong><small>confirmed · 3 checked</small>", report)
        self.assertIn("<span>Progression</span><strong>12</strong><small>confirmed · 2 checked</small>", report)
        self.assertIn("<span>Loyalty</span><strong>4</strong><small>confirmed · 1 checked</small>", report)


if __name__ == "__main__":
    unittest.main()
