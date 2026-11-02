"""Conservative, multi-popup guard for CS Hub reward-page interactions.

This module only closes visible dialog/popup layers through an explicit,
validated close control and a native Selenium click. It never clicks purchase,
claim, confirm, accept, redeem, or the overlay background. One tightly scoped
exception allows the portal's exact ``Continue`` control inside its own
``Your rewards! xN`` success dialog to be dismissed; generic Continue buttons
remain blocked.

A second, bounded settling check handles the portal's short-lived residual
success-dialog wrapper after Continue has already been clicked. It never clicks
that residue or treats a persistent/unknown blocker as clear.
"""
from __future__ import annotations

import re
import time
from typing import Callable

DIALOG_SELECTOR = (
    '[role="dialog"], [aria-modal="true"], '
    '[class*="modal"], [class*="Modal"]'
)
BUTTON_SELECTOR = 'button, [role="button"]'

_LAYER_INSPECT_JS = r"""/* REWARD_POPUP_LAYER_INSPECT_V1 */
const isVisible = el => {
  if (!el) return false;
  const r = el.getBoundingClientRect(), s = getComputedStyle(el);
  return !!(el.getClientRects().length && r.width > 0 && r.height > 0
    && s.display !== 'none' && s.visibility !== 'hidden'
    && Number(s.opacity || '1') > 0 && s.pointerEvents !== 'none');
};
const dialogs = Array.from(document.querySelectorAll(
  '[role="dialog"], [aria-modal="true"], [class*="modal"], [class*="Modal"]'
)).filter(isVisible);
const overlays = Array.from(document.querySelectorAll('[class*="overlay"], [class*="Overlay"]'))
  .filter(el => {
    if (!isVisible(el)) return false;
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width >= window.innerWidth * 0.8
      && r.height >= window.innerHeight * 0.8
      && (s.position === 'fixed' || (r.left <= 2 && r.top <= 2));
  });
return {
  dialogCount: dialogs.length,
  dialogSummaries: dialogs.map(el => ({
    text: (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 240),
    zIndex: getComputedStyle(el).zIndex,
    className: String(el.className || '').slice(0, 180)
  })),
  fullscreenOverlayCount: overlays.length,
  fullscreenOverlayClasses: overlays.map(el => String(el.className || '').slice(0, 180)),
  url: location.href
};"""

_CLOSE_CONTROL_CHECK_JS = r"""/* REWARD_POPUP_CLOSE_CONTROL_CHECK_V2 */
const button = arguments[0], dialog = arguments[1];
if (!button || !dialog) return {ok:false, reason:'missing_element'};
const norm = v => (v || '').replace(/\s+/g, ' ').trim().toLowerCase();
const visible = el => {
  const r = el.getBoundingClientRect(), s = getComputedStyle(el);
  return !!(el.getClientRects().length && r.width > 0 && r.height > 0
    && s.display !== 'none' && s.visibility !== 'hidden'
    && Number(s.opacity || '1') > 0);
};
const text = norm(button.innerText || button.textContent);
const aria = norm(button.getAttribute('aria-label'));
const title = norm(button.getAttribute('title'));
const testid = norm(button.getAttribute('data-testid'));
const variant = norm(button.getAttribute('data-variant'));
const cls = String(button.className || '').toLowerCase();
const type = norm(button.getAttribute('type') || 'button');
const disabled = !!button.disabled || button.getAttribute('aria-disabled') === 'true';
const dialogText = norm(dialog.innerText || dialog.textContent);

// The only non-close action permitted is the portal's exact Continue button
// in a clearly labelled reward-success modal that also displays a quantity.
// Generic Continue buttons, and every purchase/claim/confirm action, stay blocked.
const rewardSuccessTitle = /\byour rewards!(?:\s|$)/.test(dialogText);
const rewardQuantity = /\bx\s*\d+\b/.test(dialogText)
  || /\b\d+\s*(?:coins?|items?|cards?|rewards?)\b/.test(dialogText);
const dialogControls = Array.from(dialog.querySelectorAll('button,[role="button"]'))
  .filter(el => visible(el) && !el.disabled && el.getAttribute('aria-disabled') !== 'true');
const nonCloseControls = dialogControls.filter(el => {
  const elText = norm(el.innerText || el.textContent);
  const elAria = norm(el.getAttribute('aria-label'));
  const elTitle = norm(el.getAttribute('title'));
  const elTestId = norm(el.getAttribute('data-testid'));
  const elClass = String(el.className || '').toLowerCase();
  return !(elTestId === 'close' || ['close', 'close dialog', 'dismiss'].includes(elAria)
    || ['close', 'dismiss'].includes(elTitle) || elClass.includes('button-close')
    || elClass.includes('modal-close') || elClass.includes('dialog-close')
    || ['x', '×', 'close', 'dismiss'].includes(elText));
});
const safeRewardContinue = text === 'continue' && rewardSuccessTitle
  && rewardQuantity && nonCloseControls.length === 1 && nonCloseControls[0] === button;

const actionText = [text, aria, title, testid, variant].join(' ');
const dangerousAction = /\b(buy|purchase|claim|confirm|accept|redeem|subscribe|pay|submit|yes)\b/i.test(actionText)
  || ( /\bcontinue\b/i.test(actionText) && !safeRewardContinue );
const closeEvidence = testid === 'close' || variant === 'close'
  || ['close', 'close dialog', 'dismiss'].includes(aria)
  || ['close', 'dismiss'].includes(title)
  || cls.includes('button-close') || cls.includes('modal-close')
  || cls.includes('dialog-close')
  || ['x', '×', 'close', 'dismiss'].includes(text)
  || safeRewardContinue;
const r = button.getBoundingClientRect();
const top = visible(button) ? document.elementFromPoint(r.left + r.width/2, r.top + r.height/2) : null;
const topmost = !!(top && (top === button || button.contains(top)));
const dialogVisible = visible(dialog);
const z = Number.parseInt(getComputedStyle(dialog).zIndex || '0', 10) || 0;
return {
  ok: visible(button) && dialogVisible && !disabled && type === 'button'
    && closeEvidence && !dangerousAction && topmost,
  reason: !visible(button) ? 'close_button_not_visible'
    : !dialogVisible ? 'dialog_not_visible'
    : disabled ? 'close_button_disabled'
    : type !== 'button' ? 'close_button_not_button_type'
    : !closeEvidence ? 'no_explicit_close_evidence'
    : dangerousAction ? 'control_looks_like_action_not_close'
    : !topmost ? 'close_button_not_topmost'
    : safeRewardContinue ? 'validated_reward_continue'
    : 'validated',
  text, aria, title, testid, variant, className:cls.slice(0,180),
  topmost, zIndex:z, dialogVisible, safeRewardContinue
};"""


