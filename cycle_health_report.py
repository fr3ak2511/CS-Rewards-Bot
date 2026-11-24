"""Read-only roll-up of full-cycle, Grenade and Progression inspection status.

This module only formats evidence already present in the run results and runtime
state. It does not decide which rewards to claim or resolve any queue entries.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
from html import escape
from typing import Any

from grenade_balance import (
    PROGRESSION_MILESTONE_THRESHOLDS,
    eligible_cycle_days,
    summarize_saved_progression_balance,
)

MAX_PROGRESS_INSPECTION_SLOTS_PER_RESET = 4


def _count(value: Any) -> int:
    try:
        if isinstance(value, bool):
            return 0
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _is_nonnegative_int(value: Any) -> bool:
    """Accept only persisted integer evidence, not booleans or coerced strings."""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


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
    full_claim_coverage: dict[str, Any] | None = None,
    player_display_names: dict[str, str] | None = None,
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
    cycle_configured_count = cycle.get("configured_player_count")
    cycle_complete_raw = cycle.get("accounts_complete")
    cycle_pending_raw = cycle.get("accounts_pending")
    coverage_derived = False
    if cycle_complete_raw is None and cycle_pending_raw is None and isinstance(full_claim_coverage, dict):
        # Progression-only recovery intentionally skips the cycle-summary
        # updater. In that path, rebuild the counts read-only from the same
        # per-account coverage evidence used by update_daily_cycle_summary().
        cycle_complete_raw = sum(
            1 for pid in roster
            if (
                isinstance(full_claim_coverage.get(pid), dict)
                and full_claim_coverage[pid].get("reset_date") == str(reset_date)
                and full_claim_coverage[pid].get("status") == "complete"
                and not full_claim_coverage[pid].get("retry_required", False)
            )
        )
        cycle_pending_raw = configured - cycle_complete_raw
        coverage_derived = True
    cycle_complete_count = _count(cycle_complete_raw)
    cycle_pending_count = _count(cycle_pending_raw)
    cycle_valid = (
        str(cycle.get("reset_date") or "") == str(reset_date)
        and _is_nonnegative_int(cycle_configured_count)
        and cycle_configured_count == configured
        and configured > 0
        and _is_nonnegative_int(cycle_complete_raw)
        and _is_nonnegative_int(cycle_pending_raw)
        and cycle_complete_count <= configured
        and cycle_pending_count == configured - cycle_complete_count
    )
    core_cycle = {
        "available": cycle_valid,
        "reset_date": str(cycle.get("reset_date") or ""),
        "configured": configured,
        "complete": cycle_complete_count if cycle_valid else 0,
        "pending": cycle_pending_count if cycle_valid else configured,
        "coverage_derived": bool(cycle_valid and coverage_derived),
        "source_run_label": str(cycle.get("primary_run_label") or "") if cycle_valid else "",
        "source_timestamp": str(cycle.get("primary_run_timestamp") or "") if cycle_valid else "",
    }

    # Build a roster-complete balance register. Live readings override saved
    # observations for the same account; saved readings retain their own date
    # and original expected floor and are never presented as fresh.
    grenade = Counter()
    balance_accounts: list[dict[str, Any]] = []
    balance_state = grenade_state if isinstance(grenade_state, dict) else {}
    cycle_states = balance_state.get("cycles")
    current_cycle_state = (
        cycle_states.get(str(reset_date)[:7], {})
        if isinstance(cycle_states, dict) else {}
    )
    cycle_players = (
        current_cycle_state.get("players", {})
        if isinstance(current_cycle_state, dict) else {}
    )
    if not isinstance(cycle_players, dict):
        cycle_players = {}
    display_names = player_display_names if isinstance(player_display_names, dict) else {}
    as_of_date = date.fromisoformat(str(reset_date))

    for pid in roster:
        row = by_pid.get(pid, {})
        live = row.get("grenade_balance")
        observed_count = None
        observed_date = None
        observed_at = None
        expected_at_observation = None
        expected_as_of = None
        status = "not_observed"
        source = "no_saved_observation"
        portal_next_milestone = None
        baseline_status = None

        if isinstance(live, dict) and live.get("current_count") is not None:
            try:
                observed_count = int(live.get("current_count"))
            except (TypeError, ValueError, OverflowError):
                observed_count = None
            if observed_count is not None and observed_count >= 0:
                observed_date = str(live.get("observation_date") or reset_date)
                observed_at = live.get("observed_at_ist")
                expected_at_observation = live.get("expected_minimum")
                expected_as_of = live.get("expected_minimum")
                status = str(live.get("status") or "not_validated")
                portal_next_milestone = live.get("next_milestone")
                source = "live_run"
            else:
                observed_count = None

        saved = None
        if observed_count is None:
            saved = summarize_saved_progression_balance(
                balance_state, pid, reset_date=as_of_date
            )
            if isinstance(saved, dict):
                try:
                    observed_count = int(saved.get("last_observed_count"))
                except (TypeError, ValueError, OverflowError):
                    observed_count = None
                observed_date = str(saved.get("last_observed_date") or "") or None
                observed_at = saved.get("last_observed_at_ist")
                expected_at_observation = saved.get("expected_minimum_at_observation")
                expected_as_of = saved.get("expected_minimum_as_of")
                status = str(saved.get("status_at_observation") or "status_not_retained")
                portal_next_milestone = saved.get("next_milestone_at_observation")
                source = "saved_portal_observation"

        entry = cycle_players.get(pid)
        entry = entry if isinstance(entry, dict) else {}
        baseline_status = str(entry.get("baseline_status") or "")
        if expected_as_of is None and baseline_status == "active":
            try:
                baseline_count = int(entry.get("baseline_count", 0))
                tracking_start = date.fromisoformat(str(entry.get("tracking_start_date") or ""))
                expected_as_of = baseline_count + 3 * eligible_cycle_days(
                    tracking_start, as_of_date
                )
            except (TypeError, ValueError, OverflowError):
                expected_as_of = None
        if observed_count is None and baseline_status == "pending_baseline":
            status = "pending_baseline"
            source = "baseline_pending"

        observation_day = None
        observation_age_days = None
        if observed_date:
            try:
                observation_day = date.fromisoformat(observed_date)
                if observation_day <= as_of_date:
                    observation_age_days = (as_of_date - observation_day).days
            except ValueError:
                observation_day = None

        comparison = "not_observed"
        difference = None
        if observed_count is not None and expected_at_observation is not None:
            try:
                expected_number = int(expected_at_observation)
                difference = observed_count - expected_number
                if difference < 0:
                    comparison = "below_expected"
                elif difference == 0:
                    comparison = "at_expected"
                else:
                    comparison = "above_expected"
            except (TypeError, ValueError, OverflowError):
                comparison = "baseline_pending"
        elif baseline_status == "pending_baseline" or status == "pending_baseline":
            comparison = "baseline_pending"

        next_threshold = None
        reached_thresholds: list[int] = []
        if observed_count is not None:
            reached_thresholds = [
                threshold for threshold in PROGRESSION_MILESTONE_THRESHOLDS
                if observed_count >= threshold
            ]
            next_threshold = next(
                (
                    threshold for threshold in PROGRESSION_MILESTONE_THRESHOLDS
                    if observed_count < threshold
                ),
                None,
            )
        mismatch = (
            portal_next_milestone is not None
            and next_threshold is not None
            and str(portal_next_milestone) != str(next_threshold)
        )
        balance_accounts.append({
            "player_id": pid,
            "display_name": str(display_names.get(pid) or "").strip(),
            "last_four": pid[-4:],
            "observed_count": observed_count,
            "observed_date": observed_date,
            "observed_at_ist": observed_at,
            "observation_age_days": observation_age_days,
            "fresh_today": bool(observation_day == as_of_date),
            "expected_minimum_at_observation": expected_at_observation,
            "expected_minimum_as_of": expected_as_of,
            "difference_at_observation": difference,
            "comparison": comparison,
            "validation_status": status,
            "source": source,
            "milestones_reached_by_balance": reached_thresholds,
            "milestone_count_reached_by_balance": len(reached_thresholds),
            "next_milestone_from_reference": next_threshold,
            "portal_next_milestone": portal_next_milestone,
            "portal_ladder_mismatch": bool(mismatch),
            # Balance alone never proves card delivery.
            "delivery_status": "not_verified_by_balance",
        })

        if observation_day != as_of_date:
            grenade["not_checked_today"] += 1
        else:
            grenade["observed_today"] += 1
            if status == "pass":
                grenade["passed_today"] += 1
            elif status == "below_minimum":
                grenade["below_minimum_today"] += 1
            elif status == "counter_regressed":
                grenade["counter_regressed_today"] += 1
            else:
                grenade["other_status_today"] += 1

    group_order = {
        "below_expected": 0,
        "at_expected": 1,
        "above_expected": 2,
        "baseline_pending": 3,
        "not_observed": 4,
    }
    balance_accounts.sort(
        key=lambda item: (
            group_order.get(item["comparison"], 9),
            item.get("display_name", "").casefold(),
            item["player_id"],
        )
    )

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
        if slot and attempted_at and (not latest_attempt_at or attempted_at >= latest_attempt_at):
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
        "milestone_thresholds": list(PROGRESSION_MILESTONE_THRESHOLDS),
        "grenade": {
            "observed_today": grenade["observed_today"],
            "passed_today": grenade["passed_today"],
            "below_minimum_today": grenade["below_minimum_today"],
            "counter_regressed_today": grenade["counter_regressed_today"],
            "other_status_today": grenade["other_status_today"],
            "not_checked_today": grenade["not_checked_today"],
            "accounts": balance_accounts,
            "accounts_at_expected": sum(1 for item in balance_accounts if item["comparison"] == "at_expected"),
            "accounts_above_expected": sum(1 for item in balance_accounts if item["comparison"] == "above_expected"),
            "accounts_below_expected": sum(1 for item in balance_accounts if item["comparison"] == "below_expected"),
            "accounts_baseline_pending": sum(1 for item in balance_accounts if item["comparison"] == "baseline_pending"),
            "accounts_without_observation": sum(1 for item in balance_accounts if item["comparison"] == "not_observed"),
            "accounts_with_ladder_mismatch": sum(1 for item in balance_accounts if item["portal_ladder_mismatch"]),
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
        if cycle.get("coverage_derived"):
            core_note += " · Account counts re-derived from saved full-claim coverage records"
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
        "Pending inspections are not automatically failed claims. An empty scan is not treated "
        "as proof that no Progression reward exists; an unconfirmed claim outcome is shown "
        "separately from a deferred inspection."
        + slots_note
        + outside_roster_note
    )

    milestone_thresholds = snapshot.get("milestone_thresholds")
    if not isinstance(milestone_thresholds, list) or not milestone_thresholds:
        milestone_thresholds = list(PROGRESSION_MILESTONE_THRESHOLDS)
    milestone_cells = []
    for threshold in milestone_thresholds:
        try:
            value = int(threshold)
        except (TypeError, ValueError, OverflowError):
            continue
        milestone_cells.append(
            "<td style='padding:5px 3px;text-align:center;border:1px solid #e2e8f0;"
            "border-radius:4px;font-size:12px;font-weight:700'>"
            f"{value:,}</td>"
        )
    milestone_rows = []
    for start in range(0, len(milestone_cells), 5):
        cells = milestone_cells[start:start + 5]
        cells += ["<td style='padding:5px 3px'></td>"] * (5 - len(cells))
        milestone_rows.append("<tr>" + "".join(cells) + "</tr>")
    milestone_reference_html = (
        "<section style='margin-top:12px;padding-top:10px;border-top:1px solid #e2e8f0'>"
        "<strong style='font-size:13px'>Permanent PP milestone reference</strong>"
        "<p style='font-size:11px;color:#64748b;margin:4px 0 7px'>25 monthly Grenade thresholds. "
        "A balance reaching a threshold does not prove its reward card was Delivered.</p>"
        "<table role='presentation' width='100%' cellspacing='4' cellpadding='0' border='0'>"
        + "".join(milestone_rows)
        + "</table></section>"
    )

    accounts = grenade.get("accounts") if isinstance(grenade.get("accounts"), list) else []
    comparison_groups = [
        ("below_expected", "Below expected at last observation"),
        ("at_expected", "Exactly at expected minimum"),
        ("above_expected", "Above expected minimum"),
        ("baseline_pending", "Baseline pending"),
        ("not_observed", "No saved balance observation"),
    ]
    fresh_count = sum(1 for item in accounts if item.get("fresh_today"))
    older_count = sum(
        1 for item in accounts
        if item.get("observed_count") is not None and not item.get("fresh_today")
    )
    no_observation_count = sum(
        1 for item in accounts if item.get("observed_count") is None
    )
    register_rows = []
    for comparison, heading in comparison_groups:
        members = [
            item for item in accounts
            if item.get("comparison") == comparison
        ]
        if not members:
            continue
        cards = []
        for item in members:
            player_id = str(item.get("player_id") or "")
            display_name = str(item.get("display_name") or "").strip()
            name = display_name or f"Player …{str(item.get('last_four') or player_id[-4:])}"
            actual = "—" if item.get("observed_count") is None else f"{_count(item.get('observed_count')):,}"
            expected_then = (
                "Pending"
                if item.get("expected_minimum_at_observation") is None
                else f"{_count(item.get('expected_minimum_at_observation')):,}"
            )
            expected_today = (
                "—"
                if item.get("expected_minimum_as_of") is None
                else f"{_count(item.get('expected_minimum_as_of')):,}"
            )
            delta = item.get("difference_at_observation")
            if delta is None:
                delta_text = "—"
            elif int(delta) > 0:
                delta_text = f"+{int(delta):,}"
            elif int(delta) < 0:
                delta_text = f"{int(delta):,}"
            else:
                delta_text = "0"
            observed_date = str(item.get("observed_date") or "No observation")
            age = item.get("observation_age_days")
            freshness = (
                "Fresh today"
                if item.get("fresh_today")
                else f"{_count(age)} day(s) old"
                if age is not None
                else "Not observed"
            )
            next_milestone = item.get("next_milestone_from_reference")
            next_text = "All thresholds reached" if next_milestone is None and item.get("observed_count") is not None else (
                "—" if next_milestone is None else f"{_count(next_milestone):,}"
            )
            reached = _count(item.get("milestone_count_reached_by_balance"))
            validation = str(item.get("validation_status") or "not_observed").replace("_", " ")
            if item.get("portal_ladder_mismatch"):
                validation += " · portal threshold differs from reference"
            cards.append(
                "<tr><td style='padding:9px 10px;border-top:1px solid #e2e8f0'>"
                "<table role='presentation' width='100%' cellspacing='0' cellpadding='0' border='0'>"
                "<tr><td style='font-size:13px;font-weight:700;overflow-wrap:anywhere'>"
                f"{escape(name)}</td><td align='right' style='font-size:12px;font-weight:700'>"
                f"Balance: {escape(actual)}</td></tr>"
                "<tr><td colspan='2' style='font-size:10px;color:#64748b;overflow-wrap:anywhere'>"
                f"Player ID: {escape(player_id or 'unavailable')}</td></tr>"
                "<tr><td colspan='2' style='font-size:11px;padding-top:4px'>"
                f"Expected at reading: {escape(expected_then)} · Difference: {escape(delta_text)}"
                f" · Expected today: {escape(expected_today)}</td></tr>"
                "<tr><td colspan='2' style='font-size:11px;padding-top:3px'>"
                f"Last reading: {escape(observed_date)} ({escape(freshness)})"
                f" · Next milestone: {escape(next_text)}"
                f" · Thresholds reached by balance: {reached}/25</td></tr>"
                "<tr><td colspan='2' style='font-size:10px;color:#64748b;padding-top:3px'>"
                f"Balance validation: {escape(validation)} · Reward delivery: not established by balance</td></tr>"
                "</table></td></tr>"
            )
        register_rows.append(
            "<tr><td style='padding:10px 10px 3px;background:#f1f5f9;border-top:1px solid #cbd5e1'>"
            f"<strong style='font-size:12px'>{escape(heading)} ({len(members)})</strong></td></tr>"
            + "".join(cards)
        )
    register_html = (
        "<section style='margin-top:12px;padding-top:10px;border-top:1px solid #e2e8f0'>"
        "<strong style='font-size:13px'>All-account Grenade balance register</strong>"
        f"<p style='font-size:11px;color:#64748b;margin:4px 0 7px'>{len(accounts)}/{configured} accounts listed · "
        f"{fresh_count} fresh today · {older_count} older readings · {no_observation_count} without a saved count. "
        "Older balances are compared with the minimum at their own observation date; the current-date floor is shown separately.</p>"
        "<table role='presentation' width='100%' cellspacing='0' cellpadding='0' border='0'>"
        + "".join(register_rows)
        + "</table></section>"
    ) if accounts else (
        "<p style='font-size:11px;color:#64748b'>Per-account Grenade register unavailable for this report.</p>"
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
        + milestone_reference_html
        + register_html
    )
