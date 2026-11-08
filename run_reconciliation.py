"""Read-only reconciliation of per-account and aggregate reward totals."""

from __future__ import annotations

from typing import Any


def _integer(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _event_claims(player: dict[str, Any]) -> int:
    explicit = player.get("event_reward_claims")
    if explicit is not None:
        return _integer(explicit)
    events = player.get("event_rewards")
    if not isinstance(events, list):
        return 0
    return sum(
        len(event.get("claims_this_run", []) or [])
        for event in events if isinstance(event, dict)
    )


def _player_claims(player: dict[str, Any]) -> int:
    return sum((
        _integer(player.get("daily")),
        _integer(player.get("store")),
        _integer(player.get("store_temp")),
        _integer(player.get("progression")),
        _integer(player.get("loyalty")),
        _event_claims(player),
    ))


def build_run_reconciliation(
    results: list[dict[str, Any]],
    *,
    run_label: str,
    configured_player_count: int,
    totals: dict[str, Any],
    counters: dict[str, Any],
) -> dict[str, Any]:
    """Compare independent per-account sums, report totals, and run-mode counts.

    This function is deliberately read-only. It does not change claim history,
    trigger retries, or infer an expected Progression reward count.
    """
    rows = [row for row in results if isinstance(row, dict)]
    processed = len(rows)
    configured = _integer(configured_player_count)

    per_account_claims = sum(_player_claims(row) for row in rows)
    category_claims = sum(_integer(totals.get(key)) for key in (
        "daily_claimed_this_run",
        "standard_store_claimed_this_run",
        "promotional_claims_this_run",
        "event_reward_claims_this_run",
        "progression_claimed_this_run",
        "loyalty_claimed_this_run",
    ))
    recorded_claims = _integer(counters.get("claims_made"))

    full_claim_accounts = _integer(counters.get("full_claim_accounts"))
    promotional_only_accounts = _integer(counters.get("promotional_only_accounts"))
    backup_reconciliation_accounts = _integer(counters.get("backup_reconciliation_accounts"))
    mode_total = full_claim_accounts + promotional_only_accounts + backup_reconciliation_accounts

    checks: dict[str, bool | None] = {
        "per_account_claims_match_category_totals": per_account_claims == category_claims,
        "category_totals_match_claim_counter": category_claims == recorded_claims,
        "processing_modes_match_processed_accounts": mode_total == processed,
        "primary_processed_full_configured_roster": (
            processed == configured and full_claim_accounts == configured
            if run_label == "Primary Run" else None
        ),
    }
    warnings = [name for name, passed in checks.items() if passed is False]

    return {
        "status": "matched" if not warnings else "attention_required",
        "scope": "full_roster_primary" if run_label == "Primary Run" else "partial_or_mixed_run",
        "configured_player_count": configured,
        "accounts_processed": processed,
        "processing_mode_counts": {
            "full_claim": full_claim_accounts,
            "promotional_only": promotional_only_accounts,
            "backup_reconciliation": backup_reconciliation_accounts,
            "sum": mode_total,
        },
        "claim_counts": {
            "per_account_sum": per_account_claims,
            "category_sum": category_claims,
            "recorded_claim_counter": recorded_claims,
        },
        "checks": checks,
        "warnings": warnings,
        "progression_validation": {
            "claimed_count": _integer(totals.get("progression_claimed_this_run")),
            "expected_count": None,
            "note": "No expected Progression count is inferred; rewards depend on each account's live portal state.",
        },
    }
