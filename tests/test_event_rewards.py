import unittest
from unittest.mock import patch

from event_rewards import (
    _SNAPSHOT_SCRIPT, _parse_balance, _parse_threshold, _parse_reward_names,
    _normalise_snapshot, _tier_status, _validate_claim_button,
    _dismiss_event_promotion_popup, claim_event_program,
    get_active_event_programs, merge_claim_ledger, event_claim_count,
    event_retry_required, EventRewardsError,
)


class FakeElement:
    def __init__(self, text="", attrs=None, displayed=True, click_action=None):
        self.text = text
        self.attrs = attrs or {}
        self.displayed = displayed
        self.click_action = click_action
        self.disabled = bool(self.attrs.get("disabled", False))
        self.clicked = False

    def is_displayed(self):
        return self.displayed

    def get_attribute(self, name):
        return self.attrs.get(name, "")

    def find_elements(self, by, selector):
        if by == "css selector" and (selector.startswith("button[data-testid=\"close\"]") or selector.startswith("button[aria-label")):
            return getattr(self, "close_buttons", [])
        if by == "tag name" and selector == "button":
            return getattr(self, "buttons", [])
        return []

    def click(self):
        self.clicked = True
        if self.click_action:
            self.click_action()


class FakeCard(FakeElement):
    def __init__(self, threshold, button):
        super().__init__(f"{threshold}\nAbyss\nClaim")
        self.threshold = threshold
        self.buttons = [button]


class FakeDriver:
    def __init__(self, popup=True, dismissible=True, click_delivers=True, obstruct=False):
        self.url = ""
        self.popup = popup
        self.dismissible = dismissible
        self.deliver_on_click = click_delivers
        self.obstruct = obstruct
        self.click_count = 0
        self.refresh_count = 0
        self.dialog = None
        self.button = FakeElement("Claim")
        self.button.click_action = self._on_claim
        self.card = FakeCard(900, self.button)
        self._build_dialog()

    def _build_dialog(self):
        if self.popup:
            close = FakeElement("", {
                "data-testid": "close", "aria-label": "Close dialog", "data-variant": "close"
            }, click_action=self._dismiss)
            self.dialog = FakeElement("Exclusive Discount Activated! Purchase or Lose it! Store Bonus +5 ₹623.00")
            self.dialog.close_buttons = [close]
            # A purchase button is deliberately NOT in the safe close selector result.
            self.dialog.all_buttons = [close, FakeElement("₹623.00", {"data-testid": "buy-button"})]
        else:
            self.dialog = None

    def _dismiss(self):
        if self.dismissible:
            self.popup = False
            self.dialog = None

    def _on_claim(self):
        self.click_count += 1
        if self.deliver_on_click:
            self.delivered = True

    @property
    def delivered(self):
        return getattr(self, "_delivered", False)

    @delivered.setter
    def delivered(self, value):
        self._delivered = value

    def get(self, url):
        self.url = url

    def refresh(self):
        self.refresh_count += 1

    def find_elements(self, by, selector):
        if by == "css selector" and selector == '[role="dialog"], [aria-modal="true"]':
            return [self.dialog] if self.dialog else []
        if by == "css selector" and selector == "div.relative.rounded-lp-card":
            return [self.card]
        if by == "tag name" and selector == "button":
            return [self.button]
        return []

    def execute_script(self, script, *args):
        if "EVENT_POPUP_INSPECT_V1" in script:
            visible = bool(self.popup)
            return {
                "visibleDialogCount": 1 if visible else 0,
                "dialogDescriptions": [{"text": "Exclusive Discount Activated!"}] if visible else [],
                "promotionalDialogVisible": visible,
                "promotionalDialogText": "Exclusive Discount Activated! Purchase or Lose it!" if visible else "",
                "url": self.url,
            }
        if "EVENT_CLAIM_BUTTON_CHECK_V2" in script:
            return {
                "ok": not self.popup and not self.obstruct,
                "visible": True, "disabled": False, "buttonText": "claim",
                "threshold": 900, "topmost": not self.popup and not self.obstruct,
                "blockingDialogCount": 1 if self.popup else 0,
            }
        if "EVENT_REWARDS_SNAPSHOT_V2" in script:
            delivered = self.delivered
            return {
                "title": "Halloween 2026", "url": self.url,
                "bodyText": "Lost Soul Rewards\n1,200 / 1,300",
                "cards": [
                    {"thresholdText": "400", "rewardItems": [], "claimButtonEnabled": False,
                     "buttonIndex": 0, "buttonText": "Delivered", "disabled": True,
                     "visible": True, "cardText": "400\nDelivered"},
                    {"thresholdText": "900", "rewardItems": [{"name": "Abyss", "quantity": ""}],
                     "claimButtonEnabled": not delivered, "buttonIndex": 1,
                     "buttonText": "Delivered" if delivered else "Claim", "disabled": delivered,
                     "visible": True, "cardText": "900\nAbyss\n" + ("Delivered" if delivered else "Claim")},
                    {"thresholdText": "1300", "rewardItems": [], "claimButtonEnabled": False,
                     "buttonIndex": 2, "buttonText": "", "disabled": True,
                     "visible": True, "cardText": "1300"},
                ],
            }
        return None


