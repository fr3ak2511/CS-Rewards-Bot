import unittest

from progression_inspection_diagnostics import (
    build_progression_inspection_diagnostic_summary,
    sanitize_progression_inspection_snapshot,
)


class ProgressionInspectionDiagnosticsTests(unittest.TestCase):
    def test_snapshot_whitelists_visible_hints_and_never_claims_completeness(self):
        safe = sanitize_progression_inspection_snapshot({
            "page_path": "/progression-program?player=SECRET",
            "ready_state": "complete",
            "viewport": {"width": 1920, "height": 1080},
            "body_text_length": 900,
            "page_valid": True,
            "visible_claimable_count": 2,
            "visible_delivered_control_count": 1,
            "candidate_container_count": 1,
            "completeness_verdict": "verified",
            "delivery_verdict": "Delivered",
            "carousel_containers": [{
                "index": 0, "tag": "div", "class_name": "reward-carousel",
                "test_id": "carousel-1",
                "rect": {"x": 10, "y": 20, "width": 900, "height": 250},
                "client_width": 900, "scroll_width": 1800, "scroll_left": 450,
                "max_scroll_left": 900, "at_start": False, "at_end": False,
                "direct_child_count": 8, "visible_child_count": 4,
                "captured_cards": [{
                    "tag": "div", "class_name": "reward-card", "test_id": "reward-3",
                    "text_snippet": "Milestone 3 x20 Claim",
                    "button_labels": ["Claim"], "exact_status_text_hints": ["Claim"],
                    "milestone_number_hints": [3],
                    "rect": {"x": 10, "y": 20, "width": 200, "height": 220},
                }],
            }],
            "raw_html": "<html>must never be copied</html>",
        })
        self.assertEqual(safe["page_path"], "/progression-program")
        self.assertEqual(safe["completeness_verdict"], "not_established")
        self.assertEqual(safe["delivery_verdict"], "not_established")
        self.assertEqual(safe["carousel_containers"][0]["captured_cards"][0]["text_snippet"], "Milestone 3 x20 Claim")
        self.assertNotIn("raw_html", safe)
        self.assertNotIn("SECRET", str(safe))

    def test_sanitizer_redacts_account_ids_urls_emails_and_credentials(self):
        safe = sanitize_progression_inspection_snapshot({
            "page_path": "/progression-program",
            "carousel_containers": [{
                "captured_cards": [{
                    "text_snippet": "Player 0123456789ABCDEF test@example.com https://x.invalid/path?token=abc password=hunter2",
                }],
            }],
        })
        text = str(safe)
        for forbidden in ("0123456789ABCDEF", "test@example.com", "https://x.invalid", "token=abc", "hunter2"):
            self.assertNotIn(forbidden, text)

    def test_sanitizer_caps_carousel_containers_cards_and_text(self):
        raw = {"carousel_containers": [
            {"captured_cards":[{"text_snippet":"X"*1000} for _ in range(20)]}
            for _ in range(8)
        ]}
        safe = sanitize_progression_inspection_snapshot(raw)
        self.assertEqual(len(safe["carousel_containers"]), 3)
        self.assertEqual(len(safe["carousel_containers"][0]["captured_cards"]), 10)
        self.assertLessEqual(len(safe["carousel_containers"][0]["captured_cards"][0]["text_snippet"]), 320)

    def test_run_summary_is_explicitly_read_only_and_privacy_minimized(self):
        summary = build_progression_inspection_diagnostic_summary({
            "0123456789ABCDEF": [{"page_path":"/progression-program"}] * 10,
            "000000000000ABCD": [],
        }, configured_player_count=36)
        self.assertTrue(summary["read_only"])
        self.assertFalse(summary["completeness_established"])
        self.assertFalse(summary["delivery_status_established"])
        self.assertEqual(summary["configured_accounts"], 36)
        self.assertEqual(summary["accounts_with_diagnostics"], 1)
        self.assertEqual(summary["snapshot_count"], 6)
        self.assertEqual(summary["accounts"][0]["player_id_last4"], "CDEF")
        self.assertNotIn("0123456789ABCDEF", str(summary))


if __name__ == "__main__":
    unittest.main()
