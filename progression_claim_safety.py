"""Safer Progression Program claim orchestration.

This helper is deliberately independent of master_claimer's global Selenium
objects. The caller supplies the existing login/page-inspection/history helpers.
It requires a popup guard, targets one live Claim button at a time, relies on a
portal state transition for success, and never retries an unconfirmed click.
"""
from __future__ import annotations

import re
import time
from typing import Any, Callable

_DELIVERED_LABELS = {
    "delivered", "already delivered", "claimed", "already claimed",
    "collected", "redeemed",
}
_CLAIM_LABELS = {"claim", "claim all"}


def _safe_snapshot(snapshot_fn: Callable, driver, log: Callable[[str], None]) -> dict[str, Any] | None:
    try:
        state = snapshot_fn(driver)
    except Exception as exc:
        log(f"⚠️ Progression state read failed: {type(exc).__name__}")
        return None
    if not isinstance(state, dict):
        return None
    return state


def _normalise_card_identity(value: Any) -> str:
    """Normalise a card identity while ignoring its changing CTA/status label.

    The live portal can render the same card as ``6 x20 Claim`` before a click
    and ``6 x20 Delivered`` after it. Those labels describe state, not identity.
    Remove only known action/status words, timers and spacing differences.
    """
    text = str(value or "").casefold()
    text = re.sub(
        r"\b(?:already\s+delivered|already\s+claimed|claim\s+all|claim|delivered|claimed|collected|redeemed)\b",
        " ",
        text,
    )
    text = re.sub(r"\b\d{1,2}:\d{2}(?::\d{2})?\b", " ", text)
    text = re.sub(r"\bnext\s+(?:reward\s+)?in\b.*$", " ", text)
    return re.sub(r"\s+", " ", text).strip(" |:-")


def _is_confirmed(before: dict[str, Any], after: dict[str, Any]) -> bool:
    """Confirm by exact or status-normalised same-card Delivered evidence.

    Exact signature equality remains preferred. If the portal changes the CTA
    text as part of the same card, compare a conservative canonical identity.
    A canonical match must be unique among delivered cards; an unrelated or
    ambiguous card must never confirm the click.
    """
    signature = str(before.get("card_signature") or "").strip()
    delivered_signatures = after.get("delivered_signatures") or []
    terminal_cards = after.get("terminal_card_signatures") or []
    candidates = list(dict.fromkeys(
        str(item).strip()
        for item in list(delivered_signatures) + list(terminal_cards)
        if str(item).strip()
    ))

    if signature:
        if signature in candidates:
            return True
        before_identity = _normalise_card_identity(before.get("card_text") or signature)
        if not before_identity:
            before_identity = _normalise_card_identity(signature)
        if before_identity:
            matches = [
                item for item in candidates
                if _normalise_card_identity(item) == before_identity
            ]
            return len(matches) == 1
        return False

    try:
        return int(after.get("delivered_count", 0) or 0) > int(before.get("delivered_count", 0) or 0)
    except (TypeError, ValueError):
        return False


def _scroll_carousel(scroll_fn: Callable | None, driver) -> bool:
    if not callable(scroll_fn):
        return False
    try:
        return bool(scroll_fn(driver))
    except Exception:
        return False