class ParsingTests(unittest.TestCase):
    def test_balance_threshold_and_reward_parser(self):
        self.assertEqual(_parse_balance("Lost Soul Rewards\n1,200 / 1,300"), (1200, 1300))
        self.assertEqual(_parse_threshold("5,200\nx20"), 5200)
        self.assertEqual(_parse_reward_names("5000\nx200K\nCash"), ["Cash ×200K"])

    def test_tier_status_requires_live_enabled_claim_and_sufficient_balance(self):
        self.assertEqual(_tier_status({"buttonText": "Delivered", "disabled": True, "cardText": "400\nDelivered"}, 1200), "delivered")
        self.assertEqual(_tier_status({"buttonText": "Claim", "disabled": False, "cardText": "900\nAbyss"}, 1200), "available")
        self.assertEqual(_tier_status({"buttonText": "Claim", "disabled": False, "cardText": "1300\nAbyss"}, 1200), "inconsistent_balance")

    def test_normalise_snapshot_and_duplicate_guard(self):
        raw = {"title": "Halloween 2026", "url": "https://hub.vertigogames.co/halloween-2026",
               "bodyText": "Lost Soul Rewards\n1,200 / 1,300",
               "cards": [{"thresholdText": "900", "buttonText": "Claim", "disabled": False,
                          "visible": True, "claimButtonEnabled": True, "buttonIndex": 3,
                          "rewardItems": [{"name": "Abyss", "quantity": ""}], "cardText": "900\nAbyss\nClaim"}]}
        state = _normalise_snapshot(raw)
        self.assertEqual(state["balance"], 1200)
        self.assertEqual(state["tiers"][0]["status"], "available")
        self.assertEqual(state["tiers"][0]["reward"], ["Abyss"])
        raw["cards"].append(dict(raw["cards"][0]))
        with self.assertRaises(EventRewardsError):
            _normalise_snapshot(raw)

    def test_snapshot_contains_new_safety_markers(self):
        self.assertIn("EVENT_REWARDS_SNAPSHOT_V2", _SNAPSHOT_SCRIPT)


class PopupSafetyTests(unittest.TestCase):
    @patch("event_rewards.time.sleep", return_value=None)
    def test_only_explicit_close_button_is_clicked_and_verified(self, _sleep):
        driver = FakeDriver(popup=True, dismissible=True)
        close = driver.dialog.close_buttons[0]
        purchase = driver.dialog.all_buttons[1]
        ok, reason = _dismiss_event_promotion_popup(driver, lambda _m: None, timeout=0.2)
        self.assertTrue(ok, reason)
        self.assertTrue(close.clicked)
        self.assertFalse(purchase.clicked)

    @patch("event_rewards.time.sleep", return_value=None)
    def test_popup_that_does_not_close_is_reported_as_blocking(self, _sleep):
        driver = FakeDriver(popup=True, dismissible=False)
        ok, reason = _dismiss_event_promotion_popup(driver, lambda _m: None, timeout=0.2)
        self.assertFalse(ok)
        self.assertEqual(reason, "popup_remained_after_close_click")


