import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import master_claimer as bot


class FakeDriver:
    def __init__(self):
        self.current_url = "https://hub.vertigogames.co/store"
        self.screenshots = []
        self.quit_called = False

    def get(self, url):
        self.current_url = url

    def refresh(self):
        return None

    def save_screenshot(self, name):
        self.screenshots.append(name)
        return True

    def quit(self):
        self.quit_called = True


class StoreClaimVerificationTests(unittest.TestCase):
    def _run_store(self, lookup_side_effect, wait_side_effect, click_side_effect=None):
        driver = FakeDriver()
        fake_button = object()
        with patch.object(bot, "bypass_cloudflare"), \
             patch.object(bot, "close_popup"), \
             patch.object(bot.time, "sleep"), \
             patch.object(bot, "_find_store_daily_button", side_effect=lookup_side_effect) as lookup, \
             patch.object(bot, "_wait_for_store_daily_state", side_effect=wait_side_effect), \
             patch.object(bot, "physical_click", side_effect=click_side_effect or [True]), \
             patch.object(bot, "discover_and_claim_promotions", return_value=[]), \
             patch.object(bot, "update_claim_history") as history_update:
            result = bot.claim_store_rewards(driver, "TESTPLAYER0001")
        return result, lookup, history_update, driver, fake_button

    def test_live_free_card_is_claimed_even_when_history_would_have_said_claimed(self):
        # There is intentionally no history check in the Store routine. The live
        # Free card is authoritative and must be eligible for recovery.
        sequence = iter([
            (object(), "available"),  # Gold before click
            (object(), "claimed"),    # Cash already claimed
            (object(), "cooldown"),   # Luckyloon already on cooldown
        ])
        result, lookup, history_update, _driver, _ = self._run_store(
            lookup_side_effect=lambda _driver, _index: next(sequence),
            wait_side_effect=["claimed"],
            click_side_effect=[True],
        )

        claimed, skipped, _promotions, unconfirmed, claimed_indices = result
        self.assertEqual(claimed, 1)
        self.assertEqual(skipped, [False, True, True])
        self.assertEqual(unconfirmed, [])
        self.assertEqual(claimed_indices, [1])
        history_update.assert_any_call(
            "TESTPLAYER0001", "store", claimed_count=1, reward_index=1
        )
        self.assertEqual(lookup.call_count, 3)

    def test_dispatched_click_that_leaves_card_free_is_not_counted_and_is_retryable(self):
        # Gold remains Free after both attempts; Cash/Luckyloon are already done.
        sequence = iter([
            (object(), "available"),  # initial Gold read
            (object(), "available"),  # fresh read after page refresh
            (object(), "available"),  # second attempt pre-click read
            (object(), "claimed"),    # Cash is already claimed
            (object(), "cooldown"),   # Luckyloon is on cooldown
        ])
        result, _lookup, history_update, _driver, _ = self._run_store(
            lookup_side_effect=lambda _driver, _index: next(sequence),
            wait_side_effect=["available", "available"],
            click_side_effect=[True, True],
        )

        claimed, skipped, _promotions, unconfirmed, claimed_indices = result
        self.assertEqual(claimed, 0)
        self.assertEqual(skipped, [False, True, True])
        self.assertEqual(unconfirmed, [1])
        self.assertEqual(claimed_indices, [])
        history_update.assert_any_call(
            "TESTPLAYER0001", "store", reward_index=1, unconfirmed=True
        )
        self.assertFalse(any(
            kwargs.get("claimed_count") == 1 and kwargs.get("reward_index") == 1
            for _args, kwargs in history_update.call_args_list
        ))

    def test_store_lookup_prioritizes_an_enabled_free_button_over_bonus_timer(self):
        driver = FakeDriver()
        fake_button = object()

        def execute_script(script, *_args):
            self.assertIn("const btn = states.find", script)
            self.assertNotIn("if (/\\bnext in\\b/.test(text)) return {status: 'cooldown'};", script)
            return {"status": "available", "button": fake_button}

        driver.execute_script = execute_script
        with patch.object(bot, "log"):
            button, state = bot._find_store_daily_button(driver, 2)
        self.assertIs(button, fake_button)
        self.assertEqual(state, "available")


    def test_failed_refresh_does_not_dispatch_a_second_store_click(self):
        driver = FakeDriver()
        driver.refresh = Mock(side_effect=RuntimeError("refresh failed"))
        lookup_sequence = iter([
            (object(), "available"),
            (object(), "claimed"),
            (object(), "cooldown"),
        ])
        with patch.object(bot, "bypass_cloudflare"), \
             patch.object(bot, "close_popup"), \
             patch.object(bot.time, "sleep"), \
             patch.object(bot, "_find_store_daily_button", side_effect=lambda _d, _i: next(lookup_sequence)), \
             patch.object(bot, "_wait_for_store_daily_state", return_value="available"), \
             patch.object(bot, "physical_click", return_value=True) as click, \
             patch.object(bot, "discover_and_claim_promotions", return_value=[]), \
             patch.object(bot, "update_claim_history") as history_update:
            result = bot.claim_store_rewards(driver, "TESTPLAYER0001")

        self.assertEqual(click.call_count, 1)
        self.assertEqual(result[3], [1])
        history_update.assert_any_call("TESTPLAYER0001", "store", reward_index=1, unconfirmed=True)


