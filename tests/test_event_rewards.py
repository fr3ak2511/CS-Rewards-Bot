import unittest
from unittest.mock import patch

from reward_popup_guard import DIALOG_SELECTOR

from event_rewards import (
    EVENT_POPUP_CLOSE_SELECTOR,
    EVENT_POPUP_SELECTOR,
    _CLAIM_BUTTON_CHECK_SCRIPT,
    _POPUP_CLOSE_BUTTON_CHECK_SCRIPT,
    _POPUP_INSPECT_SCRIPT,
    _SNAPSHOT_SCRIPT,
    EventRewardsError,
    _dismiss_event_promotion_popup,
    _normalise_snapshot,
    _parse_balance,
    _parse_reward_names,
    _parse_threshold,
    _tier_status,
    _validate_claim_button,
    claim_event_program,
    event_claim_count,
    event_retry_required,
    get_active_event_programs,
    merge_claim_ledger,
)


class FakeElement:
    def __init__(self, text="", attrs=None, displayed=True, click_action=None,
                 enabled=True):
        self.text = text
        self.attrs = dict(attrs or {})
        self.displayed = displayed
        self.click_action = click_action
        self.enabled = enabled and not bool(self.attrs.get("disabled", False))
        self.disabled = not self.enabled
        self.clicked = False
        self.close_buttons = []
        self.buttons = []
        self.all_buttons = []
        self.id = f"fake-{id(self)}"

    def is_displayed(self):
        return self.displayed

    def is_enabled(self):
        return self.enabled

    def get_attribute(self, name):
        return self.attrs.get(name)

    def find_elements(self, by, selector):
        if by == "css selector" and selector == EVENT_POPUP_CLOSE_SELECTOR:
            return list(self.close_buttons)
        if by == "css selector" and selector == "button, [role=\"button\"]":
            return list(self.all_buttons or self.buttons)
        if by == "tag name" and selector == "button":
            return list(self.buttons)
        return []

    def click(self):
        self.clicked = True
        if self.click_action:
            self.click_action()


class FakeCard(FakeElement):
    def __init__(self, threshold, button, reward_text="Abyss"):
        button_text = "Delivered" if getattr(button, "delivered", False) else "Claim"
        super().__init__(f"{threshold}\n{reward_text}\n{button_text}")
        self.threshold = threshold
        self.buttons = [button]

    def find_elements(self, by, selector):
        if by == "tag name" and selector == "button":
            return list(self.buttons)
        return super().find_elements(by, selector)