class ClaimExecutionTests(unittest.TestCase):
    def _program(self):
        return {"program_id": "pgp_test", "name": "Lost Soul Rewards", "slug": "halloween-2026",
                "end_at_epoch": 9999999999, "max_claims_per_run": 20, "expected_tier_count": 3}

    @patch("event_rewards.time.sleep", return_value=None)
    def test_popup_is_closed_then_exact_tier_is_native_clicked_and_verified(self, _sleep):
        driver = FakeDriver(popup=True, dismissible=True, click_delivers=True)
        logs = []
        result = claim_event_program(
            driver, self._program(), physical_click=lambda *_: self.fail("unsafe callback must not be used"),
            bypass_cloudflare=lambda _d: None, close_popup=lambda *_: self.fail("generic popup callback must not be used"),
            log=logs.append,
        )
        self.assertEqual(driver.click_count, 1)
        self.assertEqual(result["tiers_delivered"], 2)
        self.assertEqual(result["claims_this_run"][0]["threshold"], 900)
        self.assertFalse(result["retry_required"])
        self.assertIn("dismissed and verified", "\n".join(logs))

    @patch("event_rewards.time.sleep", return_value=None)
    def test_undismissed_popup_prevents_any_claim(self, _sleep):
        driver = FakeDriver(popup=True, dismissible=False)
        result = claim_event_program(
            driver, self._program(), physical_click=lambda *_: self.fail("must not click"),
            bypass_cloudflare=lambda _d: None, close_popup=lambda *_: None,
            log=lambda _m: None,
        )
        self.assertEqual(driver.click_count, 0)
        self.assertTrue(result["retry_required"])
        self.assertIn("popup_blocking", result["error"])

    @patch("event_rewards.time.sleep", return_value=None)
    def test_obstructed_target_refuses_claim(self, _sleep):
        driver = FakeDriver(popup=False, obstruct=True)
        result = claim_event_program(
            driver, self._program(), physical_click=lambda *_: self.fail("must not click"),
            bypass_cloudflare=lambda _d: None, close_popup=lambda _d: None,
            log=lambda _m: None,
        )
        self.assertEqual(driver.click_count, 0)
        self.assertTrue(result["retry_required"])
        self.assertIn("button validation failed", result["error"])

    @patch("event_rewards.time.sleep", return_value=None)
    def test_no_second_click_after_unconfirmed_native_click(self, _sleep):
        driver = FakeDriver(popup=False, click_delivers=False)
        result = claim_event_program(
            driver, self._program(), physical_click=lambda *_: self.fail("unsafe callback must not be used"),
            bypass_cloudflare=lambda _d: None, close_popup=lambda _d: None,
            log=lambda _m: None,
        )
        self.assertEqual(driver.click_count, 1)
        self.assertTrue(result["retry_required"])
        self.assertEqual(result["claims_this_run"], [])

    @patch("event_rewards.time.sleep", return_value=None)
    def test_native_click_exception_never_falls_back_to_javascript(self, _sleep):
        driver = FakeDriver(popup=False)
        def fail_click():
            driver.click_count += 1
            raise RuntimeError("intercepted")
        driver.button.click_action = fail_click
        result = claim_event_program(
            driver, self._program(), physical_click=lambda *_: self.fail("fallback must not run"),
            bypass_cloudflare=lambda _d: None, close_popup=lambda _d: None,
            log=lambda _m: None,
        )
        self.assertEqual(driver.click_count, 1)
        self.assertTrue(result["retry_required"])
        self.assertIn("click failed", result["error"])


class ExistingContractTests(unittest.TestCase):
    def test_active_program_config_and_expiry(self):
        config = {"enabled": True, "programs": [{"enabled": True, "name": "Test", "slug": "test",
                  "program_id": "pgp_test", "end_at": 2000, "max_claims_per_run": 100}]}
        self.assertEqual(get_active_event_programs(config, now_epoch=1999)[0]["max_claims_per_run"], 50)
        self.assertEqual(get_active_event_programs(config, now_epoch=2000), [])

    def test_claim_ledger_is_idempotent_and_aggregates_remain_compatible(self):
        old = [{"cycle": 0, "threshold": 400, "reward": ["Orb"], "claimed_at": "old"}]
        first = merge_claim_ledger(old, [{"threshold": 900, "reward": ["Abyss"]}], cycle=0, claimed_at="now")
        second = merge_claim_ledger(first, [{"threshold": 900, "reward": ["Abyss"]}], cycle=0, claimed_at="later")
        self.assertEqual(len(second), 2)
        events = [{"claims_this_run": [{"threshold": 900}], "retry_required": True}]
        self.assertEqual(event_claim_count(events), 1)
        self.assertTrue(event_retry_required(events))


if __name__ == "__main__":
    unittest.main(verbosity=2)