def _log(log: Callable[[str], None] | None, message: str) -> None:
    if callable(log):
        try:
            log(message)
        except Exception:
            pass


def _is_visible(element) -> bool:
    try:
        return bool(element.is_displayed())
    except Exception:
        return False


def _inspect(driver) -> dict:
    state = driver.execute_script(_LAYER_INSPECT_JS)
    if not isinstance(state, dict):
        raise RuntimeError("popup layer inspection returned no state")
    return state


def _is_reward_success_residue(state: dict) -> bool:
    """Return true only for one residual, exact portal reward-success dialog.

    This is intentionally stricter than generic popup detection. It is used
    only after this guard has already dismissed at least one safe layer.
    """
    if int(state.get("dialogCount", 0) or 0) != 1:
        return False
    if int(state.get("fullscreenOverlayCount", 0) or 0) != 0:
        return False
    summaries = state.get("dialogSummaries") or []
    if len(summaries) != 1:
        return False
    text = re.sub(r"\s+", " ", str(summaries[0].get("text") or "")).strip().lower()
    return bool(
        re.match(r"^your rewards!\s*x\s*\d+\b", text)
        and re.search(r"\bcontinue\b", text)
    )


def _wait_for_reward_success_residue(
    driver, *, timeout: float, poll_interval: float
) -> bool:
    """Wait briefly for the already-dismissed success-dialog wrapper to vanish.

    No controls are searched for or clicked here. Any different dialog or any
    fullscreen overlay immediately fails this special-case check.
    """
    deadline = time.monotonic() + max(0.2, min(2.0, float(timeout)))
    while time.monotonic() < deadline:
        try:
            state = _inspect(driver)
        except Exception:
            return False
        dialog_count = int(state.get("dialogCount", 0) or 0)
        overlay_count = int(state.get("fullscreenOverlayCount", 0) or 0)
        if dialog_count == 0 and overlay_count == 0:
            return True
        if not _is_reward_success_residue(state):
            return False
        time.sleep(poll_interval)
    try:
        final_state = _inspect(driver)
        return (
            int(final_state.get("dialogCount", 0) or 0) == 0
            and int(final_state.get("fullscreenOverlayCount", 0) or 0) == 0
        )
    except Exception:
        return False


