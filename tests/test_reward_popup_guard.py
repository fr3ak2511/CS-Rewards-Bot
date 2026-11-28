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
        if by == "css selector" and selector == 'button, [role="button"]':
            return list(self.children)
        return []

    def click(self):
        self.clicked = True
        if self.click_action:
            self.click_action()


class LayerDriver:
    def __init__(
        self, layers=1, closable=True, ambiguous=False, overlay_only=False,
        dialog_text=None, action_text="Buy now", extra_action=False,
        residual_success_inspections=0,
        transition_residue_inspections=0,
        close_validation_reason=None, promotional_overlay=False,
    ):
        self.overlay_only = overlay_only or promotional_overlay
        self.promotional_overlay = promotional_overlay
        self.promotion_button = None
        self.dialogs = []
        self.purchase_buttons = []
        self.close_buttons = []
        self.continue_buttons = []
        self.close_count = 0
        self.ambiguous = ambiguous
        self.residual_success_inspections = residual_success_inspections
        self.residual_dialog = None
        self.transition_residue_inspections = transition_residue_inspections
        self.transition_residue = None
        self.close_validation_reason = close_validation_reason
        if promotional_overlay:
            self.promotion_button = Element(
                "", {"type": "button", "aria-label": "Close dialog", "class": "promotion-close"},
                click_action=lambda: setattr(self, "overlay_only", False),
            )
        for i in range(layers):
            description = dialog_text if dialog_text is not None else f"Promotional offer layer {i+1}"
            dialog = Element(
                description,
                {"role": "dialog", "aria-modal": "true", "class": "modal"},
            )
            close = None
            if closable:
                close = Element("", {
                    "type": "button", "aria-label": "Close dialog",
                    "data-testid": "close", "class": "button-close",
                }, click_action=lambda d=dialog: self._close(d))
            action = Element(
                action_text,
                {"type": "button", "data-testid": "action"},
                click_action=(lambda d=dialog: self._close(d)) if action_text.strip().lower() == "continue" else None,
            )
            dialog.children = [x for x in (close, action) if x is not None]
            if action_text.strip().lower() == "continue":
                self.continue_buttons.append(action)
            else:
                self.purchase_buttons.append(action)
            if extra_action:
                other = Element("Claim", {"type": "button", "data-testid": "claim"})
                dialog.children.append(other)
                self.purchase_buttons.append(other)
            if ambiguous and close is not None:
                second = Element("×", {
                    "type": "button", "aria-label": "Close dialog",
                    "data-testid": "close", "class": "button-close",
                }, click_action=lambda d=dialog: self._close(d))
                dialog.children.append(second)
            self.dialogs.append(dialog)
            if close is not None:
                self.close_buttons.append(close)

    def _close(self, dialog):
        self.close_count += 1
        if dialog in self.dialogs:
            self.dialogs.remove(dialog)
        dialog.displayed = False
        if (
            dialog.text.strip().lower().startswith("your rewards!")
            and self.residual_success_inspections != 0
        ):
            # Simulate the portal's non-interactive wrapper remaining briefly
            # after the actual Continue control has already been clicked.
            residue = Element(
                dialog.text,
                {"role": "dialog", "aria-modal": "true", "class": "modal residue"},
            )
            residue.children = []
            self.residual_dialog = residue
            self.dialogs.append(residue)
        if self.transition_residue_inspections != 0:
            # Simulate a short-lived modal snapshot left behind by a portal
            # transition after the explicitly validated X has been clicked.
            residue = Element(
                "Exclusive Discount Activated! transition residue",
                {"role": "dialog", "aria-modal": "true", "class": "modal transition-residue"},
            )
            residue.children = []
            self.transition_residue = residue
            self.dialogs.append(residue)

    def find_elements(self, by, selector):
        if by == "css selector" and '[role="dialog"]' in selector:
            return list(self.dialogs)
        return []

    def execute_script(self, script, *args):
        if "REWARD_PROMOTIONAL_UPSELL_CLOSE_CANDIDATE_V1" in script:
            if self.promotional_overlay and self.promotion_button is not None:
                return {"ok": True, "button": self.promotion_button}
            return {"ok": False, "reason": "promotion_close_not_found", "count": 0}
        if "REWARD_POPUP_LAYER_INSPECT_V1" in script:
            if self.transition_residue is not None and self.transition_residue_inspections > 0:
                self.transition_residue_inspections -= 1
                if self.transition_residue_inspections == 0:
                    if self.transition_residue in self.dialogs:
                        self.dialogs.remove(self.transition_residue)
                    self.transition_residue.displayed = False
            if self.residual_dialog is not None and self.residual_success_inspections > 0:
                self.residual_success_inspections -= 1
                if self.residual_success_inspections == 0:
                    if self.residual_dialog in self.dialogs:
                        self.dialogs.remove(self.residual_dialog)
                    self.residual_dialog.displayed = False
            return {
                "dialogCount": sum(1 for d in self.dialogs if d.is_displayed()),
                "dialogSummaries": [{"text": d.text} for d in self.dialogs if d.is_displayed()],
                "fullscreenOverlayCount": 1 if self.overlay_only else 0,
                "fullscreenOverlayClasses": ["fixed overlay"] if self.overlay_only else [],
                "fullscreenOverlayDetails": ([{
                    "tag": "div", "className": "fixed inset-0 bg-modal-overlay",
                    "position": "fixed", "zIndex": "2000", "pointerEvents": "auto",
                    "rect": {"x": 0, "y": 0, "width": 1920, "height": 993},
                }] if self.overlay_only else []),
                "url": "https://hub.vertigogames.co/progression-program",
            }
        if "REWARD_POPUP_CLOSE_CONTROL_CHECK_V2" in script:
            button, dialog = args
            if self.close_validation_reason:
                return {
                    "ok": False, "reason": self.close_validation_reason,
                    "text": button.text.strip().lower(),
                    "aria": str(button.get_attribute("aria-label") or "").lower(),
                    "title": str(button.get_attribute("title") or "").lower(),
                    "testid": str(button.get_attribute("data-testid") or "").lower(),
                    "className": str(button.get_attribute("class") or "").lower(),
                    "topmost": False, "zIndex": 900 + len(self.dialogs),
                    "stackOrder": 0, "pointerEvents": "auto",
                }
            is_top = bool(self.dialogs and dialog is self.dialogs[-1])
            button_text = button.text.strip().lower()
            is_close = button in dialog.children and (
                button.get_attribute("aria-label") == "Close dialog"
                or button_text in {"x", "×", "close", "dismiss"}
            )
            non_close = [
                child for child in dialog.children
                if child.text.strip().lower() not in {"x", "×", "close", "dismiss"}
                and child.get_attribute("aria-label") != "Close dialog"
            ]
            is_reward_continue = (
                button_text == "continue"
                and dialog.text.strip().lower().startswith("your rewards!")
                and bool(__import__("re").search(r"\bx\s*\d+\b", dialog.text.lower()))
                and len(non_close) == 1
                and non_close[0] is button
            )
            ok = bool(is_top and (is_close or is_reward_continue))
            return {
                "ok": ok,
                "zIndex": 900 + len(self.dialogs),
                "reason": "validated" if ok else "not_safe",
                "safeRewardContinue": is_reward_continue,
            }
        if script == __import__("reward_popup_guard")._ASSOCIATED_WRAPPER_CLOSE_CANDIDATES_JS:
            return []
        if script == __import__("reward_popup_guard")._SAFE_BACKDROP_CLICK_POINT_JS:
            return {"ok": False, "reason": "no_verified_blank_backdrop"}
        if "return (arguments[0].innerText" in script:
            return args[0].text
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

    def test_waits_for_transient_modal_snapshot_after_safe_close(self):
        driver = LayerDriver(
            layers=1,
            closable=True,
            transition_residue_inspections=4,
        )
        log = []

        ok, reason = dismiss_all_safe_popups(
            driver, log=log.append, timeout=0.5, poll_interval=0.05
        )

        self.assertTrue(ok, reason)
        self.assertEqual(driver.close_count, 1)
        self.assertTrue(driver.close_buttons[0].clicked)
        self.assertEqual(driver.dialogs, [])
        self.assertFalse(any(button.clicked for button in driver.purchase_buttons))
        self.assertTrue(any("stale blocker cleared" in line.lower() for line in log))

    def test_persistent_modal_after_safe_close_still_fails_closed(self):
        driver = LayerDriver(
            layers=1,
            closable=True,
            transition_residue_inspections=-1,
        )

        ok, reason = dismiss_all_safe_popups(
            driver, log=lambda _m: None, timeout=0.2, poll_interval=0.05
        )

        self.assertFalse(ok)
        self.assertIn("no_unambiguous_safe_close", reason)
        self.assertEqual(driver.close_count, 1)
        self.assertTrue(driver.close_buttons[0].clicked)
        self.assertFalse(any(button.clicked for button in driver.purchase_buttons))

    def test_dismisses_exact_continue_in_reward_success_dialog(self):
        driver = LayerDriver(
            dialog_text="Your rewards! x20 Continue",
            action_text="Continue",
            closable=False,
        )
        ok, reason = dismiss_all_safe_popups(driver, log=lambda _m: None, timeout=0.2)
        self.assertTrue(ok, reason)
        self.assertEqual(len(driver.continue_buttons), 1)
        self.assertTrue(driver.continue_buttons[0].clicked)
        self.assertEqual(driver.dialogs, [])

    def test_waits_for_residual_reward_success_wrapper_without_second_click(self):
        driver = LayerDriver(
            dialog_text="Your rewards! x20 Continue",
            action_text="Continue",
            closable=False,
            residual_success_inspections=2,
        )
        log = []
        ok, reason = dismiss_all_safe_popups(
            driver, log=log.append, timeout=0.5, poll_interval=0.05
        )
        self.assertTrue(ok, reason)
        self.assertTrue(driver.continue_buttons[0].clicked)
        self.assertEqual(driver.close_count, 1)
        self.assertEqual(driver.dialogs, [])
        self.assertTrue(any("residue cleared" in line.lower() for line in log))

    def test_persistent_reward_success_residue_fails_closed(self):
        driver = LayerDriver(
            dialog_text="Your rewards! x150 Continue",
            action_text="Continue",
            closable=False,
            residual_success_inspections=-1,
        )
        ok, reason = dismiss_all_safe_popups(
            driver, log=lambda _m: None, timeout=0.2, poll_interval=0.05
        )
        self.assertFalse(ok)
        self.assertIn("reward_success_dialog_did_not_settle", reason)
        self.assertEqual(driver.close_count, 1)
        self.assertEqual(sum(button.clicked for button in driver.continue_buttons), 1)

    def test_generic_continue_button_is_not_allowed(self):
        driver = LayerDriver(
            dialog_text="Confirm your selection",
            action_text="Continue",
            closable=False,
        )
        ok, reason = dismiss_all_safe_popups(driver, log=lambda _m: None, timeout=0.2)
        self.assertFalse(ok)
        self.assertIn("no_unambiguous_safe_close", reason)
        self.assertFalse(driver.continue_buttons[0].clicked)

    def test_reward_continue_is_not_allowed_when_dialog_has_another_action(self):
        driver = LayerDriver(
            dialog_text="Your rewards! x20 Continue",
            action_text="Continue",
            closable=False,
            extra_action=True,
        )
        ok, reason = dismiss_all_safe_popups(driver, log=lambda _m: None, timeout=0.2)
        self.assertFalse(ok)
        self.assertIn("no_unambiguous_safe_close", reason)
        self.assertFalse(driver.continue_buttons[0].clicked)

    def test_unknown_dialog_fails_closed_without_clicking_any_control(self):
        driver = LayerDriver(layers=1, closable=False)
        ok, reason = dismiss_all_safe_popups(driver, log=lambda _m: None, timeout=0.2)
        self.assertFalse(ok)
        self.assertIn("no_unambiguous_safe_close", reason)
        self.assertEqual(driver.close_count, 0)
        self.assertFalse(any(button.clicked for button in driver.purchase_buttons))



    def test_dismisses_stacked_dialogs_with_explicit_close_buttons_in_fixed_wrappers(self):
        driver = LayerDriver(layers=2, closable=False)
        dialogs = list(driver.dialogs)
        wrappers = {dialog: Element("fixed popup wrapper") for dialog in dialogs}
        close_buttons = {
            dialog: Element("", {
                "type": "button",
                "aria-label": "Close dialog",
                "data-testid": "close",
                "class": "button-close",
            }, click_action=lambda d=dialog: driver._close(d))
            for dialog in dialogs
        }
        base_execute = driver.execute_script

        def execute_script(script, *args):
            guard = __import__("reward_popup_guard")
            if script == guard._ASSOCIATED_WRAPPER_CLOSE_CANDIDATES_JS:
                dialog = args[0]
                return [{
                    "button": close_buttons[dialog],
                    "wrapper": wrappers[dialog],
                }]
            if script == guard._WRAPPER_CLOSE_CONTROL_CHECK_JS:
                self.assertIs(args[0], close_buttons[args[1]])
                self.assertIs(args[2], wrappers[args[1]])
                return {
                    "ok": True, "zIndex": 2100,
                    "stackOrder": driver.dialogs.index(args[1]),
                    "reason": "validated",
                }
            return base_execute(script, *args)

        driver.execute_script = execute_script
        ok, reason = dismiss_all_safe_popups(driver, log=lambda _m: None, timeout=0.2)

        self.assertTrue(ok, reason)
        self.assertEqual(driver.close_count, 2)
        self.assertTrue(all(button.clicked for button in close_buttons.values()))
        self.assertEqual(driver.dialogs, [])
        self.assertFalse(any(button.clicked for button in driver.purchase_buttons))

    def test_uses_only_a_verified_backdrop_blank_point_when_no_safe_x_exists(self):
        from unittest.mock import patch
        import reward_popup_guard as guard

        driver = LayerDriver(layers=1, closable=False)
        dialog = driver.dialogs[0]
        backdrop = Element("verified dialog overlay", {
            "data-slot": "dialog-overlay",
            "class": "dialog-backdrop",
        })
        base_execute = driver.execute_script

        def execute_script(script, *args):
            if script == guard._SAFE_BACKDROP_CLICK_POINT_JS:
                self.assertEqual(args, ())
                return {
                    "ok": True, "backdrop": backdrop, "dialog": dialog,
                    "offsetX": 20, "offsetY": 20,
                    "x": 20, "y": 20, "marker": "dialog-overlay",
                }
            return base_execute(script, *args)

        driver.execute_script = execute_script
        actions = []

        class FakeActionChains:
            def __init__(self, target_driver):
                self.target_driver = target_driver
                self.point = None

            def move_to_element_with_offset(self, element, x, y):
                actions.append(("move", element, x, y))
                self.point = (element, x, y)
                return self

            def click(self):
                actions.append(("click",))
                return self

            def perform(self):
                actions.append(("perform",))
                self.target_driver._close(dialog)

        with patch.object(guard, "ActionChains", FakeActionChains):
            ok, reason = dismiss_all_safe_popups(
                driver, log=lambda _m: None, timeout=0.2
            )

        self.assertTrue(ok, reason)
        self.assertEqual(actions[0], ("move", backdrop, 20, 20))
        self.assertEqual(actions[1], ("click",))
        self.assertEqual(driver.dialogs, [])
        self.assertFalse(any(button.clicked for button in driver.purchase_buttons))



    def test_verified_backdrop_is_tried_when_safe_x_click_does_not_close_popup(self):
        from unittest.mock import patch
        import reward_popup_guard as guard

        driver = LayerDriver(layers=1, closable=True)
        dialog = driver.dialogs[0]
        close = driver.close_buttons[0]
        # The X click dispatches but this portal implementation leaves the
        # dialog open; only the separately verified backdrop dismisses it.
        close.click_action = lambda: None
        backdrop = Element("verified dialog overlay", {
            "data-slot": "dialog-overlay",
            "class": "dialog-backdrop",
        })
        base_execute = driver.execute_script

        def execute_script(script, *args):
            if script == guard._SAFE_BACKDROP_CLICK_POINT_JS:
                return {
                    "ok": True, "backdrop": backdrop, "dialog": dialog,
                    "offsetX": 20, "offsetY": 20, "marker": "dialog-overlay",
                }
            return base_execute(script, *args)

        driver.execute_script = execute_script
        actions = []

        class FakeActionChains:
            def __init__(self, target_driver):
                self.target_driver = target_driver

            def move_to_element_with_offset(self, element, x, y):
                actions.append(("move", element, x, y))
                return self

            def click(self):
                actions.append(("click",))
                return self

            def perform(self):
                actions.append(("perform",))
                self.target_driver._close(dialog)

        with patch.object(guard, "ActionChains", FakeActionChains):
            ok, reason = dismiss_all_safe_popups(
                driver, log=lambda _m: None, timeout=0.2
            )

        self.assertTrue(ok, reason)
        self.assertTrue(close.clicked)
        self.assertEqual(driver.close_count, 1)
        self.assertEqual(actions[0], ("move", backdrop, 20, 20))
        self.assertEqual(driver.dialogs, [])



    def test_ineffective_x_without_verified_backdrop_stays_fail_closed(self):
        driver = LayerDriver(layers=1, closable=True)
        driver.close_buttons[0].click_action = lambda: None

        ok, reason = dismiss_all_safe_popups(
            driver, log=lambda _m: None, timeout=0.2
        )

        self.assertFalse(ok)
        self.assertIn("dialog_remained_after_native_close_click", reason)
        self.assertIn("backdrop_fallback=no_verified_blank_backdrop", reason)
        self.assertEqual(driver.close_count, 0)
        self.assertFalse(any(button.clicked for button in driver.purchase_buttons))

    def test_does_not_click_generic_blank_page_area_when_no_backdrop_is_proven(self):
        driver = LayerDriver(layers=1, closable=False)
        ok, reason = dismiss_all_safe_popups(
            driver, log=lambda _m: None, timeout=0.2
        )
        self.assertFalse(ok)
        self.assertIn("no_verified_blank_backdrop", reason)
        self.assertEqual(driver.close_count, 0)
        self.assertFalse(any(button.clicked for button in driver.purchase_buttons))

    def test_dismisses_exact_known_promotional_upsell_without_dialog_root(self):
        driver = LayerDriver(layers=0, promotional_overlay=True)
        log = []

        ok, reason = dismiss_all_safe_popups(
            driver, log=log.append, timeout=0.2, poll_interval=0.05
        )

        self.assertTrue(ok, reason)
        self.assertTrue(driver.promotion_button.clicked)
        self.assertFalse(driver.overlay_only)
        self.assertIn("known promotional upsell", " ".join(log).lower())

    def test_unknown_fullscreen_overlay_does_not_use_promotion_close_handler(self):
        driver = LayerDriver(layers=0, overlay_only=True)

        ok, reason = dismiss_all_safe_popups(
            driver, log=lambda _m: None, timeout=0.2, poll_interval=0.05
        )

        self.assertFalse(ok)
        self.assertIn("promotion_close_not_found", reason)
        self.assertFalse(driver.close_count)

    def test_promotion_close_failure_keeps_fullscreen_overlay_blocked(self):
        driver = LayerDriver(layers=0, promotional_overlay=True)
        driver.promotion_button.click_action = lambda: None

        ok, reason = dismiss_all_safe_popups(
            driver, log=lambda _m: None, timeout=0.2, poll_interval=0.05
        )

        self.assertFalse(ok)
        self.assertIn("promotion_overlay_persisted_after_close", reason)
        self.assertTrue(driver.overlay_only)

    def test_fullscreen_overlay_without_dialog_fails_closed_with_blocker_geometry(self):
        driver = LayerDriver(layers=0, overlay_only=True)
        ok, reason = dismiss_all_safe_popups(driver, log=lambda _m: None, timeout=0.2)
        self.assertFalse(ok)
        self.assertIn("unknown_fullscreen_overlay", reason)
        self.assertIn("fullscreen_overlay_details=", reason)
        self.assertIn("1920", reason)

    def test_explicit_close_rejection_reason_is_logged_without_clicking(self):
        driver = LayerDriver(layers=1, close_validation_reason="close_button_not_topmost")
        ok, reason = dismiss_all_safe_popups(driver, log=lambda _m: None, timeout=0.2)
        self.assertFalse(ok)
        self.assertIn("close_rejections=", reason)
        self.assertIn("close_button_not_topmost", reason)
        self.assertEqual(driver.close_count, 0)
        self.assertFalse(any(button.clicked for button in driver.purchase_buttons))

    def test_ambiguous_close_controls_are_not_guessed(self):
        driver = LayerDriver(layers=1, ambiguous=True)
        ok, reason = dismiss_all_safe_popups(driver, log=lambda _m: None, timeout=0.2)
        self.assertFalse(ok)
        self.assertIn("ambiguous_topmost_close_controls", reason)
        self.assertEqual(driver.close_count, 0)
        self.assertFalse(any(button.clicked for button in driver.purchase_buttons))


    def test_promotion_only_guard_dismisses_verified_upsell_and_verifies_clearance(self):
        from reward_popup_guard import dismiss_known_promotional_upsell_only

        driver = LayerDriver(layers=0, promotional_overlay=True)
        ok, reason = dismiss_known_promotional_upsell_only(driver, timeout=0.2)

        self.assertTrue(ok, reason)
        self.assertEqual(reason, "known_promotion_closed_and_overlay_clear")
        self.assertTrue(driver.promotion_button.clicked)
        self.assertFalse(driver.overlay_only)
        self.assertEqual(driver.close_count, 0)

    def test_promotion_only_guard_does_not_touch_unknown_fullscreen_overlay(self):
        from reward_popup_guard import dismiss_known_promotional_upsell_only

        driver = LayerDriver(layers=0, overlay_only=True)
        ok, reason = dismiss_known_promotional_upsell_only(driver, timeout=0.2)

        self.assertFalse(ok)
        self.assertIn("promotion_close_not_found", reason)
        self.assertFalse(driver.close_count)

    def test_promotion_only_guard_does_not_dismiss_unrelated_dialog(self):
        from reward_popup_guard import dismiss_known_promotional_upsell_only

        driver = LayerDriver(
            dialog_text="Your rewards! x20 Continue",
            action_text="Continue",
            closable=False,
        )
        ok, reason = dismiss_known_promotional_upsell_only(driver, timeout=0.2)

        self.assertFalse(ok)
        self.assertEqual(reason, "promotion_handler_requires_no_dialog")
        self.assertFalse(driver.continue_buttons[0].clicked)
        self.assertEqual(driver.close_count, 0)

    def test_promotion_only_guard_reports_absent_overlay_without_clicking(self):
        from reward_popup_guard import dismiss_known_promotional_upsell_only

        driver = LayerDriver(layers=0)
        ok, reason = dismiss_known_promotional_upsell_only(driver, timeout=0.2)

        self.assertTrue(ok)
        self.assertEqual(reason, "no_fullscreen_promotion_overlay")
        self.assertEqual(driver.close_count, 0)


    def test_popup_context_includes_fullscreen_blocker_controls_and_redacts_secrets(self):
        from reward_popup_guard import _popup_context_diagnostics

        class ContextDriver:
            def execute_script(self, script):
                self.script = script
                return {
                    "url": "https://hub.vertigogames.co/progression-program",
                    "viewport": {"width": 1920, "height": 993},
                    "dialogRootCount": 0,
                    "dialogRoots": [],
                    "visibleOverlays": [],
                    "fullscreenBlockers": [{
                        "root": {
                            "tag": "div",
                            "className": "fixed inset-0 z-900 bg-sem-bg-bg-overlay/75",
                            "text": "Blocked account FC8E0BBD0BBC0587",
                            "position": "fixed",
                            "zIndex": "900",
                            "pointerEvents": "auto",
                            "rect": {"x": 0, "y": 0, "width": 1905, "height": 993},
                        },
                        "ancestors": [],
                        "directChildren": [{
                            "tag": "div", "className": "portal-layer", "text": "Loading"
                        }],
                        "descendantControls": [{
                            "tag": "button", "aria": "Close panel",
                            "text": "Close", "className": "close-button"
                        }],
                        "directChildCount": 1,
                        "descendantCount": 2,
                        "text": "Loading user@example.com token=abc123",
                    }],
                }

        driver = ContextDriver()
        result = _popup_context_diagnostics(driver)

        self.assertIn("REWARD_POPUP_CONTEXT_DIAGNOSTICS_V2", driver.script)
        self.assertIn('"fullscreenBlockers"', result)
        self.assertIn("bg-sem-bg-bg-overlay/75", result)
        self.assertIn("Close panel", result)
        self.assertIn("[id-redacted]", result)
        self.assertIn("[email-redacted]", result)
        self.assertIn("[credential-redacted]", result)
        self.assertNotIn("FC8E0BBD0BBC0587", result)
        self.assertNotIn("user@example.com", result)
        self.assertNotIn("abc123", result)

if __name__ == "__main__":
    unittest.main(verbosity=2)
