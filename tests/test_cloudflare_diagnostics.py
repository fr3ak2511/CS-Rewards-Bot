import unittest
from unittest.mock import Mock, patch

import master_claimer as bot


class DetectorDriver:
    def __init__(self, result=None, *, error=None, page_source=""):
        self.result = result or {}
        self.error = error
        self.page_source = page_source
        self.title = "CS Hub Rewards"
        self.current_url = "https://hub.vertigogames.co/store"

    def execute_script(self, _script):
        if self.error is not None:
            raise self.error
        return self.result


class CloudflareDiagnosticsTests(unittest.TestCase):
    def test_generic_verifying_word_in_page_source_is_not_a_challenge(self):
        driver = DetectorDriver(
            {
                "title": "CS Hub Rewards",
                "visibleTextSignals": [],
                "visibleElementSignals": [],
            },
            page_source="<script>const status = 'verifying';</script>",
        )
        with patch.object(bot, "log") as log:
            self.assertFalse(
                bot._is_cloudflare_interstitial(
                    driver, log_no_challenge=False
                )
            )
        log.assert_not_called()

    def test_just_a_moment_title_is_a_challenge_signal(self):
        driver = DetectorDriver({
            "title": "Just a moment...",
            "visibleTextSignals": [],
            "visibleElementSignals": [],
        })
        with patch.object(bot, "log"):
            self.assertTrue(
                bot._is_cloudflare_interstitial(
                    driver, log_no_challenge=False
                )
            )

    def test_visible_challenge_text_and_element_are_challenge_signals(self):
        results = (
            {
                "title": "CS Hub",
                "visibleTextSignals": ["verify you are human"],
                "visibleElementSignals": [],
            },
            {
                "title": "CS Hub",
                "visibleTextSignals": [],
                "visibleElementSignals": ["#challenge-form"],
            },
        )
        for result in results:
            with self.subTest(result=result):
                driver = DetectorDriver(result)
                with patch.object(bot, "log"):
                    self.assertTrue(
                        bot._is_cloudflare_interstitial(
                            driver, log_no_challenge=False
                        )
                    )

    def test_detector_uncertainty_can_be_treated_conservatively_by_wait_helper(self):
        driver = DetectorDriver(error=RuntimeError("DOM unavailable"))
        with patch.object(bot, "log"):
            self.assertFalse(
                bot._is_cloudflare_interstitial(
                    driver, log_no_challenge=False
                )
            )
            self.assertTrue(
                bot._is_cloudflare_interstitial(
                    driver,
                    log_no_challenge=False,
                    inconclusive_as_detected=True,
                )
            )

    def test_bypass_returns_without_wait_when_no_visible_challenge_exists(self):
        driver = Mock()
        with patch.object(bot, "_is_cloudflare_interstitial", return_value=False):
            with patch.object(bot.time, "sleep") as sleep:
                with patch.object(bot, "log"):
                    bot.bypass_cloudflare(driver)
        sleep.assert_not_called()
        driver.find_elements.assert_not_called()

    def test_bypass_logs_success_only_after_visible_challenge_disappears(self):
        driver = Mock()
        driver.current_url = "https://hub.vertigogames.co/store"
        driver.find_elements.return_value = []
        with patch.object(
            bot, "_is_cloudflare_interstitial", side_effect=[True, False]
        ):
            with patch.object(bot.time, "sleep"):
                with patch.object(bot, "log") as log:
                    bot.bypass_cloudflare(driver)
        log.assert_any_call("🛡️ Visible Cloudflare challenge detected — waiting...")
        log.assert_any_call("✅ Cloudflare challenge cleared after 1/10 state checks")

    def test_bypass_reports_unresolved_challenge_instead_of_silent_return(self):
        driver = Mock()
        driver.current_url = "https://hub.vertigogames.co/store"
        driver.find_elements.return_value = []
        with patch.object(
            bot, "_is_cloudflare_interstitial", side_effect=[True] * 11
        ):
            with patch.object(bot.time, "sleep"):
                with patch.object(bot, "log") as log:
                    bot.bypass_cloudflare(driver)
        log.assert_any_call(
            "⚠️ Cloudflare challenge remained visible after 10 state checks; no clearance was confirmed"
        )

    def test_checkbox_click_and_original_wait_are_preserved(self):
        driver = Mock()
        driver.current_url = "https://hub.vertigogames.co/store"
        checkbox = Mock()
        driver.find_elements.return_value = [checkbox]
        with patch.object(
            bot, "_is_cloudflare_interstitial", side_effect=[True, False]
        ):
            with patch.object(bot.time, "sleep") as sleep:
                with patch.object(bot, "log"):
                    bot.bypass_cloudflare(driver)
        checkbox.click.assert_called_once_with()
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [5, 3])


if __name__ == "__main__":
    unittest.main()
