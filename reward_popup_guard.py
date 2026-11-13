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

When a dialog cannot be safely dismissed, the guard records a bounded,
read-only inventory of the popup's surrounding DOM, ancestors, and nearby
interactive-looking elements. This is diagnostic-only: it does not change
which controls are eligible for dismissal.
"""
from __future__ import annotations

import json
import re
import time
import traceback
from pathlib import Path
from typing import Callable

from selenium.webdriver.common.action_chains import ActionChains

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

# Read-only diagnostics. Unlike the dismissal search, this intentionally looks
# outside the dialog boundary as well. Some sites render a close icon in a
# sibling/portal wrapper, or as an SVG/custom element rather than a button.
# Returned elements are metadata only; this script never interacts with them.
_POPUP_CONTEXT_DIAGNOSTICS_JS = r"""/* REWARD_POPUP_CONTEXT_DIAGNOSTICS_V2 */
const norm = value => String(value || '').replace(/\s+/g, ' ').trim();
const visible = el => {
  if (!el) return false;
  const r = el.getBoundingClientRect(), s = getComputedStyle(el);
  return !!(el.getClientRects().length && r.width > 0 && r.height > 0
    && s.display !== 'none' && s.visibility !== 'hidden'
    && Number(s.opacity || '1') > 0 && s.pointerEvents !== 'none');
};
const rectData = el => {
  const r = el.getBoundingClientRect();
  return {
    x: Math.round(r.x), y: Math.round(r.y),
    width: Math.round(r.width), height: Math.round(r.height)
  };
};
const brief = el => {
  const s = getComputedStyle(el);
  return {
    tag: String(el.tagName || '').toLowerCase(),
    role: norm(el.getAttribute('role')).slice(0, 32),
    aria: norm(el.getAttribute('aria-label')).slice(0, 72),
    title: norm(el.getAttribute('title')).slice(0, 72),
    testid: norm(el.getAttribute('data-testid')).slice(0, 72),
    dataState: norm(el.getAttribute('data-state')).slice(0, 40),
    dataSlot: norm(el.getAttribute('data-slot')).slice(0, 40),
    className: String(el.getAttribute('class') || '')
      .replace(/\s+/g, ' ').trim().slice(0, 120),
    text: norm(el.innerText || el.textContent).slice(0, 80),
    position: s.position,
    zIndex: s.zIndex,
    cursor: s.cursor,
    pointerEvents: s.pointerEvents,
    onclickPresent: el.hasAttribute('onclick'),
    disabled: !!el.disabled || el.getAttribute('aria-disabled') === 'true',
    rect: rectData(el)
  };
};
const rootSelector = [
  '[role="dialog"]', '[aria-modal="true"]', 'dialog',
  '[class*="modal"]', '[class*="Modal"]',
  '[class*="popup"]', '[class*="Popup"]'
].join(',');
const roots = Array.from(document.querySelectorAll(rootSelector)).filter(visible);
roots.sort((a, b) => {
  const za = Number.parseInt(getComputedStyle(a).zIndex || '0', 10) || 0;
  const zb = Number.parseInt(getComputedStyle(b).zIndex || '0', 10) || 0;
  return zb - za;
});
const candidateSelector = [
  'button', '[role="button"]', 'a[href]',
  'input[type="button"]', 'input[type="submit"]',
  '[aria-label]', '[title]', '[data-testid]', '[data-slot]',
  '[tabindex]:not([tabindex="-1"])', 'svg',
  '[class*="close" i]', '[class*="dismiss" i]', '[onclick]'
].join(',');
const allCandidates = Array.from(document.querySelectorAll(candidateSelector));
const inspectRoot = root => {
  const rr = root.getBoundingClientRect();
  const left = rr.left - 100, right = rr.right + 100;
  const top = rr.top - 100, bottom = rr.bottom + 100;
  const nearby = allCandidates.filter(el => {
    if (!visible(el)) return false;
    const r = el.getBoundingClientRect();
    const withinExpandedRect = r.right >= left && r.left <= right
      && r.bottom >= top && r.top <= bottom;
    return root.contains(el) || withinExpandedRect;
  });
  nearby.sort((a, b) => {
    const ac = root.contains(a) ? 0 : 1, bc = root.contains(b) ? 0 : 1;
    if (ac !== bc) return ac - bc;
    const ar = a.getBoundingClientRect(), br = b.getBoundingClientRect();
    const ad = Math.hypot((ar.x + ar.width/2) - (rr.x + rr.width/2),
                          (ar.y + ar.height/2) - (rr.y + rr.height/2));
    const bd = Math.hypot((br.x + br.width/2) - (rr.x + rr.width/2),
                          (br.y + br.height/2) - (rr.y + rr.height/2));
    return ad - bd;
  });
  const ancestors = [];
  let node = root.parentElement;
  for (let i = 0; node && i < 4; i++, node = node.parentElement) {
    ancestors.push(brief(node));
  }
  return {
    root: brief(root),
    ancestors,
    nearbyControls: nearby.slice(0, 16).map(brief)
  };
};
const dialogRoots = roots.slice(0, 2).map(inspectRoot);
const overlaySelector = '[class*="overlay"], [class*="Overlay"], [class*="backdrop"], [class*="Backdrop"]';
const overlays = Array.from(document.querySelectorAll(overlaySelector))
  .filter(visible).slice(0, 3).map(brief);
