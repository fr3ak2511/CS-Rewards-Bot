import unittest
from unittest.mock import Mock, patch

import master_claimer as bot


class DailyTargetSafetyTests(unittest.TestCase):
    def test_daily_lookup_is_scoped_to_daily_reward_component(self):
        button = object()
        driver = Mock()
        driver.execute_script.return_value = {
            "status": "available",
            "button": button,
            "target": {"card_text": "Extra Reward Luckyloon x80", "index": 90},
        }

        found, state = bot._find_daily_claim_button(driver)

        self.assertIs(found, button)
        self.assertEqual(state, "available")
        script = driver.execute_script.call_args.args[0]
        self.assertIn('button[data-variant="component-daily-reward"]', script)
        self.assertIn("extra reward", script.lower())
        self.assertNotIn("document.querySelectorAll('button,[role=\"button\"]')", script)

    def test_daily_lookup_preserves_blocked_state(self):
        driver = Mock()
        driver.execute_script.return_value = {
            "status": "blocked", "reason": "visible_dialog_present", "blocker_count": 1,
        }
        button, state = bot._find_daily_claim_button(driver)
        self.assertIsNone(button)
        self.assertEqual(state, "blocked")

    def test_popup_wrapper_fails_closed_and_records_reason(self):
        driver = Mock()
        with patch.object(bot, "dismiss_all_safe_popups", return_value=(False, "no_unambiguous_safe_close")):
            ok, reason = bot._dismiss_daily_reward_popups(driver)
        self.assertFalse(ok)
        self.assertIn("no_unambiguous_safe_close", reason)
        self.assertFalse(driver._last_daily_popup_guard["ok"])

    def test_popup_wrapper_accepts_guard_verified_clear_page(self):
        driver = Mock()
        with patch.object(bot, "dismiss_all_safe_popups", return_value=(True, "clear; dismissed=1")):
            ok, reason = bot._dismiss_daily_reward_popups(driver)
        self.assertTrue(ok)
        self.assertIn("dismissed=1", reason)
        self.assertTrue(driver._last_daily_popup_guard["ok"])

    def test_prepare_target_uses_instant_scroll_and_waits_for_full_viewport_hit_test(self):
        button = object()
        driver = Mock()
        driver.execute_script.side_effect = [
            None,
            {
                "ready": True,
                "reason": "ready",
                "rect": {"x": 100, "y": 300, "width": 160, "height": 44},
                "viewport": {"width": 1440, "height": 900, "scroll_x": 0, "scroll_y": 4600},
                "hit_test": {"tag": "button", "text": "Claim"},
                "blocker_count": 0,
            },
        ]
        with patch.object(bot.time, "monotonic", side_effect=[10.0, 10.1]), \
             patch.object(bot.time, "sleep"):
            ready, trace = bot._prepare_daily_claim_target(driver, button)
        self.assertTrue(ready)
        self.assertEqual(trace["reason"], "ready")
        scroll_script = driver.execute_script.call_args_list[0].args[0]
        self.assertIn("behavior:'auto'", scroll_script)
        self.assertIn("block:'center'", scroll_script)
        self.assertIn("CS_HUB_DAILY_TARGET_READY_V1", driver.execute_script.call_args_list[1].args[0])

    def test_prepare_target_refuses_a_blocked_or_offscreen_hit_test(self):
        button = object()
        driver = Mock()
        driver.execute_script.side_effect = [
            None,
            {
                "ready": False,
                "reason": "target_obscured_at_click_point",
                "rect": {"x": 100, "y": 300, "width": 160, "height": 44},
                "viewport": {"width": 1440, "height": 900, "scroll_x": 0, "scroll_y": 4600},
                "hit_test": {"tag": "div", "text": "Store Bonus"},
                "blocker_count": 0,
            },
        ]
        with patch.object(bot.time, "monotonic", side_effect=[10.0, 10.1, 14.0]), \
             patch.object(bot.time, "sleep"):
            ready, trace = bot._prepare_daily_claim_target(driver, button, timeout=3.5)
        self.assertFalse(ready)
        self.assertEqual(trace["reason"], "target_obscured_at_click_point")
        self.assertEqual(trace["hit_test"]["text"], "Store Bonus")

    def test_daily_native_click_does_not_fall_back_to_action_chains_or_javascript(self):
        class Button:
            def click(self):
                raise RuntimeError("element is intercepted")

        driver = Mock()
        with patch.object(bot, "ActionChains", side_effect=AssertionError("fallback must not run")):
            clicked = bot._click_daily_claim_target(driver, Button())
        self.assertFalse(clicked)
        self.assertEqual(driver._last_physical_click_trace["method"], "not_dispatched")
        self.assertEqual(driver._last_physical_click_trace["attempted"], ["webdriver_element_click"])
        driver.execute_script.assert_not_called()

    def test_daily_claim_defers_without_click_when_popup_guard_refuses(self):
        driver = Mock()
        driver.current_url = "https://hub.vertigogames.co/daily-rewards"
        driver.get_screenshot_as_png.return_value = b"png"
        with patch.object(bot, "bypass_cloudflare"), \
             patch.object(bot.time, "sleep"), \
             patch.object(bot, "_dismiss_daily_reward_popups", return_value=(False, "unknown overlay")), \
             patch.object(bot, "_capture_daily_failure_stage"), \
             patch.object(bot, "_persist_daily_claim_diagnostics"), \
             patch.object(bot, "_find_daily_claim_button") as lookup, \
             patch.object(bot, "_click_daily_claim_target") as click, \
             patch.object(bot, "update_claim_history") as history:
            result = bot.claim_daily_rewards(driver, "TESTPLAYER0001")
        self.assertEqual(result, (0, False, True))
        lookup.assert_not_called()
        click.assert_not_called()
        history.assert_called_once_with("TESTPLAYER0001", "daily", unconfirmed=True)


