"""Privacy-minimized, read-only Progression DOM diagnostics."""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

_MAX_CONTAINERS = 12
_MAX_CARDS_PER_CONTAINER = 40
_MAX_ACTION_DETAILS = 8
_MAX_STATUS_ELEMENTS = 8
_MAX_TEXT = 480
_MAX_SNAPSHOTS_PER_PLAYER = 24
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


def _safe_optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def sanitize_progression_inspection_snapshot(raw: Any) -> dict[str, Any] | None:
    """Whitelist bounded rail, slide, and card-status hints; never store raw HTML."""
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
            button_details = card.get("button_details")
            button_details = button_details if isinstance(button_details, list) else []
            status_elements = card.get("status_elements")
            status_elements = status_elements if isinstance(status_elements, list) else []
            rect = card.get("rect") if isinstance(card.get("rect"), dict) else {}
            safe_buttons = []
            for button in button_details[:_MAX_ACTION_DETAILS]:
                if not isinstance(button, dict):
                    continue
                safe_buttons.append({
                    "tag": _safe_text(button.get("tag"), 24),
                    "label": _safe_text(button.get("label"), 80),
                    "role": _safe_text(button.get("role"), 32),
                    "aria_label": _safe_text(button.get("aria_label"), 100),
                    "class_name": _safe_text(button.get("class_name"), 120),
                    "data_slot": _safe_text(button.get("data_slot"), 80),
                    "data_state": _safe_text(button.get("data_state"), 48),
                    "disabled": _safe_optional_bool(button.get("disabled")),
                    "aria_disabled": _safe_text(button.get("aria_disabled"), 16),
                })
            safe_status_elements = []
            for element in status_elements[:_MAX_STATUS_ELEMENTS]:
                if not isinstance(element, dict):
                    continue
                safe_status_elements.append({
                    "tag": _safe_text(element.get("tag"), 24),
                    "text": _safe_text(element.get("text"), 80),
                    "role": _safe_text(element.get("role"), 32),
                    "aria_label": _safe_text(element.get("aria_label"), 100),
                    "class_name": _safe_text(element.get("class_name"), 120),
                    "data_state": _safe_text(element.get("data_state"), 48),
                })
            safe_cards.append({
                "tag": _safe_text(card.get("tag"), 24),
                "class_name": _safe_text(card.get("class_name"), 120),
                "test_id": _safe_text(card.get("test_id"), 80),
                "role": _safe_text(card.get("role"), 32),
                "aria_label": _safe_text(card.get("aria_label"), 100),
                "data_slot": _safe_text(card.get("data_slot"), 80),
                "data_state": _safe_text(card.get("data_state"), 48),
                "text_snippet": _safe_text(card.get("text_snippet"), _MAX_TEXT),
                "button_labels": [_safe_text(item, 80) for item in labels[:_MAX_ACTION_DETAILS]],
                "button_details": safe_buttons,
                "exact_status_text_hints": [_safe_text(item, 48) for item in statuses[:_MAX_STATUS_ELEMENTS]],
                "status_elements": safe_status_elements,
                "milestone_number_hints": [
                    number for item in milestones[:25]
                    if (number := _safe_number(item, maximum=1000)) is not None
                ],
                "rect": {key: _safe_number(rect.get(key), maximum=100000) for key in ("x", "y", "width", "height")},
            })
        rect = container.get("rect") if isinstance(container.get("rect"), dict) else {}
        safe_containers.append({
            "index": _safe_number(container.get("index"), maximum=100),
            "tag": _safe_text(container.get("tag"), 24),
            "class_name": _safe_text(container.get("class_name"), 160),
            "test_id": _safe_text(container.get("test_id"), 80),
            "role": _safe_text(container.get("role"), 32),
            "aria_label": _safe_text(container.get("aria_label"), 100),
            "aria_roledescription": _safe_text(container.get("aria_roledescription"), 48),
            "data_slot": _safe_text(container.get("data_slot"), 80),
            "overflow_x": _safe_text(container.get("overflow_x"), 24),
            "scroll_snap_type": _safe_text(container.get("scroll_snap_type"), 48),
            "display": _safe_text(container.get("display"), 24),
            "flex_direction": _safe_text(container.get("flex_direction"), 24),
            "depth": _safe_number(container.get("depth"), maximum=100),
            "is_semantic_carousel_root": bool(container.get("is_semantic_carousel_root")),
            "card_capture_source": _safe_text(container.get("card_capture_source"), 48),
            "explicit_slide_count": _safe_number(container.get("explicit_slide_count"), maximum=1000),
            "rect": {key: _safe_number(rect.get(key), maximum=100000) for key in ("x", "y", "width", "height")},
            "client_width": _safe_number(container.get("client_width"), maximum=100000),
            "scroll_width": _safe_number(container.get("scroll_width"), maximum=1000000),
            "scroll_left": _safe_number(container.get("scroll_left"), maximum=1000000),
            "max_scroll_left": _safe_number(container.get("max_scroll_left"), maximum=1000000),
            "at_start": bool(container.get("at_start")),
            "at_end": bool(container.get("at_end")),
            "direct_child_count": _safe_number(container.get("direct_child_count"), maximum=10000),
            "visible_child_count": _safe_number(container.get("visible_child_count"), maximum=10000),
            "captured_card_count": _safe_number(container.get("captured_card_count"), maximum=1000),
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
        "candidate_container_count": _safe_number(raw.get("candidate_container_count"), maximum=100),
        "carousel_containers": safe_containers,
        "completeness_verdict": "not_established",
        "delivery_verdict": "not_established",
        "diagnostic_version": 2,
        "note": "Visible rail and card hints only; not proof of complete inventory coverage, Locked state, or milestone delivery.",
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
        "diagnostic_version": 2,
        "read_only": True,
        "configured_accounts": max(0, int(configured_player_count)),
        "accounts_with_diagnostics": len(accounts),
        "snapshot_count": snapshot_total,
        "completeness_established": False,
        "delivery_status_established": False,
        "accounts": accounts,
        "note": "Visible rail and card hints are diagnostic evidence only. They do not establish complete inventory coverage, Delivered/Locked milestone state, or a resolved inspection.",
    }


__all__ = ["sanitize_progression_inspection_snapshot", "build_progression_inspection_diagnostic_summary"]
