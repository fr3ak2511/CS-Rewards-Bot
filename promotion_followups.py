"""Persistent, non-claiming follow-up tracking for unresolved Store promotions.

This module records popup-blocked/unconfirmed promotional outcomes separately
from full-claim recovery. Existing scheduled Promotional Check runs perform the
next safe promotional recheck; this module never dispatches or requests a full
claim retry.
"""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from typing import Any, Callable

_OPEN_STATUSES = {"pending_recheck", "still_blocked"}
_UNRESOLVED_SOURCE_STATUSES = {
    "popup_blocked": "still_blocked",
    "claim_unconfirmed": "pending_recheck",
    "click_failed": "pending_recheck",
    "available": "pending_recheck",
}
_TERMINAL_SOURCE_STATUSES = {
    "claimed_this_run": "resolved",
    "portal_claimed": "resolved",
    "unavailable": "not_retryable",
}
_STATUS_LABELS = {
    "pending_recheck": "⏳ Pending recheck",
    "still_blocked": "⛔ Still blocked by popup safety",
    "resolved": "✅ Resolved",
    "not_retryable": "ℹ️ No longer visible — not retryable",
}
_GENERIC_POPUP_KEYS = {"store-popup-blocked"}
_GENERIC_POPUP_TITLES = {"", "store promotion popup", "promotion popup", "store popup"}


def _now_text(now: datetime | str | None) -> str:
    if isinstance(now, str):
        return now
    if isinstance(now, datetime):
        return now.isoformat()
    return datetime.now(timezone.utc).isoformat()


def _promotion_key(promotion: dict[str, Any]) -> str:
    status = str(promotion.get("status") or "").strip()
    title = " ".join(str(promotion.get("title") or "").strip().lower().split())
    # The portal can emit one generic safety-block row rather than a named
    # offer. Keep that blocker separate from actual offer identities so a
    # later clean scan can resolve it even when other offers are visible.
    if status == "popup_blocked" and title in _GENERIC_POPUP_TITLES:
        return "store-popup-blocked"
    key = str(promotion.get("key") or "").strip()
    if key:
        return key[:180]
    if status == "popup_blocked":
        return "store-popup-blocked"
    title = title or "store-promotion"
    return "title:" + title[:140]


def _is_generic_popup_blocker(promotion: dict[str, Any]) -> bool:
    return (
        str(promotion.get("status") or "") == "popup_blocked"
        and _promotion_key(promotion) in _GENERIC_POPUP_KEYS
    )


def filter_followup_results(
    results: list[dict[str, Any]],
    meta: dict[str, Any],
    is_promotion_detail_worthy: Callable[[dict[str, Any]], bool],
) -> list[dict[str, Any]]:
    """Copy results while retaining only email-eligible promotion observations.

    The supplied callback is the existing Email V2 free/actionable offer filter.
    Popup-blocked observations are tested as a hypothetical unconfirmed claim,
    so their special early-return status cannot bypass paid/discounted filtering.
    A generic Store popup blocker is retained because it contains no offer detail.
    Terminal observations are retained only for an already tracked open item,
    allowing a previously approved follow-up to resolve without introducing a
    new paid/discounted offer into the tracker.
    """
    if not callable(is_promotion_detail_worthy):
        raise TypeError("is_promotion_detail_worthy must be callable")
    tracker = meta.get("promotional_followups", {}) if isinstance(meta, dict) else {}
    if not isinstance(tracker, dict):
        tracker = {}

    filtered_results: list[dict[str, Any]] = []
    for result in results or []:
        if not isinstance(result, dict):
            continue
        player_id = str(result.get("pid") or "").strip()
        existing_items = tracker.get(player_id, {}) if player_id else {}
        if not isinstance(existing_items, dict):
            existing_items = {}

        selected: list[dict[str, Any]] = []
        observations = result.get("promotions_observed") or []
        if isinstance(observations, list):
            for promotion in observations:
                if not isinstance(promotion, dict):
                    continue
                source_status = str(promotion.get("status") or "unknown")
                key = _promotion_key(promotion)
                existing = existing_items.get(key)
                existing_is_open = (
                    isinstance(existing, dict)
                    and existing.get("status") in _OPEN_STATUSES
                )

                if _is_generic_popup_blocker(promotion):
                    selected.append(dict(promotion))
                    continue

                # A terminal observation can close a previously approved item;
                # it cannot create a new follow-up or reveal a newly seen offer.
                if source_status in _TERMINAL_SOURCE_STATUSES:
                    if existing_is_open:
                        selected.append(dict(promotion))
                    continue

                if source_status not in _UNRESOLVED_SOURCE_STATUSES:
                    continue

                # _promotion_detail_worthy() intentionally returns True early
                # for popup_blocked results. Substitute a normal unconfirmed
                # status to ensure the existing free/actionable checks still run.
                eligibility_probe = dict(promotion)
                eligibility_probe["status"] = "claim_unconfirmed"
                if is_promotion_detail_worthy(eligibility_probe):
                    selected.append(dict(promotion))

        copied = dict(result)
        copied["promotions_observed"] = selected
        filtered_results.append(copied)
    return filtered_results


