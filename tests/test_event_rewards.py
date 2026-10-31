import unittest
from unittest.mock import patch

from event_rewards import (
    _SNAPSHOT_SCRIPT,
    EventRewardsError,
    _normalise_snapshot,
    claim_event_program,
    _parse_balance,
    _parse_reward_names,
    _parse_threshold,
    _tier_status,
    _validate_claim_button,
    merge_claim_ledger,
    event_claim_count,
    event_retry_required,
    get_active_event_programs,
)


class EventRewardParsingTests(unittest.TestCase):
    def test_parse_balance(self):
        self.assertEqual(_parse_balance("Lost Soul Rewards\n1,000 / 1,300\n"), (1000, 1300))
        self.assertEqual(_parse_balance("No balance here"), (None, None))

    def test_threshold_and_reward_names(self):
        card = "400\nx10\nOrb of Calamity\nx10\nAether Extract\nDelivered"
        self.assertEqual(_parse_threshold(card), 400)
        self.assertEqual(_parse_reward_names(card), ["Orb of Calamity ×10", "Aether Extract ×10"])
        self.assertEqual(_parse_reward_names("5000\nx200K\nCash"), ["Cash ×200K"])

    def test_status_requires_balance_confirmation(self):
        self.assertEqual(_tier_status({"buttonText": "Delivered", "disabled": True, "cardText": "400\nDelivered"}, 1000), "delivered")
        self.assertEqual(_tier_status({"buttonText": "Claim", "disabled": False, "cardText": "1300\nLuckyloon"}, 1000), "inconsistent_balance")
        self.assertEqual(_tier_status({"buttonText": "Claim", "disabled": False, "cardText": "900\nAbyss"}, 1000), "available")
        self.assertEqual(_tier_status({"buttonText": "", "disabled": True, "cardText": "2000\nSuper Chest"}, 1000), "locked")

    def test_normalise_snapshot(self):
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
        reward_read = _SNAPSHOT_SCRIPT.index("const rewardRows")
        self.assertLess(claim_check, reward_read)
        self.assertIn("if (claimButtonEnabled)", _SNAPSHOT_SCRIPT)
        self.assertIn('span[class*="line-clamp-1"]', _SNAPSHOT_SCRIPT)
        self.assertIn("text-store-card-sku-img-quantity", _SNAPSHOT_SCRIPT)

        snapshot = _normalise_snapshot({
            "title": "Halloween 2026",
            "url": "https://hub.vertigogames.co/halloween-2026",
            "bodyText": "Lost Soul Rewards\\n10,000 / 11,000",
            "cards": [
                {
                    "thresholdText": "5,200",
                    "rewardItems": [{"name": "Viktor Character Card", "quantity": "x20"}],
                    "claimButtonEnabled": True,
                    "buttonIndex": 11,
                    "buttonText": "Claim",
                    "disabled": False,
                    "visible": True,
                    "cardText": "5,200\\nx20\\nViktor Character Card\\nClaim",
                },
                {
                    "thresholdText": "10,200",
                    "rewardItems": [],
                    "claimButtonEnabled": False,
                    "buttonIndex": 12,
                    "buttonText": "",
                    "disabled": True,
                    "visible": True,
                    "cardText": "10,200",
                },
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
                {
                    "thresholdText": "400",
                    "rewardItems": [
                        {"name": "Orb of Calamity", "quantity": "x10"},
                        {"name": "Aether Extract", "quantity": "x10"},
                    ],
                    "claimButtonEnabled": True,
                    "buttonIndex": 2,
                    "buttonText": "Claim",
                    "disabled": False,
                    "visible": True,
                    "cardText": "400\nx10\nOrb of Calamity\nx10\nAether Extract\nClaim",
                },
                {
                    "thresholdText": "900",
                    "rewardItems": [{"name": "Abyss", "quantity": ""}],
                    "claimButtonEnabled": True,
                    "buttonIndex": 3,
                    "buttonText": "Claim",
                    "disabled": False,
                    "visible": True,
                    "cardText": "900\nAbyss\nClaim",
                },
                {
                    "thresholdText": "1,300",
                    "rewardItems": [],
                    "claimButtonEnabled": False,
                    "buttonIndex": 4,
                    "buttonText": "",
                    "disabled": True,
                    "visible": True,
                    "cardText": "1,300\nx150\nLuckyloon",
                },
            ],
        })
        tiers = snapshot["tiers"]
        self.assertEqual([tier["threshold"] for tier in tiers], [400, 900, 1300])
        self.assertEqual(tiers[0]["reward"], ["Orb of Calamity ×10", "Aether Extract ×10"])
        self.assertEqual(tiers[1]["reward"], ["Abyss"])
        self.assertEqual([tier["status"] for tier in tiers], ["available", "available", "locked"])

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


