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
        self.assertIn("<strong>0</strong><small>Accounts with unresolved items", report)

    def test_invalid_summary_type_is_rejected(self):
        with self.assertRaises(TypeError):
            build_copyable_report([])


if __name__ == "__main__":
    unittest.main()
