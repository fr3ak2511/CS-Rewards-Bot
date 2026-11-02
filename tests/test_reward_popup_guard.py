import unittest

from reward_popup_guard import dismiss_all_safe_popups


class Element:
    _next = 0

    def __init__(self, text="", attrs=None, click_action=None):
        Element._next += 1
        self.id = f"el-{Element._next}"
        self.text = text
        self.attrs = dict(attrs or {})
        self.click_action = click_action
        self.clicked = False
        self.displayed = True
        self.disabled = False
        self.children = []

    def is_displayed(self):
        return self.displayed

    def get_attribute(self, name):
        return self.attrs.get(name)

    def find_elements(self, by, selector):
        if by == "css selector" and selector == "button, [role=\"button\"]":
            return list(self.children)
        return []

    def click(self):
        self.clicked = True
        if self.click_action:
            self.click_action()


class LayerDriver:
    def __init__(self, layers=1, closable=True, ambiguous=False, overlay_only=False):
        self.overlay_only = overlay_only
        self.dialogs = []
        self.purchase_buttons = []
        self.close_buttons = []
        self.close_count = 0
        self.ambiguous = ambiguous
        for i in range(layers):
            dialog = Element(
                f"Promotional offer layer {i+1}",
                {"role": "dialog", "aria-modal": "true", "class": "modal"},
            )
            close = None
            if closable:
                close = Element("", {
                    "type": "button", "aria-label": "Close dialog",
                    "data-testid": "close", "class": "button-close",
                }, click_action=lambda d=dialog: self._close(d))
            purchase = Element("Buy now", {
                "type": "button", "data-testid": "buy-button",
                "data-variant": "component-buy",
            })
            dialog.children = [x for x in (close, purchase) if x is not None]
            if ambiguous and close is not None:
                second = Element("×", {
                    "type": "button", "aria-label": "Close dialog",
                    "data-testid": "close", "class": "button-close",
                }, click_action=lambda d=dialog: self._close(d))
                dialog.children.append(second)
            self.dialogs.append(dialog)
            if close is not None:
                self.close_buttons.append(close)
            self.purchase_buttons.append(purchase)

    def _close(self, dialog):
        self.close_count += 1
        if dialog in self.dialogs:
            self.dialogs.remove(dialog)

    def find_elements(self, by, selector):
        if by == "css selector" and "[role=\"dialog\"]" in selector:
            return list(self.dialogs)
        return []

    def execute_script(self, script, *args):
        if "REWARD_POPUP_LAYER_INSPECT_V1" in script:
            return {
                "dialogCount": len(self.dialogs),
                "dialogSummaries": [{"text": d.text} for d in self.dialogs],
                "fullscreenOverlayCount": 1 if self.dialogs or self.overlay_only else 0,
                "fullscreenOverlayClasses": ["fixed overlay"] if self.dialogs or self.overlay_only else [],
                "url": "https://hub.vertigogames.co/progression-program",
            }
        if "REWARD_POPUP_CLOSE_CONTROL_CHECK_V1" in script:
            button, dialog = args
            is_top = bool(self.dialogs and dialog is self.dialogs[-1])
            is_close = button in dialog.children and button not in self.purchase_buttons
            ok = bool(is_top and is_close and button.get_attribute("aria-label") == "Close dialog")
            return {"ok": ok, "zIndex": 900 + len(self.dialogs), "reason": "validated" if ok else "not_safe"}
        raise AssertionError("Unexpected JavaScript execution in popup test")


class RewardPopupGuardTests(unittest.TestCase):
    def test_dismisses_multiple_stacked_dialogs_top_first(self):
        driver = LayerDriver(layers=3)
        log = []
        ok, reason = dismiss_all_safe_popups(driver, log=log.append, timeout=0.2)
        self.assertTrue(ok, reason)
        self.assertEqual(driver.close_count, 3)
        self.assertEqual(driver.dialogs, [])
        self.assertFalse(any(button.clicked for button in driver.purchase_buttons))
        self.assertTrue(any("dismissed and verified" in line.lower() for line in log))

    def test_unknown_dialog_fails_closed_without_clicking_any_control(self):
        driver = LayerDriver(layers=1, closable=False)
        ok, reason = dismiss_all_safe_popups(driver, log=lambda _m: None, timeout=0.2)
        self.assertFalse(ok)
        self.assertIn("no_unambiguous_safe_close", reason)
        self.assertEqual(driver.close_count, 0)
        self.assertFalse(any(button.clicked for button in driver.purchase_buttons))

    def test_fullscreen_overlay_without_dialog_fails_closed(self):
        driver = LayerDriver(layers=0, overlay_only=True)
        ok, reason = dismiss_all_safe_popups(driver, log=lambda _m: None, timeout=0.2)
        self.assertFalse(ok)
        self.assertIn("unknown_fullscreen_overlay", reason)

    def test_ambiguous_close_controls_are_not_guessed(self):
        driver = LayerDriver(layers=1, ambiguous=True)
        ok, reason = dismiss_all_safe_popups(driver, log=lambda _m: None, timeout=0.2)
        self.assertFalse(ok)
        self.assertIn("ambiguous_topmost_close_controls", reason)
        self.assertEqual(driver.close_count, 0)
        self.assertFalse(any(button.clicked for button in driver.purchase_buttons))


if __name__ == "__main__":
    unittest.main(verbosity=2)