class LivePortalPrecedenceTests(unittest.TestCase):
    def test_daily_available_button_overrides_unrelated_page_timer(self):
        driver = FakeDriver()
        with patch.object(bot, "_find_daily_claim_button", return_value=(object(), "available")), \
             patch.object(bot, "detect_daily_timer_js", return_value=timedelta(hours=20)):
            self.assertFalse(bot._daily_portal_confirms_claim(driver))

    def test_daily_explicit_claimed_state_confirms_without_timer(self):
        driver = FakeDriver()
        with patch.object(bot, "_find_daily_claim_button", return_value=(None, "claimed")), \
             patch.object(bot, "detect_daily_timer_js", return_value=None):
            self.assertTrue(bot._daily_portal_confirms_claim(driver))

    def test_store_generic_bonus_timer_is_not_a_claimed_state(self):
        driver = FakeDriver()
        fake_button = object()

        def execute_script(script, *_args):
            self.assertNotIn("if (/\\bnext in\\b/.test(text)) return {status: 'cooldown'};", script)
            return {"status": "button_not_found"}

        driver.execute_script = execute_script
        with patch.object(bot, "log"):
            button, state = bot._find_store_daily_button(driver, 2)
        self.assertIsNone(button)
        self.assertEqual(state, "button_not_found")

    def test_store_portal_confirmed_after_ambiguous_click_is_tracked_for_progression(self):
        sequence = iter([
            (object(), "available"),
            (None, "claimed"),
            (None, "cooldown"),
        ])
        result, _lookup, history_update, driver, _ = StoreClaimVerificationTests()._run_store(
            lookup_side_effect=lambda _driver, _index: next(sequence),
            wait_side_effect=["claimed"],
            click_side_effect=[False],
        )
        claimed, skipped, _promotions, unconfirmed, claimed_indices = result
        self.assertEqual(claimed, 0)  # no claim counted as dispatched by the bot
        self.assertEqual(skipped, [True, True, True])
        self.assertEqual(unconfirmed, [])
        self.assertEqual(claimed_indices, [])
        self.assertEqual(driver._store_daily_recovered_indices, [1])
        history_update.assert_any_call(
            "TESTPLAYER0001", "store", reward_index=1, detected_cooldown=timedelta(seconds=61)
        )


class ClaimHistoryReliabilityTests(unittest.TestCase):
    def test_unconfirmed_store_attempt_removes_same_reset_false_success(self):
        now = datetime(2026, 11, 1, 10, 0, 0)
        history = {
            "TESTPLAYER0001": {
                "daily": {"last_claim": None, "next_available": None, "status": "unknown"},
                "store": {
                    "reward_1": {"last_claim": None, "next_available": None, "status": "unknown"},
                    "reward_2": {"last_claim": now.isoformat(), "next_available": (now + timedelta(hours=19)).isoformat(), "status": "claimed"},
                    "reward_3": {"last_claim": None, "next_available": None, "status": "unknown"},
                    "reward_4": {"last_claim": None, "next_available": None, "status": "unknown"},
                },
                "progression": {"last_claim": None, "last_count": 0, "last_visit": None},
                "loyalty": {"last_claim": None, "next_available": None, "status": "unknown"},
                "promotions": {},
                "event_rewards": {},
            }
        }
        reset = datetime(2026, 11, 1, 5, 30, 0)
        with patch.object(bot, "init_player_history", return_value=history), \
             patch.object(bot, "get_ist_time", return_value=now), \
             patch.object(bot, "get_next_daily_reset", return_value=now + timedelta(hours=19)), \
             patch.object(bot, "get_last_daily_reset", return_value=reset), \
             patch.object(bot, "save_claim_history"):
            bot.update_claim_history("TESTPLAYER0001", "store", reward_index=2, unconfirmed=True)

        record = history["TESTPLAYER0001"]["store"]["reward_2"]
        self.assertIsNone(record["last_claim"])
        self.assertIsNone(record["next_available"])
        self.assertEqual(record["status"], "unconfirmed")
        self.assertEqual(record["unconfirmed_attempts"], 1)

    def test_unconfirmed_status_overrides_stale_claim_history(self):
        now = datetime(2026, 11, 1, 10, 0, 0)
        history = {
            "TESTPLAYER0001": {
                "daily": {"last_claim": None, "next_available": None, "status": "unconfirmed"},
                "store": {
                    "reward_1": {"last_claim": None, "next_available": None, "status": "unknown"},
                    "reward_2": {"last_claim": now.isoformat(), "next_available": (now + timedelta(hours=19)).isoformat(), "status": "unconfirmed"},
                    "reward_3": {"last_claim": None, "next_available": None, "status": "unknown"},
                    "reward_4": {"last_claim": None, "next_available": None, "status": "unknown"},
                },
                "progression": {"last_claim": None, "last_count": 0, "last_visit": None},
                "loyalty": {"last_claim": None, "next_available": None, "status": "unknown"},
            }
        }
        with patch.object(bot, "load_claim_history", return_value=history), \
             patch.object(bot, "get_ist_time", return_value=now), \
             patch.object(bot, "get_last_daily_reset", return_value=datetime(2026, 11, 1, 5, 30)), \
             patch.object(bot, "get_next_daily_reset", return_value=now + timedelta(hours=19)), \
             patch.object(bot, "format_time_until", return_value="19h 0m"):
            state = bot.get_reward_status("TESTPLAYER0001")
        self.assertTrue(state["daily_available"])
        self.assertTrue(state["store_available"][1])

    def test_attention_section_highlights_unconfirmed_standard_reward(self):
        result = bot.build_standard_attention_section([{
            "pid": "TESTPLAYER0001",
            "display_name": "Test Player",
            "store_unconfirmed_indices": [2],
        }])
        self.assertIn("attn-box", result)
        self.assertIn("Attention Required", result)
        self.assertIn("Cash (Daily)", result)
        self.assertIn("#fffbeb", bot._CSS)
        self.assertIn("#7c2d12", bot._CSS)



