import unittest
from datetime import datetime, timezone

from promotion_followups import (
    build_promotional_followup_section,
    filter_followup_results,
    seed_legacy_popup_followups,
    update_promotion_followups,
)


def free_actionable_filter(promo):
    """Small test double for Email V2's existing free/actionable filter."""
    return (
        str(promo.get("button_text") or "").strip().lower() in {"free", "claim", "claim reward", "claim now"}
        and not any(token in str(promo.get("card_text") or "").lower() for token in ("₹", "discount", "purchase", "buy now"))
    )


class PromotionFollowupTests(unittest.TestCase):
    def setUp(self):
        self.meta = {}
        self.when = datetime(2026, 11, 7, 3, 0, tzinfo=timezone.utc)

    def result(self, *, promos=None, completed=True, detection_failed=False, player="ABCDEF1234"):
        return {
            "pid": player,
            "promotion_scan_completed": completed,
            "promotion_detection_failed": detection_failed,
            "promotion_popup_blocked": any(p.get("status") == "popup_blocked" for p in (promos or [])),
            "promotions_observed": promos or [],
        }

    def test_popup_block_creates_still_blocked_followup_without_full_claim_retry(self):
        summary = update_promotion_followups(
            self.meta,
            [self.result(promos=[{"key": "store-popup-blocked", "title": "Store promotion popup", "status": "popup_blocked"}])],
            "Manual Run",
            now=self.when,
        )
        self.assertEqual(summary["open_count"], 1)
        self.assertEqual(summary["still_blocked_count"], 1)
        self.assertEqual(summary["open_items"][0]["status"], "still_blocked")
        self.assertEqual(summary["open_items"][0]["player_key"], "ABCDEF1234")
        self.assertEqual(summary["open_items"][0]["player_label"], "Player …1234")
        self.assertNotIn("retry_player_ids", self.meta)

    def test_filter_keeps_generic_blocker_and_free_offer_but_excludes_paid_blocked_offer(self):
        source = self.result(promos=[
            {"key": "store-popup-blocked", "title": "Store promotion popup", "status": "popup_blocked"},
            {"key": "free-offer", "title": "Free weekly reward", "status": "popup_blocked", "button_text": "Claim", "card_text": "Free reward"},
            {"key": "paid-offer", "title": "Premium pack", "status": "popup_blocked", "button_text": "Claim", "card_text": "₹499 · 50% discount"},
        ])
        filtered = filter_followup_results([source], self.meta, free_actionable_filter)

        self.assertEqual(
            [item["key"] for item in filtered[0]["promotions_observed"]],
            ["store-popup-blocked", "free-offer"],
        )
        self.assertEqual(len(source["promotions_observed"]), 3, "filter must not mutate source results")

    def test_popup_status_is_checked_as_an_unconfirmed_claim_for_paid_filter(self):
        statuses = []

        def capture_status(promo):
            statuses.append(promo["status"])
            return True

        result = self.result(promos=[{
            "key": "free-offer", "title": "Free reward", "status": "popup_blocked",
            "button_text": "Free", "card_text": "Free reward",
        }])
        filter_followup_results([result], self.meta, capture_status)
        self.assertEqual(statuses, ["claim_unconfirmed"])

    def test_clean_completed_recheck_resolves_generic_popup_blocker(self):
        update_promotion_followups(
            self.meta,
            [self.result(promos=[{"key": "store-popup-blocked", "title": "Store promotion popup", "status": "popup_blocked"}])],
            "Manual Run", now=self.when,
        )
        later = datetime(2026, 11, 7, 5, 0, tzinfo=timezone.utc)
        summary = update_promotion_followups(
            self.meta,
            [self.result(promos=[{
                "key": "different-offer", "title": "Another free offer", "status": "unavailable"
            }], completed=True)],
            "Promotional Check #1", now=later,
        )
        self.assertEqual(summary["open_count"], 0)
        self.assertEqual(summary["resolved_this_run_count"], 1)
        self.assertEqual(self.meta["promotional_followups"]["ABCDEF1234"]["store-popup-blocked"]["status"], "resolved")

    def test_inconclusive_scan_does_not_resolve_open_followup(self):
        update_promotion_followups(
            self.meta,
            [self.result(promos=[{"key": "store-popup-blocked", "status": "popup_blocked"}])],
            "Manual Run", now=self.when,
        )
        summary = update_promotion_followups(
            self.meta,
            [self.result(promos=[], completed=True, detection_failed=True)],
            "Promotional Check #1", now=self.when,
        )
        self.assertEqual(summary["open_count"], 1)
        self.assertEqual(summary["still_blocked_count"], 1)
        self.assertEqual(summary["resolved_this_run_count"], 0)

    def test_uncompleted_scan_does_not_resolve_open_followup(self):
        update_promotion_followups(
            self.meta,
            [self.result(promos=[{"key": "store-popup-blocked", "status": "popup_blocked"}])],
            "Manual Run", now=self.when,
        )
        summary = update_promotion_followups(
            self.meta,
            [self.result(promos=[], completed=False)],
            "Promotional Check #1", now=self.when,
        )
        self.assertEqual(summary["open_count"], 1)

    def test_repeated_popup_block_increments_attempt_count_without_duplicate_item(self):
        observation = {"key": "store-popup-blocked", "status": "popup_blocked"}
        first = update_promotion_followups(self.meta, [self.result(promos=[observation])], "Manual Run", now=self.when)
        second = update_promotion_followups(self.meta, [self.result(promos=[observation])], "Promotional Check #1", now=self.when)
        self.assertEqual(first["open_count"], 1)
        self.assertEqual(second["open_count"], 1)
        self.assertEqual(second["open_items"][0]["attempts"], 2)

    def test_existing_eligible_offer_can_resolve_even_when_terminal_cta_is_no_longer_claim(self):
        self.meta["promotional_followups"] = {
            "ABCDEF1234": {
                "free-offer": {
                    "player_id": "ABCDEF1234", "key": "free-offer", "title": "Free weekly reward",
                    "status": "pending_recheck", "reason": "Needs check", "attempts": 1,
                }
            }
        }
        terminal = {
            "key": "free-offer", "title": "Free weekly reward", "status": "portal_claimed",
            "button_text": "Claimed", "card_text": "Free reward",
        }
        filtered = filter_followup_results([self.result(promos=[terminal])], self.meta, free_actionable_filter)
        self.assertEqual(len(filtered[0]["promotions_observed"]), 1)
        summary = update_promotion_followups(self.meta, filtered, "Promotional Check #1", now=self.when)
        self.assertEqual(summary["open_count"], 0)
        self.assertEqual(summary["resolved_this_run_count"], 1)

    def test_new_paid_terminal_offer_is_not_added_as_followup(self):
        paid = {
            "key": "paid-offer", "title": "Premium pack", "status": "portal_claimed",
            "button_text": "Claimed", "card_text": "₹499 purchase",
        }
        filtered = filter_followup_results([self.result(promos=[paid])], self.meta, free_actionable_filter)
        self.assertEqual(filtered[0]["promotions_observed"], [])
        summary = update_promotion_followups(self.meta, filtered, "Promotional Check #1", now=self.when)
        self.assertEqual(summary["open_count"], 0)
        self.assertEqual(self.meta.get("promotional_followups"), {})

    def test_specific_offer_absent_on_complete_safe_scan_becomes_not_retryable(self):
        pending = {"key": "offer-2", "title": "Limited offer", "status": "click_failed"}
        update_promotion_followups(self.meta, [self.result(promos=[pending])], "Manual Run", now=self.when)
        summary = update_promotion_followups(self.meta, [self.result(promos=[], completed=True)], "Promotional Check #1", now=self.when)
        self.assertEqual(summary["open_count"], 0)
        self.assertEqual(summary["not_retryable_this_run_count"], 1)
        self.assertEqual(self.meta["promotional_followups"]["ABCDEF1234"]["offer-2"]["status"], "not_retryable")

    def test_html_masks_player_id_and_shows_open_and_resolved_states(self):
        update_promotion_followups(
            self.meta,
            [self.result(promos=[{"key": "store-popup-blocked", "status": "popup_blocked"}])],
            "Manual Run", now=self.when,
        )
        summary = update_promotion_followups(
            self.meta,
            [self.result(promos=[], completed=True)],
            "Promotional Check #1", now=self.when,
        )
        html = build_promotional_followup_section(summary)
        self.assertIn("Promotion Follow-ups", html)
        self.assertIn("Player …1234", html)
        self.assertNotIn("ABCDEF1234", html)
        self.assertIn("Resolved", html)

    def test_legacy_run_3796_suffixes_are_seeded_once_and_resolved_by_first_clean_scan(self):
        suffixes = ["B91F", "F01A", "B2D8", "0765", "BD48"]
        results = [self.result(player=f"TESTACCOUNT{suffix}", promos=[], completed=True) for suffix in suffixes]
        seeded = seed_legacy_popup_followups(self.meta, results, now=self.when)
        self.assertEqual(seeded["seeded_count"], 5)
        self.assertEqual(seeded["unmatched_suffixes"], [])
        self.assertEqual(len(self.meta["promotional_followups"]), 5)
        self.assertEqual(
            {item["store-popup-blocked"]["status"] for item in self.meta["promotional_followups"].values()},
            {"pending_recheck"},
        )

        # The first complete, unobstructed Store scan clears all five historical blockers.
        filtered = filter_followup_results(results, self.meta, free_actionable_filter)
        summary = update_promotion_followups(self.meta, filtered, "Promotional Check #1", now=self.when)
        self.assertEqual(summary["open_count"], 0)
        self.assertEqual(summary["resolved_this_run_count"], 5)
        self.assertEqual(self.meta["promotion_followup_legacy_seed_v1"]["source_run"], 3796)

        second = seed_legacy_popup_followups(self.meta, results, now=self.when)
        self.assertTrue(second["already_seeded"])
        self.assertEqual(second["seeded_count"], 0)

    def test_legacy_seed_refuses_ambiguous_suffix_matches(self):
        results = [
            self.result(player="FIRSTACCOUNTB91F"),
            self.result(player="SECONDACCOUNTB91F"),
        ]
        seeded = seed_legacy_popup_followups(self.meta, results, now=self.when)
        self.assertEqual(seeded["seeded_count"], 0)
        self.assertIn("B91F", seeded["unmatched_suffixes"])
        self.assertNotIn("promotion_followup_legacy_seed_v1", self.meta)

    def test_empty_tracker_returns_empty_html(self):
        self.assertEqual(build_promotional_followup_section({"open_items": [], "updated_items": []}), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
