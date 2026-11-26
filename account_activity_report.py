"""Canonical per-account activity data shared by the email and Copy Report.

This module is report-only. It never changes claim, retry, queue, or scheduler state.
Player identifiers are reduced to their last four characters before rendering.
"""
from __future__ import annotations

from html import escape
from typing import Any

MAX_PROGRESS_INSPECTION_SLOTS = 4
_HARD_FAILURES = {"login failed", "error", "failed"}
_PORTAL_CLAIMED = {
    "portal_claimed", "claimed", "already_claimed", "already claimed",
    "cooldown", "on_cooldown", "already_claimed_on_portal",
}


def _integer(value: Any) -> int:
    try:
        if isinstance(value, bool):
            return 0
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _first_value(player: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in player and player.get(key) is not None:
            return player.get(key)
    return default


def _player_suffix(player: dict[str, Any]) -> str:
    raw = _text(_first_value(player, "pid", "player_id", default=""))
    if not raw:
        raw = _text(_first_value(player, "player_id_last4", default=""))
    return raw[-4:] if raw else ""


def _account_label(player: dict[str, Any]) -> str:
    name = _text(player.get("display_name"))[:80]
    suffix = _player_suffix(player)
    if name and suffix:
        return f"{name} · …{suffix}"
    if name:
        return name
    return f"Player …{suffix}" if suffix else "Unknown player"


def _portal_claimed(value: Any) -> bool:
    return _text(value).casefold() in _PORTAL_CLAIMED


def _is_hard_failure(player: dict[str, Any]) -> bool:
    return _text(player.get("status")).casefold() in _HARD_FAILURES


def _standard_rewards_checked(player: dict[str, Any]) -> bool:
    explicit = player.get("standard_rewards_checked")
    if isinstance(explicit, bool):
        return explicit
    mode = _text(player.get("processing_mode") or "full_claim").casefold()
    return mode == "full_claim" and not _is_hard_failure(player)


def _daily_audit_present(player: dict[str, Any]) -> bool:
    events = player.get("preexisting_portal_claims")
    if not isinstance(events, list):
        return False
    return any(
        isinstance(event, dict)
        and (
            _text(event.get("reward_category")).casefold() == "daily"
            or _text(event.get("reward_key")).casefold() == "daily"
        )
        for event in events
    )


def _daily_activity(player: dict[str, Any]) -> dict[str, Any]:
    count = _integer(_first_value(player, "daily", "daily_claimed_this_run", default=0))
    portal = _text(player.get("daily_portal_status") or "unknown").casefold()
    unconfirmed = bool(player.get("daily_unconfirmed"))
    audited = _daily_audit_present(player)
    checked = _standard_rewards_checked(player)

    if count:
        state = "claimed_by_bot"
        label = "Claimed by bot"
        detail = f"{count} Daily reward{'s' if count != 1 else ''} confirmed by the portal after the bot action."
        action = ""
        attention = False
    elif unconfirmed:
        state = "retry_queued"
        label = "Retry queued"
        detail = "The Daily reward was not confirmed by the portal; it remains a retry candidate."
        action = "Use only the existing saved-target recovery path for this Daily reward."
        attention = True
    elif _portal_claimed(portal):
        state = "portal_claimed"
        label = "Portal says claimed"
        if audited:
            detail = "Claimed/cooldown was observed before bot interaction; the portal does not identify the actor."
            action = ""
            attention = False
        else:
            detail = "The portal reports Claimed/cooldown, but no pre-interaction audit event was recorded. Actor is unknown; no bot claim is credited."
            action = "Preserve as unknown provenance; do not attribute this Daily reward to the bot or retry it while the portal reports claimed."
            attention = True
    elif portal in {"available", "claimable"}:
        state = "available_not_claimed"
        label = "Available · not confirmed"
        detail = "The portal state was available, but no confirmed claim is recorded."
        action = "Review the exact Daily outcome; do not count it as claimed."
        attention = True
    elif portal == "not_checked" or not checked:
        state = "not_checked"
        label = "Not checked"
        detail = "Daily was outside this account's scope in this run."
        action = ""
        attention = False
    elif _is_hard_failure(player):
        state = "not_checked"
        label = "Not checked · login/processing failed"
        detail = "No conclusive Daily portal state was captured."
        action = "Review the account's login/processing failure before treating the cycle as complete."
        attention = True
    else:
        state = "unknown"
        label = "Unknown · status not established"
        detail = "The run did not record a conclusive Daily portal status."
        action = "Review the run evidence; do not convert an unknown state into a successful claim."
        attention = True

    return {
        "state": state, "label": label, "detail": detail, "count": count,
        "attention_required": attention, "next_action": action,
    }


def _store_activity(player: dict[str, Any]) -> dict[str, Any]:
    count = _integer(_first_value(
        player, "store", "standard_store_claimed_this_run", default=0
    ))
    raw_unconfirmed = _first_value(
        player, "store_unconfirmed_indices", "standard_store_unconfirmed_indices", default=[]
    )
    unconfirmed = sorted({
        _integer(item) for item in (raw_unconfirmed or [])
        if _integer(item) > 0
    })
    raw_claimed = _first_value(
        player, "store_claimed_indices", "standard_store_claimed_indices", default=[]
    )
    claimed_indices = sorted({
        _integer(item) for item in (raw_claimed or [])
        if _integer(item) > 0
    })
    raw_states = _first_value(
        player, "store_portal_status", "standard_store_portal_status", default=[]
    )
    states = [ _text(item).casefold() for item in (raw_states or []) ]
    checked = _standard_rewards_checked(player)

    if count:
        item_note = (
            " · confirmed item(s) " + ", ".join(f"#{item}" for item in claimed_indices)
            if claimed_indices else ""
        )
        state = "claimed_by_bot"
        label = f"Claimed by bot · {count}/3 confirmed"
        detail = f"{count} standard Store reward(s) confirmed by the portal after the bot action{item_note}."
        action = ""
        attention = False
    elif unconfirmed:
        state = "retry_queued"
        label = "Retry queued"
        detail = "Unconfirmed Store item(s): " + ", ".join(f"#{item}" for item in unconfirmed) + "."
        action = "Use only the existing saved-target recovery path for the listed Store item(s)."
        attention = True
    elif states and all(_portal_claimed(item) for item in states):
        state = "portal_claimed"
        label = "Portal says claimed"
        detail = "The Store portal shows Claimed/cooldown; the portal does not identify the actor."
        action = "Do not credit these items as bot claims unless this run has matching claim-confirmation evidence."
        attention = False
    elif not checked or any(item == "not_checked" for item in states):
        state = "not_checked"
        label = "Not checked"
        detail = "Standard Store rewards were outside this account's scope in this run."
        action = ""
        attention = False
    elif any(item in {"available", "claimable"} for item in states):
        state = "available_not_claimed"
        label = "Available · not confirmed"
        detail = "At least one Store item was observed available without a confirmed claim."
        action = "Review the exact Store item state; do not count it as claimed."
        attention = True
    else:
        state = "unknown"
        label = "Unknown · status not established"
        detail = "No bot-confirmed Store claim or conclusive portal state was recorded."
        action = "Review the Store evidence before treating these rewards as complete."
        attention = True

    return {
        "state": state, "label": label, "detail": detail, "count": count,
        "claimed_indices": claimed_indices, "unconfirmed_indices": unconfirmed,
        "attention_required": attention, "next_action": action,
    }


def _progression_reason(player: dict[str, Any]) -> str:
    outcome = _text(player.get("progression_last_outcome_category")).casefold()
    raw = _text(player.get("progression_last_reason") or player.get("progression_deferred_reason"))
    if outcome == "overlay_blocked" or "unknown_fullscreen_overlay" in raw.casefold():
        return "The safety guard blocked inspection because an unidentified full-screen overlay covered the page."
    if outcome == "empty_scan" or "no currently visible claimable reward" in raw.casefold():
        return (
            "No currently visible claimable reward was found. An empty scan does not prove "
            "that the full reward inventory has no claimable reward."
        )
    if outcome == "incomplete_scan":
        return "The Progression inspection did not capture a complete, reliable reward rail."
    if outcome == "login_failed":
        return "The account could not authenticate for the Progression inspection."
    if outcome == "claim_unconfirmed":
        return "A Progression claim was attempted, but the portal did not confirm the same card as Delivered."
    if not raw:
        return ""
    # Popup details can include a large DOM snapshot. Never copy that into a report.
    raw = raw.split("popup_context=", 1)[0].split(";", 1)[0].strip().rstrip(" ,")
    if raw.endswith("("):
        raw = raw[:-1].rstrip()
    if "(" in raw and not raw.endswith(")"):
        raw = raw.split("(", 1)[0].strip()
    return raw[:180].rstrip()


def _grenade_activity(player: dict[str, Any]) -> dict[str, Any]:
    live = player.get("grenade_balance")
    carry = player.get("grenade_balance_carry_forward")
    live = live if isinstance(live, dict) else {}
    carry = carry if isinstance(carry, dict) else {}

    if live.get("current_count") is not None:
        count = _integer(live.get("current_count"))
        minimum = live.get("expected_minimum")
        milestone = live.get("next_milestone")
        remaining = live.get("remaining_to_next_milestone")
        status = _text(live.get("status") or "not_validated")
        freshness = "Fresh · captured in this run"
        observed_date = _text(live.get("last_observed_date"))
    elif carry.get("last_observed_count") is not None:
        count = _integer(carry.get("last_observed_count"))
        minimum = _first_value(
            carry, "expected_minimum_as_of", "expected_minimum_at_observation"
        )
        minimum_at_observation = carry.get("expected_minimum_at_observation")
        milestone = carry.get("next_milestone_at_observation")
        remaining = carry.get("remaining_to_milestone_at_observation")
        status = _text(carry.get("status_at_observation") or "not_validated")
        observed_date = _text(carry.get("last_observed_date"))
        freshness = "Carried forward" + (f" · last observed {observed_date}" if observed_date else "")
    else:
        return {
            "available": False, "count": None, "minimum": None,
            "milestone": None, "remaining": None,
            "status": "not_captured", "freshness": "Not captured",
            "detail": "Grenade balance not captured.",
        }

    minimum_text = str(_integer(minimum)) if minimum is not None else "not available"
    milestone_text = str(_integer(milestone)) if milestone is not None else "not available"
    if remaining is None and milestone is not None:
        remaining = max(0, _integer(milestone) - count)
    remaining_text = f"{_integer(remaining)} to go" if remaining is not None else "remaining not available"
    status_labels = {
        "pass": (
            "Above date minimum" if minimum is not None and count > _integer(minimum)
            else "At date minimum" if minimum is not None
            else "Validated"
        ),
        "below_minimum": "Below date minimum",
        "counter_regressed": "Counter regressed",
        "pending_baseline": "Pending baseline",
        "status_not_retained": "Status not retained",
        "not_validated": "Not validated",
    }
    status_label = status_labels.get(status, status.replace("_", " ").title())
    if freshness.startswith("Carried forward") and minimum_at_observation is not None and minimum is not None:
        minimum_context = f"date minimum {minimum_text} (at observation {minimum_at_observation})"
    else:
        minimum_context = f"date minimum {minimum_text}"
    if freshness.startswith("Carried forward") and status == "pass":
        status_label = "validated at observation"
    detail = (
        f"{count} Grenades · {freshness} · {minimum_context} · "
        f"next milestone {milestone_text} · {remaining_text} · {status_label}"
    )
    return {
        "available": True, "count": count, "minimum": minimum,
        "milestone": milestone, "remaining": remaining,
        "status": status, "freshness": freshness, "observed_date": observed_date,
        "detail": detail,
    }


def _progression_activity(player: dict[str, Any]) -> dict[str, Any]:
    count = _integer(_first_value(
        player, "progression", "progression_claimed_this_run", default=0
    ))
    deferred = bool(player.get("progression_deferred"))
    unconfirmed = bool(player.get("progression_unconfirmed"))
    mode = _text(player.get("processing_mode") or "full_claim").casefold()
    hard_failure = _is_hard_failure(player)
    checked_value = player.get("progression_checked")
    checked = (
        bool(checked_value) if isinstance(checked_value, bool)
        else mode == "full_claim" and not hard_failure
    )
    reinspection_attempted = bool(player.get("progression_reinspection_attempted"))
    outcome = _text(player.get("progression_last_outcome_category")).casefold()
    reason = _progression_reason(player)
    slots_used = _integer(player.get("progression_inspection_slots_used"))
    attempts = _integer(player.get("progression_inspection_attempts"))
    same_count = _integer(player.get("progression_same_outcome_count"))
    manual_attention = bool(player.get("progression_manual_attention_required"))
    grenade = _grenade_activity(player)

    if unconfirmed:
        state = "claim_unconfirmed"
        label = f"{count} confirmed · another claim unconfirmed" if count else "Claim attempted · portal confirmation missing"
        detail = reason or "The exact Progression reward was not confirmed as Delivered."
        action = "Keep the exact target pending; require same-card portal Delivered confirmation. Do not broaden the retry."
        attention = True
        pending = True
    elif deferred:
        if count:
            label = f"{count} confirmed · inspection still pending"
        elif outcome == "overlay_blocked" or "popup/overlay" in reason.casefold() or "fullscreen_overlay" in reason.casefold():
            label = "Pending · blocked by overlay"
        elif outcome == "empty_scan" or "no currently visible claimable reward" in reason.casefold():
            label = "Pending · empty scan"
        else:
            label = "Pending inspection"
        state = "blocked" if label == "Pending · blocked by overlay" else "pending"
        detail_parts = [reason] if reason else []
        if outcome:
            detail_parts.append(f"latest outcome: {outcome.replace('_', ' ')}")
        if same_count > 1:
            detail_parts.append(f"same outcome {same_count}x")
        if attempts:
            detail_parts.append(f"{attempts} inspection attempts recorded")
        if slots_used:
            detail_parts.append(f"{slots_used}/{MAX_PROGRESS_INSPECTION_SLOTS} safe slots used this reset")
        if manual_attention:
            detail_parts.append("manual attention required")
        detail = " · ".join(detail_parts) or "Inspection is deferred; no claim is inferred."
        if slots_used >= MAX_PROGRESS_INSPECTION_SLOTS:
            if state == "blocked":
                action = (
                    f"Hold: all {MAX_PROGRESS_INSPECTION_SLOTS}/{MAX_PROGRESS_INSPECTION_SLOTS} safe inspection slots "
                    "are used this reset. Keep pending; do not bypass the unidentified overlay."
                )
            else:
                action = (
                    f"Hold: all {MAX_PROGRESS_INSPECTION_SLOTS}/{MAX_PROGRESS_INSPECTION_SLOTS} safe inspection slots "
                    "are used this reset; an empty scan is not proof that no reward exists."
                )
        elif state == "blocked":
            action = "Keep pending until the overlay is identified and safely dismissible. Do not click through."
        else:
            action = "Keep pending for another eligible safe inspection. An empty scan is not proof that no reward exists."
        if manual_attention:
            action = "Manual attention required. " + action
        attention = True
        pending = True
    elif count:
        state = "confirmed"
        label = f"{count} Progression reward{'s' if count != 1 else ''} confirmed"
        detail = "Portal confirmed the reward(s) delivered."
        action = ""
        attention = False
        pending = False
    elif checked:
        state = "checked_no_reward"
        label = "Checked · no reward claimed"
        detail = "Progression inspection completed; no reward claim was confirmed."
        action = ""
        attention = False
        pending = False
    else:
        state = "not_checked"
        label = "Not checked"
        detail = (
            "A queued inspection is pending but was not attempted in this run."
            if deferred else "Progression was outside this account's scope in this run."
        )
        action = ""
        attention = False
        pending = False

    if grenade.get("available") and grenade.get("status") in {"below_minimum", "counter_regressed"}:
        attention = True
        if not action:
            action = "Review the Grenade balance discrepancy against its date minimum."

    return {
        "state": state, "label": label, "detail": detail, "count": count,
        "pending_inspection": pending, "attention_required": attention,
        "next_action": action, "grenades": grenade,
        "outcome": outcome, "slots_used": slots_used,
        "inspection_attempts": attempts, "same_outcome_count": same_count,
        "manual_attention_required": manual_attention,
        "reinspection_attempted": reinspection_attempted,
    }


def _other_activity(player: dict[str, Any]) -> dict[str, Any]:
    mode = _text(player.get("processing_mode") or "full_claim").casefold()
    hard_failure = _is_hard_failure(player)
    loyalty_count = _integer(_first_value(player, "loyalty", "loyalty_claimed_this_run", default=0))
    has_loyalty = player.get("has_loyalty")
    if loyalty_count:
        loyalty = f"{loyalty_count} confirmed by the portal"
    elif has_loyalty is False:
        loyalty = "Not enrolled"
    elif mode not in {"full_claim", "backup_reconciliation"} or hard_failure:
        loyalty = "Not checked"
    else:
        loyalty = "Checked · no claim confirmed"

    raw_promotions = player.get("promotions_observed")
    raw_promotions = raw_promotions if isinstance(raw_promotions, list) else []
    if isinstance(player.get("reportable_promotions"), list):
        promotions = player["reportable_promotions"]
    else:
        # Without the production reportability annotation, fail closed: only the
        # generic blocker with no offer identity/details may enter user-facing output.
        promotions = [
            item for item in raw_promotions
            if isinstance(item, dict)
            and _text(item.get("status")).casefold() == "popup_blocked"
            and (
                _text(item.get("key")) == "store-popup-blocked"
                or (not _text(item.get("card_text")) and not _text(item.get("button_text")))
            )
        ]
    promo_scanned = bool(player.get("promotion_scan_completed"))
    promo_claimed = sum(
        1 for item in promotions
        if isinstance(item, dict) and _text(item.get("status")).casefold() == "claimed_this_run"
    )
    popup_observations = [
        item for item in promotions
        if isinstance(item, dict) and _text(item.get("status")).casefold() == "popup_blocked"
    ]
    reportable_promotion_issue = bool(
        player.get("promotion_detail_attention")
        if isinstance(player.get("promotion_detail_attention"), bool)
        else popup_observations or player.get("promotion_detection_failed")
    )
    reportable_promotion_issue = reportable_promotion_issue or bool(
        player.get("promotion_detection_failed")
    )
    popup_blocked = bool(reportable_promotion_issue and popup_observations)
    promo_details = []
    if popup_blocked:
        promo_details.append(
            "Safety popup blocked Store promotion inspection before any claim click; no claim was attempted."
        )
    if reportable_promotion_issue and any(
        isinstance(item, dict)
        and _text(item.get("status")).casefold() in {"claim_unconfirmed", "click_failed", "available"}
        for item in promotions
    ):
        promo_details.append("A free/actionable promotional offer needs portal confirmation.")
    if reportable_promotion_issue and player.get("promotion_detection_failed"):
        promo_details.append("Promotional offer detection was incomplete.")
    promo_text = (
        f"Scanned · {len(promotions)} offer observation(s) · {promo_claimed} confirmed claim(s)"
        if promo_scanned else "Not checked"
    )
    if popup_blocked:
        promo_text += " · safety popup blocked before click; no claim attempted"
    if player.get("promotion_detection_failed") and reportable_promotion_issue:
        promo_text += " · scan incomplete"
    if promo_details:
        promo_text += " · " + " ".join(promo_details)

    events = player.get("event_rewards")
    events = events if isinstance(events, list) else []
    event_count = _integer(_first_value(
        player, "event_reward_claims", "event_reward_claims_this_run", default=0
    ))
    event_retry = any(
        isinstance(item, dict) and bool(item.get("retry_required")) for item in events
    )
    events_checked = mode in {"full_claim", "backup_reconciliation"} and not hard_failure
    event_text = (
        f"{event_count} confirmed claim(s)" if event_count
        else ("Checked · no claim confirmed" if events_checked else "Not checked")
    )
    if event_retry:
        event_text += " · confirmation pending"

    return {
        "loyalty": loyalty,
        "promotions": promo_text,
        "promotion_attention": bool(reportable_promotion_issue),
        "promotion_popup_blocked": popup_blocked,
        "events": event_text,
        "event_attention": event_retry,
    }


def build_account_activity_records(players: Any) -> list[dict[str, Any]]:
    """Create the canonical report rows from live result or sanitized summary records."""
    records: list[dict[str, Any]] = []
    for player in players if isinstance(players, list) else []:
        if not isinstance(player, dict):
            continue
        daily = _daily_activity(player)
        store = _store_activity(player)
        progression = _progression_activity(player)
        other = _other_activity(player)
        hard_failure = _is_hard_failure(player)
        status = _text(player.get("status")) or "Status not recorded"
        promotion_next_action = (
            "Keep the promotion observation pending until the overlay is identified and safely dismissible; "
            "no claim was attempted. Do not click through."
            if other["promotion_popup_blocked"] else
            "Review promotional scan evidence; offer detection was incomplete."
            if other["promotion_attention"] else ""
        )
        next_actions = [
            item for item in (
                daily.get("next_action"),
                store.get("next_action"),
                progression.get("next_action"),
                promotion_next_action,
                "Review event reward confirmation; at least one event claim remains pending."
                    if other["event_attention"] else "",
                "Review the account's login/processing failure before treating the cycle as complete."
                    if hard_failure else "",
            ) if item
        ]
        if next_actions:
            next_action = " ".join(dict.fromkeys(next_actions))
        else:
            next_action = "No account-level follow-up required from this run."

        raw_status = _text(player.get("status")).casefold()
        fail_reason = _text(player.get("fail_reason")).casefold()
        hidden_promotion_only = (
            "at least one observed promotional claim was not confirmed by the portal" in fail_reason
            and not other["promotion_attention"]
        )
        specific_attention = bool(
            daily["attention_required"]
            or store["attention_required"]
            or progression["attention_required"]
            or other["promotion_attention"]
            or other["event_attention"]
            or hard_failure
        )
        generic_attention = (
            not specific_attention
            and not hidden_promotion_only
            and (
                raw_status in {"partial", "no rewards"}
                or (bool(player.get("retry_required")) and bool(fail_reason))
            )
        )
        attention = specific_attention or generic_attention
        if generic_attention:
            next_actions.append(
                "Review the run-level exception in the workflow log; do not infer a successful claim."
            )
        account_status = (
            "Needs attention" if attention else
            "Pending inspection" if progression["pending_inspection"] else
            status
        )
        records.append({
            "account": _account_label(player),
            "display_name": _text(player.get("display_name"))[:80] or "IGN unavailable",
            "player_id_last4": _player_suffix(player),
            "run_status": status,
            "account_status": account_status,
            "daily": daily,
            "store": store,
            "progression": progression,
            "loyalty": other["loyalty"],
            "promotions": other["promotions"],
            "events": other["events"],
            "next_action": next_action,
            "attention_required": attention,
            "pending_inspection": bool(progression["pending_inspection"]),
        })
    return records


def _category_summary(records: list[dict[str, Any]], key: str) -> str:
    values = [record.get(key, {}) for record in records]
    states = [value.get("state") for value in values if isinstance(value, dict)]
    not_checked = states.count("not_checked")
    if not states or not_checked == len(states):
        return "Not checked"
    bot_count = sum(_integer(value.get("count")) for value in values if isinstance(value, dict) and value.get("state") == "claimed_by_bot")
    portal_count = sum(1 for value in values if isinstance(value, dict) and value.get("state") == "portal_claimed")
    retry_count = sum(1 for value in values if isinstance(value, dict) and value.get("state") == "retry_queued")
    pending_count = sum(1 for value in values if isinstance(value, dict) and value.get("pending_inspection"))
    other_attention = sum(
        1 for value in values if isinstance(value, dict)
        and value.get("state") in {"unknown", "available_not_claimed", "claim_unconfirmed"}
    )
    checked_count = len(states) - not_checked
    parts = []
    if bot_count:
        parts.append(f"{bot_count} bot-confirmed")
    if portal_count:
        parts.append(f"{portal_count} portal says claimed")
    if retry_count:
        parts.append(f"{retry_count} retry queued")
    if pending_count:
        parts.append(f"{pending_count} pending")
    if other_attention:
        parts.append(f"{other_attention} needs review")
    if not parts:
        parts.append(f"{checked_count} checked")
    if not_checked:
        parts.append(f"{not_checked} not checked")
    return " · ".join(parts)


def summarize_account_activity(records: Any) -> list[dict[str, str]]:
    """Produce scope-aware category cards without turning Not checked into zero."""
    rows = records if isinstance(records, list) else []
    categories = [
        {"label": "Daily", "value": _category_summary(rows, "daily")},
        {"label": "Standard Store", "value": _category_summary(rows, "store")},
        {"label": "Progression", "value": _category_summary(rows, "progression")},
    ]

    scanned = [
        str(row.get("promotions") or "") for row in rows
        if "Scanned ·" in str(row.get("promotions") or "")
    ]
    if scanned:
        def extract_count(value: str, marker: str) -> int:
            try:
                return _integer(value.split(marker, 1)[0].split("·")[-1].strip())
            except (TypeError, ValueError, IndexError):
                return 0
        offer_count = sum(extract_count(value, " offer observation") for value in scanned)
        confirmed_count = sum(extract_count(value, " confirmed claim") for value in scanned)
        promo_value = (
            f"{len(scanned)} account(s) scanned · {offer_count} offer observation(s) · "
            f"{confirmed_count} confirmed claim(s)"
        )
    else:
        promo_value = "Not checked"
    categories.append({"label": "Promotions", "value": promo_value})

    checked_events = [str(row.get("events") or "") for row in rows if row.get("events") != "Not checked"]
    if checked_events:
        event_claims = sum(
            _integer(value.split(" confirmed claim", 1)[0])
            for value in checked_events if " confirmed claim" in value
        )
        event_pending = sum(1 for value in checked_events if "pending" in value.casefold())
        event_value = f"{event_claims} confirmed claim(s) · {len(checked_events)} account(s) checked"
        if event_pending:
            event_value += f" · {event_pending} pending"
    else:
        event_value = "Not checked"
    categories.append({"label": "Event rewards", "value": event_value})

    checked_loyalty = [str(row.get("loyalty") or "") for row in rows if row.get("loyalty") != "Not checked"]
    if checked_loyalty:
        loyalty_claims = sum(
            _integer(value.split(" confirmed", 1)[0])
            for value in checked_loyalty if " confirmed" in value
        )
        not_enrolled = sum(1 for value in checked_loyalty if value == "Not enrolled")
        categories.append({
            "label": "Loyalty",
            "value": f"{loyalty_claims} confirmed · {not_enrolled} not enrolled · {len(checked_loyalty)} checked",
        })
    else:
        categories.append({"label": "Loyalty", "value": "Not checked"})
    return categories


def render_account_activity_section(records: Any) -> str:
    """Render one compact card per account, shared unchanged by email and Copy Report."""
    rows = records if isinstance(records, list) else []
    if not rows:
        return (
            "<section class='account-activity-section'>"
            "<h2>Account Activity</h2>"
            "<p>No per-account activity records were available for this run.</p>"
            "</section>"
        )

    pending = sum(1 for row in rows if isinstance(row, dict) and row.get("pending_inspection"))
    attention = sum(1 for row in rows if isinstance(row, dict) and row.get("attention_required"))
    intro = (
        f"{len(rows)} account entries · {pending} Progression inspection(s) pending · "
        f"{attention} account(s) need attention. Each player is shown separately."
    )
    cards = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        daily = row.get("daily") if isinstance(row.get("daily"), dict) else {}
        store = row.get("store") if isinstance(row.get("store"), dict) else {}
        progression = row.get("progression") if isinstance(row.get("progression"), dict) else {}
        grenades = progression.get("grenades") if isinstance(progression.get("grenades"), dict) else {}
        grenade_detail = grenades.get("detail") or "Grenade balance not captured."
        progression_detail = _text(progression.get("detail"))
        progression_value = _text(progression.get("label")) or "Not checked"
        progression_extra = (
            f"<div style='margin-top:3px;color:#52647d;font-size:11px;line-height:1.4'>{escape(progression_detail)}</div>"
            if progression_detail else ""
        )
        status_label = _text(row.get("account_status")) or "Status not recorded"
        status_style = (
            "background:#fef3c7;color:#92400e" if row.get("attention_required")
            else "background:#e0f2fe;color:#075985" if row.get("pending_inspection")
            else "background:#dcfce7;color:#166534"
        )
        card = (
            "<article class='account-activity-card' "
            "style='margin:0 0 9px;padding:11px 12px;border:1px solid #dbe3ec;border-radius:9px;background:#ffffff'>"
            "<div style='display:block;margin-bottom:7px'>"
            f"<strong style='font-size:13px;color:#172b4d'>{escape(_text(row.get('account')) or 'Unknown account')}</strong>"
            f"<span style='display:inline-block;margin:4px 0 0 6px;padding:3px 7px;border-radius:12px;font-size:10px;font-weight:700;{status_style}'>"
            f"{escape(status_label)}</span></div>"
            "<div style='font-size:11px;line-height:1.5;color:#334155'>"
            f"<div><strong>Daily:</strong> {escape(_text(daily.get('label')) or 'Unknown')} — {escape(_text(daily.get('detail')))}</div>"
            f"<div><strong>Store:</strong> {escape(_text(store.get('label')) or 'Unknown')} — {escape(_text(store.get('detail')))}</div>"
            f"<div><strong>Progression:</strong> {escape(progression_value)}{progression_extra}</div>"
            f"<div style='margin-top:3px'><strong>Grenades:</strong> {escape(_text(grenade_detail))}</div>"
            f"<div style='margin-top:3px'><strong>Other:</strong> Loyalty {escape(_text(row.get('loyalty')))} · "
            f"Promotions {escape(_text(row.get('promotions')))} · Events {escape(_text(row.get('events')))}</div>"
            f"<div style='margin-top:5px;padding-top:5px;border-top:1px solid #edf1f6'><strong>Next action:</strong> "
            f"{escape(_text(row.get('next_action')) or 'No account-level follow-up required.')}</div>"
            "</div></article>"
        )
        cards.append(card)

    return (
        "<section class='account-activity-section' "
        "style='margin:0 0 12px;padding:15px;border:1px solid #e1e7ef;border-radius:12px;background:#ffffff'>"
        "<h2 style='font-size:15px;margin:0 0 5px;color:#172b4d'>Account Activity</h2>"
        f"<p style='font-size:11px;color:#64748b;line-height:1.45;margin:0 0 10px'>{escape(intro)}</p>"
        + "".join(cards)
        + "</section>"
    )
