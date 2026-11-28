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

    if count and unconfirmed:
        item_note = (
            " · confirmed item(s) " + ", ".join(f"#{item}" for item in claimed_indices)
            if claimed_indices else ""
        )
        state = "retry_queued"
        label = f"{count} confirmed · retry queued"
        detail = (
            f"{count} standard Store reward(s) were confirmed by the portal after the bot action{item_note}. "
            "Unconfirmed Store item(s): " + ", ".join(f"#{item}" for item in unconfirmed) + "."
        )
        action = "Keep the listed Store item(s) on the existing saved-target recovery path; do not retry confirmed items."
        attention = True
    elif count:
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
        return "Blocked by an on-screen popup or overlay. The safety guard did not click through."
    if outcome == "empty_scan" or "no currently visible claimable reward" in raw.casefold():
        return (
            "No claimable reward card visible in the captured scan. An empty scan is not proof "
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
        minimum_at_observation = carry.get("expected_minimum_at_observation")
        current_minimum = carry.get("expected_minimum_as_of")
        # The saved count is compared with the minimum that applied when it was
        # observed. A newer date minimum is shown separately and is not treated
        # as a fresh validation of the carried-forward count.
        minimum = (
            minimum_at_observation if minimum_at_observation is not None
            else current_minimum
        )
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
    ratio_text = (
        f"{count} / {minimum_text} at observation"
        if freshness.startswith("Carried forward") and minimum is not None
        else f"{count} / {minimum_text} vs date minimum"
        if minimum is not None else "date minimum not available"
    )
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
    if freshness.startswith("Carried forward"):
        current_minimum_text = (
            str(_integer(current_minimum)) if current_minimum is not None else "not available"
        )
        minimum_context = f"current date minimum {current_minimum_text}"
        if status == "pass":
            status_label = "validated at observation"
    else:
        minimum_context = f"date minimum {minimum_text}"
    detail = (
        f"{count} Grenades · {ratio_text} · {freshness} · {minimum_context} · "
        f"Next milestone {milestone_text} · {remaining_text} · {status_label}"
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

    progression_in_scope = (
        mode != "promotional_only"
        and (
            checked_value is True or deferred or unconfirmed
            or bool(player.get("progression_claim_attempted"))
            or reinspection_attempted
        )
    )
    if not progression_in_scope:
        count = 0

    if unconfirmed:
        state = "claim_unconfirmed"
        label = f"{count} confirmed · another claim unconfirmed" if count else "Claim attempted · portal confirmation missing"
        detail = reason or "The exact Progression reward was not confirmed as Delivered."
        action = "Keep the exact target pending; require the same card to show Delivered in the portal. Do not broaden the retry."
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
        f"Scanned · {len(promotions)} reportable offer observation(s) · {promo_claimed} confirmed claim(s)"
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

    recovery = player.get("immediate_recovery")
    recovery = recovery if isinstance(recovery, dict) else {}
    recovery_text = ""
    if recovery.get("attempted"):
        recovery_status = _text(recovery.get("status") or "pending").replace("_", " ").title()
        resolved_categories = recovery.get("resolved_categories")
        resolved_categories = [
            _text(item) for item in resolved_categories if _text(item)
        ] if isinstance(resolved_categories, list) else []
        recovered_claims = _integer(recovery.get("recovered_claims"))
        recovery_parts = [f"Targeted Recovery · Post-run Recovery: {recovery_status}"]
        if resolved_categories:
            recovery_parts.append(
                "Replayed exact saved target(s): " + ", ".join(resolved_categories)
            )
        if recovered_claims:
            recovery_parts.append(f"{recovered_claims} claim(s) confirmed")
        if _text(recovery.get("reason")):
            recovery_parts.append(_text(recovery.get("reason"))[:100])
        recovery_text = " · ".join(recovery_parts)

    return {
        "loyalty": loyalty,
        "promotions": promo_text,
        "promotion_attention": bool(reportable_promotion_issue),
        "promotion_popup_blocked": popup_blocked,
        "events": event_text,
        "event_attention": event_retry,
        "recovery": recovery_text,
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
        raw_status = _text(player.get("status")).casefold()
        raw_fail_reason = _text(player.get("fail_reason"))
        hidden_promotion_only = (
            "at least one observed promotional claim was not confirmed by the portal"
            in raw_fail_reason.casefold()
            and not other["promotion_attention"]
        )
        existing_detail = " ".join([
            _text(daily.get("detail")), _text(store.get("detail")),
            _text(progression.get("detail")), _text(other.get("promotions")),
        ]).casefold()
        failure_reason_action = (
            "Run exception: " + raw_fail_reason[:180]
            if raw_fail_reason and not hidden_promotion_only
            and raw_fail_reason.casefold() not in existing_detail
            and not (
                "progression" in raw_fail_reason.casefold()
                and any(term in raw_fail_reason.casefold() for term in ("no currently visible", "inspection remains queued", "overlay"))
            )
            else ""
        )
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
                failure_reason_action,
                "Review event reward confirmation; at least one event claim remains pending."
                    if other["event_attention"] else "",
                f"Recovery: {other['recovery']}" if other["recovery"] else "",
                "Review the account's login/processing failure before treating the cycle as complete."
                    if hard_failure else "",
            ) if item
        ]
        if next_actions:
            next_action = " ".join(dict.fromkeys(next_actions))
        else:
            next_action = "No account-level follow-up required from this run."

        fail_reason = raw_fail_reason.casefold()
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
            "recovery": other["recovery"],
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
    bot_count = sum(
        _integer(value.get("count")) for value in values
        if isinstance(value, dict) and value.get("state") in {"claimed_by_bot", "retry_queued"}
    )
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


def _shared_progression_detail(progression: dict[str, Any]) -> str:
    """Return the common inspection reason, excluding per-account attempt counters."""
    detail = _text(progression.get("detail"))
    for marker in (" · latest outcome:", " · same outcome ", " · "):
        if marker in detail:
            detail = detail.split(marker, 1)[0].strip()
            break
    return detail


def _account_activity_group_key(row: dict[str, Any]) -> tuple[Any, ...]:
    """Group only records with identical reward states, explanations and next action.

    Grenade readings and Progression attempt counters remain account-specific and
    are rendered alongside the grouped account names rather than splitting an
    otherwise identical operational group.
    """
    daily = row.get("daily") if isinstance(row.get("daily"), dict) else {}
    store = row.get("store") if isinstance(row.get("store"), dict) else {}
    progression = row.get("progression") if isinstance(row.get("progression"), dict) else {}

    daily_key = tuple(
        daily.get(key) for key in
        ("state", "label", "detail", "count", "attention_required", "next_action")
    )
    store_key = tuple(
        (tuple(store.get(key) or []) if key in {"claimed_indices", "unconfirmed_indices"}
         else store.get(key))
        for key in
        ("state", "label", "detail", "count", "claimed_indices",
         "unconfirmed_indices", "attention_required", "next_action")
    )
    progression_key = (
        progression.get("state"),
        progression.get("label"),
        _shared_progression_detail(progression),
        progression.get("count"),
        progression.get("pending_inspection"),
        progression.get("attention_required"),
        progression.get("next_action"),
    )
    return (
        _text(row.get("account_status")),
        daily_key,
        store_key,
        progression_key,
        _text(row.get("loyalty")),
        _text(row.get("promotions")),
        _text(row.get("events")),
        _text(row.get("recovery")),
        _text(row.get("next_action")),
        bool(row.get("attention_required")),
        bool(row.get("pending_inspection")),
    )


def _grouped_account_label(row: dict[str, Any], include_inspection_history: bool) -> str:
    """Keep each masked account identifiable and retain its own PP retry counters."""
    label = _text(row.get("account")) or "Unknown account"
    if not include_inspection_history:
        return label
    progression = row.get("progression") if isinstance(row.get("progression"), dict) else {}
    if not progression.get("pending_inspection"):
        return label
    notes = []
    slots = _integer(progression.get("slots_used"))
    attempts = _integer(progression.get("inspection_attempts"))
    repeats = _integer(progression.get("same_outcome_count"))
    if slots:
        notes.append(f"{slots}/{MAX_PROGRESS_INSPECTION_SLOTS} slots")
    if attempts:
        notes.append(f"{attempts} total attempts")
    if repeats > 1:
        notes.append(f"same outcome {repeats}x")
    if progression.get("manual_attention_required"):
        notes.append("manual review")
    return label + (f" ({'; '.join(notes)})" if notes else "")


def _render_group_grenade_balances(group: list[dict[str, Any]]) -> str:
    """Render a shared balance once, or map differing balances to their accounts."""
    balances: dict[str, list[str]] = {}
    for row in group:
        progression = row.get("progression") if isinstance(row.get("progression"), dict) else {}
        grenade = progression.get("grenades") if isinstance(progression.get("grenades"), dict) else {}
        detail = _text(grenade.get("detail")) or "Grenade balance not captured."
        balances.setdefault(detail, []).append(
            _text(row.get("account")) or "Unknown account"
        )
    if not balances:
        return ""
    if len(balances) == 1:
        return (
            "<div style='margin-top:3px'><strong>Grenades:</strong> "
            + escape(next(iter(balances))) + "</div>"
        )
    parts = []
    for detail, accounts in balances.items():
        parts.append(
            "<div style='margin-top:3px'><strong>"
            + escape(detail)
            + ":</strong> "
            + escape(", ".join(accounts))
            + "</div>"
        )
    return "<div style='margin-top:3px'><strong>Grenade balances by account:</strong>" + "".join(parts) + "</div>"


def render_account_activity_section(records: Any) -> str:
    """Render category-first outcomes and only actionable account-level exceptions."""
    rows = [row for row in records if isinstance(row, dict)] if isinstance(records, list) else []
    if not rows:
        return (
            "<section class='account-activity-section'>"
            "<h2>Reward Summary</h2>"
            "<p>No per-account activity records were available for this run.</p>"
            "</section>"
        )

    categories = summarize_account_activity(rows)
    category_html = "".join(
        "<div class='reward-category'>"
        "<strong>" + escape(item["label"]) + "</strong>"
        "<span>" + escape(item["value"]) + "</span></div>"
        for item in categories
    )

    portal_claimed: list[str] = []
    actionable: dict[str, list[str]] = {
        "Daily": [], "Store": [], "Progression": [],
        "Loyalty": [], "Promotions": [], "Event rewards": [], "Run / recovery": [],
    }
    empty_scan_pending = 0

    for row in rows:
        account = _text(row.get("account")) or "Unknown player"
        for key, label in (("daily", "Daily"), ("store", "Store")):
            item = row.get(key) if isinstance(row.get(key), dict) else {}
            state = _text(item.get("state"))
            if state == "portal_claimed":
                portal_claimed.append(f"{label} — {account}")
            elif item.get("attention_required"):
                detail = _text(item.get("detail"))
                action = _text(item.get("next_action"))
                actionable[label].append(
                    account + (f" — {detail}" if detail else "")
                    + (f" Next: {action}" if action else "")
                )

        pp = row.get("progression") if isinstance(row.get("progression"), dict) else {}
        if pp.get("pending_inspection") and _text(pp.get("outcome")) == "empty_scan":
            empty_scan_pending += 1
        elif pp.get("attention_required"):
            detail = _text(pp.get("detail"))
            action = _text(pp.get("next_action"))
            actionable["Progression"].append(
                account + (f" — {detail}" if detail else "")
                + (f" Next: {action}" if action else "")
            )

        if _text(row.get("loyalty")) not in {"", "Not checked", "Not enrolled"} and (
            "pending" in _text(row.get("loyalty")).casefold()
            or "unconfirmed" in _text(row.get("loyalty")).casefold()
        ):
            actionable["Loyalty"].append(account + " — " + _text(row.get("loyalty")))

        if "safety popup blocked" in _text(row.get("promotions")).casefold() or (
            "incomplete" in _text(row.get("promotions")).casefold()
        ):
            actionable["Promotions"].append(account + " — " + _text(row.get("promotions")))

        if "pending" in _text(row.get("events")).casefold():
            actionable["Event rewards"].append(account + " — " + _text(row.get("events")))

        if row.get("attention_required") and not any(
            (isinstance(row.get(k), dict) and row[k].get("attention_required"))
            for k in ("daily", "store", "progression")
        ) and not actionable["Promotions"] and not actionable["Event rewards"]:
            actionable["Run / recovery"].append(
                account + " — " + (_text(row.get("next_action")) or "Review run outcome.")
            )

    sections = []
    for label, entries in actionable.items():
        if entries:
            sections.append(
                "<div class='exception-group'><strong>" + escape(label) + "</strong><ul>"
                + "".join("<li>" + escape(entry) + "</li>" for entry in dict.fromkeys(entries))
                + "</ul></div>"
            )

    queue_note = (
        f"<p class='queue-note'>Progression reinspection queue: {empty_scan_pending} account(s) pending "
        "after a valid empty scan. These remain queued for a later safe pass; no account-level failure is inferred.</p>"
        if empty_scan_pending else ""
    )
    already_claimed = (
        "<div class='exception-group portal-claimed'><strong>Portal already claimed "
        "(actor unknown; not credited to the bot)</strong><ul>"
        + "".join("<li>" + escape(item) + "</li>" for item in dict.fromkeys(portal_claimed))
        + "</ul></div>"
        if portal_claimed else ""
    )
    exceptions = (
        "<section class='report-exceptions'><h3>Exceptions &amp; follow-up</h3>"
        + ("".join(sections) if sections else "<p>No actionable exceptions recorded.</p>")
        + already_claimed + queue_note + "</section>"
    )

    return (
        "<section class='account-activity-section'>"
        "<h2>Reward Summary</h2>"
        "<div class='reward-categories'>" + category_html + "</div>"
        + exceptions + "</section>"
    )
