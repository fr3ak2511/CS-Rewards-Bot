"""Safer Progression Program claim orchestration.

This helper is deliberately independent of master_claimer's global Selenium
objects. The caller supplies the existing login/page-inspection/history helpers.
It requires a popup guard, targets one live Claim button at a time, relies on a
portal state transition for success, and never retries an unconfirmed click.
"""
from __future__ import annotations

import time
from typing import Any, Callable

_DELIVERED_LABELS = {
    "delivered", "already delivered", "claimed", "already claimed",
    "collected", "redeemed",
}


def _safe_snapshot(snapshot_fn: Callable, driver, log: Callable[[str], None]) -> dict[str, Any] | None:
    try:
        state = snapshot_fn(driver)
    except Exception as exc:
        log(f"⚠️ Progression state read failed: {type(exc).__name__}")
        return None
    if not isinstance(state, dict):
        return None
    return state


def _is_confirmed(before: dict[str, Any], after: dict[str, Any]) -> bool:
    """Prefer same-card Delivered evidence; retain delivered-count fallback.

    The fallback supports the existing v3.3.2 contract and unit-test fixtures.
    The production snapshot returns a card signature, so a state change on an
    unrelated reward card cannot confirm the target click.
    """
    signature = str(before.get("card_signature") or "").strip()
    if signature:
        delivered_signatures = after.get("delivered_signatures") or []
        if signature in delivered_signatures:
            return True
        # Some portal layouts represent a Delivered status outside a button.
        terminal_cards = after.get("terminal_card_signatures") or []
        if signature in terminal_cards:
            return True
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
) -> int:
    """Claim visible Progression rewards, one card at a time, with portal proof."""
    claimed = 0
    unconfirmed = False
    max_claims_per_visit = max(1, min(12, int(max_claims_per_visit)))
    verify_polls = max(1, min(40, int(verify_polls)))

    def _dismiss() -> bool:
        try:
            ok, reason = dismiss_popups(driver)
        except Exception as exc:
            log(f"🚩 Progression popup inspection failed: {type(exc).__name__}")
            return False
        if not ok:
            log(f"🚩 Progression blocked by popup/overlay guard: {reason}")
            return False
        return True

    try:
        log("🎯 Checking Progression Program...")
        driver.get("https://hub.vertigogames.co/progression-program")
        bypass_cloudflare(driver)
        sleep(1.5)
        if not _dismiss():
            unconfirmed = True
        else:
            sweep = 0
            while claimed < max_claims_per_visit and sweep < 7:
                before = _safe_snapshot(snapshot_fn, driver, log)
                if before is None:
                    unconfirmed = True
                    log("🚩 Progression page could not be inspected reliably")
                    break
                if not before.get("valid_page", True):
                    unconfirmed = True
                    log(
                        "🚩 Progression URL/page validation failed; refusing all reward clicks "
                        f"(url={str(before.get('url') or '')[:160]!r}, "
                        f"title={str(before.get('title') or '')[:100]!r})"
                    )
                    break

                claimable_count = int(before.get("claimable_count", 0) or 0)
                if claimable_count <= 0:
                    moved = _scroll_carousel(scroll_carousel, driver)
                    sweep += 1
                    if moved:
                        sleep(0.35)
                        if not _dismiss():
                            unconfirmed = True
                            break
                        continue
                    log("ℹ️ Progression: no currently visible claimable reward after carousel scan")
                    break

                button = before.get("button")
                if button is None:
                    candidates = before.get("claimable_cards") or []
                    button = candidates[0].get("button") if candidates and isinstance(candidates[0], dict) else None
                if button is None:
                    unconfirmed = True
                    log("🚩 Progression reported an available reward but no card-bound button reference")
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
                        unconfirmed = True
                        log(f"🚩 Progression target could not be positioned: {type(exc).__name__}")
                        break
                sleep(0.15)
                if not _dismiss():
                    unconfirmed = True
                    break

                try:
                    clicked = bool(click_fn(driver, button))
                except Exception as exc:
                    clicked = False
                    log(f"⚠️ Progression native click failed: {type(exc).__name__}")
                if not clicked:
                    unconfirmed = True
                    log("🚩 Progression click was not safely dispatched; leaving reward retryable")
                    break

                button_text = str(before.get("button_text") or "claim").strip().lower()
                signature = str(before.get("card_signature") or "").strip()
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
                    if not _dismiss():
                        last_popup_reason = "blocking popup/overlay during post-click verification"
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
                log(f"✅ Progression reward confirmed by the portal ({claimed} this visit)")
                sleep(0.25)
                # Take a fresh snapshot on the next pass; a newly available
                # milestone can appear after the prior reward is redeemed.
                sweep = 0

    except Exception as exc:
        unconfirmed = True
        log(f"❌ Progression error: {type(exc).__name__}: {str(exc)[:160]}")
    finally:
        try:
            update_history(player_id, "progression", claimed_count=claimed, unconfirmed=unconfirmed)
        except Exception as exc:
            log(f"⚠️ Could not save Progression history: {type(exc).__name__}")
        try:
            driver._progression_unconfirmed = bool(unconfirmed)
        except Exception:
            pass

    return claimed


__all__ = ["claim_progression_rewards"]