class DailyClaimVerificationTests(unittest.TestCase):
    def test_daily_click_counts_only_after_portal_confirmation(self):
        driver = FakeDriver()
        with patch.object(bot, "bypass_cloudflare"), \
             patch.object(bot, "_dismiss_daily_reward_popups", return_value=(True, "test-clear")), \
             patch.object(bot.time, "sleep"), \
             patch.object(bot, "_daily_portal_confirms_claim", side_effect=[False, False]), \
             patch.object(bot, "_find_daily_claim_button", return_value=(object(), "available")), \
             patch.object(bot, "_prepare_daily_claim_target", return_value=(True, {"ready": True})), \
             patch.object(bot, "_click_daily_claim_target", return_value=True), \
             patch.object(bot, "_wait_for_daily_claim_confirmation", return_value=True), \
             patch.object(bot, "_capture_daily_claim_diagnostic", return_value={"metadata": {}, "screenshot": None}), \
             patch.object(bot, "update_claim_history") as history_update:
            result = bot.claim_daily_rewards(driver, "TESTPLAYER0001")

        self.assertEqual(result, (1, False, False))
        history_update.assert_called_once_with("TESTPLAYER0001", "daily", claimed_count=1)

    def test_daily_click_without_portal_transition_remains_unconfirmed(self):
        driver = FakeDriver()
        with patch.object(bot, "bypass_cloudflare"), \
             patch.object(bot, "_dismiss_daily_reward_popups", return_value=(True, "test-clear")), \
             patch.object(bot.time, "sleep"), \
             patch.object(bot, "_daily_portal_confirms_claim", return_value=False), \
             patch.object(bot, "_find_daily_claim_button", side_effect=[
                 (object(), "available"), (object(), "available"),
                 (object(), "available"), (object(), "available"),
                 (object(), "available"), (object(), "available"),
             ]), \
             patch.object(bot, "_prepare_daily_claim_target", return_value=(True, {"ready": True})), \
             patch.object(bot, "_click_daily_claim_target", return_value=True) as click, \
             patch.object(bot, "_wait_for_daily_claim_confirmation", side_effect=[False, False]), \
             patch.object(bot, "_capture_daily_claim_diagnostic", return_value={"metadata": {}, "screenshot": None}), \
             patch.object(bot, "_persist_daily_claim_diagnostics") as persist, \
             patch.object(bot, "update_claim_history") as history_update:
            result = bot.claim_daily_rewards(driver, "TESTPLAYER0001")

        self.assertEqual(result, (0, False, True))
        self.assertEqual(click.call_count, 2)  # exactly one retry, after a successful page refresh
        history_update.assert_called_once_with("TESTPLAYER0001", "daily", unconfirmed=True)
        persist.assert_called_once()