class FakeDriver:
    """Selenium fake for one or more stacked Vertigo promotional dialogs."""

    def __init__(self, popup=True, dismissible=True, click_delivers=True,
                 obstruct=False, close_topmost=True, close_control=True,
                 overlay_only=False, popup_on_scroll=False, missing_reward_names=False,
                 popup_layers=1):
        self.url = ""
        self.popup = popup
        self.dismissible = dismissible
        self.deliver_on_click = click_delivers
        self.obstruct = obstruct
        self.close_topmost = close_topmost
        self.close_control = close_control
        self.overlay_only = overlay_only
        self.popup_on_scroll = popup_on_scroll
        self.missing_reward_names = missing_reward_names
        self.scroll_popup_shown = False
        self.click_count = 0
        self.close_click_count = 0
        self.refresh_count = 0
        self.validation_threshold = 900
        self.dialogs = []
        self.dialog = None
        self.close_button = None
        self.purchase_button = None
        self.popup_layers = max(0, int(popup_layers)) if popup else 0
        self.button = FakeElement("Claim", {"type": "button"})
        self.button.click_action = self._on_claim
        self.card = FakeCard(900, self.button)
        self._build_dialogs()

    def _make_dialog(self, layer):
        close_attrs = {
            "type": "button", "data-testid": "close", "data-variant": "close",
            "data-color": "base", "aria-label": "Close dialog",
            "class": "group/button bg-sem-component-sf-component-button-close",
        }
        purchase_attrs = {
            "type": "button", "data-testid": "buy-button",
            "data-variant": "component-buy",
            "class": "group/button bg-sem-component-sf-component-buy",
        }
        dialog = FakeElement(
            f"Exclusive Discount Activated! Purchase or Lose it! Store Bonus +5 Layer {layer}",
            {"role": "dialog", "aria-modal": "true", "class": "pointer-events-auto modal"},
        )
        close = FakeElement("", close_attrs, click_action=lambda d=dialog: self._dismiss_dialog(d)) if self.close_control else None
        purchase = FakeElement("₹623.00\n₹890.00", purchase_attrs)
        dialog.close_buttons = [close] if close else []
        dialog.all_buttons = [x for x in (close, purchase) if x is not None]
        return dialog, close, purchase

    def _build_dialogs(self):
        self.dialogs = []
        self._layer_controls = []
        if not self.popup or self.popup_layers <= 0:
            self.dialog = self.close_button = self.purchase_button = None
            return
        for layer in range(self.popup_layers):
            dialog, close, purchase = self._make_dialog(layer + 1)
            self.dialogs.append(dialog)
            self._layer_controls.append((dialog, close, purchase))
        self.dialog = self.dialogs[-1]
        self.close_button = self._layer_controls[-1][1]
        self.purchase_button = self._layer_controls[-1][2]

    def _dismiss_dialog(self, dialog):
        self.close_click_count += 1
        if self.dismissible and dialog in self.dialogs:
            self.dialogs.remove(dialog)
            self.popup_layers = len(self.dialogs)
            if not self.dialogs:
                self.popup = False
                self.overlay_only = False
                self.dialog = self.close_button = self.purchase_button = None
            else:
                self.dialog = self.dialogs[-1]
                _pair = next(x for x in self._layer_controls if x[0] is self.dialog)
                self.close_button, self.purchase_button = _pair[1], _pair[2]

    def _dismiss(self):
        if self.dialog is not None:
            self._dismiss_dialog(self.dialog)

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
        if by == "css selector" and selector in (EVENT_POPUP_SELECTOR, DIALOG_SELECTOR):
            return list(self.dialogs)
        if by == "css selector" and selector == "div.relative.rounded-lp-card":
            return [self.card]
        if by == "tag name" and selector == "button":
            return [self.button]
        return []

    def execute_script(self, script, *args):
        if "REWARD_POPUP_LAYER_INSPECT_V1" in script:
            return {
                "dialogCount": len(self.dialogs),
                "dialogSummaries": [{"text": d.text} for d in self.dialogs],
                "fullscreenOverlayCount": 1 if (self.dialogs or self.overlay_only) else 0,
                "fullscreenOverlayClasses": ["fixed inset-0 z-900 bg-sem-bg-bg-overlay"] if (self.dialogs or self.overlay_only) else [],
                "url": self.url,
        
        
        if "REWARD_POPUP_CLOSE_CONTROL_CHECK_V2" in script:
            button, dialog = args
            pair = next(
                (item for item in self._layer_controls if item[0] is dialog),
                None,
            )
            close = pair[1] if pair else None

            is_top = bool(self.dialogs and dialog is self.dialogs[-1])
            is_close = bool(
                close is not None
                and button is close
                and self.close_control
            )

            text = (button.text or "").strip().lower()
            aria = (button.get_attribute("aria-label") or "").strip().lower()
            title = (button.get_attribute("title") or "").strip().lower()
            testid = (button.get_attribute("data-testid") or "").strip().lower()
            variant = (button.get_attribute("data-variant") or "").strip().lower()
            cls = (button.get_attribute("class") or "").lower()
            button_type = (button.get_attribute("type") or "button").lower()

            disabled = bool(
                button.disabled
                or button.get_attribute("aria-disabled") == "true"
            )

            close_evidence = (
                testid == "close"
                or variant == "close"
                or aria in {"close", "close dialog", "dismiss"}
                or title in {"close", "dismiss"}
                or "button-close" in cls
                or "modal-close" in cls
                or "dialog-close" in cls
                or text in {"x", "×", "close", "dismiss"}
            )

            action_text = " ".join(
                (text, aria, title, testid, variant)
            )

            dangerous = any(
                word in action_text.split()
                for word in {
                    "buy", "purchase", "claim", "confirm",
                    "accept", "redeem", "subscribe", "pay",
                    "submit", "yes", "continue"
                }
            )

            topmost = bool(is_top and is_close and self.close_topmost)
            dialog_visible = dialog.is_displayed()
            visible = button.is_displayed()

            ok = bool(
                visible
                and dialog_visible
                and not disabled
                and button_type == "button"
                and is_close
                and close_evidence
                and not dangerous
                and topmost
            )

            return {
                "ok": ok,
                "reason": "validated" if ok else "not_safe",
                "text": text,
                "aria": aria,
                "title": title,
                "testid": testid,
                "variant": variant,
                "className": cls,
                "topmost": topmost,
                "zIndex": 900 + len(self.dialogs),
                "dialogVisible": dialog_visible,
                "safeRewardContinue": False,
            }


        
        if "REWARD_POPUP_CLOSE_CONTROL_CHECK_V1" in script:
            button, dialog = args
            is_top = bool(self.dialogs and dialog is self.dialogs[-1])
            is_close = any(button is pair[1] and dialog is pair[0] for pair in self._layer_controls)
            ok = bool(is_close and is_top and self.close_topmost and self.close_control
                      and not button.disabled and not self.purchase_button.clicked)
            return {"ok": ok, "reason": "validated" if ok else "close_button_not_topmost_or_not_safe",
                    "zIndex": 900 + len(self.dialogs), "topmost": ok}

        if "EVENT_POPUP_INSPECT_V2" in script:
            dialog_visible = bool(self.dialogs)
            return {
                "visibleDialogCount": len(self.dialogs),
                "dialogDescriptions": [{"text": d.text} for d in self.dialogs],
                "promotionalDialogVisible": dialog_visible,
                "promotionalDialogText": self.dialog.text if self.dialog else "",
                "fullScreenBlockingOverlayCount": 1 if (dialog_visible or self.overlay_only) else 0,
                "fullScreenBlockingOverlayClasses": ["fixed inset-0 z-900 bg-sem-bg-bg-overlay"] if (dialog_visible or self.overlay_only) else [],
                "url": self.url,
            }

        if "EVENT_POPUP_CLOSE_BUTTON_CHECK_V1" in script:
            button = args[0]
            is_close = button is self.close_button
            return {"ok": bool(is_close and self.close_topmost and self.close_control),
                    "visible": bool(is_close), "disabled": False, "isPurchase": not is_close,
                    "hasCloseEvidence": is_close, "type": "button", "topmost": bool(is_close and self.close_topmost),
                    "testid": button.get_attribute("data-testid"), "variant": button.get_attribute("data-variant"),
                    "aria": button.get_attribute("aria-label")}

        if "EVENT_CLAIM_BUTTON_CHECK_V2" in script:
            return {"ok": not self.popup and not self.overlay_only and not self.obstruct,
                    "visible": True, "disabled": False, "buttonText": "claim",
                    "threshold": self.validation_threshold,
                    "topmost": not self.popup and not self.overlay_only and not self.obstruct,
                    "blockingDialogCount": len(self.dialogs)}

        if "EVENT_REWARDS_SNAPSHOT_V2" in script:
            delivered = self.delivered
            available_reward_items = [] if self.missing_reward_names or delivered else [{"name": "Abyss", "quantity": ""}]
            return {"title": "Halloween 2026", "url": self.url,
                    "bodyText": "Lost Soul Rewards\n1,200 / 1,300",
                    "cards": [
                        {"thresholdText": "400", "rewardItems": [], "claimButtonEnabled": False,
                         "buttonIndex": 0, "buttonText": "Delivered", "disabled": True, "visible": True,
                         "cardText": "400\nx10\nOrb of Calamity\nDelivered"},
                        {"thresholdText": "900", "rewardItems": available_reward_items,
                         "claimButtonEnabled": not delivered, "buttonIndex": 1,
                         "buttonText": "Delivered" if delivered else "Claim", "disabled": delivered,
                         "visible": True, "cardText": "900\nAbyss\n" + ("Delivered" if delivered else "Claim")},
                        {"thresholdText": "1300", "rewardItems": [], "claimButtonEnabled": False,
                         "buttonIndex": 2, "buttonText": "", "disabled": True, "visible": True,
                         "cardText": "1300\nx150\nLuckyloon"}],}

        if "window.scrollTo(0, document.body.scrollHeight)" in script and self.popup_on_scroll and not self.scroll_popup_shown:
            self.popup = True
            self.popup_layers = 1
            self._build_dialogs()
            self.scroll_popup_shown = True
            return None
        return None


class ParsingTests(unittest.TestCase):
    def test_parse_balance_and_missing_balance(self):
        self.assertEqual(_parse_balance("Lost Soul Rewards\n1,000 / 1,300\n"), (1000, 1300))
        self.assertEqual(_parse_balance("No balance here"), (None, None))

    def test_threshold_and_reward_names_support_multiple_items_and_quantities(self):
        card = "400\nx10\nOrb of Calamity\nx10\nAether Extract\nDelivered"
        self.assertEqual(_parse_threshold(card), 400)
        self.assertEqual(_parse_reward_names(card), ["Orb of Calamity ×10", "Aether Extract ×10"])
        self.assertEqual(_parse_reward_names("5000\nx200K\nCash"), ["Cash ×200K"])
        self.assertEqual(_parse_threshold("5,200\nx20"), 5200)

    def test_status_requires_live_enabled_claim_and_sufficient_balance(self):
        self.assertEqual(_tier_status({"buttonText": "Delivered", "disabled": True, "cardText": "400\nDelivered"}, 1000), "delivered")
        self.assertEqual(_tier_status({"buttonText": "Claim", "disabled": False, "cardText": "1300\nLuckyloon"}, 1000), "inconsistent_balance")
        self.assertEqual(_tier_status({"buttonText": "Claim", "disabled": False, "cardText": "900\nAbyss"}, 1000), "available")
        self.assertEqual(_tier_status({"buttonText": "", "disabled": True, "cardText": "2000\nSuper Chest"}, 1000), "locked")

    def test_normalise_snapshot_preserves_multiple_tiers_and_next_threshold(self):
        snapshot = _normalise_snapshot({
            "title": "Halloween 2026",
            "url": "https://hub.vertigogames.co/halloween-2026",
            "bodyText": "Lost Soul Rewards\n1,000 / 1,300",
            "cards": [
                {"buttonIndex": 3, "buttonText": "Delivered", "disabled": True,
                 "cardText": "400\nx10\nOrb of Calamity\nx10\nAether Extract\nDelivered"},
                {"buttonIndex": 4, "buttonText": "", "disabled": True,
                 "cardText": "1300\nx150\nLuckyloon"},
            ],
        })
        self.assertEqual(snapshot["balance"], 1000)
        self.assertEqual(snapshot["next_threshold"], 1300)
        self.assertEqual([tier["status"] for tier in snapshot["tiers"]], ["delivered", "locked"])

    def test_duplicate_threshold_is_rejected(self):
        with self.assertRaises(EventRewardsError):
            _normalise_snapshot({
                "bodyText": "Lost Soul Rewards\n1,000 / 1,300",
                "cards": [
                    {"buttonIndex": 0, "buttonText": "Claim", "disabled": False, "visible": True, "cardText": "400\nOrb\nClaim"},
                    {"buttonIndex": 1, "buttonText": "Claim", "disabled": False, "visible": True, "cardText": "400\nOther\nClaim"},
                ],
            })

    def test_halloween_snapshot_reads_explicit_thresholds_and_reward_labels(self):
        self.assertIn("div.relative.rounded-lp-card", _SNAPSHOT_SCRIPT)
        self.assertIn("const thresholdMatch = firstLine.match", _SNAPSHOT_SCRIPT)
        self.assertIn("(?:x|\\s|$)", _SNAPSHOT_SCRIPT)
        claim_check = _SNAPSHOT_SCRIPT.index("const claimButtonEnabled")
        reward_read = _SNAPSHOT_SCRIPT.index("const rows = Array.from(card.querySelectorAll")
        self.assertLess(claim_check, reward_read)
        self.assertIn("if (claimButtonEnabled)", _SNAPSHOT_SCRIPT)
        self.assertIn('span[class*="line-clamp-1"]', _SNAPSHOT_SCRIPT)
        self.assertIn("text-store-card-sku-img-quantity", _SNAPSHOT_SCRIPT)

        snapshot = _normalise_snapshot({
            "title": "Halloween 2026",
            "url": "https://hub.vertigogames.co/halloween-2026",
            "bodyText": "Lost Soul Rewards\n10,000 / 11,000",
            "cards": [
                {"thresholdText": "5,200", "rewardItems": [{"name": "Viktor Character Card", "quantity": "x20"}],
                 "claimButtonEnabled": True, "buttonIndex": 11, "buttonText": "Claim", "disabled": False,
                 "visible": True, "cardText": "5,200\nx20\nViktor Character Card\nClaim"},
                {"thresholdText": "10,200", "rewardItems": [], "claimButtonEnabled": False,
                 "buttonIndex": 12, "buttonText": "", "disabled": True,
                 "visible": True, "cardText": "10,200"},
            ],
        })
        self.assertEqual(snapshot["tiers"][0]["threshold"], 5200)
        self.assertEqual(snapshot["tiers"][0]["reward"], ["Viktor Character Card ×20"])
        self.assertEqual(snapshot["tiers"][0]["status"], "available")
        self.assertEqual(snapshot["tiers"][1]["threshold"], 10200)
        self.assertEqual(snapshot["tiers"][1]["reward"], [])
        self.assertEqual(snapshot["tiers"][1]["status"], "locked")

    def test_halloween_available_cards_extract_reward_names_best_effort(self):
        snapshot = _normalise_snapshot({
            "title": "Halloween 2026",
            "url": "https://hub.vertigogames.co/halloween-2026",
            "bodyText": "Lost Soul Rewards\n1,100 / 1,300",
            "cards": [
                {"thresholdText": "400", "rewardItems": [
                    {"name": "Orb of Calamity", "quantity": "x10"},
                    {"name": "Aether Extract", "quantity": "x10"}],
                 "claimButtonEnabled": True, "buttonIndex": 2, "buttonText": "Claim",
                 "disabled": False, "visible": True,
                 "cardText": "400\nx10\nOrb of Calamity\nx10\nAether Extract\nClaim"},
                {"thresholdText": "900", "rewardItems": [{"name": "Abyss", "quantity": ""}],
                 "claimButtonEnabled": True, "buttonIndex": 3, "buttonText": "Claim",
                 "disabled": False, "visible": True, "cardText": "900\nAbyss\nClaim"},
                {"thresholdText": "1,300", "rewardItems": [], "claimButtonEnabled": False,
                 "buttonIndex": 4, "buttonText": "", "disabled": True,
                 "visible": True, "cardText": "1,300\nx150\nLuckyloon"},
            ],
        })
        tiers = snapshot["tiers"]
        self.assertEqual([tier["threshold"] for tier in tiers], [400, 900, 1300])
        self.assertEqual(tiers[0]["reward"], ["Orb of Calamity ×10", "Aether Extract ×10"])
        self.assertEqual(tiers[1]["reward"], ["Abyss"])
        self.assertEqual([tier["status"] for tier in tiers], ["available", "available", "locked"])

    def test_snapshot_contains_current_safety_markers(self):
        self.assertIn("EVENT_REWARDS_SNAPSHOT_V2", _SNAPSHOT_SCRIPT)
        self.assertIn("EVENT_CLAIM_BUTTON_CHECK_V2", _CLAIM_BUTTON_CHECK_SCRIPT)


class ExistingContractTests(unittest.TestCase):
    def test_active_program_config_and_expiry(self):
        config = {"enabled": True, "programs": [{
            "enabled": True, "name": "Test", "slug": "test", "program_id": "pgp_test",
            "end_at": 2000, "max_claims_per_run": 100,
        }]}
        active = get_active_event_programs(config, now_epoch=1999)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["max_claims_per_run"], 50)
        self.assertEqual(get_active_event_programs(config, now_epoch=2000), [])
        not_started = {"enabled": True, "programs": [{
            "enabled": True, "name": "Later", "slug": "later", "program_id": "pgp_later",
            "start_at": 3000, "end_at": 4000,
        }]}
        self.assertEqual(get_active_event_programs(not_started, now_epoch=2999), [])

    def test_event_aggregates(self):
        events = [
            {"claims_this_run": [{"threshold": 400}, {"threshold": 900}], "retry_required": False},
            {"claims_this_run": [], "retry_required": True},
        ]
        self.assertEqual(event_claim_count(events), 2)
        self.assertTrue(event_retry_required(events))

    def test_claim_ledger_preserves_history_and_is_idempotent(self):
        original = [{"cycle": 0, "threshold": 400, "reward": ["Orb"], "claimed_at": "old"}]
        new_claims = [{"threshold": 900, "reward": ["Abyss"]}]
        first = merge_claim_ledger(original, new_claims, cycle=0, claimed_at="now")
        second = merge_claim_ledger(first, new_claims, cycle=0, claimed_at="later")
        self.assertEqual(len(second), 2)
        self.assertEqual(second[0]["claimed_at"], "old")
        self.assertEqual(second[1]["claimed_at"], "now")


