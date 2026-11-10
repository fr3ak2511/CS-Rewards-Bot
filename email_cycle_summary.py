"""Compact, privacy-safe daily-cycle totals for recovery emails.

The persisted record is additive metadata in bot_meta.json. It does not affect
claim decisions, claim history, scheduler gates, or reward state.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

IST = timezone(timedelta(hours=5, minutes=30))
CATEGORY_KEYS = ("daily", "store", "promotions", "progression", "loyalty", "events")


def _integer(value: Any) -> int:
    try:
        if isinstance(value, bool):
            return 0
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _normalise_counts(values: Any) -> dict[str, int]:
    values = values if isinstance(values, dict) else {}
    return {key: _integer(values.get(key)) for key in CATEGORY_KEYS}


def _add_counts(left: Any, right: Any) -> dict[str, int]:
    a = _normalise_counts(left)
    b = _normalise_counts(right)
    return {key: a[key] + b[key] for key in CATEGORY_KEYS}


def _total_claims(values: Any) -> int:
    return sum(_normalise_counts(values).values())


def _date_from_timestamp(value: Any) -> str | None:
    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(IST).replace(tzinfo=None)
        return parsed.date().isoformat()
    except (TypeError, ValueError):
        return None


def _new_cycle(
    *,
    reset_date: str,
    configured_count: int,
    run_label: str,
    run_timestamp: str,
    claims: dict[str, int],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "reset_date": reset_date,
        "configured_player_count": configured_count,
        "primary_run_label": run_label,
        "primary_run_timestamp": run_timestamp,
        "primary_claim_totals": _normalise_counts(claims),
        "recovery_claim_totals": _normalise_counts({}),
        "recovery_run_keys": [],
        "recovered_player_last4": [],
    }


def _seed_from_last_full_run(
    meta: dict[str, Any],
    *,
    reset_date: str,
    configured_count: int,
) -> dict[str, Any] | None:
    full_run = meta.get("last_full_run")
    if not isinstance(full_run, dict):
        return None
    if _date_from_timestamp(full_run.get("timestamp")) != reset_date:
        return None
    if _integer(full_run.get("configured_player_count")) != configured_count:
        return None
    if _integer(full_run.get("accounts_processed")) != configured_count:
        return None
    if _integer(full_run.get("full_claim_accounts")) != configured_count:
        return None

    per_type = full_run.get("per_type") if isinstance(full_run.get("per_type"), dict) else {}
    claims = {
        "daily": per_type.get("daily", 0),
        "store": per_type.get("store_daily", per_type.get("store", 0)),
        "promotions": full_run.get("promotional_claims_made", 0),
        "progression": per_type.get("progression", 0),
        "loyalty": per_type.get("loyalty", 0),
        "events": per_type.get("event_rewards", 0),
    }
    return _new_cycle(
        reset_date=reset_date,
        configured_count=configured_count,
        run_label=str(full_run.get("run_label") or "Primary Run"),
        run_timestamp=str(full_run.get("timestamp") or ""),
        claims=_normalise_counts(claims),
    )


def update_daily_cycle_summary(
    meta: dict[str, Any],
    *,
    reset_date: str,
    run_label: str,
    configured_player_ids: list[str],
    claim_totals: dict[str, Any],
    processed_player_ids: list[str],
    run_key: str,
    run_timestamp: str,
    is_full_roster_run: bool = False,
) -> dict[str, Any] | None:
    """Update the cycle rollup once per primary cycle and once per recovery run.

    run_key should include GITHUB_RUN_ID and GITHUB_RUN_ATTEMPT so an Actions
    retry cannot add the same recovery attempt twice.
    """
    roster = list(dict.fromkeys(
        str(pid).strip() for pid in configured_player_ids if str(pid).strip()
    ))
    configured_count = len(roster)
    if configured_count <= 0:
        return None

    cycle = meta.get("daily_cycle_email_summary")
    if not isinstance(cycle, dict) or cycle.get("reset_date") != reset_date:
        cycle = None

    if is_full_roster_run and run_label in {"Primary Run", "Manual Run"}:
        if cycle is None:
            cycle = _new_cycle(
                reset_date=reset_date,
                configured_count=configured_count,
                run_label=run_label,
                run_timestamp=run_timestamp,
                claims=_normalise_counts(claim_totals),
            )
            meta["daily_cycle_email_summary"] = cycle

    elif run_label == "Recovery Run":
        if cycle is None:
            cycle = _seed_from_last_full_run(
                meta, reset_date=reset_date, configured_count=configured_count
            )
            if cycle is not None:
                meta["daily_cycle_email_summary"] = cycle

        if cycle is not None and _integer(cycle.get("configured_player_count")) == configured_count:
            seen = cycle.get("recovery_run_keys")
            seen = seen if isinstance(seen, list) else []
            key = str(run_key or run_timestamp).strip()
            if key and key not in seen:
                cycle["recovery_claim_totals"] = _add_counts(
                    cycle.get("recovery_claim_totals"), claim_totals
                )
                cycle["recovery_run_keys"] = (seen + [key])[-20:]
                recovered = cycle.get("recovered_player_last4")
                recovered = recovered if isinstance(recovered, list) else []
                for pid in processed_player_ids:
                    suffix = str(pid).strip()[-4:]
                    if suffix and suffix not in recovered:
                        recovered.append(suffix)
                cycle["recovered_player_last4"] = recovered[-20:]
                cycle["last_recovery_timestamp"] = run_timestamp

    if cycle is None or _integer(cycle.get("configured_player_count")) != configured_count:
        return None

    coverage = meta.get("full_claim_coverage")
    coverage = coverage if isinstance(coverage, dict) else {}
    complete = 0
    pending_suffixes = []
    for pid in roster:
        entry = coverage.get(pid)
        is_complete = bool(
            isinstance(entry, dict)
            and entry.get("reset_date") == reset_date
            and entry.get("status") == "complete"
            and not entry.get("retry_required", False)
        )
        if is_complete:
            complete += 1
        else:
            pending_suffixes.append(pid[-4:])

    primary = _normalise_counts(cycle.get("primary_claim_totals"))
    recovery = _normalise_counts(cycle.get("recovery_claim_totals"))
    combined = _add_counts(primary, recovery)
    recovery_keys = cycle.get("recovery_run_keys")
    recovery_keys = recovery_keys if isinstance(recovery_keys, list) else []
    recovered_ids = cycle.get("recovered_player_last4")
    recovered_ids = recovered_ids if isinstance(recovered_ids, list) else []

    return {
        "reset_date": reset_date,
        "configured_player_count": configured_count,
        "accounts_complete": complete,
        "accounts_pending": configured_count - complete,
        "pending_player_last4": pending_suffixes,
        "claim_totals": combined,
        "total_claims": _total_claims(combined),
        "primary_claim_totals": primary,
        "recovery_claim_totals": recovery,
        "recovery_claims_added": _total_claims(recovery),
        "recovery_runs_count": len(recovery_keys),
        "recovery_accounts_count": len(recovered_ids),
        "recovered_player_last4": list(recovered_ids),
        "primary_run_label": str(cycle.get("primary_run_label") or "Primary Run"),
        "primary_run_timestamp": str(cycle.get("primary_run_timestamp") or ""),
        "cycle_complete": complete == configured_count,
    }
