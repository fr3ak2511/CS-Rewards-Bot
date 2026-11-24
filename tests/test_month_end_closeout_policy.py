import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from grenade_balance import PROGRESSION_MILESTONE_THRESHOLDS
from month_end_closeout_policy import (
    FINAL_REPORT_DEADLINE_IST,
    evaluate_month_end_closeout,
    is_last_calendar_day,
    month_end_schedule,
    month_cycle_key,
)

IST = ZoneInfo("Asia/Kolkata")


class MonthEndCloseoutPolicyTests(unittest.TestCase):
    def test_last_calendar_day_handles_month_lengths_and_leap_year(self):
        for value in (
            datetime(2026, 1, 31, 8, 0, tzinfo=IST),
            datetime(2026, 2, 28, 8, 0, tzinfo=IST),
            datetime(2028, 2, 29, 8, 0, tzinfo=IST),
            datetime(2026, 4, 30, 8, 0, tzinfo=IST),
        ):
            self.assertTrue(is_last_calendar_day(value))
        self.assertFalse(is_last_calendar_day(datetime(2026, 11, 24, 8, 0, tzinfo=IST)))
        self.assertEqual(month_cycle_key(datetime(2028, 2, 29, 8, 0, tzinfo=IST)), "2028-02")

    def test_schedule_waits_for_primary_then_runs_two_retries_and_final_report(self):
        primary_end = datetime(2026, 11, 30, 6, 25, tzinfo=IST)
        cases = [
            (datetime(2026, 11, 30, 6, 0, tzinfo=IST), "waiting_for_primary", "wait_for_primary_and_immediate_recovery"),
            (datetime(2026, 11, 30, 7, 0, tzinfo=IST), "primary_closeout", "run_full_roster_closeout"),
            (datetime(2026, 11, 30, 9, 0, tzinfo=IST), "retry_1", "retry_unresolved_accounts_and_exact_targets"),
            (datetime(2026, 11, 30, 11, 0, tzinfo=IST), "retry_2", "final_targeted_retry_before_report_deadline"),
            (datetime(2026, 11, 30, 12, 30, tzinfo=IST), "final_report", "publish_final_report"),
        ]
        for now, phase, action in cases:
            result = month_end_schedule(now, primary_finished_at=primary_end)
            self.assertEqual(result["phase"], phase)
            self.assertEqual(result["action"], action)
        before = month_end_schedule(datetime(2026, 11, 30, 5, 20, tzinfo=IST))
        self.assertEqual(before["phase"], "before_reset")
        no_primary = month_end_schedule(datetime(2026, 11, 30, 7, 30, tzinfo=IST))
        self.assertEqual(no_primary["phase"], "waiting_for_primary")
        not_end = month_end_schedule(datetime(2026, 11, 29, 7, 30, tzinfo=IST), primary_finished_at=primary_end)
        self.assertEqual(not_end["phase"], "not_month_end")
        self.assertEqual(FINAL_REPORT_DEADLINE_IST.strftime("%H:%M"), "12:30")

    def _delivered_ledger(self, player_id, balance):
        return {
            player_id: {
                str(tier): {
                    "status": "Delivered",
                    "card_signature": f"milestone-{tier}-card",
                    "same_card_delivered_confirmed": True,
                    "observed_at_ist": "2026-11-30T07:30:00+05:30",
                }
                for tier in PROGRESSION_MILESTONE_THRESHOLDS if tier <= balance
            }
        }

    def test_closeout_complete_requires_fresh_balance_and_delivered_reached_tiers(self):
        roster = ["PLAYER0001", "PLAYER0002"]
        balances = {
            pid: {"observed_count": 72, "observed_date": "2026-11-30"}
            for pid in roster
        }
        ledger = {**self._delivered_ledger(roster[0], 72), **self._delivered_ledger(roster[1], 72)}
        result = evaluate_month_end_closeout(
            report_date="2026-11-30",
            configured_player_ids=roster,
            balance_records=balances,
            milestone_ledger=ledger,
            expected_roster_count=2,
        )
        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(result["totals"]["accounts_complete"], 2)
        self.assertEqual(result["totals"]["reached_and_delivered"], 18)
        self.assertEqual(result["milestone_thresholds"], list(PROGRESSION_MILESTONE_THRESHOLDS))

    def test_claimable_reached_reward_prevents_complete_and_is_actionable(self):
        roster = ["PLAYER0001"]
        balance = {"PLAYER0001": {"observed_count": 72, "observed_date": "2026-11-30"}}
        ledger = self._delivered_ledger("PLAYER0001", 72)
        ledger["PLAYER0001"]["65"] = {
            "status": "Claimable",
            "card_signature": "milestone-65-card",
            "exact_card_verified": True,
        }
        result = evaluate_month_end_closeout(
            report_date="2026-11-30", configured_player_ids=roster,
            balance_records=balance, milestone_ledger=ledger, expected_roster_count=1,
        )
        self.assertEqual(result["status"], "ATTENTION_REQUIRED")
        self.assertEqual(result["totals"]["eligible_but_unclaimed"], 1)
        self.assertIn(65, result["accounts"][0]["claimable_milestones"])

    def test_locked_or_unverified_reached_rewards_prevent_complete(self):
        roster = ["PLAYER0001"]
        balance = {"PLAYER0001": {"observed_count": 72, "observed_date": "2026-11-30"}}
        ledger = self._delivered_ledger("PLAYER0001", 72)
        ledger["PLAYER0001"]["62"] = {
            "status": "Locked", "card_signature": "milestone-62-card", "exact_card_verified": True,
        }
        ledger["PLAYER0001"]["65"] = {
            "status": "Delivered", "card_signature": "milestone-65-card", "same_card_delivered_confirmed": False,
        }
        result = evaluate_month_end_closeout(
            report_date="2026-11-30", configured_player_ids=roster,
            balance_records=balance, milestone_ledger=ledger, expected_roster_count=1,
        )
        self.assertEqual(result["status"], "ATTENTION_REQUIRED")
        self.assertEqual(result["totals"]["reached_but_locked"], 1)
        self.assertEqual(result["totals"]["reached_but_unverified"], 1)

    def test_missing_stale_balance_missing_tier_and_roster_drift_are_not_cleared(self):
        roster = ["PLAYER0001", "PLAYER0002"]
        balances = {
            "PLAYER0001": {"observed_count": 72, "observed_date": "2026-11-29"},
            "PLAYER0002": {"observed_count": None, "observed_date": None},
        }
        result = evaluate_month_end_closeout(
            report_date="2026-11-30", configured_player_ids=roster,
            balance_records=balances, milestone_ledger={},
            expected_roster_count=3,
        )
        self.assertEqual(result["status"], "ATTENTION_REQUIRED")
        self.assertIn("configured_roster_count_mismatch", result["roster_issues"])
        self.assertEqual(result["totals"]["balances_missing_or_stale"], 2)
        self.assertGreater(result["totals"]["reached_but_unverified"], 0)

    def test_non_month_end_is_not_a_closeout(self):
        result = evaluate_month_end_closeout(
            report_date="2026-11-29", configured_player_ids=["PLAYER0001"],
            balance_records={}, milestone_ledger={}, expected_roster_count=1,
        )
        self.assertEqual(result["status"], "NOT_MONTH_END")


if __name__ == "__main__":
    unittest.main()