class ImmediateRecoveryTests(unittest.TestCase):
    def _failed_daily_result(self):
        return {
            "pid": "TESTPLAYER0001",
            "display_name": "Test Player",
            "processing_mode": "full_claim",
            "status": "Partial",
            "daily": 0,
            "daily_skipped": False,
            "daily_unconfirmed": True,
            "store": 0,
            "store_skipped": [True, True, True],
            "store_unconfirmed_indices": [],
            "store_claimed_indices": [],
            "store_possible": 0,
            "possible": 1,
            "progression": 0,
            "progression_unconfirmed": False,
            "event_rewards": [],
            "event_reward_claims": 0,
            "event_rewards_retry_required": False,
            "retry_required": True,
            "duration_s": 10,
        }

    def test_immediate_daily_recovery_uses_one_fresh_session_and_one_attempt(self):
        driver = Mock()
        result = self._failed_daily_result()
        with patch.object(bot, "create_driver", return_value=driver), \
             patch.object(bot, "login_to_hub", return_value=True), \
             patch.object(bot, "claim_daily_rewards", return_value=(1, False, False)) as daily, \
             patch.object(bot, "claim_store_rewards") as store, \
             patch.object(bot, "claim_progression_program_rewards") as progression, \
             patch.object(bot, "get_reward_status", return_value={
                 "daily_status": "claimed", "daily_next": "next reset",
                 "store_status": ["claimed"] * 3, "store_next": [None] * 3,
             }), \
             patch.object(bot.time, "sleep"):
            recovered = bot.immediate_recover_player_claims(
                "TESTPLAYER0001", False, False, "Primary Run", result
            )
        daily.assert_called_once_with(driver, "TESTPLAYER0001", max_attempts=1)
        store.assert_not_called()
        progression.assert_not_called()
        self.assertEqual(recovered["daily"], 1)
        self.assertFalse(recovered["daily_unconfirmed"])
        self.assertEqual(recovered["immediate_recovery"]["status"], "resolved")
        driver.quit.assert_called_once()

    def test_immediate_recovery_does_not_click_when_fresh_login_fails(self):
        driver = Mock()
        result = self._failed_daily_result()
        with patch.object(bot, "create_driver", return_value=driver), \
             patch.object(bot, "login_to_hub", return_value=False), \
             patch.object(bot, "claim_daily_rewards") as daily:
            recovered = bot.immediate_recover_player_claims(
                "TESTPLAYER0001", False, False, "Primary Run", result
            )
        daily.assert_not_called()
        self.assertTrue(recovered["daily_unconfirmed"])
        self.assertEqual(recovered["immediate_recovery"]["status"], "unresolved")
        driver.quit.assert_called_once()

    def test_promotional_only_result_never_enters_full_claim_immediate_recovery(self):
        result = {
            "pid": "PROMO0001", "processing_mode": "promotional_only",
            "status": "Partial", "promotion_detection_failed": True,
        }
        with patch.object(bot, "create_driver") as create_driver:
            unchanged = bot.immediate_recover_player_claims(
                "PROMO0001", False, False, "Promotional Check #1", result
            )
        create_driver.assert_not_called()
        self.assertNotIn("immediate_recovery", unchanged)

    def test_failed_progression_without_card_identity_is_deferred_safely(self):
        result = {
            "pid": "PROG0001", "processing_mode": "full_claim", "status": "Partial",
            "progression_unconfirmed": True, "progression_unconfirmed_card_signature": None,
            "daily_unconfirmed": False, "store_unconfirmed_indices": [],
            "event_rewards": [], "event_rewards_retry_required": False,
        }
        with patch.object(bot, "create_driver") as create_driver:
            recovered = bot.immediate_recover_player_claims(
                "PROG0001", False, False, "Primary Run", result
            )
        create_driver.assert_not_called()
        self.assertEqual(recovered["immediate_recovery"]["status"], "deferred")
        self.assertTrue(recovered["progression_unconfirmed"])


class BoundedRewardRetryTests(unittest.TestCase):
    def test_daily_max_attempts_one_never_dispatches_a_second_click(self):
        driver = FakeDriver()
        with patch.object(bot, "bypass_cloudflare"), \
             patch.object(bot, "_dismiss_daily_reward_popups", return_value=(True, "test-clear")), \
             patch.object(bot.time, "sleep"), \
             patch.object(bot, "_daily_portal_confirms_claim", return_value=False), \
             patch.object(bot, "_find_daily_claim_button", return_value=(object(), "available")), \
             patch.object(bot, "_prepare_daily_claim_target", return_value=(True, {"ready": True})), \
             patch.object(bot, "_click_daily_claim_target", return_value=True) as click, \
             patch.object(bot, "_wait_for_daily_claim_confirmation", return_value=False), \
             patch.object(bot, "_capture_daily_claim_diagnostic", return_value={"metadata": {}, "screenshot": None}), \
             patch.object(bot, "_persist_daily_claim_diagnostics"), \
             patch.object(bot, "update_claim_history"):
            result = bot.claim_daily_rewards(driver, "TESTPLAYER0001", max_attempts=1)
        self.assertEqual(result, (0, False, True))
        self.assertEqual(click.call_count, 1)

    def test_store_recovery_only_inspects_requested_card_and_skips_promotions(self):
        driver = FakeDriver()
        with patch.object(bot, "bypass_cloudflare"), \
             patch.object(bot, "close_popup"), \
             patch.object(bot.time, "sleep"), \
             patch.object(bot, "_find_store_daily_button", return_value=(None, "claimed")) as lookup, \
             patch.object(bot, "discover_and_claim_promotions") as promotions, \
             patch.object(bot, "update_claim_history"):
            result = bot.claim_store_rewards(
                driver, "TESTPLAYER0001", reward_indices=[2],
                max_attempts=1, scan_promotions=False,
            )
        self.assertEqual(len(lookup.call_args_list), 1)
        self.assertEqual(lookup.call_args_list[0].args[1], 2)
        promotions.assert_not_called()
        self.assertEqual(result[3], [])



