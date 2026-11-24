"""Read-only roll-up of full-cycle, Grenade and Progression inspection status.

This module only formats evidence already present in the run results and runtime
state. It does not decide which rewards to claim or resolve any queue entries.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
from html import escape
from typing import Any

from grenade_balance import summarize_saved_progression_balance

MAX_PROGRESS_INSPECTION_SLOTS_PER_RESET = 4


def _count(value: Any) -> int:
    try:
        if isinstance(value, bool):
            return 0
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _dedupe_ids(values: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        pid = str(value or "").strip()
        if pid and pid not in seen:
            seen.add(pid)
            result.append(pid)
    return result


def build_cycle_health_overview(
    player_results: list[dict[str, Any]],
    *,
    configured_player_ids: list[str],
    reset_date: str,
    grenade_state: dict[str, Any],
    daily_cycle_summary: dict[str, Any] | None,
    progression_queue: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build a privacy-minimised overview using the configured roster as denominator."""
    roster = _dedupe_ids(configured_player_ids)
    configured = len(roster)
    by_pid = {
        str(row.get("pid") or "").strip(): row
        for row in (player_results or [])
        if isinstance(row, dict) and str(row.get("pid") or "").strip()
    }

    cycle = daily_cycle_summary if isinstance(daily_cycle_summary, dict) else {}
    cycle_complete_count = _count(cycle.get("accounts_complete"))
    cycle_pending_count = _count(cycle.get("accounts_pending"))
    cycle_valid = (
        str(cycle.get("reset_date") or "") == str(reset_date)
        and _count(cycle.get("configured_player_count")) == configured
        and configured > 0
        and cycle_complete_count <= configured
        and cycle_pending_count == configured - cycle_complete_count
    )
    core_cycle = {
        "available": cycle_valid,
        "reset_date": str(cycle.get("reset_date") or ""),
        "configured": configured,
        "complete": cycle_complete_count if cycle_valid else 0,
        "pending": cycle_pending_count if cycle_valid else configured,
        "source_run_label": str(cycle.get("primary_run_label") or "") if cycle_valid else "",
        "source_timestamp": str(cycle.get("primary_run_timestamp") or "") if cycle_valid else "",
    }

    grenade = Counter()
    for pid in roster:
        row = by_pid.get(pid, {})
        live = row.get("grenade_balance")
        status = None
        observed_date = None
        if isinstance(live, dict) and live.get("current_count") is not None:
            # A balance captured by this run is a current-reset observation.
            status = str(live.get("status") or "not_validated")
            observed_date = str(reset_date)
        else:
            saved = summarize_saved_progression_balance(
                grenade_state, pid, reset_date=date.fromisoformat(reset_date)
            ) if isinstance(grenade_state, dict) else None
            if isinstance(saved, dict):
                observed_date = str(saved.get("last_observed_date") or "")
                status = str(saved.get("status_at_observation") or "not_validated")
        if observed_date != str(reset_date):
            grenade["not_checked_today"] += 1
            continue
        grenade["observed_today"] += 1
        if status == "pass":
            grenade["passed_today"] += 1
        elif status == "below_minimum":
            grenade["below_minimum_today"] += 1
        elif status == "counter_regressed":
            grenade["counter_regressed_today"] += 1
        else:
            grenade["other_status_today"] += 1

    queue = progression_queue if isinstance(progression_queue, dict) else {}
    roster_set = set(roster)
    pending_entries: list[tuple[str, dict[str, Any]]] = []
    pending_outside_roster = 0
    for raw_pid, entry in queue.items():
        if not isinstance(entry, dict) or entry.get("status") not in {"deferred", "claim_unconfirmed"}:
            continue
        pid = str(raw_pid or "").strip()
        if pid not in roster_set:
            pending_outside_roster += 1
            continue
        pending_entries.append((pid, entry))

    reasons = Counter()
    attempt_counts = Counter()
    latest_attempt_at = ""
    latest_attempt_slot = ""
    for _pid, entry in pending_entries:
        if entry.get("status") == "claim_unconfirmed":
            # An attempted claim with no portal confirmation is materially
            # different from a deferred inspection that never clicked.
            reasons["claim_unconfirmed"] += 1
        else:
            reason = str(entry.get("last_reason") or "").lower()
            if "no currently visible claimable reward" in reason:
                reasons["no_visible_claimable_card"] += 1
            elif "popup/overlay" in reason or "fullscreen_overlay" in reason:
                reasons["blocked_by_overlay"] += 1
            else:
                reasons["other_pending"] += 1
        slots = entry.get("attempted_slots")
        slots = list(dict.fromkeys(
            str(slot).strip() for slot in slots
            if isinstance(slot, str) and slot.strip()
        )) if isinstance(slots, list) else []
        attempt_counts[len(slots)] += 1
        slot = str(entry.get("last_attempt_slot") or "").strip()
        attempted_at = str(entry.get("last_attempt_at") or "").strip()
        # Queue timestamps are ISO-8601 strings; lexical ordering is stable
        # for the consistently formatted timestamps written by the claimer.
        if slot and (not latest_attempt_at or attempted_at >= latest_attempt_at):
            latest_attempt_at = attempted_at
            latest_attempt_slot = slot

    most_common_attempt_count = (
        attempt_counts.most_common(1)[0][0] if attempt_counts else 0
    )
    accounts_at_common_attempt_count = (
        attempt_counts[most_common_attempt_count] if attempt_counts else 0
    )
    progression = {
        "pending": len(pending_entries),
        "no_visible_claimable_card": reasons["no_visible_claimable_card"],
        "blocked_by_overlay": reasons["blocked_by_overlay"],
        "claim_unconfirmed": reasons["claim_unconfirmed"],
        "other_pending": reasons["other_pending"],
        "outside_current_roster": pending_outside_roster,
        "most_common_slots_used": most_common_attempt_count,
        "accounts_at_most_common_slots_used": accounts_at_common_attempt_count,
        "max_slots_per_reset": MAX_PROGRESS_INSPECTION_SLOTS_PER_RESET,
        "latest_attempt_at": latest_attempt_at,
        "latest_attempt_slot": latest_attempt_slot,
    }

    return {
        "reset_date": str(reset_date),
        "configured": configured,
        "core_cycle": core_cycle,
        "grenade": {
            "observed_today": grenade["observed_today"],
            "passed_today": grenade["passed_today"],
            "below_minimum_today": grenade["below_minimum_today"],
            "counter_regressed_today": grenade["counter_regressed_today"],
            "other_status_today": grenade["other_status_today"],
            "not_checked_today": grenade["not_checked_today"],
        },
        "progression": progression,
    }


