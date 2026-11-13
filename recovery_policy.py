"""Shared, fail-closed policy for full-claim recovery candidate selection."""

from datetime import datetime, timedelta
from typing import Any

ALLOWED_RECOVERY_RUN_LABELS = {
    "Primary Run",
    "Manual Run",
    "Promotional Check #1",
    "Promotional Check #2",
    "Promotional Check #3",
    "Promotional Check #4",
    "Backup Run #1",
    "Backup Run #2",
    "Backup Run #3",
    "Backup Run #4",
}


ALLOWED_FULL_ROSTER_RUN_LABELS = {
    "Primary Run",
    "Manual Run",
    "Promotional Check #1",
    "Promotional Check #2",
    "Promotional Check #3",
    "Promotional Check #4",
    "Backup Run #1",
    "Backup Run #2",
    "Backup Run #3",
    "Backup Run #4",
}


def has_complete_full_roster_coverage(
    full_run: Any,
    *,
    player_count: int,
    full_end: datetime | None,
    full_start: datetime | None,
    now: datetime,
    reset_at: datetime,
) -> bool:
    """Return whether a complete full-claim roster cycle already ran after reset."""
    if not isinstance(full_run, dict):
        return False
    if full_run.get("run_label") not in ALLOWED_FULL_ROSTER_RUN_LABELS:
        return False
    if not all(isinstance(value, datetime) for value in (full_end, full_start, now, reset_at)):
        return False

    try:
        recorded_player_count = int(full_run.get("player_count", 0) or 0)
        full_claim_accounts = int(full_run.get("full_claim_accounts", recorded_player_count) or 0)
    except (TypeError, ValueError, OverflowError):
        return False

    return bool(
        full_start >= reset_at
        and full_end <= now
        and recorded_player_count == player_count
        and full_claim_accounts == player_count
    )


def full_claim_retry_ids(last_run: Any) -> list[str]:
    """Return eligible full-claim IDs only when scope, counts and run mode agree.

    The metadata is written by master_claimer.py. Requiring at least one account
    that actually traversed the full-claim or backup-reconciliation path prevents
    a promotional-only scan from becoming a recovery source.
    """
    if not isinstance(last_run, dict):
        return []
    if last_run.get("retry_candidate_scope") != "full_claim_only":
        return []

    raw_ids = last_run.get("retry_player_ids")
    if not isinstance(raw_ids, list):
        return []

    retry_ids = list(dict.fromkeys(
        item.strip() for item in raw_ids if isinstance(item, str) and item.strip()
    ))
    if not retry_ids:
        return []

    recorded_count = last_run.get("retry_player_count")
    if isinstance(recorded_count, bool) or not isinstance(recorded_count, int):
        return []
    if recorded_count != len(retry_ids):
        return []

    full_claim_accounts = last_run.get("full_claim_accounts")
    backup_reconciliation_accounts = last_run.get("backup_reconciliation_accounts")
    if (isinstance(full_claim_accounts, bool)
            or not isinstance(full_claim_accounts, int)
            or full_claim_accounts < 0):
        return []
    if (isinstance(backup_reconciliation_accounts, bool)
            or not isinstance(backup_reconciliation_accounts, int)
            or backup_reconciliation_accounts < 0):
        return []
    if full_claim_accounts + backup_reconciliation_accounts <= 0:
        return []

    return retry_ids




DAILY_RECOVERY_SLOT_LABELS = {
    "Promotional Check #1",
    "Promotional Check #2",
    "Promotional Check #3",
    "Promotional Check #4",
}


def _is_daily_only_target(targets: Any) -> bool:
    """Accept only a saved Daily failure; never broaden this path to other rewards."""
    if not isinstance(targets, dict) or targets.get("daily_unconfirmed") is not True:
        return False
    if targets.get("login_retry") or targets.get("processing_error"):
        return False
    if targets.get("store_unconfirmed_indices"):
        return False
    if targets.get("progression_unconfirmed"):
        return False
    if targets.get("event_retry_required"):
        return False
    if targets.get("event_rewards"):
        return False
    event_targets = targets.get("event_thresholds_by_program")
    if event_targets and (not isinstance(event_targets, dict) or len(event_targets) > 0):
        return False
    return True


