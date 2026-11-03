import unittest

import reward_popup_guard as guard


class FakeElement:
    def __init__(self, *, element_id="element-1", children=None):
        self.id = element_id
        self.children = children or []
        self.clicked = False

    def is_displayed(self):
        return True

    def find_elements(self, by, selector):
        return self.children

    def click(self):
        self.clicked = True


class FakeDriver:
    def __init__(self, dialog, button, controls):
        self.dialog = dialog
        self.button = button
        self.controls = controls
        self.diagnostic_calls = 0

    def find_elements(self, by, selector):
        return [self.dialog]

    def execute_script(self, script, *args):
        if script == guard._LAYER_INSPECT_JS:
            return {
                "dialogCount": 1,
                "dialogSummaries": [{
                    "text": "Exclusive Discount Activated! Purchase or Lose it!",
                    "zIndex": "1000",
                    "className": "promotion-modal",
                }],
                "fullscreenOverlayCount": 0,
                "fullscreenOverlayClasses": [],
                "url": "https://hub.example/store",
            }
        if script == guard._CLOSE_CONTROL_CHECK_JS:
            return {"ok": False, "reason": "no_explicit_close_evidence"}
        if script == guard._DIALOG_CONTROL_DIAGNOSTICS_JS:
            self.diagnostic_calls += 1
            return self.controls
        raise AssertionError("Unexpected JavaScript execution")


class PopupDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.button = FakeElement(element_id="discount-action")
        self.dialog = FakeElement(
            element_id="discount-dialog",
            children=[self.button],
        )
        self.controls = [{
            "tag": "button",
            "text": "Get 7 Days Premium",
            "aria": "",
            "title": "",
            "testid": "",
            "variant": "primary",
            "type": "button",
            "role": "",
            "className": "btn btn-primary",
            "disabled": False,
        }]
        self.driver = FakeDriver(self.dialog, self.button, self.controls)

    def test_blocking_dialog_includes_control_metadata_without_clicking(self):
        ok, reason = guard.dismiss_all_safe_popups(self.driver)

        self.assertFalse(ok)
        self.assertIn("blocking_dialog_has_no_unambiguous_safe_close", reason)
        self.assertIn("visible_controls=", reason)
        self.assertIn("Get 7 Days Premium", reason)
        self.assertIn("btn btn-primary", reason)
        self.assertEqual(self.driver.diagnostic_calls, 1)
        self.assertFalse(self.button.clicked)

    def test_diagnostic_failure_does_not_change_fail_closed_result(self):
        class BrokenDiagnosticDriver(FakeDriver):
            def execute_script(self, script, *args):
                if script == guard._DIALOG_CONTROL_DIAGNOSTICS_JS:
                    raise RuntimeError("diagnostic unavailable")
                return super().execute_script(script, *args)

        driver = BrokenDiagnosticDriver(self.dialog, self.button, self.controls)
        ok, reason = guard.dismiss_all_safe_popups(driver)

        self.assertFalse(ok)
        self.assertIn("blocking_dialog_has_no_unambiguous_safe_close", reason)
        self.assertIn("diagnostic_error", reason)
        self.assertFalse(self.button.clicked)

    def test_diagnostic_inventory_is_bounded_to_twelve_controls(self):
        many_controls = [dict(self.controls[0], text=f"Control {i}") for i in range(20)]
        driver = FakeDriver(self.dialog, self.button, many_controls)
        ok, reason = guard.dismiss_all_safe_popups(driver)

        self.assertFalse(ok)
        self.assertIn("Control 11", reason)
        self.assertNotIn("Control 12", reason)
        self.assertFalse(self.button.clicked)


if __name__ == "__main__":
    unittest.main(verbosity=2)