def render_cycle_health_overview(snapshot: dict[str, Any] | None) -> str:
    """Render the same explicit status distinctions for email and Copy Report."""
    if not isinstance(snapshot, dict) or _count(snapshot.get("configured")) <= 0:
        return ""
    configured = _count(snapshot.get("configured"))
    cycle = snapshot.get("core_cycle") if isinstance(snapshot.get("core_cycle"), dict) else {}
    grenade = snapshot.get("grenade") if isinstance(snapshot.get("grenade"), dict) else {}
    progression = snapshot.get("progression") if isinstance(snapshot.get("progression"), dict) else {}

    if cycle.get("available"):
        core_text = (
            f'{_count(cycle.get("complete"))}/{configured} accounts complete'
            f' · {_count(cycle.get("pending"))} pending'
        )
        source = str(cycle.get("source_run_label") or "Full-roster run")
        timestamp = str(cycle.get("source_timestamp") or "")
        if timestamp:
            source += f" · {timestamp}"
        core_note = f"Last full-roster cycle: {source}"
    else:
        core_text = "No verified full-roster cycle recorded for this reset"
        core_note = "This run's account count is not a substitute for full-roster coverage."

    grenade_text = (
        f'{_count(grenade.get("passed_today"))} PASS today'
        f' · {_count(grenade.get("below_minimum_today"))} below minimum'
        f' · {_count(grenade.get("counter_regressed_today"))} counter regressions'
        f' · {_count(grenade.get("other_status_today"))} other / baseline-pending'
        f' · {_count(grenade.get("not_checked_today"))} not checked today'
    )
    grenade_note = (
        f'{_count(grenade.get("observed_today"))}/{configured} accounts have a balance '
        "observation dated today. Older saved readings are not counted as current passes."
    )

    pending = _count(progression.get("pending"))
    pp_text = (
        f'{pending}/{configured} inspections pending'
        f' · {_count(progression.get("no_visible_claimable_card"))} with no visible claimable card'
        f' · {_count(progression.get("blocked_by_overlay"))} blocked by overlay'
        f' · {_count(progression.get("claim_unconfirmed"))} with claim outcome unconfirmed'
        f' · {_count(progression.get("other_pending"))} other pending'
    )
    slots_used = _count(progression.get("most_common_slots_used"))
    slots_note = ""
    if pending and slots_used:
        left = max(0, _count(progression.get("max_slots_per_reset")) - slots_used)
        slots_note = (
            f' For {_count(progression.get("accounts_at_most_common_slots_used"))} pending accounts, '
            f'the most common usage is {slots_used}/'
            f'{_count(progression.get("max_slots_per_reset"))} inspection slots this reset'
            f' ({left} remaining for those accounts).'
        )
    outside_roster_note = ""
    outside_roster = _count(progression.get("outside_current_roster"))
    if outside_roster:
        outside_roster_note = (
            f" {outside_roster} queued record(s) outside the current roster are excluded "
            "from the account count."
        )
    pp_note = (
        "Pending inspections are not automatically failed claims. An empty scan is not "
        "proof that no Progression reward exists; an unconfirmed claim outcome is shown "
        "separately from a deferred inspection."
        + slots_note
        + outside_roster_note
    )

    rows = [
        ("Core Daily + Store coverage", core_text, core_note),
        ("Grenade balance validation", grenade_text, grenade_note),
        ("Progression inspection queue", pp_text, pp_note),
    ]
    row_html = "".join(
        "<tr><td style='padding:8px 10px;border-top:1px solid #e2e8f0;vertical-align:top;width:34%'>"
        f"<strong>{escape(label)}</strong></td>"
        "<td style='padding:8px 10px;border-top:1px solid #e2e8f0;vertical-align:top'>"
        f"<strong>{escape(value)}</strong><br>"
        f"<span style='font-size:11px;color:#64748b;line-height:1.45'>{escape(note)}</span>"
        "</td></tr>"
        for label, value, note in rows
    )
    return (
        "<section class='section' style='margin-bottom:14px;padding:15px;border:1px solid #e2e8f0;border-radius:10px;background:#fff'>"
        "<h2 style='font-size:15px;margin:0 0 8px'>Overall reset-cycle health</h2>"
        f"<p style='font-size:11px;color:#64748b;margin:0 0 8px'>Reset date: {escape(str(snapshot.get('reset_date') or '—'))} · "
        "The cycle, balance and inspection figures are separate measures.</p>"
        "<table class='cycle-health-table' role='presentation' width='100%' cellpadding='0' cellspacing='0' border='0'>"
        f"{row_html}</table></section>"
    )