class PopupSafetyTests(unittest.TestCase):
    @patch("event_rewards.time.sleep", return_value=None)
    def test_actual_portal_close_button_is_used_and_purchase_button_is_never_clicked(self, _sleep):
        driver = FakeDriver(popup=True, dismissible=True)
        close = driver.close_button
        purchase = driver.purchase_button
        self.assertIn('button[data-testid="close"]', EVENT_POPUP_CLOSE_SELECTOR)
        self.assertIn('button[data-variant="close"]', EVENT_POPUP_CLOSE_SELECTOR)
        ok, reason = _dismiss_event_promotion_popup(driver, lambda _m: None, timeout=0.2)
        self.assertTrue(ok, reason)
        self.assertTrue(close.clicked)
        self.assertFalse(purchase.clicked)

    @patch("event_rewards.time.sleep", return_value=None)
    def test_popup_that_does_not_close_is_reported_as_blocking(self, _sleep):
        driver = FakeDriver(popup=True, dismissible=False)
        ok, reason = _dismiss_event_promotion_popup(driver, lambda _m: None, timeout=0.2)
        self.assertFalse(ok)
        self.assertIn("remained_after", reason)
        self.assertFalse(driver.purchase_button.clicked)

    @patch("event_rewards.time.sleep", return_value=None)
    def test_ambiguous_or_missing_close_control_never_uses_purchase_button(self, _sleep):
        driver = FakeDriver(popup=True, close_control=False)
        ok, reason = _dismiss_event_promotion_popup(driver, lambda _m: None, timeout=0.2)
        self.assertFalse(ok)
        self.assertIn("no_unambiguous_safe_close", reason)
        self.assertFalse(driver.purchase_button.clicked)

    @patch("event_rewards.time.sleep", return_value=None)
    def test_close_control_must_be_topmost_before_native_click(self, _sleep):
        driver = FakeDriver(popup=True, close_topmost=False)
        ok, reason = _dismiss_event_promotion_popup(driver, lambda _m: None, timeout=0.2)
        self.assertFalse(ok)
        self.assertIn("no_unambiguous_safe_close", reason)
        self.assertFalse(driver.close_button.clicked)
        self.assertFalse(driver.purchase_button.clicked)

    def test_fullscreen_overlay_without_dialog_is_still_blocking(self):
        driver = FakeDriver(popup=False, overlay_only=True)
        ok, reason = _dismiss_event_promotion_popup(driver, lambda _m: None)
        self.assertFalse(ok)
        self.assertIn("unknown_fullscreen_overlay_blocks_page", reason)

    @patch("event_rewards.time.sleep", return_value=None)
    def test_multiple_stacked_promotional_popups_are_all_dismissed_before_claim(self, _sleep):
        driver = FakeDriver(popup=True, dismissible=True, popup_layers=2)
        purchase = driver.purchase_button
        ok, reason = _dismiss_event_promotion_popup(driver, lambda _message: None, timeout=0.3)
        self.assertTrue(ok, reason)
        self.assertEqual(driver.close_click_count, 2)
        self.assertEqual(len(driver.dialogs), 0)
        self.assertFalse(purchase.clicked)

    def test_popup_detection_includes_the_captured_role_and_modal_markers(self):
        self.assertIn("[role=\"dialog\"]", _POPUP_INSPECT_SCRIPT)
        self.assertIn("[aria-modal=\"true\"]", _POPUP_INSPECT_SCRIPT)
        self.assertIn("[class*=\"overlay\"]", _POPUP_INSPECT_SCRIPT)
        self.assertIn("EVENT_POPUP_CLOSE_BUTTON_CHECK_V1", _POPUP_CLOSE_BUTTON_CHECK_SCRIPT)