def _reason_for(promotion: dict[str, Any], state: str) -> str:
    if state == "still_blocked":
        # Keep implementation tracebacks in Actions logs, not the email body.
        return "Popup safety check blocked this promotion. No claim click was attempted."
    supplied = str(promotion.get("reason") or promotion.get("error") or "").strip()
    if supplied:
        return supplied[:240]
    if state == "pending_recheck":
        return "The promotional claim was not confirmed; a later promotional scan will recheck it."
    if state == "resolved":
        return "The portal confirmed the promotion as claimed."
    return "The offer was no longer available during a completed Store scan."


def _public_item(item: dict[str, Any]) -> dict[str, Any]:
    """Return an HTML-safe item with a separate internal merge key and masked label."""
    player_id = str(item.get("player_id") or "")
    return {
        # Internal-only key used to deduplicate Need Attention by account.
        # HTML and run-summary renderers use player_label / last four only.
        "player_key": player_id,
        "player_label": f"Player …{player_id[-4:]}" if player_id else "Player",
        "key": str(item.get("key") or ""),
        "title": str(item.get("title") or "Store promotion"),
        "status": str(item.get("status") or "pending_recheck"),
        "reason": str(item.get("reason") or ""),
        "attempts": int(item.get("attempts", 0) or 0),
        "first_seen_at": item.get("first_seen_at"),
        "last_checked_at": item.get("last_checked_at"),
        "last_run_label": item.get("last_run_label"),
    }



_LEGACY_RUN_3796_SUFFIXES = {"B91F", "F01A", "B2D8", "0765", "BD48"}
_LEGACY_RUN_3796_TIMESTAMP = "2026-11-07T07:19:16.290477"
_LEGACY_SEED_FLAG = "promotion_followup_legacy_seed_v1"