def claim_progression_rewards(
    driver,
    player_id: str,
    *,
    log: Callable[[str], None],
    bypass_cloudflare: Callable,
    snapshot_fn: Callable,
    click_fn: Callable,
    update_history: Callable,
    dismiss_popups: Callable,
    scroll_carousel: Callable | None = None,
    sleep: Callable[[float], None] = time.sleep,
    verify_polls: int = 16,
    poll_seconds: float = 0.5,
    max_claims_per_visit: int = 6,
    target_card_signature: str | None = None,
) -> int:
    """Claim visible Progression rewards, optionally restricting recovery to one exact card.

    A blocked inspection is not a failed claim. Only a dispatched click without
    same-card portal confirmation remains an unconfirmed claim. If this is a
    recheck of an earlier unconfirmed card, a blocked inspection preserves that
    earlier unresolved target without dispatching another click.
    """
    claimed = 0
    unconfirmed = False
    deferred = False
    deferred_reason = None
    claim_attempted = False
    prior_target_signature = str(target_card_signature or "").strip() or None
    unconfirmed_card_signature = prior_target_signature
    max_claims_per_visit = max(1, min(12, int(max_claims_per_visit)))
    verify_polls = max(1, min(40, int(verify_polls)))

    def _defer(reason: str) -> None:
        nonlocal deferred, deferred_reason, unconfirmed, unconfirmed_card_signature
        deferred = True
        deferred_reason = str(reason or "Progression inspection could not be completed")
        # A saved target signature proves that an earlier click was already
        # dispatched. Keep that earlier failure unresolved, but never create a
        # new unconfirmed-claim state for an inspection that made no click.
        if prior_target_signature:
            unconfirmed = True
            unconfirmed_card_signature = prior_target_signature
        log(f"⏭️ Progression inspection deferred: {deferred_reason}")

    def _dismiss() -> tuple[bool, str]:
        try:
            ok, reason = dismiss_popups(driver)
        except Exception as exc:
            reason = f"popup inspection failed ({type(exc).__name__})"
            log(f"🚩 Progression popup inspection failed: {type(exc).__name__}")
            return False, reason
        reason = str(reason or "popup/overlay safety guard did not clear")
        if not ok:
            log(f"🚩 Progression blocked by popup/overlay guard: {reason}")
            return False, reason
        return True, reason

    try:
        log("🎯 Checking Progression Program...")
        driver.get("https://hub.vertigogames.co/progression-program")
        bypass_cloudflare(driver)
        sleep(1.5)
        popup_clear, popup_reason = _dismiss()
        if not popup_clear:
            _defer(f"popup/overlay safety guard blocked inspection ({popup_reason})")
        else:
            sweep = 0
            while claimed < max_claims_per_visit and sweep < 7:
                before = _safe_snapshot(snapshot_fn, driver, log)
                if before is None:
                    _defer("Progression page snapshot was unavailable")
                    break
                if not before.get("valid_page", True):
                    _defer(
                        "Progression page validation failed "
                        f"(url={str(before.get('url') or '')[:160]!r}, "
                        f"title={str(before.get('title') or '')[:100]!r})"
                    )
                    break

                if prior_target_signature:
                    target_signature = prior_target_signature
                    claimable_cards = [
                        card for card in (before.get("claimable_cards") or [])
                        if isinstance(card, dict)
                        and str(card.get("card_signature") or "").strip() == target_signature
                    ]
                    if len(claimable_cards) == 1:
                        target_card = claimable_cards[0]
                        before = dict(before)
                        before["button"] = target_card.get("button")
                        before["button_text"] = target_card.get("button_text") or "claim"
                        before["card_signature"] = target_signature
                        before["card_text"] = target_card.get("card_text") or target_signature
                        before["claimable_count"] = 1
                    else:
                        target_identity = _normalise_card_identity(target_signature)
                        delivered = [
                            str(item).strip()
                            for item in list(before.get("delivered_signatures") or []) + list(before.get("terminal_card_signatures") or [])
                            if str(item).strip()
                        ]
                        delivered_matches = [
                            item for item in delivered
                            if item == target_signature
                            or (target_identity and _normalise_card_identity(item) == target_identity)
                        ]
                        if len(delivered_matches) == 1:
                            log("✅ Progression recovery: the exact previously unconfirmed card is now portal-confirmed Delivered")
                            unconfirmed = False
                            unconfirmed_card_signature = None
                            break
                        unconfirmed = True
                        unconfirmed_card_signature = target_signature
                        log("🚩 Progression recovery: the previously unconfirmed card could not be uniquely identified as claimable or Delivered; refusing to click another card")
                        break

                claimable_count = int(before.get("claimable_count", 0) or 0)
                if claimable_count <= 0:
                    moved = _scroll_carousel(scroll_carousel, driver)
                    sweep += 1
                    if moved:
                        sleep(0.35)
                        popup_clear, popup_reason = _dismiss()
                        if not popup_clear:
                            _defer(f"popup/overlay safety guard blocked carousel recheck ({popup_reason})")
                            break
                        continue
                    log("ℹ️ Progression: no currently visible claimable reward after carousel scan")
                    break

                button = before.get("button")
                if button is None:
                    candidates = before.get("claimable_cards") or []
                    button = candidates[0].get("button") if candidates and isinstance(candidates[0], dict) else None
                if button is None:
                    _defer("Progression showed a possible reward but supplied no safe card-bound button reference")
                    break

                # Scroll is positioning only. Re-run the multi-popup guard after
                # positioning because an offer may animate in during lazy render.
                if callable(getattr(driver, "execute_script", None)):
                    try:
                        driver.execute_script(
                            "arguments[0].scrollIntoView({block:'center', inline:'center', behavior:'instant'});",
                            button,
                        )
                    except Exception as exc:
                        _defer(f"Progression target could not be positioned ({type(exc).__name__})")
                        break
                sleep(0.15)
                popup_clear, popup_reason = _dismiss()
                if not popup_clear:
                    _defer(f"popup/overlay safety guard blocked the claim before click ({popup_reason})")
                    break

                try:
                    clicked = bool(click_fn(driver, button))
                except Exception as exc:
                    clicked = False
                    log(f"⚠️ Progression native click failed: {type(exc).__name__}")
                if not clicked:
                    # Keep this conservative: a failed/ambiguous native dispatch
                    # remains retryable, but is distinguished from a confirmed
                    # click through claim_attempted=False in the run evidence.
                    unconfirmed = True
                    unconfirmed_card_signature = str(before.get("card_signature") or prior_target_signature or "").strip() or None
                    log("🚩 Progression click was not safely dispatched; preserving the exact target for retry")
                    break

                claim_attempted = True
                button_text = str(before.get("button_text") or "claim").strip().lower()
                signature = str(before.get("card_signature") or prior_target_signature or "").strip()
                log(
                    f"🖱️ Progression {button_text!r} click dispatched"
                    + (f" for card {signature[:90]!r}" if signature else "")
                    + "; awaiting same-card Delivered confirmation"
                )

                confirmed = False
                last_after = None
                last_popup_reason = "clear"
                for _ in range(verify_polls):
                    # Dismiss any offer that appears after dispatch; if an
                    # unknown overlay remains, it cannot count as a success.
                    popup_clear, popup_reason = _dismiss()
                    if not popup_clear:
                        last_popup_reason = f"blocking popup/overlay during post-click verification ({popup_reason})"
                        sleep(poll_seconds)
                        continue
                    after = _safe_snapshot(snapshot_fn, driver, log)
                    if after is not None:
                        last_after = after
                        if _is_confirmed(before, after):
                            confirmed = True
                            break
                    sleep(poll_seconds)

                if not confirmed:
                    unconfirmed = True
                    unconfirmed_card_signature = signature or prior_target_signature or None
                    after_count = (last_after or {}).get("claimable_count", "unknown")
                    delivered_count = (last_after or {}).get("delivered_count", "unknown")
                    log(
                        "🚩 Progression click did not produce a same-card Delivered transition; "
                        "no second click will be attempted. "
                        f"Evidence: target_card={signature[:120]!r}; "
                        f"claimable_after={after_count}; delivered_after={delivered_count}; "
                        f"popup_state={last_popup_reason}"
                    )
                    break

                claimed += 1
                unconfirmed = False
                unconfirmed_card_signature = None
                log(f"✅ Progression reward confirmed by the portal ({claimed} this visit)")
                sleep(0.25)
                # Take a fresh snapshot on the next pass; a newly available
                # milestone can appear after the prior reward is redeemed.
                sweep = 0

    except Exception as exc:
        if claim_attempted:
            unconfirmed = True
            unconfirmed_card_signature = (
                unconfirmed_card_signature or prior_target_signature
            )
            log(f"❌ Progression post-click verification failed: {type(exc).__name__}: {str(exc)[:160]}")
        else:
            _defer(f"Progression inspection failed before a claim click ({type(exc).__name__}: {str(exc)[:120]})")
    finally:
        # Do not rewrite claim history as an unconfirmed claim merely because
        # inspection was blocked before any click. A saved old target remains
        # unconfirmed and is deliberately preserved during its deferred recheck.
        if not deferred or prior_target_signature:
            try:
                update_history(player_id, "progression", claimed_count=claimed, unconfirmed=unconfirmed)
            except Exception as exc:
                log(f"⚠️ Could not save Progression history: {type(exc).__name__}")
        try:
            driver._progression_unconfirmed = bool(unconfirmed)
            driver._progression_unconfirmed_card_signature = unconfirmed_card_signature if unconfirmed else None
            driver._progression_deferred = bool(deferred)
            driver._progression_deferred_reason = deferred_reason if deferred else None
            driver._progression_claim_attempted = bool(claim_attempted)
            driver._progression_checked = not bool(deferred)
        except Exception:
            pass

    return claimed

__all__ = ["claim_progression_rewards"]
