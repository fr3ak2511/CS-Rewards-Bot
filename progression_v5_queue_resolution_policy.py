"""Dry-run policy for scoped v5 Progression queue resolution proposals.

This policy can propose closure only for a deferred empty-scan entry when a
later read-only diagnostic proves that the selected horizontal reward rail was
captured to its end, every captured card has stable identity and an explicit
Delivered/Locked classification, and no Claim control was observed in any
snapshot. It does not claim portal-wide inventory completeness and never
mutates runtime state.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from progression_v5_evidence_adapter import (
    build_progression_v5_evidence_reconciliation_report,
)

_IST = timezone(timedelta(hours=5, minutes=30))
_ALLOWED_CARD_CAPTURE_SOURCES = {
    "semantic_slide_nodes",
    "repeated_reward_siblings",
}
_RESOLUTION = "no_claimable_in_captured_rail"


def _integer(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _parse_timestamp(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_IST)
    return parsed.astimezone(timezone.utc)


def _container_rank(container: Mapping[str, Any]) -> tuple[int, int, int, int, int, int]:
    """Mirror the production diagnostic's authoritative-rail ranking."""
    max_scroll = _integer(container.get("max_scroll_left")) or 0
    scroll_width = _integer(container.get("scroll_width")) or 0
    semantic = bool(container.get("is_semantic_carousel_root"))
    overflow = str(container.get("overflow_x") or "").strip().casefold()
    snap = str(container.get("scroll_snap_type") or "").strip().casefold()
    cards = container.get("captured_cards")
    card_count = len(cards) if isinstance(cards, list) else 0
    return (
        max_scroll,
        int(semantic),
        int(overflow in {"auto", "scroll"}),
        int(bool(snap and snap != "none")),
        scroll_width,
        card_count,
    )