class ProgressionVerificationTests(unittest.TestCase):
    def test_progression_click_counts_only_when_portal_state_changes(self):
        driver = FakeDriver()
        before = {"claimable_count": 1, "delivered_count": 0, "button": object(), "button_text": "claim"}
        after = {"claimable_count": 0, "delivered_count": 1, "button": None, "button_text": None}
        with patch.object(bot, "bypass_cloudflare"), \
             patch.object(bot, "close_popup"), \
             patch.object(bot.time, "sleep"), \
             patch.object(bot, "_progression_claim_snapshot", side_effect=[before, after, after]), \
             patch.object(bot, "physical_click", return_value=True), \
             patch.object(bot, "update_claim_history") as history_update:
            result = bot.claim_progression_program_rewards(driver, "TESTPLAYER0001")

        self.assertEqual(result, 1)
        self.assertFalse(driver._progression_unconfirmed)
        history_update.assert_called_once_with("TESTPLAYER0001", "progression", claimed_count=1, unconfirmed=False)

    def test_progression_click_without_delivered_state_is_unconfirmed(self):
        driver = FakeDriver()
        snapshot = {"claimable_count": 1, "delivered_count": 0, "button": object(), "button_text": "claim"}
        with patch.object(bot, "bypass_cloudflare"), \
             patch.object(bot, "close_popup"), \
             patch.object(bot.time, "sleep"), \
             patch.object(bot, "_progression_claim_snapshot", return_value=snapshot), \
             patch.object(bot, "physical_click", return_value=True), \
             patch.object(bot, "update_claim_history") as history_update:
            result = bot.claim_progression_program_rewards(driver, "TESTPLAYER0001")

        self.assertEqual(result, 0)
        self.assertTrue(driver._progression_unconfirmed)
        history_update.assert_called_once_with("TESTPLAYER0001", "progression", claimed_count=0, unconfirmed=True)


    def test_progression_claim_button_disappearing_without_delivered_state_is_not_success(self):
        driver = FakeDriver()
        before = {"claimable_count": 1, "delivered_count": 0, "button": object(), "button_text": "claim"}
        after = {"claimable_count": 0, "delivered_count": 0, "button": None, "button_text": None}
        with patch.object(bot, "bypass_cloudflare"), \
             patch.object(bot, "close_popup"), \
             patch.object(bot.time, "sleep"), \
             patch.object(bot, "_progression_claim_snapshot", side_effect=[before, after, after]), \
             patch.object(bot, "physical_click", return_value=True), \
             patch.object(bot, "update_claim_history") as history_update:
            result = bot.claim_progression_program_rewards(driver, "TESTPLAYER0001")

        self.assertEqual(result, 0)
        self.assertTrue(driver._progression_unconfirmed)
        history_update.assert_called_once_with("TESTPLAYER0001", "progression", claimed_count=0, unconfirmed=True)


class RecoveryEligibilityTests(unittest.TestCase):
    def test_backup_unconfirmed_store_reward_is_retryable(self):
        result = {
            "processing_mode": "backup_reconciliation",
            "status": "Partial",
            "retry_required": True,
            "store_unconfirmed_indices": [2],
            "daily_unconfirmed": False,
            "progression_unconfirmed": False,
        }
        self.assertTrue(bot._result_needs_recovery(result))

    def test_completed_backup_with_all_portal_states_confirmed_is_not_retryable(self):
        result = {
            "processing_mode": "backup_reconciliation",
            "status": "Backup Reconciliation Complete",
            "retry_required": False,
            "possible": 0,
            "store_unconfirmed_indices": [],
            "daily_unconfirmed": False,
            "progression_unconfirmed": False,
            "promotions_observed": [],
            "event_rewards": [],
        }
        self.assertFalse(bot._result_needs_recovery(result))


