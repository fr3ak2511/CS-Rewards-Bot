import json
import unittest

import reward_popup_guard as guard


class DiagnosticOnlyDriver:
    def __init__(self, context):
        self.context = context
        self.clicks = 0

    def execute_script(self, script, *args):
        if script != guard._POPUP_CONTEXT_DIAGNOSTICS_JS:
            raise AssertionError("The diagnostics helper must not run click scripts")
        return self.context


class BlockingOverlayDriver:
    """Minimal driver proving that overlay diagnostics do not authorize a click."""

    def __init__(self, context):
        self.context = context
        self.clicks = 0

    def find_elements(self, by, selector):
        return []

    def execute_script(self, script, *args):
        if script == guard._LAYER_INSPECT_JS:
            return {
                "dialogCount": 0,
                "dialogSummaries": [],
                "fullscreenOverlayCount": 1,
                "fullscreenOverlayClasses": ["blocking-overlay"],
                "url": "https://hub.example/store",
            }
        if script == guard._POPUP_CONTEXT_DIAGNOSTICS_JS:
            return self.context
        raise AssertionError("Unexpected script: popup guard must not try a click fallback")


def overlay_context():
    return {
        "url": "https://hub.example/store",
        "viewport": {"width": 1200, "height": 800},
        "dialogRootCount": 0,
        "dialogRoots": [],
        "visibleOverlays": [{
            "tag": "div",
            "role": "presentation",
            "aria": "",
            "title": "",
            "testid": "",
            "dataState": "open",
            "dataSlot": "overlay",
            "className": "blocking-overlay",
            "text": "Store promotion popup",
            "position": "fixed",
            "zIndex": "10000",
            "cursor": "default",
            "pointerEvents": "auto",
            "onclickPresent": False,
            "disabled": False,
            "rect": {"x": 0, "y": 0, "width": 1200, "height": 800},
        }],
    }


class PopupOverlayWithoutDialogRootTests(unittest.TestCase):
    def test_visible_overlay_is_sanitized_when_dialog_roots_are_empty(self):
        driver = DiagnosticOnlyDriver(overlay_context())
        diagnostic = guard._popup_context_diagnostics(driver)

        self.assertTrue(diagnostic.startswith("popup_context="))
        payload = json.loads(diagnostic[len("popup_context="):])
        self.assertEqual(payload["dialogRootCount"], 0)
        self.assertEqual(len(payload["dialogRoots"]), 0)
        self.assertEqual(payload["visibleOverlays"][0]["className"], "blocking-overlay")
        self.assertEqual(payload["visibleOverlays"][0]["text"], "Store promotion popup")
        self.assertEqual(driver.clicks, 0)

    def test_overlay_only_block_remains_fail_closed_after_diagnostics(self):
        driver = BlockingOverlayDriver(overlay_context())
        ok, reason = guard.dismiss_all_safe_popups(driver, timeout=0.2)

        self.assertFalse(ok)
        self.assertIn("unknown_fullscreen_overlay_blocks_page", reason)
        self.assertIn("blocking-overlay", reason)
        self.assertIn("Store promotion popup", reason)
        self.assertEqual(driver.clicks, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