def _index_by_suffix(items: Any, *, label: str, errors: list[str]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    if not isinstance(items, list):
        errors.append(f"{label}_not_a_list")
        return indexed
    for item in items:
        if not isinstance(item, dict):
            errors.append(f"{label}_record_not_an_object")
            continue
        suffix = str(item.get("player_id_last4") or "").strip().upper()
        if len(suffix) != 4 or any(char not in "0123456789ABCDEF" for char in suffix):
            errors.append(f"{label}_suffix_invalid")
            continue
        if suffix in indexed:
            errors.append(f"{label}_duplicate_suffix:{suffix}")
            continue
        indexed[suffix] = item
    return indexed


def _validate_account_capture(
    account: Mapping[str, Any],
    account_result: Mapping[str, Any],
    expected_thresholds: list[int],
) -> list[str]:
    """Validate live run metadata and each selected raw rail snapshot."""
    errors: list[str] = []
    suffix = str(account.get("player_id_last4") or "").strip().upper()
    snapshots = account.get("snapshots")
    snapshot_count = _integer(account.get("snapshot_count"))
    if (
        not isinstance(snapshots, list)
        or snapshot_count is None
        or snapshot_count < 2
        or len(snapshots) != snapshot_count
    ):
        return ["snapshot_count_mismatch_or_insufficient"]

    if account_result.get("login_status") != "authenticated":
        errors.append("account_login_not_authenticated")
    if account_result.get("page_status") != "authenticated_progression_page_observed":
        errors.append("account_progression_page_not_verified")
    if account_result.get("scroll_stop_reason") != "no_further_horizontal_movement_observed":
        errors.append("diagnostic_did_not_observe_horizontal_scroll_end")

    result_snapshot_count = _integer(account_result.get("snapshot_count"))
    if result_snapshot_count != snapshot_count:
        errors.append("account_result_snapshot_count_mismatch")
    scroll_steps = _integer(account_result.get("scroll_steps"))
    if scroll_steps != snapshot_count - 1:
        errors.append("diagnostic_scroll_step_count_mismatch")

    claimable_counts = account_result.get("claimable_control_counts_by_snapshot")
    if (
        not isinstance(claimable_counts, list)
        or len(claimable_counts) != snapshot_count
        or any(_integer(value) != 0 for value in claimable_counts)
    ):
        errors.append("claimable_control_count_not_zero_for_every_snapshot")

    rail = account.get("normalized_progression_rail")
    if not isinstance(rail, dict):
        return errors + ["normalized_progression_rail_missing"]
    selected_rail = rail.get("selected_rail")
    if not isinstance(selected_rail, dict):
        return errors + ["selected_rail_metadata_missing"]

    selected_index = _integer(selected_rail.get("container_index"))
    expected_rail_cards = _integer(selected_rail.get("captured_card_count"))
    selected_client_width = _integer(selected_rail.get("client_width"))
    selected_scroll_width = _integer(selected_rail.get("scroll_width"))
    selected_max_scroll = _integer(selected_rail.get("max_scroll_left"))
    if selected_index is None:
        errors.append("selected_rail_container_index_invalid")
    if expected_rail_cards is None or expected_rail_cards < 1:
        errors.append("selected_rail_card_count_invalid")
    if (
        selected_client_width is None
        or selected_scroll_width is None
        or selected_max_scroll is None
        or selected_scroll_width <= selected_client_width
        or abs((selected_scroll_width - selected_client_width) - selected_max_scroll) > 2
    ):
        errors.append("selected_rail_scroll_geometry_invalid")
    if selected_rail.get("at_end_observed") is not True:
        errors.append("selected_rail_end_not_observed")

    observed_times: list[datetime] = []
    scroll_positions: list[int] = []
    capture_sources: set[str] = set()
    selected_card_threshold_sets: list[set[int]] = []
    selected_scroll_metrics: set[tuple[int, int, int]] = set()

    for snapshot_index, snapshot in enumerate(snapshots):
        if not isinstance(snapshot, dict):
            errors.append(f"snapshot_not_an_object:{snapshot_index}")
            continue
        observed_at = _parse_timestamp(snapshot.get("observed_at_ist"))
        if observed_at is None:
            errors.append(f"snapshot_timestamp_invalid:{snapshot_index}")
        else:
            observed_times.append(observed_at)

        containers = snapshot.get("carousel_containers")
        if not isinstance(containers, list) or not containers:
            errors.append(f"snapshot_carousel_candidates_missing:{snapshot_index}")
            continue

        candidate_records: list[tuple[tuple[int, int, int, int, int, int], Mapping[str, Any]]] = []
        candidate_indices: list[int] = []
        for container in containers:
            if not isinstance(container, dict):
                continue
            index = _integer(container.get("index"))
            if index is not None:
                candidate_indices.append(index)
            candidate_records.append((_container_rank(container), container))
        if len(candidate_records) != len(containers):
            errors.append(f"snapshot_carousel_candidate_invalid:{snapshot_index}")
            continue
        if len(candidate_indices) != len(set(candidate_indices)):
            errors.append(f"snapshot_carousel_candidate_index_duplicate:{snapshot_index}")
            continue

        best_rank = max(rank for rank, _container in candidate_records)
        best = [container for rank, container in candidate_records if rank == best_rank]
        if len(best) != 1:
            errors.append(f"snapshot_authoritative_rail_ambiguous:{snapshot_index}")
            continue
        selected = best[0]
        if _integer(selected.get("index")) != selected_index:
            errors.append(f"snapshot_selected_rail_index_mismatch:{snapshot_index}")

        source = str(selected.get("card_capture_source") or "").strip()
        capture_sources.add(source)
        if source not in _ALLOWED_CARD_CAPTURE_SOURCES:
            errors.append(f"snapshot_card_capture_source_invalid:{snapshot_index}")

        cards = selected.get("captured_cards")
        captured_count = _integer(selected.get("captured_card_count"))
        if (
            not isinstance(cards, list)
            or captured_count is None
            or captured_count != len(cards)
            or captured_count != expected_rail_cards
        ):
            errors.append(f"snapshot_captured_card_count_mismatch:{snapshot_index}")
            continue

        explicit_count = _integer(selected.get("explicit_slide_count"))
        if source == "semantic_slide_nodes" and explicit_count != captured_count:
            errors.append(f"snapshot_semantic_slide_count_mismatch:{snapshot_index}")
        if source == "repeated_reward_siblings" and explicit_count != 0:
            errors.append(f"snapshot_fallback_slide_count_mismatch:{snapshot_index}")

        thresholds: list[int] = []
        for card_index, card in enumerate(cards):
            if not isinstance(card, dict):
                errors.append(f"snapshot_card_not_an_object:{snapshot_index}:{card_index}")
                continue
            hints = card.get("milestone_number_hints")
            valid = sorted({
                value for raw in hints if (value := _integer(raw)) is not None and value > 0
            }) if isinstance(hints, list) else []
            if len(valid) != 1:
                errors.append(f"snapshot_card_milestone_identity_invalid:{snapshot_index}:{card_index}")
                continue
            thresholds.append(valid[0])
        if len(thresholds) != len(set(thresholds)):
            errors.append(f"snapshot_card_milestone_identity_duplicate:{snapshot_index}")
        if set(thresholds) != set(expected_thresholds):
            errors.append(f"snapshot_card_inventory_mismatch:{snapshot_index}")
        selected_card_threshold_sets.append(set(thresholds))

        scroll_left = _integer(selected.get("scroll_left"))
        max_scroll = _integer(selected.get("max_scroll_left"))
        scroll_width = _integer(selected.get("scroll_width"))
        client_width = _integer(selected.get("client_width"))
        if (
            scroll_left is None
            or max_scroll is None
            or scroll_width is None
            or client_width is None
            or max_scroll <= 0
            or scroll_width <= client_width
            or abs((scroll_width - client_width) - max_scroll) > 2
        ):
            errors.append(f"snapshot_selected_rail_scroll_metrics_invalid:{snapshot_index}")
        else:
            scroll_positions.append(scroll_left)
            selected_scroll_metrics.add((client_width, scroll_width, max_scroll))
            derived_at_end = scroll_left >= max_scroll - 2
            if selected.get("at_end") is not derived_at_end:
                errors.append(f"snapshot_selected_rail_end_flag_mismatch:{snapshot_index}")
            if derived_at_end and snapshot_index != snapshot_count - 1:
                # Reaching the end early is acceptable only if later snapshots
                # still show the same terminal position.
                pass

    if len(capture_sources) != 1:
        errors.append("card_capture_source_changed_or_missing_across_snapshots")
    if len(selected_card_threshold_sets) != snapshot_count:
        errors.append("selected_rail_snapshot_capture_coverage_mismatch")
    elif any(values != set(expected_thresholds) for values in selected_card_threshold_sets):
        errors.append("selected_rail_card_inventory_not_stable")
    if len(selected_scroll_metrics) != 1:
        errors.append("selected_rail_scroll_geometry_changed_across_snapshots")
    if len(scroll_positions) != snapshot_count:
        errors.append("selected_rail_scroll_position_coverage_mismatch")
    elif any(right < left for left, right in zip(scroll_positions, scroll_positions[1:])):
        errors.append("selected_rail_scroll_position_moved_backwards")
    elif selected_max_scroll is not None and scroll_positions[-1] < selected_max_scroll - 2:
        errors.append("selected_rail_final_position_not_at_end")

    return sorted(set(errors))


def build_progression_v5_queue_resolution_proposal(
    diagnostic_summary: Any,
    *,
    roster_player_ids: Sequence[str],
    progression_deferred_inspections: Mapping[str, Any],
    source_run_id: str,
) -> dict[str, Any]:
    """Build a non-mutating proposal for scoped empty-scan queue closure.

    Resolution means only that no claimable reward was observed in the fully
    captured selected horizontal rail at the diagnostic time. It does not mean
    that the portal's complete inventory or future rewards are exhausted.
    """
    base = build_progression_v5_evidence_reconciliation_report(
        diagnostic_summary,
        roster_player_ids=roster_player_ids,
        progression_deferred_inspections=progression_deferred_inspections,
        source_run_id=source_run_id,
    )
    diagnostic = diagnostic_summary if isinstance(diagnostic_summary, dict) else {}
    queue = progression_deferred_inspections if isinstance(progression_deferred_inspections, Mapping) else {}
    global_errors = list(base["global_validation"]["errors"])

    accounts = _index_by_suffix(
        diagnostic.get("accounts"),
        label="diagnostic_account",
        errors=global_errors,
    )
    results = _index_by_suffix(
        diagnostic.get("account_results"),
        label="diagnostic_account_result",
        errors=global_errors,
    )
    if set(accounts) != set(results):
        global_errors.append("account_and_result_suffix_coverage_mismatch")

    raw_thresholds = diagnostic.get("common_observed_milestone_thresholds")
    expected_thresholds = [
        value for raw in raw_thresholds
        if (value := _integer(raw)) is not None and value > 0
    ] if isinstance(raw_thresholds, list) else []
    if not expected_thresholds or len(expected_thresholds) != len(set(expected_thresholds)):
        global_errors.append("common_milestone_threshold_inventory_invalid")

    generated_at = _parse_timestamp(diagnostic.get("generated_at_ist"))
    if generated_at is None:
        global_errors.append("diagnostic_generated_timestamp_invalid")

    total_snapshots = 0
    for suffix, account in accounts.items():
        result = results.get(suffix)
        if not isinstance(result, dict):
            global_errors.append(f"account_result_missing:{suffix}")
            continue
        total_snapshots += _integer(account.get("snapshot_count")) or 0
        account_errors = _validate_account_capture(account, result, expected_thresholds)
        if account_errors:
            global_errors.extend(
                f"account_capture_invalid:{suffix}:{error}" for error in account_errors
            )
        if generated_at is not None:
            snapshots = account.get("snapshots")
            times = [
                _parse_timestamp(snapshot.get("observed_at_ist"))
                for snapshot in snapshots if isinstance(snapshot, dict)
            ] if isinstance(snapshots, list) else []
            if not times or any(value is None or value > generated_at for value in times):
                global_errors.append(f"account_snapshot_time_out_of_diagnostic_window:{suffix}")

    if _integer(diagnostic.get("snapshot_count")) != total_snapshots:
        global_errors.append("diagnostic_snapshot_total_mismatch")

    entries = []
    for row in base["entries"]:
        row = dict(row)
        row_errors = list(row.get("validation_errors") or [])
        player_id = str(row.get("player_id") or "").strip().upper()
        record = queue.get(player_id)
        suffix = str(row.get("player_id_last4") or "").strip().upper()

        if not isinstance(record, dict):
            row_errors.append("current_queue_record_missing")
        elif generated_at is not None:
            latest_activity: list[datetime] = []
            for field in ("last_attempt_at", "last_deferred_at"):
                parsed = _parse_timestamp(record.get(field))
                if parsed is None:
                    row_errors.append(f"queue_{field}_missing_or_invalid")
                else:
                    latest_activity.append(parsed)
            if latest_activity and any(value > generated_at for value in latest_activity):
                row_errors.append("queue_changed_after_diagnostic")
            if latest_activity and suffix in accounts:
                snapshots = accounts[suffix].get("snapshots")
                first_snapshot = (
                    _parse_timestamp(snapshots[0].get("observed_at_ist"))
                    if isinstance(snapshots, list) and snapshots and isinstance(snapshots[0], dict)
                    else None
                )
                if first_snapshot is None or max(latest_activity) >= first_snapshot:
                    row_errors.append("diagnostic_does_not_postdate_last_queue_attempt")

        if global_errors:
            row_errors.append("global_diagnostic_or_capture_validation_failed")

        eligible = not row_errors and row.get("evidence_status") == "READY_FOR_POLICY_REVIEW"
        row["evidence_status"] = (
            "PROPOSED_NO_CLAIMABLE_IN_CAPTURED_RAIL" if eligible else "BLOCKED"
        )
        row["resolution_eligible"] = bool(eligible)
        row["proposed_resolution"] = _RESOLUTION if eligible else None
        row["resolution_scope"] = "selected_horizontal_progression_reward_rail_at_diagnostic_time"
        row["validation_errors"] = sorted(set(row_errors))
        row["resolution_blockers"] = [] if eligible else sorted(set(row_errors))
        entries.append(row)

    entries.sort(key=lambda row: (row.get("player_id_last4") or "", row.get("player_id") or ""))
    eligible_entries = [row for row in entries if row["resolution_eligible"]]
    status_totals = {"Delivered": 0, "Claimable": 0, "Locked": 0, "Unknown": 0}
    for row in eligible_entries:
        for status in status_totals:
            status_totals[status] += int(row["card_status_totals"].get(status, 0) or 0)

    global_errors = sorted(set(global_errors))
    return {
        "report_version": 1,
        "policy": "PROGRESSION_V5_NO_CLAIMABLE_CAPTURED_RAIL_V1",
        "dry_run": True,
        "source": {
            "kind": "one_time_progression_diagnostic",
            "source_run_id": str(source_run_id or "").strip(),
            "diagnostic_version": diagnostic.get("diagnostic_version"),
            "generated_at_ist": diagnostic.get("generated_at_ist"),
        },
        "global_validation": {
            "valid": not global_errors,
            "errors": global_errors,
            "configured_roster_count": base["global_validation"]["configured_roster_count"],
            "diagnostic_account_count": len(accounts),
            "queue_entry_count": len(queue),
        },
        "queue_summary": {
            "entries_proposed_for_scoped_resolution": len(eligible_entries),
            "entries_blocked": len(entries) - len(eligible_entries),
            "ledger_records_for_proposed_entries": sum(
                len(row.get("card_ledger") or []) for row in eligible_entries
            ),
            "proposed_card_status_totals": status_totals,
        },
        "resolution_policy": {
            "resolution_authorized": False,
            "resolution_eligible_count": len(eligible_entries),
            "resolution": _RESOLUTION,
            "scope": "selected_horizontal_progression_reward_rail_at_diagnostic_time",
            "portal_inventory_completeness_established": False,
            "delivery_status_completeness_established": False,
            "queue_mutated": False,
            "claim_history_mutated": False,
            "claim_actions_dispatched": False,
            "limitations": [
                "does_not_establish_complete_portal_inventory",
                "does_not_establish_delivery_status_completeness_beyond_captured_cards",
                "does_not_claim_future_unlocks_are_exhausted",
                "requires_separate_explicit_authorization_before_runtime_state_mutation",
            ],
        },
        "entries": entries,
    }


__all__ = ["build_progression_v5_queue_resolution_proposal"]
