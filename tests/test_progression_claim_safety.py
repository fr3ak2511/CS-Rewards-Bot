import unittest
from unittest.mock import Mock

from progression_claim_safety import _is_confirmed, claim_progression_rewards


class BrowserStub:
    current_url = "https://hub.vertigogames.co/progression-program"

    def get(self, url):
        self.current_url = url


class ProgressionSafetyTests(unittest.TestCase):
    def _run(self, snapshots, *, dismiss_results=None, verify_polls=3,
             target_card_signature=None, target_card_text_fragments=None,
             require_unique_claimable_card=False, defer_if_more_claimable=False,
             max_claims_per_visit=6):
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
            target_card_signature=target_card_signature,
            target_card_text_fragments=target_card_text_fragments,
            require_unique_claimable_card=require_unique_claimable_card,
            defer_if_more_claimable=defer_if_more_claimable,
            max_claims_per_visit=max_claims_per_visit,
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
        self.assertFalse(driver._progression_deferred)
        self.assertTrue(driver._progression_claim_attempted)
        self.assertEqual(driver._progression_unconfirmed_card_signature, "Grenade reward 3")
        self.assertEqual(history[-1][1], {"claimed_count": 0, "unconfirmed": True})

    def test_unknown_popup_blocks_claim_before_click(self):
        button = object()
        before = {"valid_page": True, "claimable_count": 1, "delivered_count": 0,
                  "button": button, "button_text": "claim", "card_signature": "Grenade reward 3"}
        claimed, driver, clicks, history = self._run(before, dismiss_results=[False])
        self.assertEqual(claimed, 0)
        self.assertEqual(clicks, [])
        self.assertFalse(driver._progression_unconfirmed)
        self.assertTrue(driver._progression_deferred)
        self.assertFalse(driver._progression_claim_attempted)
        self.assertFalse(driver._progression_checked)
        # A blocked inspection is not written into claim history as a failed claim.
        self.assertEqual(history, [])

    def test_popup_blocked_recheck_preserves_a_previously_dispatched_target(self):
        claimed, driver, clicks, history = self._run(
            {"valid_page": True, "claimable_count": 1, "delivered_count": 0,
             "button": object(), "button_text": "claim", "card_signature": "Prior target"},
            dismiss_results=[False],
            target_card_signature="Prior target",
        )
        self.assertEqual(claimed, 0)
        self.assertEqual(clicks, [])
        self.assertTrue(driver._progression_unconfirmed)
        self.assertTrue(driver._progression_deferred)
        self.assertFalse(driver._progression_claim_attempted)
        self.assertEqual(driver._progression_unconfirmed_card_signature, "Prior target")
        self.assertEqual(history[-1][1], {"claimed_count": 0, "unconfirmed": True})

    def test_snapshot_failure_is_deferred_without_false_claim_failure(self):
        claimed, driver, clicks, history = self._run(None)
        self.assertEqual(claimed, 0)
        self.assertEqual(clicks, [])
        self.assertFalse(driver._progression_unconfirmed)
        self.assertTrue(driver._progression_deferred)
        self.assertFalse(driver._progression_claim_attempted)
        self.assertEqual(history, [])

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


    def test_recovery_clicks_only_the_matching_failed_card(self):
        target_button, other_button = object(), object()
        before = {
            "valid_page": True, "claimable_count": 2, "delivered_count": 0,
            "button": other_button, "button_text": "claim",
            "card_signature": "Other reward", "card_text": "Other reward",
            "claimable_cards": [
                {"button": other_button, "button_text": "claim",
                 "card_signature": "Other reward", "card_text": "Other reward"},
                {"button": target_button, "button_text": "claim",
                 "card_signature": "Failed reward", "card_text": "Failed reward"},
            ],
        }
        after = {
            "valid_page": True, "claimable_count": 1, "delivered_count": 1,
            "button": other_button, "button_text": "claim",
            "card_signature": "Other reward", "card_text": "Other reward",
            "claimable_cards": [
                {"button": other_button, "button_text": "claim",
                 "card_signature": "Other reward", "card_text": "Other reward"}
            ],
            "delivered_signatures": ["Failed reward"],
        }
        claimed, driver, clicks, _history = self._run(
            [before, after],
            target_card_signature="Failed reward",
            max_claims_per_visit=1,
        )
        self.assertEqual(claimed, 1)
        self.assertEqual(clicks, [target_button])
        self.assertFalse(driver._progression_unconfirmed)

    def test_recovery_does_not_click_another_card_when_failed_card_is_already_delivered(self):
        other_button = object()
        snapshot = {
            "valid_page": True, "claimable_count": 1, "delivered_count": 1,
            "button": other_button, "button_text": "claim",
            "card_signature": "Other reward", "card_text": "Other reward",
            "claimable_cards": [
                {"button": other_button, "button_text": "claim",
                 "card_signature": "Other reward", "card_text": "Other reward"}
            ],
            "delivered_signatures": ["Failed reward"],
        }
        claimed, driver, clicks, _history = self._run(
            [snapshot],
            target_card_signature="Failed reward",
            max_claims_per_visit=1,
        )
        self.assertEqual(claimed, 0)
        self.assertEqual(clicks, [])
        self.assertFalse(driver._progression_unconfirmed)




    def test_queued_text_selector_claims_only_the_unique_62_x20_milestone(self):
        other_button = object()
        target_button = object()
        before = {
            "valid_page": True, "claimable_count": 2, "delivered_count": 0,
            "claimable_cards": [
                {"button": other_button, "button_text": "claim",
                 "card_signature": "Milestone 65 | x80",
                 "card_text": "Milestone 65\nx80"},
                {"button": target_button, "button_text": "claim",
                 "card_signature": "Milestone 62 | x20",
                 "card_text": "Milestone 62\nx20"},
            ],
            "delivered_signatures": [],
        }
        after = {
            "valid_page": True, "claimable_count": 1, "delivered_count": 1,
            "claimable_cards": [
                {"button": other_button, "button_text": "claim",
                 "card_signature": "Milestone 65 | x80",
                 "card_text": "Milestone 65\nx80"},
            ],
            "delivered_signatures": ["Milestone 62 | x20"],
            "terminal_card_signatures": ["Milestone 62 | x20"],
        }
        claimed, driver, clicks, _history = self._run(
            [before, after],
            target_card_text_fragments=("62", "x20"),
            max_claims_per_visit=1,
        )
        self.assertEqual(claimed, 1)
        self.assertEqual(clicks, [target_button])
        self.assertFalse(driver._progression_unconfirmed)
        self.assertFalse(driver._progression_deferred)

    def test_queued_text_selector_refuses_other_unlocked_milestone(self):
        other_button = object()
        snapshot = {
            "valid_page": True, "claimable_count": 1, "delivered_count": 0,
            "claimable_cards": [
                {"button": other_button, "button_text": "claim",
                 "card_signature": "Milestone 65 | x80",
                 "card_text": "Milestone 65\nx80"},
            ],
            "delivered_signatures": [],
        }
        claimed, driver, clicks, _history = self._run(
            snapshot, target_card_text_fragments=("62", "x20")
        )
        self.assertEqual(claimed, 0)
        self.assertEqual(clicks, [])
        self.assertTrue(driver._progression_deferred)
        self.assertFalse(driver._progression_claim_attempted)

    def test_queued_text_selector_refuses_ambiguous_matching_cards(self):
        first, second = object(), object()
        snapshot = {
            "valid_page": True, "claimable_count": 2, "delivered_count": 0,
            "claimable_cards": [
                {"button": first, "button_text": "claim",
                 "card_signature": "Milestone 62 | x20 | A",
                 "card_text": "Milestone 62\nx20\nA"},
                {"button": second, "button_text": "claim",
                 "card_signature": "Milestone 62 | x20 | B",
                 "card_text": "Milestone 62\nx20\nB"},
            ],
            "delivered_signatures": [],
        }
        claimed, driver, clicks, _history = self._run(
            snapshot, target_card_text_fragments=("62", "x20")
        )
        self.assertEqual(claimed, 0)
        self.assertEqual(clicks, [])
        self.assertTrue(driver._progression_deferred)

    def test_queued_text_selector_accepts_already_delivered_target_without_clicking_other_card(self):
        other_button = object()
        snapshot = {
            "valid_page": True, "claimable_count": 1, "delivered_count": 1,
            "claimable_cards": [
                {"button": other_button, "button_text": "claim",
                 "card_signature": "Milestone 65 | x80",
                 "card_text": "Milestone 65\nx80"},
            ],
            "delivered_signatures": ["Milestone 62 | x20"],
            "terminal_card_signatures": ["Milestone 62 | x20"],
        }
        claimed, driver, clicks, _history = self._run(
            snapshot, target_card_text_fragments=("62", "x20")
        )
        self.assertEqual(claimed, 0)
        self.assertEqual(clicks, [])
        self.assertFalse(driver._progression_deferred)
        self.assertTrue(driver._progression_checked)


    def test_unscoped_queued_inspection_refuses_multiple_claimable_cards(self):
        first, second = object(), object()
        snapshot = {
            "valid_page": True, "claimable_count": 2, "delivered_count": 0,
            "claimable_cards": [
                {"button": first, "button_text": "claim",
                 "card_signature": "Milestone 62 | x20",
                 "card_text": "Milestone 62\nx20"},
                {"button": second, "button_text": "claim",
                 "card_signature": "Milestone 65 | x80",
                 "card_text": "Milestone 65\nx80"},
            ],
        }
        claimed, driver, clicks, _history = self._run(
            snapshot,
            require_unique_claimable_card=True,
            defer_if_more_claimable=True,
            max_claims_per_visit=1,
        )
        self.assertEqual(claimed, 0)
        self.assertEqual(clicks, [])
        self.assertTrue(driver._progression_deferred)
        self.assertFalse(driver._progression_claim_attempted)
        self.assertFalse(driver._progression_checked)

    def test_unscoped_queued_inspection_stays_open_when_another_card_remains(self):
        first_button, second_button = object(), object()
        before = {
            "valid_page": True, "claimable_count": 1, "delivered_count": 0,
            "button": first_button, "button_text": "claim",
            "card_signature": "First reward", "card_text": "First reward",
            "claimable_cards": [
                {"button": first_button, "button_text": "claim",
                 "card_signature": "First reward", "card_text": "First reward"},
            ],
            "delivered_signatures": [],
        }
        after = {
            "valid_page": True, "claimable_count": 1, "delivered_count": 1,
            "button": second_button, "button_text": "claim",
            "card_signature": "Second reward", "card_text": "Second reward",
            "claimable_cards": [
                {"button": second_button, "button_text": "claim",
                 "card_signature": "Second reward", "card_text": "Second reward"},
            ],
            "delivered_signatures": ["First reward"],
        }
        claimed, driver, clicks, _history = self._run(
            [before, after, after],
            require_unique_claimable_card=True,
            defer_if_more_claimable=True,
            max_claims_per_visit=1,
        )
        self.assertEqual(claimed, 1)
        self.assertEqual(clicks, [first_button])
        self.assertTrue(driver._progression_deferred)
        self.assertTrue(driver._progression_claim_attempted)
        self.assertFalse(driver._progression_checked)
        self.assertIn("additional claimable cards remain", driver._progression_deferred_reason)

    def test_unscoped_queued_inspection_resolves_after_one_card_and_complete_scan(self):
        button = object()
        before = {
            "valid_page": True, "claimable_count": 1, "delivered_count": 0,
            "button": button, "button_text": "claim",
            "card_signature": "Only reward", "card_text": "Only reward",
            "claimable_cards": [
                {"button": button, "button_text": "claim",
                 "card_signature": "Only reward", "card_text": "Only reward"},
            ],
            "delivered_signatures": [],
        }
        after = {
            "valid_page": True, "claimable_count": 0, "delivered_count": 1,
            "delivered_signatures": ["Only reward"],
        }
        claimed, driver, clicks, _history = self._run(
            [before, after, after],
            require_unique_claimable_card=True,
            defer_if_more_claimable=True,
            max_claims_per_visit=1,
        )
        self.assertEqual(claimed, 1)
        self.assertEqual(clicks, [button])
        self.assertFalse(driver._progression_deferred)
        self.assertTrue(driver._progression_checked)

if __name__ == "__main__":
    unittest.main(verbosity=2)