return {
  url: location.href,
  viewport: { width: window.innerWidth, height: window.innerHeight },
  dialogRootCount: roots.length,
  dialogRoots,
  visibleOverlays: overlays
};"""



# Some portal versions render the dialog's explicit X control in a fixed
# portal wrapper beside (rather than inside) the dialog content. Only inspect
# the wrapper chain containing this dialog, and only its top-right close area.
_ASSOCIATED_WRAPPER_CLOSE_CANDIDATES_JS = r"""/* REWARD_POPUP_WRAPPER_CLOSE_CANDIDATES_V1 */
const dialog = arguments[0];
if (!dialog || !dialog.isConnected) return [];
const norm = value => String(value || '').replace(/\s+/g, ' ').trim();
const visible = el => {
  if (!el) return false;
  const r = el.getBoundingClientRect(), s = getComputedStyle(el);
  return !!(el.isConnected && el.getClientRects().length && r.width > 0 && r.height > 0
    && s.display !== 'none' && s.visibility !== 'hidden'
    && Number(s.opacity || '1') > 0 && s.pointerEvents !== 'none');
};
const isExplicitClose = button => {
  const text = norm(button.innerText || button.textContent).toLowerCase();
  const aria = norm(button.getAttribute('aria-label')).toLowerCase();
  const title = norm(button.getAttribute('title')).toLowerCase();
  const testid = norm(button.getAttribute('data-testid')).toLowerCase();
  const variant = norm(button.getAttribute('data-variant')).toLowerCase();
  const cls = String(button.className || '').toLowerCase();
  const action = [text, aria, title, testid, variant].join(' ');
  const explicit = testid === 'close' || variant === 'close'
    || ['close', 'close dialog', 'dismiss'].includes(aria)
    || ['close', 'dismiss'].includes(title)
    || cls.includes('button-close') || cls.includes('modal-close')
    || cls.includes('dialog-close')
    || ['x', '×', 'close', 'dismiss'].includes(text);
  return explicit && !/\b(buy|purchase|claim|confirm|accept|redeem|subscribe|pay|submit|yes)\b/i.test(action);
};
const dr = dialog.getBoundingClientRect();
const candidates = [];
let wrapper = dialog.parentElement;
for (let depth = 0; wrapper && wrapper !== document.body && depth < 5;
     depth++, wrapper = wrapper.parentElement) {
  const ws = getComputedStyle(wrapper), wr = wrapper.getBoundingClientRect();
  const viewportWrapper = ws.position === 'fixed'
    && wr.width >= window.innerWidth * 0.8
    && wr.height >= window.innerHeight * 0.8
    && wrapper.contains(dialog);
  if (!viewportWrapper) continue;

  for (const button of Array.from(wrapper.querySelectorAll('button,[role="button"]'))) {
    if (dialog.contains(button) || !visible(button) || button.disabled
        || button.getAttribute('aria-disabled') === 'true'
        || norm(button.getAttribute('type') || 'button').toLowerCase() !== 'button'
        || !isExplicitClose(button)) continue;
    const br = button.getBoundingClientRect();
    const cx = br.left + br.width / 2, cy = br.top + br.height / 2;
    const nearTopRight = cx >= dr.right - Math.max(56, Math.min(128, dr.width * 0.2))
      && cx <= dr.right + 16 && cy >= dr.top - 8 && cy <= dr.top + 104;
    if (!nearTopRight) continue;
    const hit = document.elementFromPoint(cx, cy);
    if (!(hit && (hit === button || button.contains(hit)))) continue;
    candidates.push({button, wrapper});
  }
  if (candidates.length) break;
}
return candidates;
""";

_WRAPPER_CLOSE_CONTROL_CHECK_JS = r"""/* REWARD_POPUP_WRAPPER_CLOSE_CONTROL_CHECK_V1 */
const button = arguments[0], dialog = arguments[1], wrapper = arguments[2];
if (!button || !dialog || !wrapper || !wrapper.contains(dialog)
    || !wrapper.contains(button) || dialog.contains(button)) {
  return {ok:false, reason:'wrapper_relationship_invalid'};
}
const norm = value => String(value || '').replace(/\s+/g, ' ').trim().toLowerCase();
const visible = el => {
  if (!el) return false;
  const r = el.getBoundingClientRect(), s = getComputedStyle(el);
  return !!(el.isConnected && el.getClientRects().length && r.width > 0 && r.height > 0
    && s.display !== 'none' && s.visibility !== 'hidden'
    && Number(s.opacity || '1') > 0 && s.pointerEvents !== 'none');
};
const dr = dialog.getBoundingClientRect(), br = button.getBoundingClientRect();
const ws = getComputedStyle(wrapper), wr = wrapper.getBoundingClientRect();
const text = norm(button.innerText || button.textContent);
const aria = norm(button.getAttribute('aria-label'));
const title = norm(button.getAttribute('title'));
const testid = norm(button.getAttribute('data-testid'));
const variant = norm(button.getAttribute('data-variant'));
const cls = String(button.className || '').toLowerCase();
const type = norm(button.getAttribute('type') || 'button');
const disabled = !!button.disabled || button.getAttribute('aria-disabled') === 'true';
const actionText = [text, aria, title, testid, variant].join(' ');
const closeEvidence = testid === 'close' || variant === 'close'
  || ['close', 'close dialog', 'dismiss'].includes(aria)
  || ['close', 'dismiss'].includes(title)
  || cls.includes('button-close') || cls.includes('modal-close')
  || cls.includes('dialog-close')
  || ['x', '×', 'close', 'dismiss'].includes(text);
