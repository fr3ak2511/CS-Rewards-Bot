"""Privacy-minimized, read-only Progression DOM diagnostics."""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

_MAX_CONTAINERS = 12
_MAX_CARDS_PER_CONTAINER = 40
_MAX_ACTION_DETAILS = 8
_MAX_STATUS_ELEMENTS = 8
_MAX_ICON_HINTS = 16
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
            icon_hints = card.get("icon_hints")
            icon_hints = icon_hints if isinstance(icon_hints, list) else []
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
                    "title": _safe_text(button.get("title"), 100),
                    "data_icon": _safe_text(button.get("data_icon"), 48),
                    "data_lucide": _safe_text(button.get("data_lucide"), 48),
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
            safe_icon_hints = []
            for hint in icon_hints[:_MAX_ICON_HINTS]:
                if not isinstance(hint, dict):
                    continue
                safe_icon_hints.append({
                    "tag": _safe_text(hint.get("tag"), 24),
                    "aria_label": _safe_text(hint.get("aria_label"), 100),
                    "title": _safe_text(hint.get("title"), 100),
                    "alt": _safe_text(hint.get("alt"), 100),
                    "data_icon": _safe_text(hint.get("data_icon"), 48),
                    "data_lucide": _safe_text(hint.get("data_lucide"), 48),
                    "data_state": _safe_text(hint.get("data_state"), 48),
                    "role": _safe_text(hint.get("role"), 32),
                    "class_name": _safe_text(hint.get("class_name"), 160),
                    "text": _safe_text(hint.get("text"), 80),
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
                "icon_hints": safe_icon_hints,
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
        "diagnostic_version": _safe_number(raw.get("diagnostic_version"), maximum=2) or 1,
        "note": "Visible rail and card hints only; not proof of complete inventory coverage, Locked state, or milestone delivery.",
    }



_DELIVERED_STATUS_LABELS = {
    "delivered", "already delivered", "claimed", "already claimed",
    "collected", "redeemed",
}
_CLAIM_STATUS_LABELS = {"claim", "claim all"}
_LOCKED_STATUS_LABELS = {"locked", "lock", "lock icon", "locked icon"}
_STATUS_LABELS = sorted(
    _DELIVERED_STATUS_LABELS | _CLAIM_STATUS_LABELS | _LOCKED_STATUS_LABELS,
    key=len,
    reverse=True,
)


def _status_classification(value: Any) -> str | None:
    label = re.sub(r"\s+", " ", str(value or "")).strip().casefold()
    if label in _DELIVERED_STATUS_LABELS:
        return "Delivered"
    if label in _CLAIM_STATUS_LABELS:
        return "Claimable"
    if label in _LOCKED_STATUS_LABELS:
        return "Locked"
    return None


def _icon_semantic_status(hint: dict[str, Any]) -> str | None:
    """Recognize only explicit, named lock semantics; a disabled button is not enough."""
    for field in ("aria_label", "title", "alt", "text", "data_state"):
        classification = _status_classification(hint.get(field))
        if classification:
            return classification

    lock_icon_names = {
        "lock", "locked", "lock-closed", "lock-keyhole", "lock-keyhole-closed",
        "lucide-lock", "i-lucide-lock", "tabler-icon-lock", "icon-lock",
        "icon-lock-closed", "fa-lock", "fa-lock-alt", "material-symbols-lock",
    }
    for field in ("data_icon", "data_lucide"):
        value = str(hint.get(field) or "").strip().casefold()
        if value in lock_icon_names:
            return "Locked"

    classes = str(hint.get("class_name") or "").casefold().split()
    if any(token in lock_icon_names for token in classes):
        return "Locked"
    return None


def _progression_card_status(card: dict[str, Any]) -> dict[str, Any]:
    """Classify only explicit, card-bound portal signals; disabled is never Locked."""
    evidence: list[tuple[str, str]] = []
    claim_controls: list[dict[str, Any]] = []

    def add(value: Any, source: str) -> None:
        classification = _status_classification(value)
        if classification:
            evidence.append((classification, source))

    for value in card.get("exact_status_text_hints", []) if isinstance(card.get("exact_status_text_hints"), list) else []:
        add(value, "exact_status_text")
    for value in card.get("button_labels", []) if isinstance(card.get("button_labels"), list) else []:
        add(value, "button_label")

    status_elements = card.get("status_elements")
    for element in status_elements if isinstance(status_elements, list) else []:
        if not isinstance(element, dict):
            continue
        add(element.get("text"), "status_element_text")
        add(element.get("aria_label"), "status_element_aria_label")
        add(element.get("data_state"), "status_element_data_state")

    icon_hints = card.get("icon_hints")
    for hint in icon_hints if isinstance(icon_hints, list) else []:
        if not isinstance(hint, dict):
            continue
        for field in ("aria_label", "title", "alt", "text", "data_state"):
            add(hint.get(field), "icon_accessible_status")
        icon_status = _icon_semantic_status(hint)
        if icon_status:
            evidence.append((icon_status, "icon_semantic_name"))

    button_details = card.get("button_details")
    for button in button_details if isinstance(button_details, list) else []:
        if not isinstance(button, dict):
            continue
        values = (
            button.get("label"),
            button.get("aria_label"),
            button.get("data_state"),
        )
        for value in values:
            add(value, "button_detail")
        if any(_status_classification(value) == "Claimable" for value in values):
            claim_controls.append(button)

    # The portal sometimes renders the status as a text suffix in the card's
    # own text node (for example, "3x20,000Delivered") rather than a child node.
    card_text = re.sub(r"\s+", " ", str(card.get("text_snippet") or "")).strip()
    folded_text = card_text.casefold()
    for label in _STATUS_LABELS:
        if folded_text.endswith(label):
            classification = _status_classification(label)
            if classification:
                evidence.append((classification, "card_text_suffix"))
            break

    classifications = {classification for classification, _ in evidence}
    sources = sorted({source for _, source in evidence})
    if len(classifications) > 1:
        return {
            "classification": "Unknown",
            "reason": "conflicting_card_status_signals",
            "evidence_sources": sources,
        }
    if not classifications:
        return {
            "classification": "Unknown",
            "reason": "no_explicit_card_status",
            "evidence_sources": [],
        }

    classification = next(iter(classifications))
    if classification == "Claimable":
        enabled = any(
            button.get("disabled") is False
            and str(button.get("aria_disabled") or "").strip().casefold() not in {"true", "1"}
            for button in claim_controls
        )
        if not enabled:
            return {
                "classification": "Unknown",
                "reason": "claim_control_not_proven_enabled",
                "evidence_sources": sources,
            }
    return {
        "classification": classification,
        "reason": None,
        "evidence_sources": sources,
    }


def _card_milestone_identity(card: dict[str, Any]) -> tuple[int | None, str | None]:
    hints = card.get("milestone_number_hints")
    hints = hints if isinstance(hints, list) else []
    values = sorted({
        number for item in hints
        if (number := _safe_number(item, maximum=1000)) is not None and number > 0
    })
    if len(values) != 1:
        return None, None

    threshold = values[0]
    text = re.sub(r"\s+", " ", str(card.get("text_snippet") or "")).strip()
    folded = text.casefold()
    for label in _STATUS_LABELS:
        if folded.endswith(label):
            text = text[:-len(label)].rstrip()
            break
    # Whitespace and visual line wrapping vary between snapshots. A compact,
    # redacted text signature is stable enough for within-run card correlation.
    compact_text = re.sub(r"\s+", "", _safe_text(text, 240)).casefold()
    test_id = _safe_text(card.get("test_id"), 80)
    class_name = _safe_text(card.get("class_name"), 100)
    signature = f"{threshold}|{test_id or class_name}|{compact_text}"[:300]
    return threshold, signature


def _select_authoritative_rail(snapshot: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    containers = snapshot.get("carousel_containers")
    if not isinstance(containers, list):
        return None, "no_container_candidates"
    candidates = []
    for container in containers:
        if not isinstance(container, dict):
            continue
        cards = container.get("captured_cards")
        if not isinstance(cards, list) or not cards:
            continue
        max_scroll = _safe_number(container.get("max_scroll_left"), maximum=1000000) or 0
        scroll_width = _safe_number(container.get("scroll_width"), maximum=1000000) or 0
        semantic = bool(container.get("is_semantic_carousel_root"))
        overflow = str(container.get("overflow_x") or "").strip().casefold()
        snap = str(container.get("scroll_snap_type") or "").strip().casefold()
        candidates.append((
            (
                max_scroll,
                int(semantic),
                int(overflow in {"auto", "scroll"}),
                int(bool(snap and snap != "none")),
                scroll_width,
                len(cards),
            ),
            container,
        ))
    if not candidates:
        return None, "no_card_bearing_container"

    best_rank = max(rank for rank, _ in candidates)
    best = [container for rank, container in candidates if rank == best_rank]
    if len(best) != 1:
        return None, "ambiguous_authoritative_rail"
    return best[0], "largest_observed_horizontal_rail"


def _normalize_rail_snapshot(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    rail, selection_reason = _select_authoritative_rail(snapshot)
    if rail is None:
        return None
    raw_cards = rail.get("captured_cards")
    raw_cards = raw_cards if isinstance(raw_cards, list) else []
    cards = []
    counts: dict[int, int] = {}
    for raw_card in raw_cards:
        if not isinstance(raw_card, dict):
            continue
        threshold, signature = _card_milestone_identity(raw_card)
        status = _progression_card_status(raw_card)
        if threshold is not None:
            counts[threshold] = counts.get(threshold, 0) + 1
        cards.append({
            "threshold": threshold,
            "card_signature": signature,
            **status,
        })

    duplicate_thresholds = sorted(threshold for threshold, count in counts.items() if count > 1)
    if duplicate_thresholds:
        for card in cards:
            if card["threshold"] in duplicate_thresholds:
                card["classification"] = "Unknown"
                card["reason"] = "duplicate_milestone_identity_in_authoritative_rail"

    return {
        "container_index": rail.get("index"),
        "container_class": _safe_text(rail.get("class_name"), 160),
        "client_width": _safe_number(rail.get("client_width"), maximum=100000),
        "scroll_width": _safe_number(rail.get("scroll_width"), maximum=1000000),
        "max_scroll_left": _safe_number(rail.get("max_scroll_left"), maximum=1000000),
        "scroll_left": _safe_number(rail.get("scroll_left"), maximum=1000000),
        "at_end": bool(rail.get("at_end")),
        "captured_card_count": len(cards),
        "captured_milestone_count": len({card["threshold"] for card in cards if card["threshold"] is not None}),
        "invalid_milestone_identity_count": sum(card["threshold"] is None for card in cards),
        "duplicate_milestone_thresholds": duplicate_thresholds,
        "selection_reason": selection_reason,
        "cards": cards,
    }


def _aggregate_rail_observations(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    normalized = [
        item for snapshot in snapshots
        if isinstance(snapshot, dict)
        if (item := _normalize_rail_snapshot(snapshot)) is not None
    ]
    if not normalized:
        return {
            "captured_rail_scan_completed": False,
            "rail_scan_reason": "no_authoritative_rail_observed",
            "selected_rail": None,
            "observed_milestone_thresholds": [],
            "milestone_cards": [],
            "card_status_totals": {"Delivered": 0, "Claimable": 0, "Locked": 0, "Unknown": 0},
        }

    inventory_sets = [
        tuple(sorted({card["threshold"] for card in item["cards"] if card["threshold"] is not None}))
        for item in normalized
    ]
    inventory_stable = bool(inventory_sets) and all(values == inventory_sets[0] for values in inventory_sets)
    identities_valid = all(
        item["invalid_milestone_identity_count"] == 0
        and not item["duplicate_milestone_thresholds"]
        and item["captured_card_count"] == item["captured_milestone_count"]
        for item in normalized
    )
    signatures_by_threshold: dict[int, set[str]] = {}
    observations: dict[int, list[dict[str, Any]]] = {}
    for item in normalized:
        for card in item["cards"]:
            threshold = card["threshold"]
            if threshold is None:
                continue
            signature = str(card.get("card_signature") or "")
            signatures_by_threshold.setdefault(threshold, set()).add(signature)
            observations.setdefault(threshold, []).append(card)

    card_signatures_stable = bool(signatures_by_threshold) and all(
        len(signatures) == 1 and next(iter(signatures))
        for signatures in signatures_by_threshold.values()
    )

    at_end = any(item["at_end"] for item in normalized)
    selected_count = len(normalized)
    captured_scan_complete = (
        selected_count == len(snapshots)
        and at_end
        and inventory_stable
        and identities_valid
        and card_signatures_stable
        and bool(inventory_sets[0])
    )

    milestone_cards = []
    totals = {"Delivered": 0, "Claimable": 0, "Locked": 0, "Unknown": 0}
    for threshold in sorted(observations):
        records = observations[threshold]
        signatures = {record["card_signature"] for record in records if record.get("card_signature")}
        known_statuses = {
            record["classification"] for record in records
            if record.get("classification") in {"Delivered", "Claimable", "Locked"}
        }
        sources = sorted({
            source
            for record in records
            for source in (record.get("evidence_sources") or [])
        })
        if len(signatures) > 1:
            classification, reason = "Unknown", "card_identity_changed_across_snapshots"
        elif len(known_statuses) > 1:
            classification, reason = "Unknown", "conflicting_status_across_snapshots"
        elif len(known_statuses) == 1:
            classification, reason = next(iter(known_statuses)), None
        else:
            classification = "Unknown"
            reasons = sorted({record.get("reason") for record in records if record.get("reason")})
            reason = reasons[0] if len(reasons) == 1 else "no_consistent_explicit_status"

        totals[classification] += 1
        milestone_cards.append({
            "threshold": threshold,
            "classification": classification,
            "reason": reason,
            "card_signature": next(iter(signatures)) if len(signatures) == 1 else None,
            "evidence_sources": sources,
            "observations": len(records),
        })

    latest = normalized[-1]
    return {
        "captured_rail_scan_completed": captured_scan_complete,
        "rail_scan_reason": (
            "authoritative_rail_reached_end_with_stable_unique_milestone_identities"
            if captured_scan_complete
            else "rail_end_or_stable_unique_inventory_not_established"
        ),
        "selected_rail": {
            "container_index": latest["container_index"],
            "container_class": latest["container_class"],
            "client_width": latest["client_width"],
            "scroll_width": latest["scroll_width"],
            "max_scroll_left": latest["max_scroll_left"],
            "captured_card_count": latest["captured_card_count"],
            "at_end_observed": at_end,
        },
        "rail_snapshots_normalized": selected_count,
        "rail_snapshots_expected": len(snapshots),
        "inventory_stable_across_snapshots": inventory_stable,
        "milestone_identity_valid_across_snapshots": identities_valid,
        "card_signatures_stable_across_snapshots": card_signatures_stable,
        "observed_milestone_thresholds": list(inventory_sets[0]) if inventory_stable else [],
        "milestone_cards": milestone_cards,
        "card_status_totals": totals,
        "inventory_completeness_established": False,
        "note": (
            "A completed DOM rail scan means the selected rail reached its end and the captured "
            "milestone identities stayed stable. It does not prove the portal has no additional "
            "unrendered milestones. Blank or disabled controls are Unknown, never Locked."
        ),
    }


def build_progression_inspection_diagnostic_summary(
    diagnostics_by_player: Any, *, configured_player_count: int
) -> dict[str, Any]:
    """Build a privacy-minimized summary with deduplicated per-card observations."""
    source = diagnostics_by_player if isinstance(diagnostics_by_player, dict) else {}
    accounts = []
    snapshot_total = 0
    inventory_by_account: list[tuple[str, ...]] = []
    status_totals = {"Delivered": 0, "Claimable": 0, "Locked": 0, "Unknown": 0}
    accounts_with_completed_rail_scan = 0

    for player_id, snapshots in source.items():
        if not isinstance(snapshots, list):
            continue
        safe = []
        for item in snapshots[-_MAX_SNAPSHOTS_PER_PLAYER:]:
            if not isinstance(item, dict):
                continue
            sanitized = sanitize_progression_inspection_snapshot(item)
            if sanitized is None:
                continue
            safe.append({
                "observed_at_ist": _safe_text(item.get("observed_at_ist"), 40),
                **sanitized,
            })
        if not safe:
            continue

        snapshot_total += len(safe)
        rail_summary = _aggregate_rail_observations(safe)
        if rail_summary["captured_rail_scan_completed"]:
            accounts_with_completed_rail_scan += 1
            inventory_by_account.append(tuple(rail_summary["observed_milestone_thresholds"]))
        for status, count in rail_summary["card_status_totals"].items():
            status_totals[status] += count
        accounts.append({
            "player_id_last4": str(player_id or "")[-4:],
            "snapshot_count": len(safe),
            "snapshots": safe,
            "normalized_progression_rail": rail_summary,
        })

    expected_accounts = max(0, int(configured_player_count))
    inventories_consistent = (
        expected_accounts > 0
        and len(accounts) == expected_accounts
        and len(inventory_by_account) == expected_accounts
        and bool(inventory_by_account)
        and all(value == inventory_by_account[0] for value in inventory_by_account)
    )
    common_inventory = list(inventory_by_account[0]) if inventories_consistent else []
    return {
        "diagnostic_version": 4,
        "read_only": True,
        "configured_accounts": max(0, int(configured_player_count)),
        "accounts_with_diagnostics": len(accounts),
        "snapshot_count": snapshot_total,
        "accounts_with_completed_rail_scan": accounts_with_completed_rail_scan,
        "inventory_consistency_across_accounts": inventories_consistent,
        "common_observed_milestone_thresholds": common_inventory,
        "card_status_totals": status_totals,
        "completeness_established": False,
        "delivery_status_established": False,
        "accounts": accounts,
        "note": (
            "Per-card classifications are derived only from explicit card-bound text or control "
            "signals. Claimable requires an explicitly enabled Claim control. Blank or disabled "
            "controls remain Unknown, not Locked. A full observed rail scan and cross-account "
            "inventory consistency do not prove an independently complete portal inventory."
        ),
    }


__all__ = ["sanitize_progression_inspection_snapshot", "build_progression_inspection_diagnostic_summary"]
