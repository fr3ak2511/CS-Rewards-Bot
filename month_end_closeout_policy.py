"""Pure policy for the monthly Progression Program reset closeout.

This module decides calendar windows and evaluates supplied, already-verified
evidence. It does not dispatch workflows, open browser sessions, click rewards,
or mutate runtime state.
"""
from __future__ import annotations

from collections import Counter
import calendar
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from grenade_balance import PROGRESSION_MILESTONE_THRESHOLDS

IST = ZoneInfo("Asia/Kolkata")
RESET_TIME_IST = time(5, 30)
PRIMARY_CLOSEOUT_TIME_IST = time(7, 0)
RETRY_ONE_TIME_IST = time(9, 0)
RETRY_TWO_TIME_IST = time(11, 0)
FINAL_REPORT_DEADLINE_IST = time(12, 30)

_DELIVERY_STATES = {"delivered", "claimable", "locked", "unverified"}


def _to_ist(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=IST) if value.tzinfo is None else value.astimezone(IST)
    if isinstance(value, date):
        return datetime.combine(value, time.min, tzinfo=IST)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            try:
                parsed_date = date.fromisoformat(text)
            except ValueError:
                return None
            return datetime.combine(parsed_date, time.min, tzinfo=IST)
        return parsed.replace(tzinfo=IST) if parsed.tzinfo is None else parsed.astimezone(IST)
    return None


def is_last_calendar_day(value: Any) -> bool:
    """True only for the final calendar date of the month, including leap years."""
    parsed = _to_ist(value)
    if parsed is None:
        return False
    return parsed.date().day == calendar.monthrange(parsed.year, parsed.month)[1]


def month_cycle_key(value: Any) -> str | None:
    parsed = _to_ist(value)
    return parsed.strftime("%Y-%m") if parsed else None


def month_end_schedule(
    value: Any,
    *,
    primary_finished_at: Any = None,
    immediate_recovery_finished_at: Any = None,
) -> dict[str, Any]:
    """Return the due phase for the last day; all timestamps are IST-aware."""
    now = _to_ist(value)
    if now is None:
        return {"phase": "invalid_time", "action": "no_action", "is_month_end": False}
    day = now.date()
    month_end = is_last_calendar_day(now)
    deadlines = {
        "primary_closeout": PRIMARY_CLOSEOUT_TIME_IST.strftime("%H:%M"),
        "retry_1": RETRY_ONE_TIME_IST.strftime("%H:%M"),
        "retry_2": RETRY_TWO_TIME_IST.strftime("%H:%M"),
        "final_report": FINAL_REPORT_DEADLINE_IST.strftime("%H:%M"),
    }
    if not month_end:
        return {
            "phase": "not_month_end",
            "action": "no_action",
            "is_month_end": False,
            "cycle_key": month_cycle_key(now),
            "deadlines_ist": deadlines,
        }
    if now.timetz().replace(tzinfo=None) < RESET_TIME_IST:
        return {
            "phase": "before_reset",
            "action": "wait_for_monthly_reset",
            "is_month_end": True,
            "cycle_key": month_cycle_key(now),
            "deadlines_ist": deadlines,
        }
    primary_end = _to_ist(primary_finished_at)
    recovery_end = _to_ist(immediate_recovery_finished_at)
    if now >= datetime.combine(day, FINAL_REPORT_DEADLINE_IST, tzinfo=IST):
        return {
            "phase": "final_report",
            "action": "publish_final_report",
            "is_month_end": True,
            "cycle_key": month_cycle_key(now),
            "deadline_at_ist": datetime.combine(day, FINAL_REPORT_DEADLINE_IST, tzinfo=IST).isoformat(),
            "deadlines_ist": deadlines,
        }
    if primary_end is None or primary_end > now:
        return {
            "phase": "waiting_for_primary",
            "action": "wait_for_primary_and_immediate_recovery",
            "is_month_end": True,
            "cycle_key": month_cycle_key(now),
            "deadlines_ist": deadlines,
        }
    if recovery_end is None or recovery_end > now:
        return {
            "phase": "waiting_for_immediate_recovery",
            "action": "wait_for_immediate_recovery_completion",
            "is_month_end": True,
            "cycle_key": month_cycle_key(now),
            "deadlines_ist": deadlines,
        }
    primary_due = datetime.combine(day, PRIMARY_CLOSEOUT_TIME_IST, tzinfo=IST)
    retry_one_due = datetime.combine(day, RETRY_ONE_TIME_IST, tzinfo=IST)
    retry_two_due = datetime.combine(day, RETRY_TWO_TIME_IST, tzinfo=IST)
    if now < primary_due:
        phase, action = "waiting_for_closeout_start", "wait_until_0700"
        next_at = primary_due
    elif now < retry_one_due:
        phase, action = "primary_closeout", "run_full_roster_closeout"
        next_at = retry_one_due
    elif now < retry_two_due:
        phase, action = "retry_1", "retry_unresolved_accounts_and_exact_targets"
        next_at = retry_two_due
    else:
        phase, action = "retry_2", "final_targeted_retry_before_report_deadline"
        next_at = datetime.combine(day, FINAL_REPORT_DEADLINE_IST, tzinfo=IST)
    return {
        "phase": phase,
        "action": action,
        "is_month_end": True,
        "cycle_key": month_cycle_key(now),
        "next_checkpoint_at_ist": next_at.isoformat(),
        "deadline_at_ist": datetime.combine(day, FINAL_REPORT_DEADLINE_IST, tzinfo=IST).isoformat(),
        "deadlines_ist": deadlines,
    }


