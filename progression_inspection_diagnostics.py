"""Privacy-minimized, read-only Progression DOM diagnostics."""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

_MAX_CONTAINERS = 3
_MAX_CARDS_PER_CONTAINER = 10
_MAX_TEXT = 320
_MAX_SNAPSHOTS_PER_PLAYER = 6
_HEX_ID_RE = re.compile(r"(?i)\b[0-9a-f]{16,}\b")
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_URL_RE = re.compile(r"https?://\S+", re.I)
_TOKEN_RE = re.compile(r"(?i)\b(?:token|session|authorization|password|secret)=?\s*[^\s,;]+")


def _safe_text(value: Any, limit: int = _MAX_TEXT) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = _URL_RE.sub("[url-redacted]", text)
    text = _EMAIL_RE.sub("[email-redacted]", text)
    text = _HEX_ID_RE.sub("[id-redacted]", text)
    text = _TOKEN_RE.sub("[credential-redacted]", text)
    return text[:max(0, int(limit))]


def _safe_number(value: Any, *, maximum: int = 10000000) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if 0 <= number <= maximum else None


def sanitize_progression_inspection_snapshot(raw: Any) -> dict[str, Any] | None:
    """Whitelist bounded DOM evidence; omit raw page text and full URLs."""
    if not isinstance(raw, dict):
        return None
    path = _safe_text(urlsplit(str(raw.get("page_path") or "")).path, 160)
    viewport = raw.get("viewport") if isinstance(raw.get("viewport"), dict) else {}
    containers = raw.get("carousel_containers")
    if not isinstance(containers, list):
        containers = []
    safe_containers = []
    for container in containers[:_MAX_CONTAINERS]:
        if not isinstance(container, dict):
            continue
        cards = container.get("captured_cards")
        if not isinstance(cards, list):
            cards = []
        safe_cards = []
        for card in cards[:_MAX_CARDS_PER_CONTAINER]:
            if not isinstance(card, dict):
                continue
            labels = card.get("button_labels")
            labels = labels if isinstance(labels, list) else []
            statuses = card.get("exact_status_text_hints")
            statuses = statuses if isinstance(statuses, list) else []
            milestones = card.get("milestone_number_hints")
            milestones = milestones if isinstance(milestones, list) else []
            rect = card.get("rect") if isinstance(card.get("rect"), dict) else {}
            safe_cards.append({
                "tag": _safe_text(card.get("tag"), 24),
                "class_name": _safe_text(card.get("class_name"), 120),
                "test_id": _safe_text(card.get("test_id"), 80),
                "text_snippet": _safe_text(card.get("text_snippet"), _MAX_TEXT),
                "button_labels": [_safe_text(item, 48) for item in labels[:8]],
                "exact_status_text_hints": [_safe_text(item, 48) for item in statuses[:8]],
                "milestone_number_hints": [
                    number for item in milestones[:25]
                    if (number := _safe_number(item, maximum=1000)) is not None
                ],
                "rect": {key: _safe_number(rect.get(key), maximum=100000) for key in ("x", "y", "width", "height")},
            })
        rect = container.get("rect") if isinstance(container.get("rect"), dict) else {}
        safe_containers.append({
            "index": _safe_number(container.get("index"), maximum=20),
            "tag": _safe_text(container.get("tag"), 24),
            "class_name": _safe_text(container.get("class_name"), 120),
            "test_id": _safe_text(container.get("test_id"), 80),
            "rect": {key: _safe_number(rect.get(key), maximum=100000) for key in ("x", "y", "width", "height")},
            "client_width": _safe_number(container.get("client_width"), maximum=100000),
            "scroll_width": _safe_number(container.get("scroll_width"), maximum=1000000),
            "scroll_left": _safe_number(container.get("scroll_left"), maximum=1000000),
            "max_scroll_left": _safe_number(container.get("max_scroll_left"), maximum=1000000),
            "at_start": bool(container.get("at_start")),
            "at_end": bool(container.get("at_end")),
            "direct_child_count": _safe_number(container.get("direct_child_count"), maximum=10000),
            "visible_child_count": _safe_number(container.get("visible_child_count"), maximum=10000),
            "captured_cards": safe_cards,
        })
    return {
        "page_path": path,
        "ready_state": _safe_text(raw.get("ready_state"), 24),
        "viewport": {"width": _safe_number(viewport.get("width"), maximum=20000), "height": _safe_number(viewport.get("height"), maximum=20000)},
        "body_text_length": _safe_number(raw.get("body_text_length"), maximum=100000000),
        "page_valid": bool(raw.get("page_valid")),
        "visible_claimable_count": _safe_number(raw.get("visible_claimable_count"), maximum=1000),
        "visible_delivered_control_count": _safe_number(raw.get("visible_delivered_control_count"), maximum=1000),
        "candidate_container_count": _safe_number(raw.get("candidate_container_count"), maximum=20),
        "carousel_containers": safe_containers,
        "completeness_verdict": "not_established",
        "delivery_verdict": "not_established",
        "diagnostic_version": 1,
        "note": "Visible DOM hints only; not proof of complete carousel coverage, Locked state, or milestone delivery.",
    }


def build_progression_inspection_diagnostic_summary(
    diagnostics_by_player: Any, *, configured_player_count: int
) -> dict[str, Any]:
    """Build a privacy-minimized per-run diagnostic summary."""
    source = diagnostics_by_player if isinstance(diagnostics_by_player, dict) else {}
    accounts = []
    snapshot_total = 0
    for player_id, snapshots in source.items():
        if not isinstance(snapshots, list):
            continue
        safe = [item for item in snapshots[-_MAX_SNAPSHOTS_PER_PLAYER:] if isinstance(item, dict)]
        if not safe:
            continue
        snapshot_total += len(safe)
        accounts.append({
            "player_id_last4": str(player_id or "")[-4:],
            "snapshot_count": len(safe),
            "snapshots": safe,
        })
    return {
        "diagnostic_version": 1,
        "read_only": True,
        "configured_accounts": max(0, int(configured_player_count)),
        "accounts_with_diagnostics": len(accounts),
        "snapshot_count": snapshot_total,
        "completeness_established": False,
        "delivery_status_established": False,
        "accounts": accounts,
        "note": "Diagnostics are visible DOM hints only. They do not establish a complete inspection or a Delivered/Locked milestone state.",
    }


__all__ = ["sanitize_progression_inspection_snapshot", "build_progression_inspection_diagnostic_summary"]