def dismiss_all_safe_popups(
    driver,
    *,
    log: Callable[[str], None] | None = None,
    max_popups: int = 8,
    timeout: float = 2.5,
    poll_interval: float = 0.15,
) -> tuple[bool, str]:
    """Dismiss stacked dialogs one at a time; fail closed on unknown blockers.

    The candidate must be inside a visible dialog, have explicit close
    semantics (or be the narrowly validated reward-success Continue control),
    be enabled, be a real button, and be the topmost hit-test target. Every
    dismissal is a native Selenium click followed by a visibility check.
    A narrowly recognised residual success-dialog wrapper is allowed only a
    bounded, click-free settling wait after a safe dialog has been dismissed.
    """
    dismissed = 0
    max_popups = max(1, min(12, int(max_popups)))
    timeout = max(0.2, min(8.0, float(timeout)))
    poll_interval = max(0.05, min(0.5, float(poll_interval)))

    try:
        for _ in range(max_popups + 1):
            state = _inspect(driver)
            dialog_count = int(state.get("dialogCount", 0) or 0)
            overlay_count = int(state.get("fullscreenOverlayCount", 0) or 0)
            if dialog_count == 0 and overlay_count == 0:
                reason = f"clear; dismissed={dismissed}"
                if dismissed:
                    _log(log, f"✅ Reward popup guard: dismissed and verified {dismissed} popup(s)")
                return True, reason

            try:
                dialogs = driver.find_elements("css selector", DIALOG_SELECTOR)
            except Exception as exc:
                return False, f"dialog_lookup_failed:{type(exc).__name__}; dismissed={dismissed}"

            candidates = []
            seen_button_ids = set()
            for dialog in dialogs:
                if not _is_visible(dialog):
                    continue
                try:
                    buttons = dialog.find_elements("css selector", BUTTON_SELECTOR)
                except Exception:
                    continue
                for button in buttons:
                    if not _is_visible(button):
                        continue
                    button_id = getattr(button, "id", None) or id(button)
                    if button_id in seen_button_ids:
                        continue
                    seen_button_ids.add(button_id)
                    try:
                        validation = driver.execute_script(
                            _CLOSE_CONTROL_CHECK_JS, button, dialog
                        )
                    except Exception:
                        continue
                    if isinstance(validation, dict) and validation.get("ok"):
                        candidates.append((
                            int(validation.get("zIndex", 0) or 0),
                            0 if validation.get("safeRewardContinue") else 1,
                            button_id,
                            dialog,
                            button,
                            validation,
                        ))

            if not candidates:
                # The portal can leave a short-lived, non-interactive wrapper
                # for the just-dismissed "Your rewards! xN Continue" layer.
                # Wait for that exact residue to disappear; never click it again.
                if dismissed > 0 and _is_reward_success_residue(state):
                    if _wait_for_reward_success_residue(
                        driver, timeout=min(1.5, timeout), poll_interval=poll_interval
                    ):
                        _log(log, "✅ Reward success dialog residue cleared after transition")
                        return True, f"clear_after_reward_transition; dismissed={dismissed}"
                    return False, (
                        "reward_success_dialog_did_not_settle; "
                        f"dismissed={dismissed}; no additional click attempted"
                    )

                if overlay_count and not dialog_count:
                    return False, f"unknown_fullscreen_overlay_blocks_page; dismissed={dismissed}"
                summary = state.get("dialogSummaries") or []
                description = summary[0].get("text", "")[:100] if summary else ""
                return False, (
                    f"blocking_dialog_has_no_unambiguous_safe_close; dialogs={dialog_count}; "
                    f"overlays={overlay_count}; top_dialog={description!r}; dismissed={dismissed}"
                )

            # Only one close control should be topmost at its click point. Prefer
            # the highest dialog z-index; ambiguous peers are not guessed at.
            highest_z = max(item[0] for item in candidates)
            highest_priority = max(item[1] for item in candidates if item[0] == highest_z)
            top_candidates = [
                item for item in candidates
                if item[0] == highest_z and item[1] == highest_priority
            ]
            if len(top_candidates) != 1:
                return False, f"ambiguous_topmost_close_controls:{len(top_candidates)}; dismissed={dismissed}"

            _, _, _, target_dialog, target_button, validation = top_candidates[0]
            dialog_description = ""
            try:
                dialog_description = driver.execute_script(
                    "return (arguments[0].innerText || arguments[0].textContent || '').replace(/\\s+/g,' ').trim().slice(0,120);",
                    target_dialog,
                ) or ""
            except Exception:
                pass

            try:
                target_button.click()  # Native click only. No JS or overlay fallback.
            except Exception as exc:
                return False, f"native_close_click_failed:{type(exc).__name__}; dismissed={dismissed}"

            closed = False
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if not _is_visible(target_dialog):
                    closed = True
                    break
                try:
                    current = _inspect(driver)
                    current_dialogs = int(current.get("dialogCount", 0) or 0)
                    if current_dialogs < dialog_count:
                        closed = True
                        break
                except Exception:
                    pass
                time.sleep(poll_interval)

            if not closed:
                return False, f"dialog_remained_after_native_close_click; dismissed={dismissed}"

            dismissed += 1
            control_kind = "reward Continue" if validation.get("safeRewardContinue") else "close"
            _log(log, f"🧹 Reward popup guard: {control_kind} layer {dismissed} ({dialog_description[:80]!r})")

        final_state = _inspect(driver)
        if int(final_state.get("dialogCount", 0) or 0) == 0 and int(final_state.get("fullscreenOverlayCount", 0) or 0) == 0:
            _log(log, f"✅ Reward popup guard: dismissed and verified {dismissed} popup(s)")
            return True, f"clear; dismissed={dismissed}"
        return False, f"popup_limit_reached; dialogs={final_state.get('dialogCount')}; overlays={final_state.get('fullscreenOverlayCount')}; dismissed={dismissed}"
    except Exception as exc:
        return False, f"popup_guard_error:{type(exc).__name__}; dismissed={dismissed}"


__all__ = ["dismiss_all_safe_popups"]