def _milestone_entry(ledger: dict[str, Any], player_id: str, threshold: int) -> dict[str, Any]:
    player = ledger.get(player_id, {})
    if not isinstance(player, dict):
        return {}
    value = player.get(str(threshold), player.get(threshold, {}))
    return value if isinstance(value, dict) else ({"status": value} if isinstance(value, str) else {})


def _verified_milestone_state(
    entry: dict[str, Any],
    *,
    report_date: date,
) -> tuple[str, str | None]:
    observed_at = _to_ist(entry.get("observed_at_ist"))
    if observed_at is None or observed_at.date() != report_date:
        return "unverified", "milestone_not_observed_on_closeout_date"
    raw_status = str(entry.get("status") or "").strip().casefold()
    if raw_status not in _DELIVERY_STATES:
        return "unverified", "missing_or_unknown_status"
    signature = str(entry.get("card_signature") or "").strip()
    if raw_status == "delivered":
        if entry.get("same_card_delivered_confirmed") is True and signature:
            return "delivered", None
        return "unverified", "delivered_without_same_card_confirmation"
    if raw_status in {"claimable", "locked"}:
        if entry.get("exact_card_verified") is True and signature:
            return raw_status, None
        return "unverified", "card_identity_not_verified"
    return "unverified", str(entry.get("reason") or "inspection_not_conclusive")