def seed_legacy_popup_followups(
    meta: dict[str, Any],
    results: list[dict[str, Any]],
    *,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    """Seed the five known Run #3796 popup exceptions once, using masked suffixes.

    The old run's bot_meta.json retained only an aggregate exception count, so
    these five masked suffixes are the only safe bootstrap keys available from
    the reviewed report. Each suffix must match exactly one current result PID;
    ambiguous or missing matches are not guessed and keep the migration pending.
    """
    if not isinstance(meta, dict):
        raise TypeError("meta must be a dict")
    if meta.get(_LEGACY_SEED_FLAG):
        return {"seeded_count": 0, "already_seeded": True, "unmatched_suffixes": []}

    results_by_suffix: dict[str, list[str]] = {}
    for result in results or []:
        if not isinstance(result, dict):
            continue
        player_id = str(result.get("pid") or "").strip()
        if player_id:
            results_by_suffix.setdefault(player_id[-4:].upper(), []).append(player_id)

    store = meta.get("promotional_followups")
    if not isinstance(store, dict):
        store = {}
        meta["promotional_followups"] = store

    matched_suffixes: list[str] = []
    newly_seeded_suffixes: list[str] = []
    unmatched_suffixes: list[str] = []
    for suffix in sorted(_LEGACY_RUN_3796_SUFFIXES):
        matches = results_by_suffix.get(suffix, [])
        if len(matches) != 1:
            unmatched_suffixes.append(suffix)
            continue

        player_id = matches[0]
        matched_suffixes.append(suffix)
        player_items = store.get(player_id)
        if not isinstance(player_items, dict):
            player_items = {}
        existing = player_items.get("store-popup-blocked")
        if not isinstance(existing, dict):
            player_items["store-popup-blocked"] = {
                "player_id": player_id,
                "key": "store-popup-blocked",
                "title": "Store promotion popup",
                "status": "pending_recheck",
                "reason": "Popup safety blocked this account during full run #3796. Waiting for the first post-release safe Store recheck.",
                "attempts": 0,
                "first_seen_at": _LEGACY_RUN_3796_TIMESTAMP,
                "last_checked_at": None,
                "last_run_label": "Legacy bootstrap · Run #3796",
            }
            newly_seeded_suffixes.append(suffix)
        store[player_id] = player_items

    if not unmatched_suffixes:
        meta.pop("promotion_followup_legacy_seed_attempt", None)
        meta[_LEGACY_SEED_FLAG] = {
            "source_run": 3796,
            "source_date": "2026-11-07",
            "seeded_at": _now_text(now),
            "seeded_suffixes": matched_suffixes,
        }
    else:
        meta["promotion_followup_legacy_seed_attempt"] = {
            "source_run": 3796,
            "attempted_at": _now_text(now),
            "seeded_suffixes": matched_suffixes,
            "unmatched_suffixes": unmatched_suffixes,
        }

    return {
        "seeded_count": len(newly_seeded_suffixes),
        "matched_count": len(matched_suffixes),
        "already_seeded": False,
        "seeded_suffixes": matched_suffixes,
        "unmatched_suffixes": unmatched_suffixes,
    }

def update_promotion_followups(
    meta: dict[str, Any],
    results: list[dict[str, Any]],
    run_label: str,
    *,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    """Record unresolved promotions and resolve them only after a safe scan.

    A scan is conclusive only when the account's Store scan completed and the
    dynamic promotion scan did not fail. A popup blocker is an observed state,
    not a failed full-claim retry. No retry_player_ids are written by this code.
    """
    if not isinstance(meta, dict):
        raise TypeError("meta must be a dict")

    stamp = _now_text(now)
    store = meta.get("promotional_followups")
    if not isinstance(store, dict):
        store = {}
        meta["promotional_followups"] = store

    updated: dict[tuple[str, str], dict[str, Any]] = {}
    for result in results or []:
        player_id = str(result.get("pid") or "").strip()
        if not player_id:
            continue
        player_items = store.get(player_id, {})
        if not isinstance(player_items, dict):
            player_items = {}

        observations = result.get("promotions_observed") or []
        if not isinstance(observations, list):
            observations = []
        scan_completed = bool(result.get("promotion_scan_completed"))
        scan_inconclusive = bool(result.get("promotion_detection_failed"))
        observed_keys: set[str] = set()
        any_popup_blocked = False

        for promotion in observations:
            if not isinstance(promotion, dict):
                continue
            source_status = str(promotion.get("status") or "unknown")
            state = _UNRESOLVED_SOURCE_STATUSES.get(source_status)
            terminal_state = _TERMINAL_SOURCE_STATUSES.get(source_status)
            if state is None and terminal_state is None:
                continue

            key = _promotion_key(promotion)
            observed_keys.add(key)
            any_popup_blocked = any_popup_blocked or source_status == "popup_blocked"
            existing = player_items.get(key)
            if state:
                if not isinstance(existing, dict):
                    existing = {
                        "player_id": player_id,
                        "key": key,
                        "title": str(promotion.get("title") or "Store promotion")[:160],
                        "first_seen_at": stamp,
                        "attempts": 0,
                    }
                existing.update({
                    "player_id": player_id,
                    "key": key,
                    # Preserve the first email-approved title. A later scan may
                    # expose changed commercial text for the same campaign key.
                    "title": str(existing.get("title") or promotion.get("title") or "Store promotion")[:160],
                    "status": state,
                    "reason": _reason_for(promotion, state),
                    "attempts": int(existing.get("attempts", 0) or 0) + 1,
                    "last_seen_at": stamp,
                    "last_checked_at": stamp,
                    "last_run_label": str(run_label or "Reward Run"),
                })
                player_items[key] = existing
                updated[(player_id, key)] = dict(existing)
            elif isinstance(existing, dict) and existing.get("status") in _OPEN_STATUSES:
                existing.update({
                    "status": terminal_state,
                    "reason": _reason_for(promotion, terminal_state),
                    "last_seen_at": stamp,
                    "last_checked_at": stamp,
                    "last_run_label": str(run_label or "Reward Run"),
                    "resolved_at": stamp,
                })
                updated[(player_id, key)] = dict(existing)

        # Existing open items absent from this scan are only closed when the
        # complete scan is conclusive and no popup blocker remains on the page.
        if not scan_completed or scan_inconclusive:
            continue
        for key, existing in list(player_items.items()):
            if not isinstance(existing, dict) or existing.get("status") not in _OPEN_STATUSES:
                continue
            if key in observed_keys:
                continue
            if any_popup_blocked or result.get("promotion_popup_blocked"):
                existing.update({
                    "status": "still_blocked",
                    "reason": "A popup safety blocker remained during this Store scan; no promotion click was attempted.",
                    "attempts": int(existing.get("attempts", 0) or 0) + 1,
                    "last_checked_at": stamp,
                    "last_run_label": str(run_label or "Reward Run"),
                })
            else:
                terminal_state = "resolved" if key == "store-popup-blocked" else "not_retryable"
                existing.update({
                    "status": terminal_state,
                    "reason": (
                        "The Store popup blocker was absent during a completed safe recheck."
                        if terminal_state == "resolved"
                        else "The previously unresolved offer was not visible during a completed safe Store recheck."
                    ),
                    "last_checked_at": stamp,
                    "last_run_label": str(run_label or "Reward Run"),
                    "resolved_at": stamp,
                })
            updated[(player_id, key)] = dict(existing)

        if player_items:
            store[player_id] = player_items

    all_open = []
    for player_items in store.values():
        if not isinstance(player_items, dict):
            continue
        for item in player_items.values():
            if isinstance(item, dict) and item.get("status") in _OPEN_STATUSES:
                all_open.append(_public_item(item))

    updates = [_public_item(item) for item in updated.values()]
    still_blocked = sum(1 for item in all_open if item["status"] == "still_blocked")
    pending = sum(1 for item in all_open if item["status"] == "pending_recheck")
    resolved_this_run = sum(1 for item in updates if item["status"] == "resolved")
    not_retryable_this_run = sum(1 for item in updates if item["status"] == "not_retryable")
    return {
        "open_count": len(all_open),
        "still_blocked_count": still_blocked,
        "pending_recheck_count": pending,
        "resolved_this_run_count": resolved_this_run,
        "not_retryable_this_run_count": not_retryable_this_run,
        "open_items": all_open,
        "updated_items": updates,
    }


def build_promotional_followup_section(summary: dict[str, Any] | None) -> str:
    """Render persistent promotion follow-ups in the HTML email body."""
    summary = summary if isinstance(summary, dict) else {}
    open_items = summary.get("open_items") or []
    updated_items = summary.get("updated_items") or []
    if not open_items and not updated_items:
        return ""

    def row(item: dict[str, Any]) -> str:
        label = _STATUS_LABELS.get(str(item.get("status") or ""), "⚠️ Status unknown")
        player = escape(str(item.get("player_label") or "Player"))
        title = escape(str(item.get("title") or "Store promotion"))
        reason = escape(str(item.get("reason") or ""))
        attempts = int(item.get("attempts", 0) or 0)
        detail = f"{reason} · Checks: {attempts}" if attempts else reason
        return (
            "<div class='v2-item'>"
            f"<div><strong>{title}</strong><small>{player}</small></div>"
            f"<span class='v2-result'>{escape(label)}<small>{escape(detail)}</small></span>"
            "</div>"
        )

    open_html = "".join(row(item) for item in open_items if isinstance(item, dict))
    terminal_updates = [
        item for item in updated_items
        if isinstance(item, dict) and item.get("status") in {"resolved", "not_retryable"}
    ]
    updates_html = "".join(row(item) for item in terminal_updates)
    parts = [
        "<section class='v2-section'>",
        "<div class='v2-section-head'><h2>Promotion Follow-ups</h2>",
        f"<span>{len(open_items)} open · {summary.get('resolved_this_run_count', 0)} resolved · {summary.get('not_retryable_this_run_count', 0)} no longer available</span></div>",
    ]
    if open_html:
        parts.extend(["<div><strong>Still requiring follow-up</strong></div>", open_html])
    if updates_html:
        parts.extend(["<div style='margin-top:10px'><strong>Updates this run</strong></div>", updates_html])
    parts.append("</section>")
    return "".join(parts)
