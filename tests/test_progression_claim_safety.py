import unittest
from unittest.mock import Mock

from progression_claim_safety import _is_confirmed, claim_progression_rewards


class BrowserStub:
    current_url = "https://hub.vertigogames.co/progression-program"

    def get(self, url):
        self.current_url = url


class ProgressionSafetyTests(unittest.TestCase):
    def _run(self, snapshots, *, dismiss_results=None, verify_polls=3):
        driver = BrowserStub()
        click_calls = []
        history_calls = []
        dismiss_iter = iter(dismiss_results or [True] * 20)

        def snapshot(_driver):
            if callable(snapshots):
                return snapshots()
            if isinstance(snapshots, list):
                return snapshots.pop(0)
            return snapshots

        def dismiss(_driver):
            try:
                value = next(dismiss_iter)
            except StopIteration:
                value = True
            return (value, "test")

        def click(_driver, button):
            click_calls.append(button)
            return True

        claimed = claim_progression_rewards(
            driver, "TESTPLAYER0001", log=lambda _m: None,
            bypass_cloudflare=lambda _d: None, snapshot_fn=snapshot,
            click_fn=click,
            update_history=lambda *args, **kwargs: history_calls.append((args, kwargs)),
            dismiss_popups=dismiss, scroll_carousel=lambda _d: False,
            sleep=lambda _seconds: None, verify_polls=verify_polls,
        )
        return claimed, driver, click_calls, history_calls

    def test_same_card_delivered_signature_confirms_claim(self):
        button = object()
        before = {"valid_page": True, "claimable_count": 1, "delivered_count": 0,
                  "button": button, "button_text": "claim", "card_signature": "Grenade reward 3"}
        after = {"valid_page": True, "claimable_count": 0, "delivered_count": 1,
                 "button": None, "delivered_signatures": ["Grenade reward 3"]}
        claimed, driver, clicks, history = self._run([before, after, after])
        self.assertEqual(claimed, 1)
        self.assertEqual(len(clicks), 1)
        self.assertFalse(driver._progression_unconfirmed)
        self.assertEqual(history[-1][1], {"claimed_count": 1, "unconfirmed": False})

    def test_claim_to_delivered_label_change_preserves_card_identity(self):
        before = {
            "card_signature": "6 x20 Claim",
            "card_text": "6 x20 Claim",
            "delivered_signatures": [],
        }
        after = {
            "delivered_signatures": ["6 x20 Delivered"],
            "terminal_card_signatures": ["6 x20 Delivered"],
        }
        self.assertTrue(_is_confirmed(before, after))

    def test_different_delivered_card_does_not_confirm_target(self):
        before = {
            "card_signature": "6 x20 Claim",
            "card_text": "6 x20 Claim",
            "delivered_signatures": [],
        }
        after = {"delivered_signatures": ["12 x80 Delivered"]}
        self.assertFalse(_is_confirmed(before, after))

    def test_ambiguous_canonical_match_does_not_confirm_target(self):
        before = {
            "card_signature": "6 x20 Claim",
            "card_text": "6 x20 Claim",
            "delivered_signatures": [],
        }
        after = {"delivered_signatures": ["6 x20 Delivered", "6  x20 Delivered"]}
        # Distinct DOM candidates that collapse to the same identity are
        # ambiguous and must not confirm the attempted card.
        self.assertFalse(_is_confirmed(before, after))

    def test_claim_button_disappearing_without_same_card_delivered_is_not_success(self):
        button = object()
        before = {"valid_page": True, "claimable_count": 1, "delivered_count": 0,
                  "button": button, "button_text": "claim", "card_signature": "Grenade reward 3"}
        after = {"valid_page": True, "claimable_count": 0, "delivered_count": 0,
                 "button": None, "delivered_signatures": []}
        claimed, driver, clicks, history = self._run([before] + [after] * 5)
        self.assertEqual(claimed, 0)
        self.assertEqual(len(clicks), 1)
        self.assertTrue(driver._progression_unconfirmed)
        self.assertEqual(history[-1][1], {"claimed_count": 0, "unconfirmed": True})

    def test_unknown_popup_blocks_claim_before_click(self):
        button = object()
        before = {"valid_page": True, "claimable_count": 1, "delivered_count": 0,
                  "button": button, "button_text": "claim", "card_signature": "Grenade reward 3"}
        claimed, driver, clicks, history = self._run(before, dismiss_results=[False])
        self.assertEqual(claimed, 0)
        self.assertEqual(clicks, [])
        self.assertTrue(driver._progression_unconfirmed)
        self.assertEqual(history[-1][1], {"claimed_count": 0, "unconfirmed": True})

    def test_popup_that_appears_after_click_is_rechecked_before_confirmation(self):
        button = object()
        before = {"valid_page": True, "claimable_count": 1, "delivered_count": 0,
                  "button": button, "button_text": "claim", "card_signature": "Grenade reward 3"}
        after = {"valid_page": True, "claimable_count": 0, "delivered_count": 1,
                 "button": None, "delivered_signatures": ["Grenade reward 3"]}
        # Initial page clear, clear after positioning, one blocked polling pass,
        # then a successfully cleared pass and portal confirmation.
        claimed, _driver, clicks, _history = self._run(
            [before, after, after], dismiss_results=[True, True, False, True]
        )
        self.assertEqual(claimed, 1)
        self.assertEqual(len(clicks), 1)

    def test_unconfirmed_click_is_never_dispatched_twice(self):
        button = object()
        before = {"valid_page": True, "claimable_count": 1, "delivered_count": 0,
                  "button": button, "button_text": "claim", "card_signature": "Grenade reward 3"}
        after = {"valid_page": True, "claimable_count": 1, "delivered_count": 0,
                 "button": object(), "button_text": "claim", "card_signature": "Grenade reward 3"}
        claimed, _driver, clicks, _history = self._run([before] + [after] * 5)
        self.assertEqual(claimed, 0)
        self.assertEqual(len(clicks), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