class BackupReconciliationTests(unittest.TestCase):
    def test_backup_revisits_daily_and_standard_store_cards(self):
        driver = FakeDriver()
        with patch.object(bot, "init_player_history"), \
             patch.object(bot, "create_driver", return_value=driver), \
             patch.object(bot, "login_to_hub", return_value=True), \
             patch.object(bot, "capture_display_name", return_value="Test Player"), \
             patch.object(bot, "claim_daily_rewards", return_value=(0, True, False)) as daily, \
             patch.object(bot, "claim_store_rewards", return_value=(0, [True, True, True], [], [], [])) as store, \
             patch.object(bot, "run_event_reward_checks", return_value=[]), \
             patch.object(bot, "get_reward_status", return_value={
                 "daily_status": "portal_claimed", "daily_next": "next reset",
                 "store_status": ["portal_claimed"] * 3, "store_next": ["next reset"] * 3,
                 "loyalty_next": None,
             }), \
             patch.object(bot.time, "sleep"):
            result = bot.process_player_backup_reconciliation("TESTPLAYER0001", False, "Promotional Check #1")

        daily.assert_called_once_with(driver, "TESTPLAYER0001")
        store.assert_called_once_with(driver, "TESTPLAYER0001")
        self.assertTrue(result["standard_rewards_checked"])
        self.assertEqual(result["processing_mode"], "backup_reconciliation")
        self.assertEqual(result["status"], "Backup Reconciliation Complete")
        self.assertFalse(result["retry_required"])
        self.assertTrue(driver.quit_called)

    def test_backup_reopens_progression_when_store_bonus_is_recovered(self):
        driver = FakeDriver()
        with patch.object(bot, "init_player_history"), \
             patch.object(bot, "create_driver", return_value=driver), \
             patch.object(bot, "login_to_hub", return_value=True), \
             patch.object(bot, "capture_display_name", return_value="Test Player"), \
             patch.object(bot, "claim_daily_rewards", return_value=(0, True, False)), \
             patch.object(bot, "claim_store_rewards", side_effect=lambda d, _pid: (setattr(d, "_store_daily_recovered_indices", [1]) or (1, [False, True, True], [], [], [1]))), \
             patch.object(bot, "claim_progression_program_rewards", return_value=1) as progression, \
             patch.object(bot, "run_event_reward_checks", return_value=[]), \
             patch.object(bot, "get_reward_status", return_value={
                 "daily_status": "portal_claimed", "daily_next": "next reset",
                 "store_status": ["claimed", "portal_claimed", "portal_claimed"], "store_next": [None] * 3,
                 "loyalty_next": None,
             }), \
             patch.object(bot.time, "sleep"):
            result = bot.process_player_backup_reconciliation("TESTPLAYER0001", False, "Promotional Check #1")

        progression.assert_called_once_with(driver, "TESTPLAYER0001")
        self.assertTrue(result["progression_checked"])
        self.assertEqual(result["progression"], 1)
        self.assertEqual(result["status"], "Claims Made")


    def test_backup_reopens_progression_for_portal_confirmed_store_transition_even_if_click_was_ambiguous(self):
        driver = FakeDriver()
        with patch.object(bot, "init_player_history"), \
             patch.object(bot, "create_driver", return_value=driver), \
             patch.object(bot, "login_to_hub", return_value=True), \
             patch.object(bot, "capture_display_name", return_value="Test Player"), \
             patch.object(bot, "claim_daily_rewards", return_value=(0, True, False)), \
             patch.object(bot, "claim_store_rewards", side_effect=lambda d, _pid: (setattr(d, "_store_daily_recovered_indices", [2]) or (0, [True, True, True], [], [], []))), \
             patch.object(bot, "claim_progression_program_rewards", return_value=1) as progression, \
             patch.object(bot, "run_event_reward_checks", return_value=[]), \
             patch.object(bot, "get_reward_status", return_value={
                 "daily_status": "portal_claimed", "daily_next": "next reset",
                 "store_status": ["portal_claimed"] * 3, "store_next": [None] * 3,
                 "loyalty_next": None,
             }), \
             patch.object(bot.time, "sleep"):
            result = bot.process_player_backup_reconciliation("TESTPLAYER0001", False, "Promotional Check #1")

        progression.assert_called_once_with(driver, "TESTPLAYER0001")
        self.assertTrue(result["progression_checked"])
        self.assertEqual(result["progression"], 1)


class StandardBackupAuditCadenceTests(unittest.TestCase):
    def test_old_complete_coverage_requires_one_standard_audit(self):
        meta = {"full_claim_coverage": {"TESTPLAYER0001": {
            "reset_date": "2026-11-01", "status": "complete"
        }}}
        with patch.object(bot, "_current_reset_date", return_value="2026-11-01"):
            self.assertTrue(bot.needs_standard_backup_audit("TESTPLAYER0001", meta))

    def test_same_reset_audit_marker_skips_repeated_full_standard_audit(self):
        meta = {"full_claim_coverage": {"TESTPLAYER0001": {
            "reset_date": "2026-11-01", "status": "complete",
            "standard_rewards_audited_reset": "2026-11-01"
        }}}
        with patch.object(bot, "_current_reset_date", return_value="2026-11-01"):
            self.assertFalse(bot.needs_standard_backup_audit("TESTPLAYER0001", meta))

    def test_new_reset_requires_a_fresh_standard_audit(self):
        meta = {"full_claim_coverage": {"TESTPLAYER0001": {
            "reset_date": "2026-11-01", "status": "complete",
            "standard_rewards_audited_reset": "2026-11-01"
        }}}
        with patch.object(bot, "_current_reset_date", return_value="2026-11-02"):
            self.assertTrue(bot.needs_standard_backup_audit("TESTPLAYER0001", meta))

    def test_clean_full_claim_marks_standard_rewards_audited(self):
        meta = {}
        result = {
            "status": "Success", "standard_rewards_checked": True,
            "daily_unconfirmed": False, "store_unconfirmed_indices": [],
            "progression_unconfirmed": False, "retry_required": False,
            "promotions_observed": [], "event_rewards": [],
            "possible": 4, "daily": 1, "store": 3,
        }
        with patch.object(bot, "_current_reset_date", return_value="2026-11-01"), \
             patch.object(bot, "get_ist_time", return_value=datetime(2026, 11, 1, 10, 0)):
            bot._record_full_claim_coverage(meta, "TESTPLAYER0001", result, "Primary Run")
        entry = meta["full_claim_coverage"]["TESTPLAYER0001"]
        self.assertEqual(entry["status"], "complete")
        self.assertEqual(entry["standard_rewards_audited_reset"], "2026-11-01")

    def test_unconfirmed_store_does_not_mark_standard_audit_complete(self):
        meta = {}
        result = {
            "status": "Partial", "standard_rewards_checked": True,
            "daily_unconfirmed": False, "store_unconfirmed_indices": [2],
            "retry_required": True, "promotions_observed": [], "event_rewards": [],
            "possible": 3, "daily": 1, "store": 1,
        }
        with patch.object(bot, "_current_reset_date", return_value="2026-11-01"), \
             patch.object(bot, "get_ist_time", return_value=datetime(2026, 11, 1, 10, 0)):
            bot._record_full_claim_coverage(meta, "TESTPLAYER0001", result, "Primary Run")
        entry = meta["full_claim_coverage"]["TESTPLAYER0001"]
        self.assertEqual(entry["status"], "pending")
        self.assertNotIn("standard_rewards_audited_reset", entry)