class ClaimExecutionTests(unittest.TestCase):
    def _program(self, expected_tier_count=3):
        return {"program_id": "pgp_test", "name": "Lost Soul Rewards", "slug": "halloween-2026",
                "end_at_epoch": 9999999999, "max_claims_per_run": 20,
                "expected_tier_count": expected_tier_count}

    @patch("event_rewards.time.sleep", return_value=None)
    def test_popup_is_closed_then_exact_tier_is_native_clicked_and_verified(self, _sleep):
        driver = FakeDriver(popup=True, dismissible=True, click_delivers=True)
        logs = []
        result = claim_event_program(
            driver, self._program(), physical_click=lambda *_: self.fail("unsafe callback must not be used"),
            bypass_cloudflare=lambda _d: None,
            close_popup=lambda *_: self.fail("generic popup callback must not be used"),
            log=logs.append,
        )
        self.assertEqual(driver.click_count, 1)
        self.assertEqual(result["tiers_delivered"], 2)
        self.assertEqual(result["claims_this_run"][0]["threshold"], 900)
        self.assertFalse(result["retry_required"])
        self.assertIn("dismissed and verified", "\n".join(logs))

    @patch("event_rewards.time.sleep", return_value=None)
    def test_promotion_appearing_after_lazy_scroll_is_closed_before_claim(self, _sleep):
        driver = FakeDriver(popup=False, popup_on_scroll=True, dismissible=True)
        result = claim_event_program(
            driver, self._program(), physical_click=lambda *_: self.fail("unsafe callback must not be used"),
            bypass_cloudflare=lambda _d: None, close_popup=lambda _d: None, log=lambda _m: None,
        )
        self.assertEqual(driver.click_count, 1)
        self.assertEqual(result["claims_this_run"][0]["threshold"], 900)
        self.assertFalse(result["retry_required"])

    @patch("event_rewards.time.sleep", return_value=None)
    def test_undismissed_popup_prevents_any_claim(self, _sleep):
        driver = FakeDriver(popup=True, dismissible=False)
        result = claim_event_program(
            driver, self._program(), physical_click=lambda *_: self.fail("must not click"),
            bypass_cloudflare=lambda _d: None, close_popup=lambda *_: None, log=lambda _m: None,
        )
        self.assertEqual(driver.click_count, 0)
        self.assertTrue(result["retry_required"])
        self.assertIn("popup_blocking", result["error"])

    @patch("event_rewards.time.sleep", return_value=None)
    def test_obstructed_target_refuses_claim(self, _sleep):
        driver = FakeDriver(popup=False, obstruct=True)
        result = claim_event_program(
            driver, self._program(), physical_click=lambda *_: self.fail("must not click"),
            bypass_cloudflare=lambda _d: None, close_popup=lambda _d: None, log=lambda _m: None,
        )
        self.assertEqual(driver.click_count, 0)
        self.assertTrue(result["retry_required"])
        self.assertIn("button validation failed", result["error"])

    @patch("event_rewards.time.sleep", return_value=None)
    def test_no_second_click_after_unconfirmed_native_click(self, _sleep):
        driver = FakeDriver(popup=False, click_delivers=False)
        result = claim_event_program(
            driver, self._program(), physical_click=lambda *_: self.fail("unsafe callback must not be used"),
            bypass_cloudflare=lambda _d: None, close_popup=lambda _d: None, log=lambda _m: None,
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
            bypass_cloudflare=lambda _d: None, close_popup=lambda _d: None, log=lambda _m: None,
        )
        self.assertEqual(driver.click_count, 1)
        self.assertTrue(result["retry_required"])
        self.assertIn("click failed", result["error"])

    @patch("event_rewards.time.sleep", return_value=None)
    def test_expected_tier_count_mismatch_fails_before_click(self, _sleep):
        driver = FakeDriver(popup=False)
        with self.assertRaises(EventRewardsError):
            claim_event_program(
                driver, self._program(expected_tier_count=14),
                physical_click=lambda *_: self.fail("must not click"),
                bypass_cloudflare=lambda _d: None, close_popup=lambda _d: None, log=lambda _m: None,
            )
        self.assertEqual(driver.click_count, 0)

    @patch("event_rewards.time.sleep", return_value=None)
    def test_available_claim_without_reward_name_is_allowed_and_verified(self, _sleep):
        driver = FakeDriver(popup=False, missing_reward_names=True)
        result = claim_event_program(
            driver, self._program(), physical_click=lambda *_: self.fail("unsafe callback must not be used"),
            bypass_cloudflare=lambda _d: None, close_popup=lambda _d: None, log=lambda _m: None,
        )
        self.assertEqual(driver.click_count, 1)
        self.assertEqual(result["tiers_delivered"], 2)
        self.assertEqual(result["claims_this_run"][0]["reward"], [])
        self.assertFalse(result["retry_required"])

    @patch("event_rewards.time.sleep", return_value=None)
    def test_claims_enabled_tier_and_verifies_delivered(self, _sleep):
        driver = FakeDriver(popup=False, click_delivers=True)
        result = claim_event_program(
            driver, self._program(), physical_click=lambda *_: self.fail("unsafe callback must not be used"),
            bypass_cloudflare=lambda _d: None, close_popup=lambda _d: None, log=lambda _m: None,
        )
        self.assertEqual(driver.click_count, 1)
        self.assertEqual(result["balance"], 1200)
        self.assertEqual(result["tiers_delivered"], 2)
        self.assertEqual(result["claims_this_run"][0]["threshold"], 900)
        self.assertFalse(result["retry_required"])

    @patch("event_rewards.time.sleep", return_value=None)
    def test_mismatched_button_is_not_clicked(self, _sleep):
        driver = FakeDriver(popup=False)
        driver.validation_threshold = 1300
        result = claim_event_program(
            driver, self._program(), physical_click=lambda *_: self.fail("must not click"),
            bypass_cloudflare=lambda _d: None, close_popup=lambda _d: None, log=lambda _m: None,
        )
        self.assertEqual(driver.click_count, 0)
        self.assertTrue(result["retry_required"])
        self.assertIn("button validation failed", result["error"])


class ClaimButtonGuardTests(unittest.TestCase):
    def test_button_must_match_expected_tier(self):
        driver = FakeDriver(popup=False)
        driver.validation_threshold = 1300
        valid, reason = _validate_claim_button(driver, driver.button, 900)
        self.assertFalse(valid)
        self.assertIn("tier_mismatch", reason)

    def test_button_must_be_an_enabled_claim_control(self):
        driver = FakeDriver(popup=False)
        valid, reason = _validate_claim_button(driver, driver.button, 900)
        self.assertTrue(valid)
        self.assertEqual(reason, "validated")

    def test_validator_parses_threshold_concatenated_with_quantity(self):
        driver = FakeDriver(popup=False)
        driver.validation_threshold = 5200
        valid, reason = _validate_claim_button(driver, driver.button, 5200)
        self.assertTrue(valid, reason)
        self.assertIn("const match = firstLine.match", _CLAIM_BUTTON_CHECK_SCRIPT)
        self.assertIn(r"(?=\s*(?:x|\s|$))", _CLAIM_BUTTON_CHECK_SCRIPT)
        self.assertNotIn("thresholdElement = card", _CLAIM_BUTTON_CHECK_SCRIPT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
