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
             patch.object(bot, "close_popup"), \
             patch.object(bot.time, "sleep"), \
             patch.object(bot, "_daily_portal_confirms_claim", side_effect=[False, False]), \
             patch.object(bot, "_find_daily_claim_button", return_value=(object(), "available")), \
             patch.object(bot, "physical_click", return_value=True), \
             patch.object(bot, "_wait_for_daily_claim_confirmation", return_value=True), \
             patch.object(bot, "update_claim_history") as history_update:
            result = bot.claim_daily_rewards(driver, "TESTPLAYER0001")

        self.assertEqual(result, (1, False, False))
        history_update.assert_called_once_with("TESTPLAYER0001", "daily", claimed_count=1)

    def test_daily_click_without_portal_transition_remains_unconfirmed(self):
        driver = FakeDriver()
        with patch.object(bot, "bypass_cloudflare"), \
             patch.object(bot, "close_popup"), \
             patch.object(bot.time, "sleep"), \
             patch.object(bot, "_daily_portal_confirms_claim", return_value=False), \
             patch.object(bot, "_find_daily_claim_button", side_effect=[
                 (object(), "available"), (object(), "available"),
                 (object(), "available"), (object(), "available"),
             ]), \
             patch.object(bot, "physical_click", return_value=True) as click, \
             patch.object(bot, "_wait_for_daily_claim_confirmation", side_effect=[False, False]), \
             patch.object(bot, "_capture_daily_claim_diagnostic", return_value={"metadata": {}, "screenshot": None}), \
             patch.object(bot, "_persist_daily_claim_diagnostics") as persist, \
             patch.object(bot, "update_claim_history") as history_update:
            result = bot.claim_daily_rewards(driver, "TESTPLAYER0001")

        self.assertEqual(result, (0, False, True))
        self.assertEqual(click.call_count, 2)  # exactly one retry, after a successful page refresh
        history_update.assert_called_once_with("TESTPLAYER0001", "daily", unconfirmed=True)
        persist.assert_called_once()


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


if __name__ == "__main__":
    unittest.main()