class SavedPostRunRecoveryTests(unittest.TestCase):
    def _event_failure(self):
        return {
            "program_id": "event-a",
            "name": "Event A",
            "cycle": 2,
            "retry_required": True,
            "retry_thresholds": [800],
            "claims_this_run": [{"threshold": 400, "reward": "Earlier confirmed tier"}],
            "tiers": [
                {"threshold": 800, "status": "claim_unconfirmed", "reward": ["Failed target"]}
            ],
        }

    def test_saved_target_map_preserves_exact_failed_reward_identities(self):
        result = {
            "pid": "TARGET0001",
            "status": "Partial",
            "daily_unconfirmed": True,
            "store_unconfirmed_indices": [3, 2, 99],
            "progression_unconfirmed": True,
            "progression_unconfirmed_card_signature": "Grenade reward 3 | 500 points",
            "event_rewards_retry_required": True,
            "event_rewards": [self._event_failure()],
        }
        targets = bot._recovery_targets_from_result(result)
        self.assertEqual(targets["store_unconfirmed_indices"], [2, 3])
        self.assertTrue(targets["daily_unconfirmed"])
        self.assertEqual(
            targets["progression_card_signature"],
            "Grenade reward 3 | 500 points",
        )
        self.assertEqual(
            targets["event_thresholds_by_program"],
            {"event-a": [800]},
        )
        self.assertEqual(targets["event_rewards"][0]["claims_this_run"], [{"threshold": 400, "reward": "Earlier confirmed tier"}])

    def test_saved_targets_reconstruct_only_the_failed_reward_categories(self):
        targets = {
            "login_retry": False,
            "processing_error": False,
            "daily_unconfirmed": True,
            "store_unconfirmed_indices": [2],
            "progression_unconfirmed": True,
            "progression_card_signature": "Original progression card",
            "event_retry_required": True,
            "event_thresholds_by_program": {"event-a": [800]},
            "event_rewards": [self._event_failure()],
        }
        result = bot._recovery_result_from_saved_targets("TARGET0002", targets)
        self.assertTrue(result["daily_unconfirmed"])
        self.assertEqual(result["store_unconfirmed_indices"], [2])
        self.assertTrue(result["progression_unconfirmed"])
        self.assertEqual(result["progression_unconfirmed_card_signature"], "Original progression card")
        self.assertTrue(result["event_rewards_retry_required"])
        self.assertEqual(
            bot._event_retry_targets(result["event_rewards"]),
            {"event-a": [800]},
        )
        self.assertEqual(result["event_rewards"][0]["claims_this_run"], [])

    def test_post_run_recovery_uses_saved_daily_target_without_full_account_processing(self):
        driver = FakeDriver()
        targets = {
            "login_retry": False,
            "processing_error": False,
            "daily_unconfirmed": True,
            "store_unconfirmed_indices": [],
            "progression_unconfirmed": False,
            "progression_card_signature": None,
            "event_retry_required": False,
            "event_thresholds_by_program": {},
            "event_rewards": [],
        }
        with patch.object(bot, "RECOVERY_ONLY_FAILED", True), \
             patch.object(bot, "create_driver", return_value=driver), \
             patch.object(bot, "login_to_hub", return_value=True), \
             patch.object(bot, "claim_daily_rewards", return_value=(1, False, False)) as daily, \
             patch.object(bot, "claim_store_rewards") as store, \
             patch.object(bot, "claim_progression_program_rewards") as progression, \
             patch.object(bot, "run_event_reward_checks") as events, \
             patch.object(bot, "process_player") as full_account, \
             patch.object(bot, "get_reward_status", return_value={
                 "daily_status": "claimed", "daily_next": "next reset",
                 "store_status": ["claimed"] * 3, "store_next": [None] * 3,
             }), \
             patch.object(bot.time, "sleep"):
            recovered, coverage_rechecked = bot.recover_player_from_saved_targets(
                "TARGET0003", False, False, "Recovery Run", targets
            )
        self.assertTrue(coverage_rechecked)
        daily.assert_called_once_with(driver, "TARGET0003", max_attempts=1)
        store.assert_not_called()
        progression.assert_not_called()
        events.assert_not_called()
        full_account.assert_not_called()
        self.assertEqual(recovered["immediate_recovery"]["status"], "resolved")
        self.assertTrue(driver.quit_called)

    def test_post_run_recovery_replays_exact_progression_and_event_targets(self):
        driver = FakeDriver()
        driver._progression_unconfirmed = False
        event = self._event_failure()
        targets = {
            "login_retry": False,
            "processing_error": False,
            "daily_unconfirmed": False,
            "store_unconfirmed_indices": [],
            "progression_unconfirmed": True,
            "progression_card_signature": "Original progression card",
            "event_retry_required": True,
            "event_thresholds_by_program": {"event-a": [800]},
            "event_rewards": [event],
        }
        recovered_event = {
            "program_id": "event-a",
            "name": "Event A",
            "cycle": 2,
            "retry_required": False,
            "retry_thresholds": [],
            "claims_this_run": [],
            "tiers": [{"threshold": 800, "status": "delivered"}],
        }
        with patch.object(bot, "RECOVERY_ONLY_FAILED", True), \
             patch.object(bot, "create_driver", return_value=driver), \
             patch.object(bot, "login_to_hub", return_value=True), \
             patch.object(bot, "claim_progression_program_rewards", return_value=0) as progression, \
             patch.object(bot, "run_event_reward_checks", return_value=[recovered_event]) as events, \
             patch.object(bot, "process_player") as full_account, \
             patch.object(bot, "get_reward_status", return_value={
                 "daily_status": "claimed", "daily_next": "next reset",
                 "store_status": ["claimed"] * 3, "store_next": [None] * 3,
             }), \
             patch.object(bot.time, "sleep"):
            recovered, coverage_rechecked = bot.recover_player_from_saved_targets(
                "TARGET0004", False, False, "Recovery Run", targets
            )
        self.assertTrue(coverage_rechecked)
        progression.assert_called_once_with(
            driver, "TARGET0004",
            target_card_signature="Original progression card",
            max_claims_per_visit=1,
        )
        events.assert_called_once_with(
            driver, "TARGET0004",
            only_program_ids={"event-a"},
            retry_thresholds_by_program={"event-a": [800]},
            max_claims_per_program=1,
        )
        full_account.assert_not_called()
        self.assertEqual(recovered["immediate_recovery"]["status"], "resolved")
        self.assertTrue(driver.quit_called)

    def test_missing_saved_target_metadata_is_deferred_without_full_account_claims(self):
        with patch.object(bot, "process_player") as full_account, \
             patch.object(bot, "immediate_recover_player_claims") as recovery:
            result, coverage_rechecked = bot.recover_player_from_saved_targets(
                "TARGET0005", False, False, "Recovery Run", None
            )
        self.assertFalse(coverage_rechecked)
        full_account.assert_not_called()
        recovery.assert_not_called()
        self.assertEqual(result["immediate_recovery"]["status"], "deferred")
        self.assertTrue(result["recovery_deferred"])

    def test_saved_generic_processing_error_is_deferred_not_replayed_as_full_account(self):
        targets = {
            "login_retry": False,
            "processing_error": True,
            "daily_unconfirmed": False,
            "store_unconfirmed_indices": [],
            "progression_unconfirmed": False,
            "progression_card_signature": None,
            "event_retry_required": False,
            "event_thresholds_by_program": {},
            "event_rewards": [],
        }
        with patch.object(bot, "RECOVERY_ONLY_FAILED", True), \
             patch.object(bot, "create_driver") as create_driver, \
             patch.object(bot, "process_player") as full_account:
            result, coverage_rechecked = bot.recover_player_from_saved_targets(
                "TARGET0006", False, False, "Recovery Run", targets
            )
        self.assertTrue(coverage_rechecked)
        create_driver.assert_not_called()
        full_account.assert_not_called()
        self.assertEqual(result["immediate_recovery"]["status"], "deferred")





    def test_daily_preexisting_claim_is_recorded_before_history_suppression(self):
        driver = FakeDriver()
        driver._portal_claim_audit_enabled = True
        driver._portal_claim_display_name = "FREAKv20"
        driver._portal_claim_run_label = "Primary Run"
        driver._preexisting_portal_claims = []
        with patch.object(bot, "bypass_cloudflare"), \
             patch.object(bot, "close_popup"), \
             patch.object(bot.time, "sleep"), \
             patch.object(bot, "_daily_portal_confirms_claim", return_value=True), \
             patch.object(bot, "record_preexisting_portal_claim") as record, \
             patch.object(bot, "update_claim_history") as history_update:
            result = bot.claim_daily_rewards(driver, "1234567890123456")
        self.assertEqual(result, (0, True, False))
        record.assert_called_once_with(
            driver, "1234567890123456", "daily", "daily",
            "Daily Reward", "daily_initial_portal_state",
        )
        history_update.assert_called_once_with(
            "1234567890123456", "daily",
            detected_cooldown=timedelta(seconds=61),
        )

    def test_store_claim_that_changes_to_claimed_after_our_click_is_not_a_preexisting_claim(self):
        driver = FakeDriver()
        driver._portal_claim_audit_enabled = True
        driver._portal_claim_display_name = "FREAKv20"
        driver._portal_claim_run_label = "Primary Run"
        driver._preexisting_portal_claims = []
        with patch.object(bot, "bypass_cloudflare"), \
             patch.object(bot, "close_popup"), \
             patch.object(bot.time, "sleep"), \
             patch.object(bot, "_find_store_daily_button", return_value=(object(), "available")), \
             patch.object(bot, "_wait_for_store_daily_state", return_value="claimed"), \
             patch.object(bot, "physical_click", return_value=True), \
             patch.object(bot, "record_preexisting_portal_claim") as record, \
             patch.object(bot, "update_claim_history"):
            result = bot.claim_store_rewards(
                driver, "1234567890123456",
                reward_indices=[1], max_attempts=1, scan_promotions=False,
            )
        self.assertEqual(result[0], 1)
        self.assertEqual(result[4], [1])
        record.assert_not_called()

if __name__ == "__main__":
    unittest.main()
