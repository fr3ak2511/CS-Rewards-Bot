import unittest
from unittest.mock import patch

import master_claimer as bot


class StorePromotionPopupSafetyTests(unittest.TestCase):
    @staticmethod
    def _card(title="Halloween Offer", state="available", button=None):
        return {
            "key": title.lower().replace(" ", "-"),
            "title": title,
            "campaign_id": None,
            "state": state,
            "button": button if button is not None else object(),
            "disabled": state != "available",
        }

    def test_unknown_popup_before_discovery_prevents_all_promotion_clicks(self):
        driver = object()
        with patch.object(
            bot, "_dismiss_store_promotion_popups",
            return_value=(False, "unknown_fullscreen_overlay_blocks_page"),
        ), patch.object(bot, "discover_promotional_cards") as discover, patch.object(
            bot, "physical_click"
        ) as click:
            results = bot.discover_and_claim_promotions(driver, "TESTPLAYER0001")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "popup_blocked")
        self.assertEqual(results[0]["claimable_observed"], 0)
        discover.assert_not_called()
        click.assert_not_called()

    def test_popup_appearing_before_claim_prevents_click(self):
        driver = object()
        card = self._card()
        with patch.object(
            bot, "_dismiss_store_promotion_popups",
            side_effect=[
                (True, "clear; dismissed=0"),
                (False, "blocking_dialog_has_no_unambiguous_safe_close"),
            ],
        ), patch.object(
            bot, "discover_promotional_cards", return_value=[card]
        ), patch.object(bot, "physical_click") as click, patch.object(
            bot, "_record_promotion_history"
        ) as history, patch.object(bot, "log"), patch.object(bot.time, "sleep"):
            results = bot.discover_and_claim_promotions(driver, "TESTPLAYER0001")

        self.assertEqual(results[0]["status"], "popup_blocked")
        click.assert_not_called()
        history.assert_called_once()
        self.assertEqual(history.call_args.args[2], "popup_blocked")

    def test_promotion_click_forbids_javascript_fallback(self):
        driver = object()
        button = object()
        card = self._card(button=button)
        claimed_card = self._card(state="portal_claimed", button=None)
        with patch.object(
            bot, "_dismiss_store_promotion_popups",
            side_effect=[
                (True, "clear; dismissed=0"),
                (True, "clear; dismissed=0"),
                (True, "clear; dismissed=1"),
            ],
        ) as guard, patch.object(
            bot, "discover_promotional_cards",
            side_effect=[[card], [card], [claimed_card]],
        ), patch.object(bot, "physical_click", return_value=True) as click, patch.object(
            bot, "_record_promotion_history"
        ) as history, patch.object(bot, "log"), patch.object(bot.time, "sleep"):
            results = bot.discover_and_claim_promotions(driver, "TESTPLAYER0001")

        self.assertEqual(results[0]["status"], "claimed_this_run")
        click.assert_called_once_with(driver, button, allow_js_fallback=False)
        self.assertEqual(guard.call_count, 3)
        history.assert_called_once_with(
            "TESTPLAYER0001", card, "claimed_this_run", claimed=True
        )

    def test_popup_after_first_claim_stops_remaining_promotions(self):
        driver = object()
        first = self._card("First Offer")
        second = self._card("Second Offer")
        with patch.object(
            bot, "_dismiss_store_promotion_popups",
            side_effect=[
                (True, "clear; dismissed=0"),
                (True, "clear; dismissed=0"),
                (False, "unknown_fullscreen_overlay_blocks_page"),
            ],
        ), patch.object(
            bot, "discover_promotional_cards",
            side_effect=[[first, second], [first]],
        ), patch.object(bot, "physical_click", return_value=True) as click, patch.object(
            bot, "_record_promotion_history"
        ) as history, patch.object(bot, "log"), patch.object(bot.time, "sleep"):
            results = bot.discover_and_claim_promotions(driver, "TESTPLAYER0001")

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "First Offer")
        self.assertEqual(results[0]["status"], "popup_blocked")
        click.assert_called_once_with(
            driver, first["button"], allow_js_fallback=False
        )
        self.assertEqual(history.call_count, 1)
        self.assertEqual(history.call_args.args[2], "popup_blocked")


if __name__ == "__main__":
    unittest.main()