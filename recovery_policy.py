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
