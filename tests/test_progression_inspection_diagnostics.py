import unittest

from progression_inspection_diagnostics import (
    build_progression_inspection_diagnostic_summary,
    sanitize_progression_inspection_snapshot,
)


class ProgressionInspectionDiagnosticsTests(unittest.TestCase):
    def test_snapshot_whitelists_rail_and_card_hints_without_claiming_completeness(self):
        safe = sanitize_progression_inspection_snapshot({
            "page_path": "/progression-program?player=SECRET",
            "ready_state": "complete",
            "viewport": {"width": 1920, "height": 1080},
            "body_text_length": 900,
            "page_valid": True,
            "diagnostic_version": 2,
            "visible_claimable_count": 2,
            "visible_delivered_control_count": 1,
            "candidate_container_count": 14,
            "completeness_verdict": "verified",
            "delivery_verdict": "Delivered",
            "carousel_containers": [{
                "index": 0, "tag": "div", "class_name": "reward-carousel",
                "test_id": "carousel-1", "role": "region",
                "aria_label": "Reward chain", "aria_roledescription": "carousel",
                "data_slot": "carousel-content", "overflow_x": "hidden",
                "scroll_snap_type": "x mandatory", "display": "flex",
                "flex_direction": "row", "depth": 9,
                "is_semantic_carousel_root": True,
                "card_capture_source": "semantic_slide_nodes",
                "explicit_slide_count": 2,
                "rect": {"x": 10, "y": 20, "width": 900, "height": 250},
                "client_width": 900, "scroll_width": 1800, "scroll_left": 450,
                "max_scroll_left": 900, "at_start": False, "at_end": False,
                "direct_child_count": 8, "visible_child_count": 4,
                "captured_card_count": 1,
                "captured_cards": [{
                    "tag": "div", "class_name": "reward-card", "test_id": "reward-3",
                    "role": "group", "aria_label": "Milestone 3",
                    "data_slot": "carousel-item", "data_state": "delivered",
                    "text_snippet": "Milestone 3 x20 Delivered",
                    "button_labels": ["Delivered"],
                    "button_details": [{
                        "tag": "button", "label": "Delivered", "role": "",
                        "aria_label": "Delivered", "class_name": "status",
                        "data_slot": "button", "data_state": "delivered",
                        "disabled": True, "aria_disabled": "true"
                    }],
                    "exact_status_text_hints": ["Delivered"],
                    "status_elements": [{
                        "tag": "button", "text": "Delivered", "role": "",
                        "aria_label": "Delivered", "class_name": "status",
                        "data_state": "delivered"
                    }],
                    "milestone_number_hints": [3],
                    "rect": {"x": 10, "y": 20, "width": 200, "height": 220},
                }],
            }],
            "raw_html": "<html>must never be copied</html>",
        })
        self.assertEqual(safe["page_path"], "/progression-program")
        self.assertEqual(safe["diagnostic_version"], 2)
        self.assertEqual(safe["completeness_verdict"], "not_established")
        self.assertEqual(safe["delivery_verdict"], "not_established")
        self.assertEqual(safe["carousel_containers"][0]["role"], "region")
        card = safe["carousel_containers"][0]["captured_cards"][0]
        self.assertEqual(card["text_snippet"], "Milestone 3 x20 Delivered")
        self.assertTrue(card["button_details"][0]["disabled"])
        self.assertEqual(card["status_elements"][0]["text"], "Delivered")
        self.assertNotIn("raw_html", safe)
        self.assertNotIn("SECRET", str(safe))

    def test_sanitizer_redacts_account_ids_urls_emails_and_credentials_in_card_metadata(self):
        safe = sanitize_progression_inspection_snapshot({
            "page_path": "/progression-program",
            "carousel_containers": [{
                "aria_label": "test@example.com https://x.invalid/path",
                "captured_cards": [{
                    "text_snippet": "Player 0123456789ABCDEF https://x.invalid/path?token=abc password=hunter2",
                    "button_details": [{
                        "label": "https://x.invalid/?session=secret",
                        "aria_label": "test@example.com",
                        "data_state": "token=abc"
                    }]
                }],
            }],
        })
        text = str(safe)
        for forbidden in ("0123456789ABCDEF", "test@example.com", "https://x.invalid", "token=abc", "hunter2", "session=secret"):
            self.assertNotIn(forbidden, text)

    def test_sanitizer_caps_candidate_containers_cards_and_text(self):
        raw = {"carousel_containers": [
            {"captured_cards":[{"text_snippet":"X"*1000} for _ in range(60)]}
            for _ in range(20)
        ]}
        safe = sanitize_progression_inspection_snapshot(raw)
        self.assertEqual(len(safe["carousel_containers"]), 12)
        self.assertEqual(len(safe["carousel_containers"][0]["captured_cards"]), 40)
        self.assertLessEqual(len(safe["carousel_containers"][0]["captured_cards"][0]["text_snippet"]), 480)

    def test_compact_production_snapshot_keeps_diagnostic_schema_version_one(self):
        safe = sanitize_progression_inspection_snapshot({
            "page_path": "/progression-program",
            "carousel_containers": [],
        })
        self.assertEqual(safe["diagnostic_version"], 1)

    def test_run_summary_is_read_only_and_privacy_minimized(self):
        summary = build_progression_inspection_diagnostic_summary({
            "0123456789ABCDEF": [{"page_path":"/progression-program"}] * 30,
            "000000000000ABCD": [],
        }, configured_player_count=36)
        self.assertEqual(summary["diagnostic_version"], 2)
        self.assertTrue(summary["read_only"])
        self.assertFalse(summary["completeness_established"])
        self.assertFalse(summary["delivery_status_established"])
        self.assertEqual(summary["configured_accounts"], 36)
        self.assertEqual(summary["accounts_with_diagnostics"], 1)
        self.assertEqual(summary["snapshot_count"], 24)
        self.assertEqual(summary["accounts"][0]["player_id_last4"], "CDEF")
        self.assertNotIn("0123456789ABCDEF", str(summary))


if __name__ == "__main__":
    unittest.main()
