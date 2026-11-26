import unittest

from account_activity_report import (
    build_account_activity_records,
    render_account_activity_section,
    summarize_account_activity,
)


class AccountActivityReportTests(unittest.TestCase):
    def test_promotional_only_run_marks_daily_store_and_uninspected_progression_not_checked(self):
        records = build_account_activity_records([{
            "pid": "PLAYER0001",
            "display_name": "Promo Player",
            "status": "Promotional Check Complete",
            "processing_mode": "promotional_only",
            "daily_portal_status": "not_checked",
            "standard_rewards_checked": False,
            "promotion_scan_completed": True,
            "promotions_observed": [],
            "progression_checked": False,
        }])

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["daily"]["state"], "not_checked")
        self.assertEqual(records[0]["store"]["state"], "not_checked")
        self.assertEqual(records[0]["progression"]["state"], "not_checked")
        categories = {row["label"]: row["value"] for row in summarize_account_activity(records)}
        self.assertEqual(categories["Daily"], "Not checked")
        self.assertEqual(categories["Standard Store"], "Not checked")
        self.assertEqual(categories["Progression"], "Not checked")
        self.assertIn("scanned", categories["Promotions"])

    def test_portal_claimed_daily_without_audit_is_not_credited_to_bot(self):
        records = build_account_activity_records([{
            "pid": "PLAYERB91F",
            "display_name": "Umang1984",
            "status": "Success",
            "processing_mode": "full_claim",
            "standard_rewards_checked": True,
            "daily": 0,
            "daily_portal_status": "portal_claimed",
            "daily_unconfirmed": False,
            "preexisting_portal_claims": [],
        }])
        daily = records[0]["daily"]
        self.assertEqual(daily["state"], "portal_claimed")
        self.assertEqual(daily["label"], "Portal says claimed")
        self.assertIn("Actor is unknown", daily["detail"])
        self.assertNotIn("Claimed by bot", daily["label"])
        self.assertTrue(records[0]["attention_required"])
        self.assertIn("unknown provenance", records[0]["next_action"].lower())

    def test_audited_preexisting_portal_claim_is_still_not_a_bot_claim(self):
        records = build_account_activity_records([{
            "pid": "PLAYER62E4",
            "display_name": "annu411",
            "status": "Success",
            "processing_mode": "full_claim",
            "standard_rewards_checked": True,
            "daily": 0,
            "daily_portal_status": "portal_claimed",
            "daily_unconfirmed": False,
            "preexisting_portal_claims": [{
                "reward_category": "daily",
                "reward_key": "daily",
                "reward_name": "Daily Reward",
                "actor": "unknown_portal_actor",
            }],
        }])
        self.assertEqual(records[0]["daily"]["state"], "portal_claimed")
        self.assertIn("before bot interaction", records[0]["daily"]["detail"])
        self.assertFalse(records[0]["daily"]["attention_required"])

    def test_bot_confirmed_daily_and_unconfirmed_daily_are_distinguished(self):
        records = build_account_activity_records([
            {
                "pid": "PLAYER0001", "display_name": "Claimed",
                "status": "Success", "processing_mode": "full_claim",
                "standard_rewards_checked": True, "daily": 1,
                "daily_portal_status": "claimed",
            },
            {
                "pid": "PLAYER0002", "display_name": "Pending",
                "status": "Partial", "processing_mode": "full_claim",
                "standard_rewards_checked": True, "daily": 0,
                "daily_portal_status": "unknown", "daily_unconfirmed": True,
                "retry_required": True,
            },
        ])
        self.assertEqual(records[0]["daily"]["state"], "claimed_by_bot")
        self.assertEqual(records[1]["daily"]["state"], "retry_queued")
        self.assertIn("retry candidate", records[1]["daily"]["detail"])

    def test_progression_overlay_and_empty_scan_remain_individual_pending_accounts(self):
        records = build_account_activity_records([
            {
                "pid": "OVERLAY0001", "display_name": "Freaksson",
                "status": "Promotional Check Complete", "processing_mode": "promotional_only",
                "progression_deferred": True, "progression_checked": False,
                "progression_reinspection_attempted": True,
                "progression_last_outcome_category": "overlay_blocked",
                "progression_last_reason": (
                    "popup/overlay safety guard blocked carousel recheck; "
                    "popup_context={\"private\":\"not for report\"}"
                ),
                "progression_same_outcome_count": 4,
                "progression_inspection_attempts": 4,
                "progression_inspection_slots_used": 4,
                "progression_manual_attention_required": True,
                "grenade_balance": None,
                "grenade_balance_carry_forward": {
                    "last_observed_count": 78,
                    "last_observed_date": "2026-11-26",
                    "expected_minimum_at_observation": 78,
                    "expected_minimum_as_of": 78,
                    "next_milestone_at_observation": 123,
                    "remaining_to_milestone_at_observation": 45,
                    "status_at_observation": "pass",
                },
            },
            {
                "pid": "EMPTYSCAN0002", "display_name": "AnwrHatela",
                "status": "Promotional Check Complete", "processing_mode": "promotional_only",
                "progression_deferred": True, "progression_checked": False,
                "progression_reinspection_attempted": True,
                "progression_last_outcome_category": "empty_scan",
                "progression_last_reason": "Queued Progression inspection found no currently visible claimable reward.",
                "progression_inspection_slots_used": 4,
                "progression_manual_attention_required": True,
            },
        ])
        self.assertEqual(len(records), 2)
        first = records[0]["progression"]
        self.assertEqual(first["state"], "blocked")
        self.assertTrue(first["pending_inspection"])
        self.assertIn("4/4 safe slots used", first["detail"])
        self.assertIn("78 Grenades", first["grenades"]["detail"])
        self.assertIn("Carried forward", first["grenades"]["detail"])
        self.assertIn("2026-11-26", first["grenades"]["detail"])
        self.assertIn("123", first["grenades"]["detail"])
        self.assertIn("45 to go", first["grenades"]["detail"])
        self.assertIn("all 4/4 safe inspection slots", first["next_action"])
        self.assertNotIn("popup_context", first["detail"])
        self.assertIn("Manual attention required", first["next_action"])

        second = records[1]["progression"]
        self.assertEqual(second["state"], "pending")
        self.assertIn("empty scan", second["label"].lower())
        self.assertTrue(second["pending_inspection"])
        self.assertIn("empty scan is not proof", second["next_action"].lower())

    def test_render_shows_one_masked_account_entry_per_player_and_next_action(self):
        records = build_account_activity_records([
            {
                "pid": "SENSITIVEPLAYER0001", "display_name": "Player One",
                "status": "Promotional Check Complete", "processing_mode": "promotional_only",
                "progression_deferred": True, "progression_checked": False,
                "progression_last_outcome_category": "empty_scan",
                "progression_inspection_slots_used": 4,
            },
            {
                "pid": "SENSITIVEPLAYER0002", "display_name": "Player Two",
                "status": "Promotional Check Complete", "processing_mode": "promotional_only",
            },
        ])
        report = render_account_activity_section(records)
        self.assertEqual(report.count("class='account-activity-card'"), 2)
        self.assertIn("Player One", report)
        self.assertIn("Player Two", report)
        self.assertIn("Next action:", report)
        self.assertNotIn("SENSITIVEPLAYER0001", report)
        self.assertNotIn("SENSITIVEPLAYER0002", report)


if __name__ == "__main__":
    unittest.main()