class DisplayNameCaptureTests(unittest.TestCase):
    def test_capture_polls_until_profile_name_appears(self):
        driver = Mock()
        driver.execute_script.side_effect = [
            None,
            None,
            {"name": "SAVAggEEEE", "source": "text_near_profile_avatar"},
        ]
        with patch.object(bot.time, "sleep") as sleep:
            result = bot.capture_display_name(driver, attempts=3, poll_interval=0.1)
        self.assertEqual(result, "SAVAggEEEE")
        self.assertEqual(driver.execute_script.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_capture_accepts_alphanumeric_and_stylised_igns(self):
        self.assertEqual(bot._normalize_display_name("Gvk9916"), "Gvk9916")
        self.assertEqual(bot._normalize_display_name("SAVAggEEEE"), "SAVAggEEEE")
        self.assertEqual(bot._normalize_display_name("retHellGØD"), "retHellGØD")

    def test_capture_rejects_portal_navigation_labels(self):
        self.assertIsNone(bot._normalize_display_name("Claim your Daily Rewards"))
        self.assertIsNone(bot._normalize_display_name("Daily Rewards"))
        self.assertIsNone(bot._normalize_display_name("Official Page"))

    def test_capture_returns_none_after_bounded_empty_profile_reads(self):
        driver = Mock()
        driver.execute_script.return_value = None
        with patch.object(bot.time, "sleep"):
            result = bot.capture_display_name(driver, attempts=3, poll_interval=0.05)
        self.assertIsNone(result)
        self.assertEqual(driver.execute_script.call_count, 3)

    def test_capture_handles_dom_read_errors_without_raising(self):
        driver = Mock()
        driver.execute_script.side_effect = RuntimeError("profile root not rendered")
        with patch.object(bot.time, "sleep"):
            result = bot.capture_display_name(driver, attempts=2, poll_interval=0.05)
        self.assertIsNone(result)
        self.assertEqual(driver.execute_script.call_count, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