const top = visible(button) ? document.elementFromPoint(
  br.left + br.width/2, br.top + br.height/2
) : null;
const topmost = !!(top && (top === button || button.contains(top)));
const nearTopRight = (br.left + br.width/2) >= dr.right - Math.max(56, Math.min(128, dr.width * 0.2))
  && (br.left + br.width/2) <= dr.right + 16
  && (br.top + br.height/2) >= dr.top - 8
  && (br.top + br.height/2) <= dr.top + 104;
const viewportWrapper = ws.position === 'fixed'
  && wr.width >= window.innerWidth * 0.8
  && wr.height >= window.innerHeight * 0.8;
return {
  ok: visible(button) && visible(dialog) && viewportWrapper && !disabled
    && type === 'button' && closeEvidence
    && !/\b(buy|purchase|claim|confirm|accept|redeem|subscribe|pay|submit|yes)\b/i.test(actionText)
    && nearTopRight && topmost,
  reason: !visible(button) ? 'wrapper_close_not_visible'
    : !visible(dialog) ? 'dialog_not_visible'
    : !viewportWrapper ? 'wrapper_not_fullscreen_fixed'
    : disabled ? 'wrapper_close_disabled'
    : type !== 'button' ? 'wrapper_close_not_button_type'
    : !closeEvidence ? 'wrapper_control_not_explicit_close'
    : /\b(buy|purchase|claim|confirm|accept|redeem|subscribe|pay|submit|yes)\b/i.test(actionText)
      ? 'wrapper_control_looks_dangerous'
    : !nearTopRight ? 'wrapper_close_not_near_dialog_top_right'
    : !topmost ? 'wrapper_close_not_topmost'
    : 'validated',
  zIndex: Number.parseInt(ws.zIndex || getComputedStyle(dialog).zIndex || '0', 10) || 0,
  topmost, nearTopRight, viewportWrapper
};""";

# A backdrop is eligible only when its own DOM marker identifies it as a
# dialog/modal backdrop, it belongs to the same portal tree, and hit-testing
# proves the selected blank point lands on that backdrop (not the page).
_SAFE_BACKDROP_CLICK_POINT_JS = r"""/* REWARD_POPUP_SAFE_BACKDROP_POINT_V1 */
const allDialogs = Array.from(document.querySelectorAll(
  '[role="dialog"],[aria-modal="true"],dialog,[class*="modal"],[class*="Modal"]'
)).filter(el => {
  const r = el.getBoundingClientRect(), s = getComputedStyle(el);
  return !!(el.isConnected && el.getClientRects().length && r.width > 0 && r.height > 0
    && s.display !== 'none' && s.visibility !== 'hidden' && Number(s.opacity || '1') > 0);
});
if (!allDialogs.length) return {ok:false, reason:'dialog_missing'};
const dialog = allDialogs.reduce((best, item) => {
  const z = el => Number.parseInt(getComputedStyle(el).zIndex || '0', 10) || 0;
  return z(item) >= z(best) ? item : best;
}, allDialogs[0]);
const visible = el => {
  if (!el) return false;
  const r = el.getBoundingClientRect(), s = getComputedStyle(el);
  return !!(el.isConnected && el.getClientRects().length && r.width > 0 && r.height > 0
    && s.display !== 'none' && s.visibility !== 'hidden'
    && Number(s.opacity || '1') > 0);
};
const selectors = [
  '[data-slot="dialog-overlay"]', '[data-slot="modal-overlay"]',
  '[data-slot="dialog-backdrop"]', '[data-slot="modal-backdrop"]',
  '[data-testid="dialog-overlay"]', '[data-testid="modal-backdrop"]',
  '[data-testid="popup-backdrop"]', '[role="dialog-overlay"]'
].join(',');
const isExplicitBackdrop = el => {
  const slot = String(el.getAttribute('data-slot') || '').toLowerCase();
  const testid = String(el.getAttribute('data-testid') || '').toLowerCase();
  const role = String(el.getAttribute('role') || '').toLowerCase();
  const cls = String(el.getAttribute('class') || '');
  return [
    'dialog-overlay','modal-overlay','dialog-backdrop','modal-backdrop'
  ].includes(slot) || [
    'dialog-overlay','modal-backdrop','popup-backdrop'
  ].includes(testid) || role === 'dialog-overlay'
    || /(^|[\s_-])(dialog|modal|popup)[_-](overlay|backdrop)([\s_-]|$)/i.test(cls);
};
const relatedPortal = candidate => {
  if (candidate.contains(dialog)) return true;
  let node = dialog;
  for (let depth = 0; node && depth <= 4; depth++, node = node.parentElement) {
    if (node === document.body || node === document.documentElement) break;
    if (node.contains(candidate)) {
      let candidateDepth = 0, parent = candidate;
      while (parent && parent !== node && candidateDepth <= 4) {
        candidateDepth++;
        parent = parent.parentElement;
      }
      if (parent === node && candidateDepth <= 3) return true;
    }
  }
  return false;
};
const dialogs = Array.from(document.querySelectorAll(
  '[role="dialog"],[aria-modal="true"],dialog,[class*="modal"],[class*="Modal"]'
)).filter(visible);
const w = window.innerWidth, h = window.innerHeight, margin = 20;
const points = [
  [margin, margin], [w-margin, margin],
  [margin, h-margin], [w-margin, h-margin],
  [w/2, margin], [w/2, h-margin],
  [margin, h/2], [w-margin, h/2]
];
const safePointsFor = candidate => {
  const r = candidate.getBoundingClientRect();
  for (const [x,y] of points) {
    if (x < r.left || x > r.right || y < r.top || y > r.bottom) continue;
    if (dialogs.some(d => {
      const q = d.getBoundingClientRect();
      return x >= q.left-4 && x <= q.right+4 && y >= q.top-4 && y <= q.bottom+4;
    })) continue;
    const hit = document.elementFromPoint(x,y);
    if (!(hit && (hit === candidate || candidate.contains(hit)))) continue;
    let node = hit, interactive = false;
    while (node && node !== candidate) {
      if (node.matches && node.matches(
        'button,a[href],input,select,textarea,[role="button"],[role="link"],[contenteditable="true"]'
      )) { interactive = true; break; }
      node = node.parentElement;
    }
    if (!interactive) return {x,y};
  }
  return null;
};
const candidates = [];
for (const candidate of Array.from(document.querySelectorAll(selectors))) {
  if (!visible(candidate) || !isExplicitBackdrop(candidate) || !relatedPortal(candidate)) continue;
  const s = getComputedStyle(candidate), r = candidate.getBoundingClientRect();
  if (s.position !== 'fixed' || s.pointerEvents !== 'auto'
      || r.width < w*0.8 || r.height < h*0.8) continue;
  const state = String(candidate.getAttribute('data-state') || '').toLowerCase();
  if (state && !['open','visible','shown'].includes(state)) continue;
  const point = safePointsFor(candidate);
  if (point) candidates.push({candidate, point, marker:
    candidate.getAttribute('data-slot') || candidate.getAttribute('data-testid')
    || candidate.getAttribute('role') || 'explicit-backdrop'});
}
if (candidates.length !== 1) {
  return {ok:false, reason:candidates.length ? 'ambiguous_safe_backdrops' : 'no_verified_blank_backdrop'};
}
const chosen = candidates[0], r = chosen.candidate.getBoundingClientRect();
return {
  ok:true, backdrop:chosen.candidate,
  offsetX:Math.round(chosen.point.x - r.left),
  offsetY:Math.round(chosen.point.y - r.top),
  x:Math.round(chosen.point.x), y:Math.round(chosen.point.y),
  marker:String(chosen.marker).slice(0,80)
};""";


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



def _native_click_backdrop(driver, backdrop, offset_x: int, offset_y: int) -> None:
    """Dispatch a real pointer click at a prevalidated blank point on a modal backdrop."""
    ActionChains(driver).move_to_element_with_offset(
        backdrop, int(offset_x), int(offset_y)
    ).click().perform()


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


def _format_exception_diagnostic(exc: BaseException) -> str:
    """Return a bounded, content-free traceback summary.

    Only source basenames, function names, line numbers, exception type, and
    Python's validated NameError.name identifier are retained. Raw exception
    messages are deliberately excluded because this repository is public and
    third-party exception text can contain account or session data.
    This helper is diagnostic-only and never affects popup click decisions.
    """
    try:
        extracted = traceback.extract_tb(exc.__traceback__) if exc.__traceback__ else []
        frames = [
            f"{Path(frame.filename).name[:100]}:{frame.name[:100]}:{frame.lineno}"
            for frame in extracted[-8:]
        ]
        frame_text = " <- ".join(frames) if frames else "unavailable"
    except Exception:
        frame_text = "unavailable"

    details = [
        f"traceback={frame_text}",
        f"exception={type(exc).__name__}",
    ]
    undefined_name = getattr(exc, "name", None)
    if isinstance(exc, NameError) and isinstance(undefined_name, str):
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", undefined_name):
            details.append(f"undefined_name={undefined_name}")
    return "; ".join(details)[:1600]


def _popup_context_diagnostics(driver) -> str:
    """Return bounded popup DOM context; never click, type, or mutate elements."""
    try:
        context = driver.execute_script(_POPUP_CONTEXT_DIAGNOSTICS_JS)
    except Exception as exc:
        return "popup_context=" + json.dumps({"diagnostic_error": type(exc).__name__})

    if not isinstance(context, dict):
        return 'popup_context={"diagnostic_error":"invalid_result"}'

    # Sanitize and cap once more in Python. Never include input values, HTML,
    # arbitrary attributes, or page-wide text in the diagnostic payload.
    def safe_element(element):
        if not isinstance(element, dict):
            return {}
        rect = element.get("rect") if isinstance(element.get("rect"), dict) else {}
        return {
            key: str(element.get(key) or "")[:limit]
            for key, limit in (
                ("tag", 16), ("role", 32), ("aria", 72), ("title", 72),
                ("testid", 72), ("dataState", 40), ("dataSlot", 40),
                ("className", 120), ("text", 80), ("position", 20),
                ("zIndex", 20), ("cursor", 20), ("pointerEvents", 20),
            )
        } | {
            "onclickPresent": bool(element.get("onclickPresent")),
            "disabled": bool(element.get("disabled")),
            "rect": {
                key: int(rect.get(key) or 0)
                for key in ("x", "y", "width", "height")
            },
        }

    safe_roots = []
    for item in (context.get("dialogRoots") or [])[:2]:
        if not isinstance(item, dict):
            continue

        ancestors = [safe_element(x) for x in (item.get("ancestors") or [])[:4]]
        controls = [safe_element(x) for x in (item.get("nearbyControls") or [])[:16]]
        safe_roots.append({
            "root": safe_element(item.get("root")),
            "ancestors": ancestors,
            "nearbyControls": controls,
        })

    overlays = [safe_element(x) for x in (context.get("visibleOverlays") or [])[:3]]
    viewport = context.get("viewport") if isinstance(context.get("viewport"), dict) else {}
    sanitized = {
        "url": str(context.get("url") or "")[:180],
        "viewport": {
            "width": int(viewport.get("width") or 0),
            "height": int(viewport.get("height") or 0),
        },
        "dialogRootCount": int(context.get("dialogRootCount") or 0),
        "dialogRoots": safe_roots,
        "visibleOverlays": overlays,
    }
    serialized = json.dumps(sanitized, ensure_ascii=True, separators=(",", ":"))
    if len(serialized) > 7000:
        serialized = serialized[:7000] + "...[truncated]"
    return "popup_context=" + serialized


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

    If no safe close candidate exists, bounded read-only popup DOM context is
    appended to the failure reason. This metadata does not relax any safety
    condition or add any new click target.
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
                    buttons = []
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
                            button_id, dialog, button, validation, None,
                        ))

                # The portal's own X can live in a fixed wrapper as a sibling
                # of the role=dialog node. Only explicit close controls in the
                # dialog's own fullscreen wrapper are considered.
                try:
                    wrapper_pairs = driver.execute_script(
                        _ASSOCIATED_WRAPPER_CLOSE_CANDIDATES_JS, dialog
                    ) or []
                except Exception:
                    wrapper_pairs = []
                for pair in wrapper_pairs:
                    if not isinstance(pair, dict):
                        continue
                    button, wrapper = pair.get("button"), pair.get("wrapper")
                    if button is None or wrapper is None or not _is_visible(button):
                        continue
                    button_id = getattr(button, "id", None) or id(button)
                    if button_id in seen_button_ids:
                        continue
                    seen_button_ids.add(button_id)
                    try:
                        validation = driver.execute_script(
                            _WRAPPER_CLOSE_CONTROL_CHECK_JS, button, dialog, wrapper
                        )
                    except Exception:
                        continue
                    if isinstance(validation, dict) and validation.get("ok"):
                        candidates.append((
                            int(validation.get("zIndex", 0) or 0),
                            1, button_id, dialog, button, validation, wrapper,
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
                        f"dismissed={dismissed}; {_popup_context_diagnostics(driver)}; "
                        "no additional click attempted"
                    )

                # A blank-area click is allowed only on a uniquely identified,
                # pointer-active backdrop in this dialog's own portal tree.
                # Generic screen/page space is never clicked.
                if dialog_count:
                    try:
                        backdrop_check = driver.execute_script(
                            _SAFE_BACKDROP_CLICK_POINT_JS
                        )
                    except Exception as exc:
                        backdrop_check = {
                            "ok": False, "reason": f"backdrop_inspection_failed:{type(exc).__name__}"
                        }
                    if isinstance(backdrop_check, dict) and backdrop_check.get("ok"):
                        try:
                            _native_click_backdrop(
                                driver,
                                backdrop_check["backdrop"],
                                backdrop_check["offsetX"],
                                backdrop_check["offsetY"],
                            )
                        except Exception as exc:
                            return False, (
                                f"safe_backdrop_native_click_failed:{type(exc).__name__}; "
                                f"dismissed={dismissed}"
                            )
                        deadline = time.monotonic() + timeout
                        cleared = False
                        while time.monotonic() < deadline:
                            try:
                                current = _inspect(driver)
                                if int(current.get("dialogCount", 0) or 0) < dialog_count:
                                    cleared = True
                                    break
                                if not _is_visible(dialogs[0]):
                                    cleared = True
                                    break
                            except Exception:
                                pass
                            time.sleep(poll_interval)
                        if cleared:
                            dismissed += 1
                            _log(
                                log,
                                "🧹 Reward popup guard: dismissed layer "
                                f"{dismissed} using verified blank backdrop point "
                                f"({backdrop_check.get('marker','backdrop')})",
                            )
                            continue
                        return False, (
                            "safe_backdrop_click_did_not_dismiss_dialog; "
                            f"marker={backdrop_check.get('marker','backdrop')}; "
                            f"dismissed={dismissed}; {_popup_context_diagnostics(driver)}"
                        )

                diagnostics = _popup_context_diagnostics(driver)
                if overlay_count and not dialog_count:
                    return False, (
                        "unknown_fullscreen_overlay_blocks_page; "
                        f"dismissed={dismissed}; {diagnostics}"
                    )
                summary = state.get("dialogSummaries") or []
                description = summary[0].get("text", "")[:100] if summary else ""
                backdrop_reason = (
                    backdrop_check.get("reason", "not_checked")
                    if isinstance(backdrop_check, dict) else "not_checked"
                )
                return False, (
                    f"blocking_dialog_has_no_unambiguous_safe_close; dialogs={dialog_count}; "
                    f"overlays={overlay_count}; top_dialog={description!r}; "
                    f"backdrop={backdrop_reason}; dismissed={dismissed}; {diagnostics}"
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

            _, _, _, target_dialog, target_button, validation, _target_wrapper = top_candidates[0]
            dialog_description = ""
            try:
                dialog_description = driver.execute_script(
                    "return (arguments[0].innerText || arguments[0].textContent || '').replace(/\\\\s+/g,' ').trim().slice(0,120);",
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
        diagnostic = _format_exception_diagnostic(exc)
        _log(log, f"⚠️ Popup guard exception diagnostic: {diagnostic}")
        return False, (
            f"popup_guard_error:{type(exc).__name__}; dismissed={dismissed}; {diagnostic}"
        )


__all__ = ["dismiss_all_safe_popups"]
