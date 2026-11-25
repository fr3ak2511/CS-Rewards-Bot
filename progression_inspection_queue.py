"""Durable, non-claim retry queue for safely deferred Progression inspections.

This state is intentionally separate from claim_history and full_claim_coverage:
a blocked inspection is not evidence of a failed claim and must not widen the
normal full-claim recovery scope.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from typing import Any

QUEUE_KEY = "progression_deferred_inspections"
HISTORY_KEY = "progression_deferred_inspection_history"
_ALLOWED_SLOTS = {
    f"Promotional Check #{number}" for number in range(1, 5)
} | {"Primary Run", "Manual Run", "Immediate Progression Recovery"}
_MAX_ATTEMPTS_PER_RESET = 4
_MAX_HISTORY = 50
_MANUAL_ATTENTION_REPEAT_THRESHOLD = 3


def _queue(meta: dict[str, Any]) -> dict[str, Any]:
    value = meta.get(QUEUE_KEY)
    if not isinstance(value, dict):
        value = {}
        meta[QUEUE_KEY] = value
    return value


def _history(meta: dict[str, Any]) -> list[dict[str, Any]]:
    value = meta.get(HISTORY_KEY)
    if not isinstance(value, list):
        value = []
        meta[HISTORY_KEY] = value
    return value


def _safe_reason(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    # Popup context contains large DOM fragments. Keep the operational reason,
    # not raw page diagnostics, in long-lived runtime metadata.
    raw = raw.split("popup_context=", 1)[0].strip().rstrip(" ,;")
    return raw[:240].rstrip() or None


def _outcome_category(result: dict[str, Any], reason: str | None) -> str:
    """Classify a bounded PP outcome without persisting raw DOM diagnostics."""
    status = str(result.get("status") or "").strip().casefold()
    if result.get("progression_unconfirmed"):
        return "claim_unconfirmed"
    if status == "login failed":
        return "login_failed"
    if status in {"error", "failed"}:
        return "processing_failed"

    text = str(reason or "").casefold()
    if "popup/overlay" in text or "fullscreen_overlay" in text or "blocking_dialog" in text:
        return "overlay_blocked"
    if "no currently visible claimable reward" in text or "empty snapshot" in text:
        return "empty_scan"
    if any(token in text for token in (
        "snapshot was unavailable", "page validation failed",
        "could not establish", "carousel scan", "inspection was incomplete",
        "page snapshot", "inspection could not be completed",
    )):
        return "incomplete_scan"
    return "other_pending"


def _record_latest_outcome(
    entry: dict[str, Any],
    result: dict[str, Any],
    *,
    reason: str | None,
    now: str,
) -> None:
    """Persist a stable outcome fingerprint and flag three identical repeats."""
    category = _outcome_category(result, reason)
    # Category-only fingerprints deliberately ignore changing popup titles,
    # countdowns and raw DOM diagnostics.
    fingerprint = hashlib.sha256(category.encode("utf-8")).hexdigest()[:20]
    previous = str(entry.get("last_outcome_fingerprint") or "")
    try:
        previous_count = max(0, int(entry.get("same_outcome_count", 0) or 0))
    except (TypeError, ValueError, OverflowError):
        previous_count = 0
    same_count = previous_count + 1 if previous == fingerprint else 1

    entry["last_outcome_category"] = category
    entry["last_outcome_fingerprint"] = fingerprint
    entry["same_outcome_count"] = same_count
    entry["last_outcome_at"] = now
    manual_attention = category == "claim_unconfirmed" or (
        category in {
            "login_failed", "processing_failed", "overlay_blocked",
            "empty_scan", "incomplete_scan", "other_pending",
        }
        and same_count >= _MANUAL_ATTENTION_REPEAT_THRESHOLD
    )
    entry["manual_attention_required"] = bool(manual_attention)
    if manual_attention:
        entry["manual_attention_since"] = entry.get("manual_attention_since") or now
        entry["manual_attention_reason"] = (
            "Claim outcome requires exact-target verification"
            if category == "claim_unconfirmed"
            else f"{same_count} consecutive PP attempts produced the same {category.replace('_', ' ')} outcome"
        )
    else:
        entry.pop("manual_attention_since", None)
        entry.pop("manual_attention_reason", None)


def get_pending_progression_inspection(
    meta: dict[str, Any],
    player_id: str,
) -> dict[str, Any] | None:
    """Return a copy of a pending entry; never expose mutable metadata internals."""
    if not isinstance(meta, dict):
        return None
    queue = meta.get(QUEUE_KEY)
    if not isinstance(queue, dict):
        return None
    entry = queue.get(str(player_id or "").strip())
    if not isinstance(entry, dict):
        return None
    if entry.get("status") not in {"deferred", "claim_unconfirmed"}:
        return None
    return deepcopy(entry)


def record_progression_inspection_result(
    meta: dict[str, Any],
    player_id: str,
    result: dict[str, Any],
    *,
    run_label: str,
    now: str,
    source_run_id: str | None = None,
    reset_date: str | None = None,
) -> str | None:
    """Queue a deferred inspection, retain exact unconfirmed targets, or resolve it.

    This function never edits claim history or full-claim coverage. It only
    records an inspection obligation in bot_meta.
    """
    if not isinstance(meta, dict) or not isinstance(result, dict):
        return None
    pid = str(player_id or "").strip()
    if not pid:
        return None

    result = dict(result)
    deferred = bool(result.get("progression_deferred"))
    queue = meta.get(QUEUE_KEY)
    if not isinstance(queue, dict):
        if not deferred:
            return None
        queue = {}
        meta[QUEUE_KEY] = queue
    entry = queue.get(pid)
    entry = entry if isinstance(entry, dict) else None

    unconfirmed = bool(result.get("progression_unconfirmed"))
    signature = str(
        result.get("progression_unconfirmed_card_signature") or ""
    ).strip()

    # A queue slot is reserved before account login. Record the authentication
    # or processing failure as the latest PP outcome instead of retaining an
    # older empty-snapshot reason. This does not start a PP page or claim click.
    result_status = str(result.get("status") or "").strip()
    reserved_attempt = bool(result.get("progression_reinspection_attempted"))
    if (
        entry is not None
        and reserved_attempt
        and result_status in {"Login Failed", "Error", "Failed"}
        and not deferred
        and not unconfirmed
    ):
        result["progression_deferred"] = True
        result["progression_checked"] = False
        result["progression_deferred_reason"] = (
            "Login failed before the reserved Progression inspection; no PP page was inspected and no claim click was attempted."
            if result_status == "Login Failed"
            else "Account processing failed before the reserved Progression inspection; no claim click was attempted."
        )
        deferred = True

    if deferred:
        if entry is not None and not result.get("progression_reinspection_attempted"):
            # The queue was intentionally not serviced in this run (for example,
            # a Daily-only pass or a slot already consumed). Preserve its original
            # source/attempt metadata; do not record a skipped slot as a new defer.
            return "retained"
        if entry is None:
            entry = {
                "status": "deferred",
                "first_deferred_at": now,
                "first_source_run_label": str(run_label or ""),
                "first_source_run_id": str(source_run_id or "") or None,
                "attempt_reset_date": None,
                "attempted_slots": [],
                "total_attempts": 0,
                "target_card_signature": None,
                "requires_exact_target": False,
            }
            # The run that first discovered the deferral has already spent this
            # slot inspecting Progression. Count it so a duplicate dispatch with
            # the same run label cannot immediately consume another queue attempt.
            source_slot = str(run_label or "").strip()
            source_reset = str(reset_date or str(now)[:10]).strip()
            if source_slot in _ALLOWED_SLOTS and source_reset:
                entry["attempt_reset_date"] = source_reset
                entry["attempted_slots"] = [source_slot]
                entry["total_attempts"] = 1
                entry["last_attempt_at"] = now
                entry["last_attempt_slot"] = source_slot
        entry["status"] = "claim_unconfirmed" if unconfirmed else "deferred"
        entry["last_deferred_at"] = now
        entry["last_source_run_label"] = str(run_label or "")
        entry["last_source_run_id"] = str(source_run_id or "") or None
        reason = _safe_reason(result.get("progression_deferred_reason"))
        if reason:
            entry["last_reason"] = reason
        _record_latest_outcome(entry, result, reason=reason, now=now)
        if signature:
            entry["target_card_signature"] = signature[:512]
            entry["requires_exact_target"] = True
        elif unconfirmed:
            # Never fall back to a generic scan when an earlier click's exact
            # target identity is unavailable.
            entry["requires_exact_target"] = True
        queue[pid] = entry
        return "queued"

    if entry is None:
        # A first-time dispatched-but-unconfirmed claim belongs to the normal
        # exact-target recovery path, not this inspection-only queue.
        return None

    if unconfirmed:
        entry["status"] = "claim_unconfirmed"
        entry["last_unconfirmed_at"] = now
        reason = _safe_reason(result.get("progression_deferred_reason"))
        if reason:
            entry["last_reason"] = reason
        entry["last_source_run_label"] = str(run_label or "")
        entry["last_source_run_id"] = str(source_run_id or "") or None
        _record_latest_outcome(entry, result, reason=reason, now=now)
        if signature:
            entry["target_card_signature"] = signature[:512]
            entry["requires_exact_target"] = True
        else:
            # Once a claim may have been dispatched, never allow the next
            # inspection to fall back to a generic scan. Preserve an older
            # exact signature if one exists; otherwise fail closed on the next run.
            entry["requires_exact_target"] = True
            entry["target_card_signature"] = (
                entry.get("target_card_signature") or None
            )
        queue[pid] = entry
        return "retained"

    if (
        result.get("progression_checked") is True
        and str(result.get("status") or "") not in {"Login Failed", "Error", "Failed"}
    ):
        confirmed_claims = int(result.get("progression", 0) or 0)
        inspection_complete = result.get("progression_inspection_complete") is True
        if confirmed_claims <= 0 and not inspection_complete:
            # Do not turn a momentary empty DOM snapshot into a resolved queue
            # item. Keep it eligible for another bounded inspection slot.
            return "retained"

        resolved = deepcopy(entry)
        resolved["status"] = "resolved"
        resolved["resolved_at"] = now
        resolved["resolution_run_label"] = str(run_label or "")
        resolved["resolution_run_id"] = str(source_run_id or "") or None
        resolved["resolution"] = (
            "claim_confirmed" if confirmed_claims > 0
            else "inspection_completed"
        )
        queue.pop(pid, None)
        history = _history(meta)
        history.append(resolved)
        meta[HISTORY_KEY] = history[-_MAX_HISTORY:]
        return "resolved"

    return None



def eligible_progression_inspection_ids(
    meta: dict[str, Any],
    player_ids: list[str] | tuple[str, ...],
    *,
    reset_date: str,
    slot_label: str,
    max_attempts: int = _MAX_ATTEMPTS_PER_RESET,
    source_run_id: str | None = None,
) -> list[str]:
    """Return current-reset queued IDs eligible for one inspection slot.

    This is read-only. The executor must reserve each ID again after it acquires
    the shared claim concurrency lock.
    """
    if not isinstance(meta, dict):
        return []
    slot = str(slot_label or "").strip()
    reset = str(reset_date or "").strip()
    if slot not in _ALLOWED_SLOTS or not reset:
        return []

    queue = meta.get(QUEUE_KEY)
    if not isinstance(queue, dict):
        return []

    selected: list[str] = []
    seen: set[str] = set()
    for raw_pid in player_ids or []:
        pid = str(raw_pid or "").strip()
        if not pid or pid in seen:
            continue
        seen.add(pid)
        entry = queue.get(pid)
        if not isinstance(entry, dict) or entry.get("status") not in {
            "deferred", "claim_unconfirmed"
        }:
            continue
        if str(entry.get("attempt_reset_date") or "") != reset:
            continue
        if source_run_id and str(entry.get("last_source_run_id") or "") != str(source_run_id).strip():
            continue

        attempted = entry.get("attempted_slots")
        if not isinstance(attempted, list):
            attempted = []
        attempted = list(dict.fromkeys(
            str(item).strip() for item in attempted if str(item).strip()
        ))
        if slot in attempted or len(attempted) >= max(1, int(max_attempts)):
            continue

        if entry.get("requires_exact_target"):
            signature = str(entry.get("target_card_signature") or "").strip()
            fragments = entry.get("target_card_text_fragments")
            has_fragments = (
                isinstance(fragments, (list, tuple))
                and any(str(item).strip() for item in fragments)
            )
            if not signature and not has_fragments:
                # Never convert a prior, unconfirmed click into a generic scan.
                continue

        selected.append(pid)
    return selected


def mark_progression_inspection_attempt(
    meta: dict[str, Any],
    player_id: str,
    *,
    slot_label: str,
    reset_date: str,
    now: str,
    max_attempts: int = _MAX_ATTEMPTS_PER_RESET,
) -> dict[str, Any] | None:
    """Reserve one backup slot for one queued account, at most once per slot/reset."""
    if not isinstance(meta, dict):
        return None
    slot = str(slot_label or "").strip()
    if slot not in _ALLOWED_SLOTS:
        return None
    pid = str(player_id or "").strip()
    entry = _queue(meta).get(pid)
    if not isinstance(entry, dict) or entry.get("status") not in {"deferred", "claim_unconfirmed"}:
        return None

    reset = str(reset_date or "").strip()
    if not reset:
        return None
    if entry.get("attempt_reset_date") != reset:
        entry["attempt_reset_date"] = reset
        entry["attempted_slots"] = []

    attempted = entry.get("attempted_slots")
    if not isinstance(attempted, list):
        attempted = []
    attempted = list(dict.fromkeys(
        str(item) for item in attempted if str(item).strip()
    ))
    if slot in attempted or len(attempted) >= max(1, int(max_attempts)):
        entry["attempted_slots"] = attempted
        return None

    attempted.append(slot)
    entry["attempted_slots"] = attempted
    entry["total_attempts"] = int(entry.get("total_attempts", 0) or 0) + 1
    entry["last_attempt_at"] = now
    entry["last_attempt_slot"] = slot
    _queue(meta)[pid] = entry
    return deepcopy(entry)


__all__ = [
    "get_pending_progression_inspection",
    "eligible_progression_inspection_ids",
    "mark_progression_inspection_attempt",
    "record_progression_inspection_result",
]