def daily_only_pending_targets(
    meta: Any,
    player_ids: Any,
    *,
    reset_date: str,
) -> dict[str, dict[str, Any]]:
    """Return roster IDs with pending current-reset coverage and an exact Daily-only target."""
    if not isinstance(meta, dict) or not isinstance(reset_date, str) or not reset_date:
        return {}
    if not isinstance(player_ids, (list, tuple, set)):
        return {}

    coverage = meta.get("full_claim_coverage")
    if not isinstance(coverage, dict):
        return {}

    # Prefer the most recent saved target set, but retain last_full_run as the
    # durable source if an intervening Recovery Run or promo-only pass omitted it.
    target_sources = []
    for source_name in ("last_run", "last_full_run"):
        source = meta.get(source_name)
        targets_by_player = source.get("retry_targets_by_player") if isinstance(source, dict) else None
        if isinstance(targets_by_player, dict):
            target_sources.append(targets_by_player)

    candidates: dict[str, dict[str, Any]] = {}
    for raw_id in player_ids:
        if not isinstance(raw_id, str) or not raw_id.strip():
            continue
        player_id = raw_id.strip()
        entry = coverage.get(player_id)
        if not isinstance(entry, dict):
            continue
        if entry.get("reset_date") != reset_date:
            continue
        if entry.get("status") != "pending" or entry.get("retry_required") is not True:
            continue

        saved_targets = None
        for source in target_sources:
            candidate = source.get(player_id)
            if _is_daily_only_target(candidate):
                saved_targets = dict(candidate)
                break
        if saved_targets is not None:
            candidates[player_id] = saved_targets

    return candidates


def manual_daily_recovery_targets(
    meta: Any,
    player_ids: Any,
    *,
    reset_date: str,
    player_id: str,
) -> dict[str, dict[str, Any]]:
    """Select exactly one configured account with a saved current-reset Daily-only failure.

    This manual path deliberately ignores scheduled-slot counters: it is an
    explicit, one-account recovery request, not a scheduled backup attempt.
    """
    if not isinstance(player_id, str) or not player_id.strip():
        return {}
    requested_id = player_id.strip()
    if not isinstance(player_ids, (list, tuple, set)):
        return {}
    configured_ids = {
        item.strip() for item in player_ids
        if isinstance(item, str) and item.strip()
    }
    if requested_id not in configured_ids:
        return {}

    pending = daily_only_pending_targets(
        meta, player_ids, reset_date=reset_date
    )
    targets = pending.get(requested_id)
    if not isinstance(targets, dict):
        return {}
    return {requested_id: dict(targets)}


def eligible_daily_recovery_ids(
    meta: Any,
    player_ids: Any,
    *,
    reset_date: str,
    slot_label: str,
    max_attempts: int = 4,
) -> list[str]:
    """Return pending Daily-only IDs not yet attempted in this backup slot or cap."""
    if slot_label not in DAILY_RECOVERY_SLOT_LABELS:
        return []
    try:
        max_attempts = max(1, min(4, int(max_attempts)))
    except (TypeError, ValueError, OverflowError):
        max_attempts = 4

    pending = daily_only_pending_targets(meta, player_ids, reset_date=reset_date)
    coverage = meta.get("full_claim_coverage", {}) if isinstance(meta, dict) else {}
    eligible = []
    for player_id in pending:
        entry = coverage.get(player_id, {})
        attempted = entry.get("daily_recovery_slots_attempted", [])
        if not isinstance(attempted, list):
            attempted = []
        attempted_slots = list(dict.fromkeys(
            item.strip() for item in attempted if isinstance(item, str) and item.strip()
        ))
        if slot_label in attempted_slots or len(attempted_slots) >= max_attempts:
            continue
        eligible.append(player_id)
    return eligible


def is_recent_full_claim_recovery(
    last_run: Any,
    *,
    last_end: datetime | None,
    last_start: datetime | None,
    now: datetime,
    reset_at: datetime,
    max_age_hours: int = 4,
) -> bool:
    """Apply the shared label, reset-window and freshness gates to retry metadata."""
    if not full_claim_retry_ids(last_run):
        return False
    if last_run.get("run_label") not in ALLOWED_RECOVERY_RUN_LABELS:
        return False
    if not isinstance(last_end, datetime) or not isinstance(last_start, datetime):
        return False
    if not isinstance(now, datetime) or not isinstance(reset_at, datetime):
        return False

    age_seconds = (now - last_end).total_seconds()
    return bool(
        last_start >= reset_at
        and last_end <= now
        and 0 <= age_seconds <= max_age_hours * 3600
    )