class _FakeButton:
    def __init__(self):
        self.disabled = False

    def is_displayed(self):
        return True

    def is_enabled(self):
        return not self.disabled


class _FakeDriver:
    def __init__(self):
        self.button = _FakeButton()
        self.delivered = False
        self.current_url = ""
        self.missing_reward_names = False

    def get(self, url):
        self.current_url = url

    def execute_script(self, script, *args):
        if "EVENT_CLAIM_BUTTON_CHECK" in script:
            self.validation_script = script
            return {
                "ok": True, "visible": True, "disabled": False,
                "buttonText": "claim", "threshold": getattr(self, "validation_threshold", 900),
                "cardText": "900\\nAbyss\\nClaim",
            }
        if "const buttons = Array.from(document.querySelectorAll('button'))" not in script:
            return None
        delivered_text = "Delivered" if self.delivered else "Claim"
        enabled = not self.delivered
        return {
            "title": "Halloween 2026",
            "url": self.current_url,
            "bodyText": "Lost Soul Rewards\\n1,000 / 1,300",
            "cards": [
                {
                    "thresholdText": "900",
                    "rewardItems": [] if (self.missing_reward_names or self.delivered) else [{"name": "Abyss", "quantity": ""}],
                    "claimButtonEnabled": enabled,
                    "buttonIndex": 0,
                    "buttonText": delivered_text,
                    "disabled": self.delivered,
                    "visible": True,
                    "cardText": "900\\nAbyss\\n" + delivered_text,
                },
                {
                    "thresholdText": "1300",
                    "rewardItems": [],
                    "claimButtonEnabled": False,
                    "buttonIndex": 1,
                    "buttonText": "",
                    "disabled": True,
                    "visible": True,
                    "cardText": "1300",
                },
            ],
        }

    def find_elements(self, by, value):
        self.assert_locator = (by, value)
        return [self.button, _FakeButton()]

    def refresh(self):
        return None


class EventRewardButtonGuardTests(unittest.TestCase):
    def test_button_must_match_expected_tier(self):
        driver = _FakeDriver()
        button = _FakeButton()
        driver.validation_threshold = 1300
        valid, reason = _validate_claim_button(driver, button, 900)
        self.assertFalse(valid)
        self.assertIn("tier_mismatch", reason)

    def test_button_must_be_an_enabled_claim_control(self):
        driver = _FakeDriver()
        button = _FakeButton()
        valid, reason = _validate_claim_button(driver, button, 900)
        self.assertTrue(valid)
        self.assertEqual(reason, "validated")

    def test_validator_parses_threshold_concatenated_with_quantity(self):
        driver = _FakeDriver()
        button = _FakeButton()
        driver.validation_threshold = 5200
        valid, reason = _validate_claim_button(driver, button, 5200)
        self.assertTrue(valid, reason)
        self.assertIn("const thresholdMatch = firstLine.match", driver.validation_script)
        self.assertIn(r"(?=\s*(?:x|\s|$))", driver.validation_script)
        self.assertNotIn("thresholdElement = card", driver.validation_script)