def evaluate_month_end_closeout(
    *,
    report_date: Any,
    configured_player_ids: list[str] | tuple[str, ...],
    balance_records: dict[str, Any],
    milestone_ledger: dict[str, Any],
    expected_roster_count: int = 36,
) -> dict[str, Any]:
    """Evaluate whether all 36 balances are fresh and every reached reward is Delivered.

    Balance thresholds define which milestones must be checked. The balance never
    proves delivery. Only a same-card Delivered confirmation can clear a reached tier.
    """
    parsed = _to_ist(report_date)
    if parsed is None:
        return {"status": "ATTENTION_REQUIRED", "reason": "invalid_report_date"}
    as_of = parsed.date()
    cycle_key = as_of.strftime("%Y-%m")
    roster = [str(pid or "").strip() for pid in (configured_player_ids or []) if str(pid or "").strip()]
    duplicates = sorted({pid for pid in roster if roster.count(pid) > 1})
    roster_issues = []
    if duplicates:
        roster_issues.append("duplicate_player_ids")
    if len(roster) != int(expected_roster_count):
        roster_issues.append("configured_roster_count_mismatch")
    if not is_last_calendar_day(parsed):
        return {
            "status": "NOT_MONTH_END",
            "cycle_key": cycle_key,
            "report_date": as_of.isoformat(),
            "reason": "report_date_is_not_last_calendar_day",
        }

    balances = balance_records if isinstance(balance_records, dict) else {}
    ledger = milestone_ledger if isinstance(milestone_ledger, dict) else {}
    accounts = []
    totals = Counter()
    for pid in dict.fromkeys(roster):
        raw_balance = balances.get(pid)
        balance = raw_balance if isinstance(raw_balance, dict) else {}
        try:
            count = int(balance.get("observed_count"))
            if count < 0:
                count = None
        except (TypeError, ValueError, OverflowError):
            count = None
        observed_date = str(balance.get("observed_date") or "")[:10]
        fresh = count is not None and observed_date == as_of.isoformat()
        if not fresh:
            totals["balances_missing_or_stale"] += 1

        reached = [
            threshold for threshold in PROGRESSION_MILESTONE_THRESHOLDS
            if count is not None and count >= threshold
        ]
        states = []
        claimable = []
        attention = []
        for threshold in reached:
            entry = _milestone_entry(ledger, pid, threshold)
            status, reason = _verified_milestone_state(entry, report_date=as_of)
            item = {
                "threshold": threshold,
                "status": status,
                "card_signature": str(entry.get("card_signature") or "")[:200] or None,
                "evidence_at_ist": str(entry.get("observed_at_ist") or "")[:40] or None,
                "reason": reason,
            }
            states.append(item)
            if status == "claimable":
                claimable.append(threshold)
            if status != "delivered":
                attention.append(threshold)
                if status == "locked":
                    totals["reached_but_locked"] += 1
                elif status == "claimable":
                    totals["eligible_but_unclaimed"] += 1
                else:
                    totals["reached_but_unverified"] += 1
            else:
                totals["reached_and_delivered"] += 1

        account_complete = fresh and not attention
        if account_complete:
            totals["accounts_complete"] += 1
        else:
            totals["accounts_attention_required"] += 1
        accounts.append({
            "player_id_last4": pid[-4:],
            "balance": count,
            "balance_observed_date": observed_date or None,
            "balance_fresh_for_closeout": fresh,
            "milestones_reached_by_balance": reached,
            "milestone_states": states,
            "claimable_milestones": claimable,
            "milestones_needing_attention": attention,
            "status": "COMPLETE" if account_complete else "ATTENTION_REQUIRED",
        })

    complete = (
        not roster_issues
        and len(roster) == int(expected_roster_count)
        and totals["accounts_complete"] == int(expected_roster_count)
        and totals["eligible_but_unclaimed"] == 0
        and totals["reached_but_unverified"] == 0
        and totals["reached_but_locked"] == 0
    )
    return {
        "status": "COMPLETE" if complete else "ATTENTION_REQUIRED",
        "cycle_key": cycle_key,
        "report_date": as_of.isoformat(),
        "final_report_deadline_ist": datetime.combine(as_of, FINAL_REPORT_DEADLINE_IST, tzinfo=IST).isoformat(),
        "milestone_thresholds": list(PROGRESSION_MILESTONE_THRESHOLDS),
        "configured_account_count": len(roster),
        "expected_roster_count": int(expected_roster_count),
        "roster_issues": roster_issues,
        "totals": {
            "accounts_complete": totals["accounts_complete"],
            "accounts_attention_required": totals["accounts_attention_required"],
            "balances_missing_or_stale": totals["balances_missing_or_stale"],
            "reached_and_delivered": totals["reached_and_delivered"],
            "eligible_but_unclaimed": totals["eligible_but_unclaimed"],
            "reached_but_unverified": totals["reached_but_unverified"],
            "reached_but_locked": totals["reached_but_locked"],
        },
        "accounts": accounts,
        "completion_rule": "All configured accounts need a fresh closeout-date balance, and every milestone reached by that balance must have same-card Delivered confirmation.",
    }


__all__ = [
    "IST",
    "RESET_TIME_IST",
    "PRIMARY_CLOSEOUT_TIME_IST",
    "RETRY_ONE_TIME_IST",
    "RETRY_TWO_TIME_IST",
    "FINAL_REPORT_DEADLINE_IST",
    "is_last_calendar_day",
    "month_cycle_key",
    "month_end_schedule",
    "evaluate_month_end_closeout",
]
