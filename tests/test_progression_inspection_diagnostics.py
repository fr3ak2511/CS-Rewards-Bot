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
        self.assertEqual(summary["diagnostic_version"], 3)
        self.assertTrue(summary["read_only"])
        self.assertFalse(summary["completeness_established"])
        self.assertFalse(summary["delivery_status_established"])
        self.assertEqual(summary["configured_accounts"], 36)
        self.assertEqual(summary["accounts_with_diagnostics"], 1)
        self.assertEqual(summary["snapshot_count"], 24)
        self.assertEqual(summary["accounts"][0]["player_id_last4"], "CDEF")
        self.assertNotIn("0123456789ABCDEF", str(summary))


    def test_summary_selects_the_scrollable_rail_and_classifies_only_explicit_card_status(self):
        def card(threshold, text, *, buttons=None, statuses=None):
            return {
                "tag": "div",
                "class_name": "reward-card",
                "test_id": f"reward-{threshold}",
                "text_snippet": text,
                "milestone_number_hints": [threshold],
                "button_labels": [item.get("label", "") for item in (buttons or [])],
                "button_details": buttons or [],
                "exact_status_text_hints": [],
                "status_elements": statuses or [],
            }

        cards = [
            card(3, "3x20,000Delivered"),
            card(56, "56 x50 Delivered", buttons=[
                {"tag": "button", "label": "Delivered", "disabled": True, "aria_disabled": ""}
            ]),
            card(123, "123 x100 Claim", buttons=[
                {"tag": "button", "label": "Claim", "disabled": False, "aria_disabled": ""}
            ]),
            card(126, "126 x40 Locked", statuses=[
                {"tag": "span", "text": "Locked", "aria_label": "", "data_state": ""}
            ]),
            card(141, "141 x20", buttons=[
                {"tag": "button", "label": "", "disabled": True, "aria_disabled": ""}
            ]),
        ]
        wrapper_cards = [dict(item) for item in cards]
        snapshot = {
            "observed_at_ist": "2026-11-25T12:33:18+05:30",
            "page_path": "/progression-program",
            "ready_state": "complete",
            "page_valid": True,
            "diagnostic_version": 2,
            "carousel_containers": [
                {
                    "index": 0, "class_name": "overflow-x-auto reward-rail",
                    "overflow_x": "auto", "scroll_snap_type": "x mandatory",
                    "client_width": 1372, "scroll_width": 7232,
                    "max_scroll_left": 5860, "scroll_left": 5860, "at_end": True,
                    "captured_card_count": len(cards), "captured_cards": cards,
                },
                {
                    "index": 1, "class_name": "@container",
                    "overflow_x": "hidden", "client_width": 1372,
                    "scroll_width": 1396, "max_scroll_left": 24, "scroll_left": 0,
                    "at_end": False, "captured_card_count": len(wrapper_cards),
                    "captured_cards": wrapper_cards,
                },
            ],
        }

        summary = build_progression_inspection_diagnostic_summary(
            {"PLAYER0001": [snapshot]}, configured_player_count=1,
        )
        account = summary["accounts"][0]["normalized_progression_rail"]
        self.assertTrue(account["captured_rail_scan_completed"])
        self.assertEqual(account["selected_rail"]["container_index"], 0)
        self.assertEqual(account["selected_rail"]["max_scroll_left"], 5860)
        self.assertEqual(account["observed_milestone_thresholds"], [3, 56, 123, 126, 141])
        states = {item["threshold"]: item for item in account["milestone_cards"]}
        self.assertEqual(states[3]["classification"], "Delivered")
        self.assertEqual(states[56]["classification"], "Delivered")
        self.assertEqual(states[123]["classification"], "Claimable")
        self.assertEqual(states[126]["classification"], "Locked")
        self.assertEqual(states[141]["classification"], "Unknown")
        self.assertEqual(states[141]["reason"], "no_explicit_card_status")
        self.assertFalse(account["inventory_completeness_established"])
        self.assertFalse(summary["completeness_established"])
        self.assertFalse(summary["delivery_status_established"])

    def test_completed_scan_requires_stable_card_signature_and_full_roster(self):
        def make_snapshot(card_text, *, observed_at_end=True):
            card = {
                "text_snippet": card_text,
                "test_id": "reward-123",
                "class_name": "reward-card",
                "milestone_number_hints": [123],
                "button_labels": ["Delivered"],
                "button_details": [{"label": "Delivered", "disabled": True}],
            }
            return {
                "page_path": "/progression-program",
                "page_valid": True,
                "carousel_containers": [{
                    "index": 0, "class_name": "reward-rail", "overflow_x": "auto",
                    "client_width": 1000, "scroll_width": 2000, "max_scroll_left": 1000,
                    "scroll_left": 1000 if observed_at_end else 0, "at_end": observed_at_end,
                    "captured_cards": [card],
                }],
            }

        changed_identity = build_progression_inspection_diagnostic_summary(
            {"PLAYER0001": [
                make_snapshot("123 x100 Delivered"),
                make_snapshot("123 x100 SPECIAL Delivered"),
            ]},
            configured_player_count=1,
        )
        rail = changed_identity["accounts"][0]["normalized_progression_rail"]
        self.assertFalse(rail["captured_rail_scan_completed"])
        self.assertFalse(rail["card_signatures_stable_across_snapshots"])
        self.assertEqual(rail["milestone_cards"][0]["classification"], "Unknown")
        self.assertEqual(rail["milestone_cards"][0]["reason"], "card_identity_changed_across_snapshots")

        missing_account = build_progression_inspection_diagnostic_summary(
            {"PLAYER0001": [make_snapshot("123 x100 Delivered")]},
            configured_player_count=2,
        )
        self.assertEqual(missing_account["accounts_with_diagnostics"], 1)
        self.assertFalse(missing_account["inventory_consistency_across_accounts"])
        self.assertEqual(missing_account["common_observed_milestone_thresholds"], [])

    def test_disabled_claim_and_conflicting_card_statuses_remain_unknown(self):
        def snapshot(statuses, button_disabled):
            card = {
                "text_snippet": "123x100",
                "milestone_number_hints": [123],
                "button_labels": ["Claim"],
                "button_details": [{
                    "tag": "button", "label": "Claim", "disabled": button_disabled,
                    "aria_disabled": "true" if button_disabled else "",
                }],
                "exact_status_text_hints": statuses,
                "status_elements": [],
            }
            return {
                "page_path": "/progression-program",
                "page_valid": True,
                "carousel_containers": [{
                    "index": 0, "class_name": "reward-rail", "overflow_x": "auto",
                    "client_width": 1000, "scroll_width": 2000, "max_scroll_left": 1000,
                    "scroll_left": 1000, "at_end": True,
                    "captured_cards": [card],
                }],
            }

        disabled = build_progression_inspection_diagnostic_summary(
            {"PLAYER0001": [snapshot([], True)]}, configured_player_count=1,
        )
        disabled_card = disabled["accounts"][0]["normalized_progression_rail"]["milestone_cards"][0]
        self.assertEqual(disabled_card["classification"], "Unknown")
        self.assertEqual(disabled_card["reason"], "claim_control_not_proven_enabled")

        conflict = build_progression_inspection_diagnostic_summary(
            {"PLAYER0001": [snapshot(["Delivered", "Locked"], False)]},
            configured_player_count=1,
        )
        conflict_card = conflict["accounts"][0]["normalized_progression_rail"]["milestone_cards"][0]
        self.assertEqual(conflict_card["classification"], "Unknown")
        self.assertEqual(conflict_card["reason"], "conflicting_card_status_signals")


if __name__ == "__main__":
    unittest.main()
