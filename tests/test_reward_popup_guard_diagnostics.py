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
    def __init__(self, dialog, button, context):
        self.dialog = dialog
        self.button = button
        self.context = context
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
        if script == guard._POPUP_CONTEXT_DIAGNOSTICS_JS:
            self.diagnostic_calls += 1
            return self.context
        raise AssertionError("Unexpected JavaScript execution")


def make_element(tag, *, text="", class_name="", aria="", rect=None):
    return {
        "tag": tag,
        "role": "",
        "aria": aria,
        "title": "",
        "testid": "",
        "dataState": "",
        "dataSlot": "",
        "className": class_name,
        "text": text,
        "position": "absolute",
        "zIndex": "1001",
        "cursor": "pointer",
        "pointerEvents": "auto",
        "onclickPresent": False,
        "disabled": False,
        "rect": rect or {"x": 10, "y": 10, "width": 24, "height": 24},
    }


class PopupDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.button = FakeElement(element_id="discount-action")
        self.dialog = FakeElement(
            element_id="discount-dialog",
            children=[self.button],
        )
        close_icon = make_element("svg", text="", class_name="popup-close-icon")
        parent = make_element("div", class_name="popup-shell")
        self.context = {
            "url": "https://hub.example/store",
            "viewport": {"width": 1280, "height": 720},
            "dialogRootCount": 1,
            "dialogRoots": [{
                "root": make_element("div", text="Exclusive Discount Activated!", class_name="promotion-modal"),
                "ancestors": [parent],
                "nearbyControls": [
                    make_element("button", text="Get 7 Days Premium", class_name="btn btn-primary"),
                    close_icon,
                ],
            }],
            "visibleOverlays": [],
        }
        self.driver = FakeDriver(self.dialog, self.button, self.context)

    def test_context_includes_ancestors_and_nearby_svg_without_clicking(self):
        ok, reason = guard.dismiss_all_safe_popups(self.driver)

        self.assertFalse(ok)
        self.assertIn("blocking_dialog_has_no_unambiguous_safe_close", reason)
        self.assertIn("popup_context=", reason)
        self.assertIn("popup-shell", reason)
        self.assertIn("popup-close-icon", reason)
        self.assertIn("Get 7 Days Premium", reason)
        self.assertEqual(self.driver.diagnostic_calls, 1)
        self.assertFalse(self.button.clicked)

    def test_diagnostic_failure_does_not_change_fail_closed_result(self):
        class BrokenDiagnosticDriver(FakeDriver):
            def execute_script(self, script, *args):
                if script == guard._POPUP_CONTEXT_DIAGNOSTICS_JS:
                    raise RuntimeError("diagnostic unavailable")
                return super().execute_script(script, *args)

        driver = BrokenDiagnosticDriver(self.dialog, self.button, self.context)
        ok, reason = guard.dismiss_all_safe_popups(driver)

        self.assertFalse(ok)
        self.assertIn("blocking_dialog_has_no_unambiguous_safe_close", reason)
        self.assertIn('diagnostic_error', reason)
        self.assertIn('RuntimeError', reason)
        self.assertFalse(self.button.clicked)

    def test_context_inventory_is_bounded_to_sixteen_nearby_controls(self):
        many_controls = [
            make_element("button", text=f"Control {i}") for i in range(24)
        ]
        context = dict(self.context)
        context["dialogRoots"] = [dict(self.context["dialogRoots"][0], nearbyControls=many_controls)]
        driver = FakeDriver(self.dialog, self.button, context)
        diagnostic = guard._popup_context_diagnostics(driver)

        self.assertIn("Control 15", diagnostic)
        self.assertNotIn("Control 16", diagnostic)
        self.assertFalse(self.button.clicked)

    def test_diagnostics_are_separate_from_click_eligibility(self):
        # Even a nearby element whose class contains "close" is only recorded.
        # It is not added to the Selenium click candidate list.
        nearby = make_element("svg", class_name="close-icon")
        context = dict(self.context)
        context["dialogRoots"] = [dict(self.context["dialogRoots"][0], nearbyControls=[nearby])]
        driver = FakeDriver(self.dialog, self.button, context)

        ok, reason = guard.dismiss_all_safe_popups(driver)

        self.assertFalse(ok)
        self.assertIn("close-icon", reason)
        self.assertFalse(self.button.clicked)


if __name__ == "__main__":
    unittest.main(verbosity=2)
