"""Read-only reconciliation of schema-v5 Progression evidence.

This adapter prepares an auditable evidence report for a separately reviewed
queue-resolution policy. It never mutates runtime state, claim history, or the
Progression inspection queue, and it never authorizes a claim or a resolution.
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

_PLAYER_ID = re.compile(r"^[0-9A-F]{16}$")
_SUFFIX = re.compile(r"^[0-9A-F]{4}$")
_STATUSES = ("Delivered", "Claimable", "Locked", "Unknown")
_REQUIRED_READ_ONLY_FLAGS = {
    "read_only": True,
    "claim_actions_permitted": False,
    "claim_clicks_dispatched": 0,
    "claim_history_modified": False,
    "progression_queue_modified": False,
    "runtime_state_loaded": False,
    "runtime_state_persisted": False,
}


def _player_id(value: Any) -> str | None:
    candidate = str(value or "").strip().upper()
    return candidate if _PLAYER_ID.fullmatch(candidate) else None


def _suffix(value: Any) -> str | None:
    candidate = str(value or "").strip().upper()
    return candidate if _SUFFIX.fullmatch(candidate) else None


def _integer(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _empty_counts() -> dict[str, int]:
    return {status: 0 for status in _STATUSES}


def _status_counts(cards: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counter = Counter(card.get("classification") for card in cards)
    return {status: int(counter.get(status, 0)) for status in _STATUSES}


def _index_unique(items: Any, *, label: str, errors: list[str]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    if not isinstance(items, list):
        errors.append(f"{label}_not_a_list")
        return indexed
    for item in items:
        if not isinstance(item, dict):
            errors.append(f"{label}_record_not_an_object")
            continue
        key = _suffix(item.get("player_id_last4"))
        if key is None:
            errors.append(f"{label}_suffix_invalid")
            continue
        if key in indexed:
            errors.append(f"{label}_duplicate_suffix:{key}")
            continue
        indexed[key] = item
    return indexed


def _validate_account(account: Mapping[str, Any], expected_thresholds: list[int]) -> tuple[list[str], dict[str, int], list[dict[str, Any]]]:
    errors: list[str] = []
    rail = account.get("normalized_progression_rail")
    if not isinstance(rail, dict):
        return ["normalized_progression_rail_missing"], _empty_counts(), []

    snapshots = account.get("snapshots")
    snapshot_count = _integer(account.get("snapshot_count"))
    if snapshot_count is None or not isinstance(snapshots, list) or len(snapshots) != snapshot_count:
        errors.append("snapshot_count_mismatch")

    if rail.get("captured_rail_scan_completed") is not True:
        errors.append("captured_rail_scan_not_completed")
    for field in (
        "inventory_stable_across_snapshots",
        "milestone_identity_valid_across_snapshots",
        "card_signatures_stable_across_snapshots",
    ):
        if rail.get(field) is not True:
            errors.append(f"rail_validation_failed:{field}")

    normalized_count = _integer(rail.get("rail_snapshots_normalized"))
    expected_count = _integer(rail.get("rail_snapshots_expected"))
    if normalized_count is None or expected_count is None or normalized_count < 1 or normalized_count != expected_count:
        errors.append("rail_snapshot_coverage_mismatch")
    if snapshot_count is not None and normalized_count != snapshot_count:
        errors.append("rail_snapshot_count_differs_from_account_snapshot_count")

    selected_rail = rail.get("selected_rail")
    if not isinstance(selected_rail, dict) or selected_rail.get("at_end_observed") is not True:
        errors.append("authoritative_rail_end_not_observed")

    raw_thresholds = rail.get("observed_milestone_thresholds")
    thresholds: list[int] = []
    if not isinstance(raw_thresholds, list) or not raw_thresholds:
        errors.append("observed_milestone_thresholds_missing")
    else:
        for raw in raw_thresholds:
            threshold = _integer(raw)
            if threshold is None or threshold < 1:
                errors.append("observed_milestone_threshold_invalid")
            else:
                thresholds.append(threshold)
        if len(thresholds) != len(set(thresholds)):
            errors.append("duplicate_observed_milestone_threshold")
        if thresholds != sorted(thresholds):
            errors.append("observed_milestone_thresholds_not_sorted")
        if thresholds != expected_thresholds:
            errors.append("account_threshold_inventory_differs_from_run_inventory")

    raw_cards = rail.get("milestone_cards")
    if not isinstance(raw_cards, list):
        errors.append("milestone_card_ledger_missing")
        raw_cards = []
    if len(raw_cards) != len(thresholds):
        errors.append("milestone_card_count_does_not_match_threshold_count")

    seen_thresholds: set[int] = set()
    cards: list[dict[str, Any]] = []
    for raw_card in raw_cards:
        if not isinstance(raw_card, dict):
            errors.append("milestone_card_record_not_an_object")
            continue
        threshold = _integer(raw_card.get("threshold"))
        if threshold is None or threshold < 1:
            errors.append("milestone_card_threshold_invalid")
            continue
        if threshold in seen_thresholds:
            errors.append(f"duplicate_milestone_card_threshold:{threshold}")
            continue
        seen_thresholds.add(threshold)

        classification = raw_card.get("classification")
        if classification not in _STATUSES:
            errors.append(f"milestone_card_status_invalid:{threshold}")
            continue
        signature = str(raw_card.get("card_signature") or "").strip()
        if not signature:
            errors.append(f"milestone_card_signature_missing:{threshold}")
        observations = _integer(raw_card.get("observations"))
        if observations is None or observations < 1:
            errors.append(f"milestone_card_observation_count_invalid:{threshold}")
        evidence = raw_card.get("evidence_sources")
        if not isinstance(evidence, list) or any(not isinstance(value, str) for value in evidence):
            errors.append(f"milestone_card_evidence_sources_invalid:{threshold}")
            evidence = []
        cards.append({
            "threshold": threshold,
            "classification": classification,
            "card_signature": signature,
            "evidence_sources": sorted(set(evidence)),
            "observations": observations if observations is not None else 0,
        })

    if seen_thresholds != set(thresholds):
        errors.append("milestone_card_threshold_set_mismatch")
    counts = _status_counts(cards)
    stated_counts = rail.get("card_status_totals")
    if not isinstance(stated_counts, dict) or any(_integer(stated_counts.get(key)) != counts[key] for key in _STATUSES):
        errors.append("account_card_status_totals_mismatch")
    signatures = [card["card_signature"] for card in cards if card["card_signature"]]
    if len(signatures) != len(set(signatures)):
        errors.append("duplicate_milestone_card_signature")
    if rail.get("inventory_completeness_established") is not False:
        errors.append("account_inventory_completeness_flag_not_explicitly_false")
    if counts["Claimable"]:
        errors.append("claimable_cards_observed_requires_live_review")
    if counts["Unknown"]:
        errors.append("unknown_cards_observed_requires_live_review")
    return errors, counts, sorted(cards, key=lambda card: card["threshold"])


def build_progression_v5_evidence_reconciliation_report(
    diagnostic_summary: Any,
    *,
    roster_player_ids: Sequence[str],
    progression_deferred_inspections: Mapping[str, Any],
    source_run_id: str,
) -> dict[str, Any]:
    """Build a deterministic dry-run evidence ledger without mutating inputs.

    A queue record may be marked READY_FOR_POLICY_REVIEW only when the exact
    Player ID maps to one authenticated v5 account and all captured card
    identities/statuses validate. This is not resolution eligibility: v5 says
    completeness and delivery status are not established, and this standalone
    diagnostic does not create the normal claim-flow completion signal.
    """
    global_errors: list[str] = []
    if not isinstance(roster_player_ids, (list, tuple)):
        roster = []
        global_errors.append("roster_not_a_list_or_tuple")
    else:
        roster = list(roster)

    roster_by_suffix: dict[str, str] = {}
    roster_ids: set[str] = set()
    for raw_id in roster:
        player_id = _player_id(raw_id)
        if player_id is None:
            global_errors.append("roster_contains_invalid_player_id")
            continue
        if player_id in roster_ids:
            global_errors.append("roster_contains_duplicate_player_id")
            continue
        roster_ids.add(player_id)
        suffix = player_id[-4:]
        if suffix in roster_by_suffix:
            global_errors.append(f"ambiguous_roster_suffix:{suffix}")
        else:
            roster_by_suffix[suffix] = player_id
    if not roster_ids:
        global_errors.append("roster_is_empty_or_invalid")

    if not isinstance(progression_deferred_inspections, Mapping):
        queue: Mapping[str, Any] = {}
        global_errors.append("progression_deferred_inspections_not_a_mapping")
    else:
        queue = progression_deferred_inspections

    run_id = str(source_run_id or "").strip()
    if not run_id.isdigit():
        global_errors.append("source_run_id_missing_or_invalid")

    if not isinstance(diagnostic_summary, dict):
        diagnostic: dict[str, Any] = {}
        global_errors.append("diagnostic_summary_not_an_object")
    else:
        diagnostic = diagnostic_summary

    expected_global = {
        "diagnostic_version": 5,
        "read_only": True,
        "configured_accounts": len(roster_ids),
        "configured_player_count": len(roster_ids),
        "accounts_with_diagnostics": len(roster_ids),
        "accounts_with_completed_rail_scan": len(roster_ids),
        "inventory_consistency_across_accounts": True,
        "run_mode": "PROGRESSION_DIAGNOSTIC_ONLY",
        "run_scope": "all_configured_accounts",
        "authenticated_progression_pages_observed": len(roster_ids),
        "accounts_with_login_failure": 0,
        "accounts_with_diagnostic_error": 0,
        **_REQUIRED_READ_ONLY_FLAGS,
    }
    for field, expected in expected_global.items():
        if diagnostic.get(field) != expected:
            global_errors.append(f"diagnostic_field_mismatch:{field}")
    if diagnostic.get("completeness_established") is not False:
        global_errors.append("diagnostic_completeness_flag_not_explicitly_false")
    if diagnostic.get("delivery_status_established") is not False:
        global_errors.append("diagnostic_delivery_status_flag_not_explicitly_false")

    accounts = _index_unique(diagnostic.get("accounts"), label="diagnostic_account", errors=global_errors)
    results = _index_unique(diagnostic.get("account_results"), label="diagnostic_account_result", errors=global_errors)
    if set(accounts) != set(results):
        global_errors.append("diagnostic_account_result_coverage_mismatch")
    if set(accounts) != set(roster_by_suffix):
        global_errors.append("diagnostic_suffixes_do_not_match_configured_roster")

    for suffix, result in results.items():
        if result.get("login_status") != "authenticated":
            global_errors.append(f"account_not_authenticated:{suffix}")
        if result.get("page_status") != "authenticated_progression_page_observed":
            global_errors.append(f"authenticated_progression_page_not_observed:{suffix}")

    thresholds: list[int] = []
    raw_common = diagnostic.get("common_observed_milestone_thresholds")
    if not isinstance(raw_common, list) or not raw_common:
        global_errors.append("common_observed_milestone_thresholds_missing")
    else:
        for raw in raw_common:
            value = _integer(raw)
            if value is None or value < 1:
                global_errors.append("common_milestone_threshold_invalid")
            else:
                thresholds.append(value)
        if len(thresholds) != len(set(thresholds)):
            global_errors.append("common_milestone_thresholds_contain_duplicates")
        if thresholds != sorted(thresholds):
            global_errors.append("common_milestone_thresholds_not_sorted")

    account_counts: dict[str, dict[str, int]] = {}
    account_cards: dict[str, list[dict[str, Any]]] = {}
    for suffix, account in accounts.items():
        errors, counts, cards = _validate_account(account, thresholds)
        if errors:
            global_errors.extend(f"diagnostic_account_invalid:{suffix}:{error}" for error in errors)
        account_counts[suffix] = counts
        account_cards[suffix] = cards

    aggregate = _empty_counts()
    for counts in account_counts.values():
        for status in _STATUSES:
            aggregate[status] += counts[status]
    stated_aggregate = diagnostic.get("card_status_totals")
    if not isinstance(stated_aggregate, dict) or any(_integer(stated_aggregate.get(key)) != aggregate[key] for key in _STATUSES):
        global_errors.append("diagnostic_aggregate_card_status_totals_mismatch")

    rows: list[dict[str, Any]] = []
    seen_queue_ids: set[str] = set()
    generated_at = diagnostic.get("generated_at_ist")
    for raw_key, raw_record in queue.items():
        errors: list[str] = []
        player_id = _player_id(raw_key)
        suffix = player_id[-4:] if player_id else None
        record = raw_record if isinstance(raw_record, dict) else {}
        if player_id is None:
            errors.append("queue_key_is_not_a_valid_player_id")
        else:
            if player_id in seen_queue_ids:
                errors.append("duplicate_normalized_queue_player_id")
            seen_queue_ids.add(player_id)
            if player_id not in roster_ids:
                errors.append("queued_player_id_not_in_configured_roster")
        if not isinstance(raw_record, dict):
            errors.append("queue_record_not_an_object")
        else:
            if record.get("status") != "deferred":
                errors.append("queue_status_not_deferred")
            if record.get("last_outcome_category") != "empty_scan":
                errors.append("queue_outcome_not_empty_scan")
            if record.get("manual_attention_required") is True:
                errors.append("manual_attention_required")
            if record.get("requires_exact_target") is True or record.get("target_card_signature"):
                errors.append("exact_target_or_unconfirmed_claim_requires_separate_handling")

        if suffix is None or suffix not in accounts:
            errors.append("queued_account_missing_from_diagnostic")
            counts, cards = _empty_counts(), []
        else:
            counts, cards = account_counts[suffix], account_cards[suffix]
            result = results.get(suffix, {})
            if result.get("login_status") != "authenticated" or result.get("page_status") != "authenticated_progression_page_observed":
                errors.append("queued_account_authentication_not_verified")
        if global_errors:
            errors.append("global_diagnostic_or_roster_validation_failed")
            if suffix:
                errors.extend(
                    error for error in global_errors
                    if error.startswith(f"diagnostic_account_invalid:{suffix}:")
                )

        ready = not errors
        ledger = []
        if ready and player_id and suffix:
            for card in cards:
                ledger.append({
                    "player_id": player_id,
                    "player_id_last4": suffix,
                    "milestone_threshold": card["threshold"],
                    "classification": card["classification"],
                    "card_signature": card["card_signature"],
                    "evidence_sources": card["evidence_sources"],
                    "observations": card["observations"],
                    "source_run_id": run_id,
                    "observed_at_ist": generated_at,
                })
        rows.append({
            "player_id": player_id,
            "player_id_last4": suffix,
            "queue_status": record.get("status"),
            "last_outcome_category": record.get("last_outcome_category"),
            "total_attempts": _integer(record.get("total_attempts")),
            "evidence_status": "READY_FOR_POLICY_REVIEW" if ready else "BLOCKED",
            "validation_errors": sorted(set(errors)),
            "card_status_totals": counts,
            "observed_milestone_count": len(cards),
            "card_ledger": ledger,
            "resolution_eligible": False,
            "resolution_blockers": [
                "diagnostic_does_not_establish_complete_portal_inventory",
                "diagnostic_does_not_establish_delivery_status_completeness",
                "standalone_diagnostic_does_not_emit_normal_claim_flow_completion_flag",
                "queue_resolution_requires_separately_reviewed_policy_and_approval",
            ],
        })

    rows.sort(key=lambda row: (row.get("player_id_last4") or "", row.get("player_id") or ""))
    ready_rows = [row for row in rows if row["evidence_status"] == "READY_FOR_POLICY_REVIEW"]
    ready_totals = _empty_counts()
    for row in ready_rows:
        for status in _STATUSES:
            ready_totals[status] += row["card_status_totals"][status]

    return {
        "report_version": 1,
        "dry_run": True,
        "source": {
            "kind": "one_time_progression_diagnostic",
            "source_run_id": run_id,
            "diagnostic_version": diagnostic.get("diagnostic_version"),
            "generated_at_ist": generated_at,
        },
        "global_validation": {
            "valid": not global_errors,
            "errors": sorted(set(global_errors)),
            "configured_roster_count": len(roster_ids),
            "diagnostic_account_count": len(accounts),
            "queue_entry_count": len(queue),
        },
        "queue_summary": {
            "entries_review_ready": len(ready_rows),
            "entries_blocked": len(rows) - len(ready_rows),
            "ledger_records_generated": sum(len(row["card_ledger"]) for row in ready_rows),
            "review_ready_card_status_totals": ready_totals,
        },
        "resolution_policy": {
            "resolution_authorized": False,
            "resolution_eligible_count": 0,
            "queue_mutated": False,
            "claim_history_mutated": False,
            "claim_actions_dispatched": False,
            "completeness_established_by_source": False,
            "blockers": [
                "diagnostic_does_not_establish_complete_portal_inventory",
                "diagnostic_does_not_establish_delivery_status_completeness",
                "standalone_diagnostic_does_not_emit_normal_claim_flow_completion_flag",
                "queue_resolution_requires_separately_reviewed_policy_and_approval",
            ],
        },
        "entries": rows,
    }


__all__ = ["build_progression_v5_evidence_reconciliation_report"]
