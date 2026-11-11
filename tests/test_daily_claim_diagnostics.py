import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

import master_claimer as bot


class DailyClaimDiagnosticCaptureTests(unittest.TestCase):
    def test_capture_records_page_metadata_and_png_in_memory(self):
        driver = Mock()
        driver.current_url = "https://hub.vertigogames.co/daily-rewards"
        driver.title = "Daily Rewards"
        driver.execute_script.return_value = {
            "total_button_count": 3,
            "claim_button_count": 1,
            "buttons": [{
                "index": 0,
                "text": "Claim",
                "visible": True,
                "enabled": True,
                "aria_disabled": None,
                "bounding_box": {"x": 10, "y": 20, "width": 80, "height": 30},
                "outer_html_excerpt": '<button class="claim">Claim</button>',
            }],
            "visible_page_text": "Daily Reward",
            "timer_text_lines": ["Daily Reward", "Next reward in"],
        }
        driver.get_screenshot_as_png.return_value = b"png-bytes"

        captured = bot._capture_daily_claim_diagnostic(
            driver, "pre_click", attempt=1,
            click_trace={"method": "webdriver_element_click"},
        )

        self.assertEqual(captured["metadata"]["stage"], "pre_click")
        self.assertEqual(captured["metadata"]["url"], driver.current_url)
        self.assertEqual(captured["metadata"]["total_button_count"], 3)
        self.assertEqual(captured["metadata"]["claim_button_count"], 1)
        self.assertTrue(captured["metadata"]["buttons"][0]["visible"])
        self.assertTrue(captured["metadata"]["buttons"][0]["enabled"])
        self.assertEqual(captured["metadata"]["click_trace"]["method"], "webdriver_element_click")
        self.assertEqual(captured["screenshot"], b"png-bytes")

    def test_capture_failure_does_not_raise_or_affect_claim_flow(self):
        driver = Mock()
        driver.execute_script.side_effect = RuntimeError("DOM unavailable")
        driver.get_screenshot_as_png.side_effect = RuntimeError("screenshot unavailable")

        captured = bot._capture_daily_claim_diagnostic(driver, "post_refresh", attempt=1)

        self.assertEqual(captured["metadata"]["dom_capture_error"], "RuntimeError")
        self.assertEqual(captured["metadata"]["screenshot_error"], "RuntimeError")
        self.assertIsNone(captured["screenshot"])

    def test_persist_writes_masked_player_id_json_and_png_files(self):
        evidence = [{
            "metadata": {"stage": "pre_click", "attempt": 1, "screenshot_captured": True},
            "screenshot": b"png-bytes",
        }, {
            "metadata": {"stage": "post_refresh", "attempt": 1, "screenshot_captured": False},
            "screenshot": None,
        }]
        with tempfile.TemporaryDirectory() as temp_dir, \
             patch.object(bot, "DAILY_CLAIM_DIAGNOSTICS_DIR", temp_dir), \
             patch.dict(os.environ, {"GITHUB_RUN_ID": "12345", "GITHUB_RUN_ATTEMPT": "2"}):
            json_path = bot._persist_daily_claim_diagnostics(
                "PLAYER-SECRET-0001", evidence, "daily_claim_unconfirmed"
            )

            self.assertIsNotNone(json_path)
            with open(json_path, encoding="utf-8") as json_file:
                payload = json.load(json_file)
            self.assertEqual(payload["player_id_last4"], "0001")
            self.assertEqual(payload["run_id"], "12345")
            self.assertEqual(payload["run_attempt"], "2")
            self.assertNotIn("PLAYER-SECRET", open(json_path, encoding="utf-8").read())
            screenshot_name = payload["stages"][0]["screenshot_file"]
            self.assertTrue(screenshot_name.endswith(".png"))
            with open(os.path.join(temp_dir, screenshot_name), "rb") as image_file:
                self.assertEqual(image_file.read(), b"png-bytes")
            self.assertIsNone(payload["stages"][1]["screenshot_file"])

    def test_physical_click_trace_records_webdriver_element_route(self):
        driver = Mock()
        element = Mock()
        with patch.object(bot.time, "sleep"):
            self.assertTrue(bot.physical_click(driver, element))

        self.assertEqual(
            driver._last_physical_click_trace["method"],
            "webdriver_element_click",
        )
        self.assertEqual(
            driver._last_physical_click_trace["attempted"],
            ["webdriver_element_click"],
        )

    def test_physical_click_trace_records_javascript_fallback_route(self):
        driver = Mock()
        element = Mock()
        element.click.side_effect = RuntimeError("element click failed")
        action_chain = Mock()
        action_chain.move_to_element.return_value = action_chain
        action_chain.click.return_value = action_chain
        action_chain.perform.side_effect = RuntimeError("action chain failed")
        with patch.object(bot, "ActionChains", return_value=action_chain), \
             patch.object(bot.time, "sleep"):
            self.assertTrue(bot.physical_click(driver, element))

        self.assertEqual(
            driver._last_physical_click_trace["method"],
            "javascript_click_fallback",
        )
        self.assertEqual(
            driver._last_physical_click_trace["attempted"],
            [
                "webdriver_element_click",
                "action_chains_click",
                "javascript_click_fallback",
            ],
        )


if __name__ == "__main__":
    unittest.main()