class EventRewardExecutionTests(unittest.TestCase):
    @patch("event_rewards.time.sleep", return_value=None)
    def test_expected_tier_count_mismatch_fails_before_click(self, _sleep):
        driver = _FakeDriver()
        clicks = []
        with self.assertRaises(EventRewardsError):
            claim_event_program(
                driver,
                {"program_id": "pgp_test", "name": "Lost Soul Rewards", "slug": "halloween-2026",
                 "end_at_epoch": 9999999999, "max_claims_per_run": 20, "expected_tier_count": 14},
                physical_click=lambda _driver, _button: clicks.append(True) or True,
                bypass_cloudflare=lambda _driver: None,
                close_popup=lambda _driver: None,
                log=lambda _message: None,
            )
        self.assertEqual(clicks, [])

    @patch("event_rewards.time.sleep", return_value=None)
    def test_available_claim_without_reward_name_is_allowed_and_verified(self, _sleep):
        driver = _FakeDriver()
        driver.missing_reward_names = True
        clicks = []
        logs = []

        def physical_click(_driver, _button):
            clicks.append(True)
            driver.delivered = True
            return True

        result = claim_event_program(
            driver,
            {"program_id": "pgp_test", "name": "Lost Soul Rewards", "slug": "halloween-2026",
             "end_at_epoch": 9999999999, "max_claims_per_run": 20},
            physical_click=physical_click,
            bypass_cloudflare=lambda _driver: None,
            close_popup=lambda _driver: None,
            log=logs.append,
        )
        self.assertEqual(len(clicks), 1)
        self.assertEqual(result["tiers_delivered"], 1)
        self.assertEqual(result["claims_this_run"][0]["threshold"], 900)
        self.assertEqual(result["claims_this_run"][0]["reward"], [])
        self.assertFalse(result["retry_required"])
        self.assertIn("reward details unavailable", "\n".join(logs))

    @patch("event_rewards.time.sleep", return_value=None)
    def test_claims_enabled_tier_and_verifies_delivered(self, _sleep):
        driver = _FakeDriver()
        clicks = []

        def physical_click(_driver, _button):
            clicks.append(True)
            driver.delivered = True
            return True

        result = claim_event_program(
            driver,
            {"program_id": "pgp_test", "name": "Lost Soul Rewards", "slug": "halloween-2026",
             "end_at_epoch": 9999999999, "max_claims_per_run": 20},
            physical_click=physical_click,
            bypass_cloudflare=lambda _driver: None,
            close_popup=lambda _driver: None,
            log=lambda _message: None,
        )
        self.assertEqual(len(clicks), 1)
        self.assertEqual(result["balance"], 1000)
        self.assertEqual(result["tiers_delivered"], 1)
        self.assertEqual(result["claims_this_run"][0]["threshold"], 900)
        self.assertFalse(result["retry_required"])

    @patch("event_rewards.time.sleep", return_value=None)
    def test_mismatched_button_is_not_clicked(self, _sleep):
        driver = _FakeDriver()
        driver.validation_threshold = 1300
        clicks = []

        result = claim_event_program(
            driver,
            {"program_id": "pgp_test", "name": "Lost Soul Rewards", "slug": "halloween-2026",
             "end_at_epoch": 9999999999, "max_claims_per_run": 20},
            physical_click=lambda _driver, _button: clicks.append(True) or True,
            bypass_cloudflare=lambda _driver: None,
            close_popup=lambda _driver: None,
            log=lambda _message: None,
        )
        self.assertEqual(clicks, [])
        self.assertTrue(result["retry_required"])
        self.assertIn("button validation failed", result["error"])

    @patch("event_rewards.time.sleep", return_value=None)
    def test_unconfirmed_claim_is_not_clicked_twice(self, _sleep):
        driver = _FakeDriver()
        clicks = []

        def physical_click(_driver, _button):
            clicks.append(True)
            return False

        result = claim_event_program(
            driver,
            {"program_id": "pgp_test", "name": "Lost Soul Rewards", "slug": "halloween-2026",
             "end_at_epoch": 9999999999, "max_claims_per_run": 20},
            physical_click=physical_click,
            bypass_cloudflare=lambda _driver: None,
            close_popup=lambda _driver: None,
            log=lambda _message: None,
        )
        self.assertEqual(len(clicks), 1)
        self.assertTrue(result["retry_required"])
        self.assertEqual(result["claims_this_run"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
