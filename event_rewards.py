"""UI-driven redemption for temporary point-based event programmes.

Halloween claims deliberately use stricter interaction rules than standard
rewards: a promotional dialog must be dismissed through its explicit close
control, the exact tier button is reacquired from its card, the button must be
the topmost hit-test target, and only a portal-confirmed Delivered state is
counted as a claim.

The public function signatures remain compatible with master_claimer.py v3.3.2.
The legacy physical_click and close_popup callbacks are accepted for interface
compatibility but are intentionally NOT used for Halloween clicks/dismissal:
master_claimer.physical_click has a JavaScript fallback, and close_popup has a
generic modal-button fallback. Both are unsafe when a store-purchase modal is
covering an event reward.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

DEFAULT_CONFIG_PATH = Path(__file__).with_name("event_rewards_config.json")
EVENT_CARD_SELECTOR = "div.relative.rounded-lp-card"
# The captured Vertigo Games promotion exposes all three close markers below.
# The purchase control in the same dialog is data-testid="buy-button" and
# data-variant="component-buy"; it must never be considered a close control.
EVENT_POPUP_SELECTOR = '[role="dialog"], [aria-modal="true"], [class*="modal"]'
EVENT_POPUP_CLOSE_SELECTOR = (
    'button[data-testid="close"], '
    'button[data-variant="close"], '
    'button[aria-label="Close dialog"], '
    'button[aria-label="Close"], '
    'button[title="Close"], '
    'button[class*="button-close"]'
)
EVENT_POPUP_WAIT_SECONDS = 3.0
EVENT_CLAIM_VERIFY_SECONDS = 8.0


# Captures card-level button state. The button index is retained for diagnostics
# and history compatibility, but claim execution no longer relies on that index.
_SNAPSHOT_SCRIPT = r"""/* EVENT_REWARDS_SNAPSHOT_V2 */
const allButtons = Array.from(document.querySelectorAll('button'));
const cards = [];
const norm = value => (value || '').replace(/\s+/g, ' ').trim();
for (const card of Array.from(document.querySelectorAll('div.relative.rounded-lp-card'))) {
  const raw = (card.innerText || card.textContent || '').trim();
  const firstLine = (raw.split(/\n+/)[0] || '').trim();
  const thresholdMatch = firstLine.match(/^(\d[\d,]*)(?=\s*(?:x|\s|$))/i);
  if (!thresholdMatch) continue;
  const button = card.querySelector('button');
  const buttonText = button
    ? norm(button.innerText || button.textContent || button.getAttribute('aria-label')).toLowerCase()
    : '';
  const disabled = !button || !!button.disabled || button.getAttribute('aria-disabled') === 'true';
  const rect = button ? button.getBoundingClientRect() : null;
  const style = button ? getComputedStyle(button) : null;
  const visible = !!(button && button.getClientRects().length && rect && rect.width > 0
    && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden');
  const claimButtonEnabled = visible && !disabled && ['claim', 'claim all'].includes(buttonText);
  let rewardItems = [];
  if (claimButtonEnabled) {
    const rows = Array.from(card.querySelectorAll('div.flex.flex-row.items-center.gap-3'));
    rewardItems = rows.map(row => {
      const name = row.querySelector('span[class*="line-clamp-1"]');
      const quantity = row.querySelector('[class*="text-store-card-sku-img-quantity"]');
      return {name: norm(name ? name.textContent : ''), quantity: norm(quantity ? quantity.textContent : '')};
    }).filter(item => item.name);
  }
  const cardLines = [thresholdMatch[1]];
  for (const item of rewardItems) {
    if (item.quantity) cardLines.push(item.quantity);
    cardLines.push(item.name);
  }
  if (buttonText) cardLines.push(buttonText);
  cards.push({
    thresholdText: thresholdMatch[1], rewardItems, claimButtonEnabled,
    buttonIndex: button ? allButtons.indexOf(button) : -1,
    buttonText, disabled, visible, cardText: cardLines.join('\n')
  });
}
return {
  title: document.title || '', url: location.href,
  bodyText: (document.body && document.body.innerText) || '', cards
};"""


_POPUP_INSPECT_SCRIPT = r"""/* EVENT_POPUP_INSPECT_V2 */
const isVisible = el => {
  if (!el) return false;
  const r = el.getBoundingClientRect(), s = getComputedStyle(el);
  const opacity = Number(s.opacity || '1');
  return !!(el.getClientRects().length && r.width > 0 && r.height > 0
    && s.display !== 'none' && s.visibility !== 'hidden' && opacity > 0);
};
const normalise = value => (value || '').replace(/\s+/g, ' ').trim().toLowerCase();
const dialogs = Array.from(document.querySelectorAll(
  '[role="dialog"], [aria-modal="true"], [class*="modal"]'
)).filter(isVisible);
const describe = el => ({
  text: normalise(el.innerText || el.textContent || '').slice(0, 700),
  ariaModal: el.getAttribute('aria-modal'), role: el.getAttribute('role'),
  className: String(el.className || '').slice(0, 300)
});
const promotional = dialogs.find(el => {
  const t = normalise(el.innerText || el.textContent || '');
  return t.includes('exclusive discount activated') || t.includes('purchase or lose it');
});
// The captured page has a fixed, viewport-sized z-900 overlay outside the
// dialog node. Count only viewport-covering overlays, not small Store-card
// image overlays (which are also present elsewhere on this page).
const fullScreenBlockers = Array.from(document.querySelectorAll('[class*="overlay"], [class*="Overlay"]'))
  .filter(el => {
    if (!isVisible(el)) return false;
    const r = el.getBoundingClientRect(), st = getComputedStyle(el);
    if (st.pointerEvents === 'none') return false;
    return r.width >= window.innerWidth * 0.8
      && r.height >= window.innerHeight * 0.8
      && (st.position === 'fixed' || (r.left <= 2 && r.top <= 2));
  });
return {
  visibleDialogCount: dialogs.length,
  dialogDescriptions: dialogs.map(describe),
  promotionalDialogVisible: !!promotional,
  promotionalDialogText: promotional ? describe(promotional).text : '',
  fullScreenBlockingOverlayCount: fullScreenBlockers.length,
  fullScreenBlockingOverlayClasses: fullScreenBlockers.map(el => String(el.className || '').slice(0, 300)),
  url: location.href
};"""


_POPUP_CLOSE_BUTTON_CHECK_SCRIPT = r"""/* EVENT_POPUP_CLOSE_BUTTON_CHECK_V1 */
const button = arguments[0];
if (!button) return {ok:false, reason:'button_missing'};
const style = getComputedStyle(button), rect = button.getBoundingClientRect();
const visible = !!(button.getClientRects().length && rect.width > 0 && rect.height > 0
  && style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || '1') > 0);
const testid = (button.getAttribute('data-testid') || '').toLowerCase();
const variant = (button.getAttribute('data-variant') || '').toLowerCase();
const aria = (button.getAttribute('aria-label') || '').toLowerCase();
const title = (button.getAttribute('title') || '').toLowerCase();
const cls = String(button.className || '').toLowerCase();
const type = (button.getAttribute('type') || 'submit').toLowerCase();
const isPurchase = testid === 'buy-button' || variant === 'component-buy'
  || button.matches('[data-testid="buy-button"], [data-variant="component-buy"]');
const hasCloseEvidence = testid === 'close' || variant === 'close'
  || aria === 'close dialog' || aria === 'close' || title === 'close'
  || cls.includes('button-close');
const disabled = !!button.disabled || button.getAttribute('aria-disabled') === 'true';
const x = rect.left + rect.width / 2, y = rect.top + rect.height / 2;
const top = visible ? document.elementFromPoint(x, y) : null;
const topmost = !!(top && (top === button || button.contains(top)));
return {
  ok: visible && !disabled && !isPurchase && hasCloseEvidence && type === 'button' && topmost,
  visible, disabled, isPurchase, hasCloseEvidence, type, topmost,
  testid, variant, aria, title, className:cls.slice(0,300),
  topElementTag: top ? top.tagName : null,
  topElementClass: top ? String(top.className || '').slice(0,240) : ''
};"""


_CLAIM_BUTTON_CHECK_SCRIPT = r"""/* EVENT_CLAIM_BUTTON_CHECK_V2 */
const button = arguments[0];
if (!button) return {ok:false, reason:'button_missing'};
const norm = value => (value || '').replace(/\s+/g, ' ').trim().toLowerCase();
const style = getComputedStyle(button), rect = button.getBoundingClientRect();
const visible = !!(button.getClientRects().length && rect.width > 0 && rect.height > 0
  && style.display !== 'none' && style.visibility !== 'hidden');
const text = norm(button.innerText || button.textContent || button.getAttribute('aria-label'));
const disabled = !!button.disabled || button.getAttribute('aria-disabled') === 'true';
const card = button.closest('div.relative.rounded-lp-card');
const cardText = card ? (card.innerText || card.textContent || '').trim() : '';
const firstLine = (cardText.split(/\n+/)[0] || '').trim();
const match = firstLine.match(/^(\d[\d,]*)(?=\s*(?:x|\s|$))/i);
const threshold = match ? Number(match[1].replace(/,/g,'')) : null;
const x = rect.left + rect.width / 2, y = rect.top + rect.height / 2;
const top = visible ? document.elementFromPoint(x, y) : null;
const topmost = !!(top && (top === button || button.contains(top)));
const dialogs = Array.from(document.querySelectorAll('[role="dialog"], [aria-modal="true"]'));
const blockingDialogs = dialogs.filter(el => {
  const r = el.getBoundingClientRect(), s = getComputedStyle(el);
  const shown = el.getClientRects().length && r.width > 0 && r.height > 0
    && s.display !== 'none' && s.visibility !== 'hidden' && s.pointerEvents !== 'none';
  return shown && x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;
});
return {
  ok: visible && !disabled && ['claim','claim all'].includes(text)
    && threshold !== null && topmost && blockingDialogs.length === 0,
  visible, disabled, buttonText:text, threshold, topmost,
  topElementTag: top ? top.tagName : null,
  topElementClass: top ? String(top.className || '').slice(0,240) : '',
  blockingDialogCount: blockingDialogs.length,
  cardText
};"""


class EventRewardsError(RuntimeError):
    """Configuration or page-state failure in an event reward programme."""


def _as_epoch(value: Any) -> int | None:
    if isinstance(value, (int, float)):
        return int(value)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    except (TypeError, ValueError, OverflowError):
        return None


def load_event_config(config_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            config = json.load(handle)
    except FileNotFoundError:
        return {"enabled": False, "programs": []}
    except (OSError, json.JSONDecodeError) as exc:
        raise EventRewardsError(f"Could not read event config: {type(exc).__name__}: {exc}") from exc
    if not isinstance(config, dict) or not isinstance(config.get("programs", []), list):
        raise EventRewardsError("Event config must be an object containing a programs array")
    return config


def get_active_event_programs(config: dict[str, Any] | None = None,
                              now_epoch: int | None = None) -> list[dict[str, Any]]:
    config = config if config is not None else load_event_config()
    if not config.get("enabled", False):
        return []
    now_epoch = int(time.time()) if now_epoch is None else int(now_epoch)
    active: list[dict[str, Any]] = []
    for raw in config.get("programs", []):
        if not isinstance(raw, dict) or not raw.get("enabled", False):
            continue
        program_id = str(raw.get("program_id") or "").strip()
        slug = str(raw.get("slug") or "").strip().strip("/")
        name = str(raw.get("name") or slug or program_id).strip()
        start_at, end_at = _as_epoch(raw.get("start_at")), _as_epoch(raw.get("end_at"))
        if not program_id or not slug or end_at is None:
            raise EventRewardsError(f"Event programme config is incomplete: {name or 'unnamed event'}")
        if (start_at is not None and now_epoch < start_at) or now_epoch >= end_at:
            continue
        try:
            max_claims = max(1, min(50, int(raw.get("max_claims_per_run", 20))))
        except (TypeError, ValueError):
            max_claims = 20
        active.append({**raw, "program_id": program_id, "slug": slug, "name": name,
                       "end_at_epoch": end_at, "max_claims_per_run": max_claims})
    return active


def _parse_balance(body_text: str) -> tuple[int | None, int | None]:
    match = re.search(r"(?<!\d)(\d[\d,]*)\s*/\s*(\d[\d,]*)(?!\d)", body_text or "")
    if not match:
        return None, None
    try:
        return int(match.group(1).replace(",", "")), int(match.group(2).replace(",", ""))
    except ValueError:
        return None, None


def _parse_threshold(card_text: str) -> int | None:
    first = (card_text or "").strip().splitlines()[0].strip() if card_text else ""
    if not re.fullmatch(r"\d[\d,]*", first):
        return None
    try:
        return int(first.replace(",", ""))
    except ValueError:
        return None


def _parse_reward_names(card_text: str) -> list[str]:
    lines = [re.sub(r"\s+", " ", line).strip() for line in (card_text or "").splitlines()]
    names: list[str] = []
    quantity: str | None = None
    ignored = {"claim", "claim all", "delivered"}
    for line in lines[1:]:
        if not line or line.casefold() in ignored:
            continue
        if re.fullmatch(r"x\s*[\d,.]+[KMB]?", line, flags=re.IGNORECASE):
            quantity = re.sub(r"\s+", "", line[1:])
            continue
        if quantity:
            names.append(f"{line} ×{quantity}")
            quantity = None
        elif not names or names[-1].split(" ×", 1)[0].casefold() != line.casefold():
            names.append(line)
    return names


def _normalise_reward_items(card: dict[str, Any], card_text: str) -> list[str]:
    raw_items = card.get("rewardItems")
    if isinstance(raw_items, list):
        parsed = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            name = re.sub(r"\s+", " ", str(item.get("name") or "")).strip()
            quantity = re.sub(r"\s+", "", str(item.get("quantity") or "").strip())
            if name:
                if quantity[:1].casefold() == "x":
                    quantity = quantity[1:]
                parsed.append(f"{name} ×{quantity}" if quantity else name)
        return parsed
    return _parse_reward_names(card_text)


def _tier_status(card: dict[str, Any], balance: int | None) -> str:
    button_text = str(card.get("buttonText") or "").strip().casefold()
    card_text = str(card.get("cardText") or "")
    if button_text == "delivered" or re.search(r"(?im)^\s*delivered\s*$", card_text):
        return "delivered"
    if not bool(card.get("visible", True)):
        return "locked"
    threshold_text = str(card.get("thresholdText") or "").strip()
    threshold = _parse_threshold(threshold_text) if threshold_text else _parse_threshold(card_text)
    if "claimButtonEnabled" in card:
        enabled = bool(card.get("claimButtonEnabled"))
    else:
        enabled = button_text in {"claim", "claim all"} and not bool(card.get("disabled", True))
    if not enabled:
        return "locked"
    if threshold is not None and balance is not None and balance >= threshold:
        return "available"
    return "inconsistent_balance"


def _normalise_snapshot(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise EventRewardsError("Event page returned no DOM snapshot")
    body = str(raw.get("bodyText") or "")
    balance, next_displayed = _parse_balance(body)
    tiers = []
    seen = set()
    for card in raw.get("cards", []) or []:
        if not isinstance(card, dict):
            continue
        card_text = str(card.get("cardText") or "")
        threshold_text = str(card.get("thresholdText") or "").strip()
        threshold = _parse_threshold(threshold_text) if threshold_text else _parse_threshold(card_text)
        if threshold is None:
            continue
        if threshold in seen:
            raise EventRewardsError(f"Duplicate event tier threshold detected: {threshold}; refusing an ambiguous snapshot")
        seen.add(threshold)
        status = _tier_status(card, balance)
        should_read = bool(card.get("claimButtonEnabled")) if "claimButtonEnabled" in card else status in {"available", "inconsistent_balance"}
        tiers.append({
            "threshold": threshold,
            "reward": _normalise_reward_items(card, card_text) if should_read else [],
            "button_text": str(card.get("buttonText") or "").strip(),
            "button_index": int(card.get("buttonIndex", -1)),
            "disabled": bool(card.get("disabled", True)),
            "visible": bool(card.get("visible", True)),
            "claim_button_enabled": should_read,
            "status": status,
        })
    tiers.sort(key=lambda item: item["threshold"])
    next_threshold = min((t["threshold"] for t in tiers
                          if t["status"] != "delivered" and (balance is None or t["threshold"] > balance)),
                         default=next_displayed)
    return {"title": str(raw.get("title") or ""), "url": str(raw.get("url") or ""),
            "body_text": body, "balance": balance, "next_threshold": next_threshold,
            "tiers": tiers}


def _read_page_state(driver) -> dict[str, Any]:
    return _normalise_snapshot(driver.execute_script(_SNAPSHOT_SCRIPT))


def _tier_by_threshold(state: dict[str, Any], threshold: int) -> dict[str, Any] | None:
    return next((tier for tier in state.get("tiers", []) if tier.get("threshold") == threshold), None)


def _visible(element) -> bool:
    try:
        return bool(element.is_displayed())
    except Exception:
        return False


def _element_text(element) -> str:
    try:
        value = element.text
        if value:
            return str(value)
    except Exception:
        pass
    try:
        return str(element.get_attribute("innerText") or element.get_attribute("textContent") or "")
    except Exception:
        return ""


def _inspect_promotion_popup(driver) -> dict[str, Any]:
    try:
        result = driver.execute_script(_POPUP_INSPECT_SCRIPT)
        return result if isinstance(result, dict) else {"visibleDialogCount": 0, "promotionalDialogVisible": False}
    except Exception as exc:
        return {"inspection_error": type(exc).__name__, "visibleDialogCount": -1,
                "promotionalDialogVisible": False}


def _dismiss_event_promotion_popup(driver, log: Callable[[str], None],
                                   timeout: float = EVENT_POPUP_WAIT_SECONDS) -> tuple[bool, str]:
    """Safely dismiss the captured Store promotion and verify its overlay is gone.

    The close button is selected from inside the identified promotion dialog.
    Its actual portal attributes are checked again before a native Selenium
    click. Generic modal buttons and the Store purchase button are never used.
    """
    state = _inspect_promotion_popup(driver)
    if state.get("inspection_error"):
        return False, "popup_state_unavailable"

    dialog_count = int(state.get("visibleDialogCount", 0) or 0)
    overlay_count = int(state.get("fullScreenBlockingOverlayCount", 0) or 0)
    promotional_visible = bool(state.get("promotionalDialogVisible"))
    if dialog_count == 0 and overlay_count == 0:
        return True, "no_visible_dialog_or_fullscreen_overlay"
    if not promotional_visible:
        if overlay_count:
            return False, "unknown_fullscreen_overlay_blocks_event_page"
        return False, "unknown_dialog_blocks_event_page"

    try:
        dialogs = driver.find_elements("css selector", EVENT_POPUP_SELECTOR)
    except Exception as exc:
        return False, f"dialog_lookup_failed:{type(exc).__name__}"

    matching_dialogs = []
    for dialog in dialogs:
        if not _visible(dialog):
            continue
        dialog_text = _element_text(dialog).casefold()
        if "exclusive discount activated" in dialog_text or "purchase or lose it" in dialog_text:
            matching_dialogs.append(dialog)
    if len(matching_dialogs) != 1:
        return False, f"promotional_dialog_match_count:{len(matching_dialogs)}"
    target_dialog = matching_dialogs[0]

    try:
        close_buttons = target_dialog.find_elements("css selector", EVENT_POPUP_CLOSE_SELECTOR)
    except Exception as exc:
        return False, f"close_control_lookup_failed:{type(exc).__name__}"

    safe_candidates = []
    for button in close_buttons:
        if not _visible(button):
            continue
        try:
            testid = (button.get_attribute("data-testid") or "").casefold()
            aria = (button.get_attribute("aria-label") or "").casefold()
            title = (button.get_attribute("title") or "").casefold()
            variant = (button.get_attribute("data-variant") or "").casefold()
            class_name = (button.get_attribute("class") or "").casefold()
            button_type = (button.get_attribute("type") or "button").casefold()
            disabled = button.get_attribute("disabled") is not None or button.get_attribute("aria-disabled") == "true"
        except Exception:
            continue

        # Reject any purchase control explicitly, even if a future portal
        # change accidentally adds an accessibility label that resembles Close.
        is_purchase = testid == "buy-button" or variant == "component-buy"
        has_close_evidence = (
            testid == "close" or variant == "close"
            or aria in {"close", "close dialog"} or title == "close"
            or "button-close" in class_name
        )
        if not is_purchase and not disabled and button_type == "button" and has_close_evidence:
            safe_candidates.append(button)

    # Multiple plausible controls are ambiguous; do not guess.
    if len(safe_candidates) != 1:
        return False, f"safe_close_candidate_count:{len(safe_candidates)}"
    safe_close = safe_candidates[0]

    try:
        close_state = driver.execute_script(_POPUP_CLOSE_BUTTON_CHECK_SCRIPT, safe_close)
    except Exception as exc:
        return False, f"close_button_validation_failed:{type(exc).__name__}"
    if not isinstance(close_state, dict) or not close_state.get("ok"):
        reason = close_state.get("reason", "close_button_not_topmost_or_not_safe") if isinstance(close_state, dict) else "close_button_validation_no_state"
        return False, str(reason)

    try:
        # Native Selenium click only. Never use JS click or generic modal fallback.
        safe_close.click()
    except Exception as exc:
        return False, f"native_close_click_failed:{type(exc).__name__}"

    poll_count = max(1, int(float(timeout) / 0.2 + 0.999))
    for _ in range(poll_count):
        time.sleep(0.2)
        current = _inspect_promotion_popup(driver)
        if current.get("inspection_error"):
            continue
        current_dialogs = int(current.get("visibleDialogCount", 0) or 0)
        current_overlays = int(current.get("fullScreenBlockingOverlayCount", 0) or 0)
        if current_dialogs == 0 and current_overlays == 0:
            log("✅ Halloween promotion popup and full-screen overlay dismissed and verified")
            return True, "dismissed_and_verified"
        if not current.get("promotionalDialogVisible"):
            if current_overlays:
                return False, "fullscreen_overlay_remained_after_close_click"
            return False, "another_dialog_remains_visible"
    return False, "popup_or_fullscreen_overlay_remained_after_close_click"


def _find_claim_button_for_threshold(driver, expected_threshold: int):
    """Reacquire the button from its reward card; never rely on document index."""
    cards = driver.find_elements("css selector", EVENT_CARD_SELECTOR)
    for card in cards:
        threshold = _parse_threshold(_element_text(card))
        if threshold != int(expected_threshold):
            continue
        buttons = card.find_elements("tag name", "button")
        if len(buttons) != 1:
            return None, "expected_exactly_one_card_button"
        return buttons[0], "found"
    return None, "tier_card_not_found"


def _validate_claim_button(driver, button, expected_threshold: int) -> tuple[bool, str]:
    """Validate the exact tier and ensure no overlay intercepts its centre."""
    try:
        state = driver.execute_script(_CLAIM_BUTTON_CHECK_SCRIPT, button)
    except Exception as exc:
        return False, f"validation_exception:{type(exc).__name__}"
    if not isinstance(state, dict):
        return False, "validation_no_state"
    if not state.get("visible"):
        return False, "button_not_visible"
    if state.get("disabled"):
        return False, "button_disabled"
    if state.get("buttonText") not in {"claim", "claim all"}:
        return False, "button_text_changed"
    try:
        actual_threshold = int(state.get("threshold"))
    except (TypeError, ValueError):
        return False, "tier_threshold_not_found"
    if actual_threshold != int(expected_threshold):
        return False, f"tier_mismatch:{actual_threshold}!={expected_threshold}"
    if not state.get("topmost"):
        return False, "click_point_obstructed"
    if int(state.get("blockingDialogCount", 0) or 0) > 0:
        return False, "blocking_dialog_present"
    return True, "validated"


def _wait_for_delivered(driver, threshold: int, timeout: float) -> dict[str, Any] | None:
    # Fixed, bounded polling keeps the wait predictable even when monotonic time
    # is mocked or the browser's execution clock stalls.
    poll_count = max(1, int(float(timeout) / 0.3 + 0.999))
    for _ in range(poll_count):
        try:
            state = _read_page_state(driver)
            updated = _tier_by_threshold(state, threshold)
            if updated and updated.get("status") == "delivered":
                return updated
        except Exception:
            pass
        time.sleep(0.3)
    return None


def _claim_one_event_tier(driver, tier: dict[str, Any], physical_click: Callable,
                          bypass_cloudflare: Callable, close_popup: Callable,
                          log: Callable) -> tuple[str, dict[str, Any]]:
    threshold = int(tier["threshold"])
    popup_ok, popup_reason = _dismiss_event_promotion_popup(driver, log)
    if not popup_ok:
        log(f"⚠️ Refusing Halloween claim at {threshold}: {popup_reason}")
        return "popup_blocking", tier

    button, locator_reason = _find_claim_button_for_threshold(driver, threshold)
    if button is None:
        log(f"⚠️ Refusing Halloween claim at {threshold}: {locator_reason}")
        return "button_not_found", tier

    # Bring the exact button into the viewport before hit-testing. Scrolling is
    # not a click; the button is reacquired afterwards in case the page rerenders.
    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({block:'center', inline:'center', behavior:'instant'});",
            button,
        )
        time.sleep(0.2)
    except Exception as exc:
        log(f"⚠️ Could not position Halloween button at {threshold}: {type(exc).__name__}")
        return "button_validation_failed", tier

    popup_ok, popup_reason = _dismiss_event_promotion_popup(driver, log)
    if not popup_ok:
        log(f"⚠️ Refusing Halloween claim at {threshold}: {popup_reason}")
        return "popup_blocking", tier
    button, locator_reason = _find_claim_button_for_threshold(driver, threshold)
    if button is None:
        log(f"⚠️ Refusing Halloween claim at {threshold}: {locator_reason}")
        return "button_not_found", tier

    valid, reason = _validate_claim_button(driver, button, threshold)
    if not valid:
        log(f"⚠️ Refusing Halloween click at {threshold}: {reason}")
        return "button_validation_failed", tier

    # Deliberately use the element's native Selenium click. Do not call the
    # injected physical_click helper: its production implementation falls back
    # to JavaScript click, which bypasses the browser's hit-testing safeguards.
    try:
        button.click()
    except Exception as exc:
        log(f"⚠️ Native Halloween click failed at {threshold}: {type(exc).__name__}")
        return "click_failed", tier

    reward_text = "; ".join(tier.get("reward", []) or []) or "reward details unavailable"
    log(f"🎃 Halloween native click dispatched at {threshold} points — {reward_text}; awaiting portal confirmation")
    delivered = _wait_for_delivered(driver, threshold, EVENT_CLAIM_VERIFY_SECONDS)
    if delivered:
        return "claimed_this_run", delivered

    # A single page refresh is permitted for durable-state verification. Never
    # issue a second claim click from this recovery path.
    try:
        driver.refresh()
        bypass_cloudflare(driver)
        time.sleep(1.0)
        popup_ok, popup_reason = _dismiss_event_promotion_popup(driver, log)
        if not popup_ok:
            log(f"⚠️ Popup remains during final verification: {popup_reason}")
        delivered = _wait_for_delivered(driver, threshold, 3.0)
        if delivered:
            return "claimed_this_run", delivered
    except Exception as exc:
        log(f"⚠️ Could not verify Halloween reward at {threshold}: {type(exc).__name__}")
    return "claim_unconfirmed", tier


def claim_event_program(driver, program: dict[str, Any], *, physical_click: Callable,
                        bypass_cloudflare: Callable, close_popup: Callable,
                        log: Callable) -> dict[str, Any]:
    """Inspect and claim currently eligible tiers for one signed-in account."""
    name, slug = str(program["name"]), str(program["slug"])
    url = f"https://hub.vertigogames.co/{slug}"
    result: dict[str, Any] = {
        "program_id": program["program_id"], "name": name, "slug": slug,
        "points_label": str(program.get("points_label") or "points"),
        "cycle": int(program.get("cycle", 0) or 0), "status": "checking",
        "balance": None, "next_threshold": None, "tiers_total": 0,
        "tiers_delivered": 0, "claims_this_run": [], "tiers": [],
        "retry_required": False, "error": None, "end_at": program["end_at_epoch"],
    }

    driver.get(url)
    bypass_cloudflare(driver)
    time.sleep(2.0)

    popup_ok, popup_reason = _dismiss_event_promotion_popup(driver, log)
    if not popup_ok:
        result["retry_required"] = True
        result["error"] = f"popup_blocking: {popup_reason}"
        log(f"⚠️ Halloween page blocked before lazy loading; no reward clicks will be attempted: {result['error']}")
    else:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(0.5)
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(0.3)
        # A delayed promotion can appear after lazy rendering; check and dismiss it
        # again before parsing or attempting any event claim.
        popup_ok_after_scroll, popup_reason_after_scroll = _dismiss_event_promotion_popup(driver, log)
        if not popup_ok_after_scroll:
            result["retry_required"] = True
            result["error"] = f"popup_blocking: {popup_reason_after_scroll}"
            log(f"⚠️ Halloween page blocked after lazy loading; no reward clicks will be attempted: {result['error']}")

    state = _read_page_state(driver)
    if name.casefold() not in state["body_text"].casefold() and not state["tiers"]:
        raise EventRewardsError(f"Could not identify the '{name}' event page")
    if state["balance"] is None:
        raise EventRewardsError(f"Could not read the point balance for '{name}'")
    if not state["tiers"]:
        raise EventRewardsError(f"No reward tiers were detected for '{name}'")

    result["balance"] = state["balance"]
    result["next_threshold"] = state["next_threshold"]
    result["tiers_total"] = len(state["tiers"])
    expected_count = program.get("expected_tier_count")
    if expected_count is not None:
        try:
            expected_count = int(expected_count)
        except (TypeError, ValueError) as exc:
            raise EventRewardsError("Configured expected_tier_count is not an integer") from exc
        if len(state["tiers"]) != expected_count:
            raise EventRewardsError(f"Detected {len(state['tiers'])}/{expected_count} expected reward tiers; refusing an incomplete page snapshot")

    inconsistent = next((tier for tier in state["tiers"] if tier["status"] == "inconsistent_balance"), None)
    if inconsistent:
        result["retry_required"] = True
        result["error"] = f"portal shows an enabled Claim button below balance threshold ({inconsistent['threshold']} points)"
        log(f"⚠️ Event reward state is inconsistent: {result['error']}")

    claims = 0
    max_claims = min(int(program["max_claims_per_run"]), len(state["tiers"]))
    while claims < max_claims and not result["retry_required"]:
        candidate = next((tier for tier in state["tiers"] if tier["status"] == "available"), None)
        if candidate is None:
            break
        outcome, updated = _claim_one_event_tier(
            driver, candidate, physical_click, bypass_cloudflare, close_popup, log
        )
        if outcome == "claimed_this_run":
            claims += 1
            result["claims_this_run"].append({
                "threshold": candidate["threshold"], "reward": candidate["reward"],
                "status": "claimed_this_run",
            })
            state = _read_page_state(driver)
            result["balance"] = state["balance"]
            result["next_threshold"] = state["next_threshold"]
            continue
        candidate["status"] = outcome
        result["retry_required"] = True
        result["error"] = f"{outcome.replace('_', ' ')} at {candidate['threshold']} points"
        log(f"⚠️ Event reward needs review: {result['error']}")
        break

    final_state = _read_page_state(driver)
    result["balance"] = final_state["balance"]
    result["next_threshold"] = final_state["next_threshold"]
    result["tiers"] = final_state["tiers"]
    result["tiers_total"] = len(final_state["tiers"])
    result["tiers_delivered"] = sum(1 for tier in final_state["tiers"] if tier["status"] == "delivered")
    result["status"] = "attention_required" if result["retry_required"] else "checked"
    log(f"🎃 {name}: {result['balance']} points, {len(result['claims_this_run'])} tier(s) claimed this run, {result['tiers_delivered']}/{result['tiers_total']} delivered")
    return result


def claim_configured_event_rewards(driver, pid: str, *, log: Callable,
                                   physical_click: Callable, bypass_cloudflare: Callable,
                                   close_popup: Callable,
                                   config_path: str | Path | None = None) -> list[dict[str, Any]]:
    """Run active configured event programmes for the current authenticated ID."""
    config = load_event_config(config_path)
    programs = get_active_event_programs(config)
    results = []
    for program in programs:
        try:
            log(f"🎃 Checking event rewards for {pid}: {program['name']}")
            results.append(claim_event_program(
                driver, program, physical_click=physical_click,
                bypass_cloudflare=bypass_cloudflare, close_popup=close_popup, log=log,
            ))
        except Exception as exc:
            error = f"{type(exc).__name__}: {str(exc)[:180]}"
            log(f"❌ Event reward check failed for {pid} / {program['name']}: {error}")
            results.append({
                "program_id": program["program_id"], "name": program["name"],
                "slug": program["slug"], "status": "error", "balance": None,
                "next_threshold": None, "tiers_total": 0, "tiers_delivered": 0,
                "claims_this_run": [], "tiers": [], "retry_required": True,
                "error": error, "end_at": program["end_at_epoch"],
            })
    return results


def merge_claim_ledger(existing: list[dict[str, Any]] | None,
                       claims: list[dict[str, Any]] | None, *, cycle: int = 0,
                       claimed_at: str | None = None) -> list[dict[str, Any]]:
    """Append confirmed tiers without overwriting earlier claim history."""
    ledger = [dict(item) for item in (existing or []) if isinstance(item, dict)]
    seen = set()
    for item in ledger:
        try:
            seen.add((int(item.get("cycle", 0) or 0), int(item.get("threshold", -1))))
        except (TypeError, ValueError):
            continue
    for claim in claims or []:
        if not isinstance(claim, dict):
            continue
        try:
            threshold = int(claim.get("threshold", -1))
        except (TypeError, ValueError):
            continue
        key = (int(cycle), threshold)
        if threshold < 0 or key in seen:
            continue
        ledger.append({
            "cycle": int(cycle), "threshold": threshold,
            "reward": list(claim.get("reward", []) or []),
            "claimed_at": claim.get("claimed_at") or claimed_at,
            "verification": "delivered",
        })
        seen.add(key)
    return ledger


def event_claim_count(event_results: list[dict[str, Any]] | None) -> int:
    return sum(len(item.get("claims_this_run", []) or [])
               for item in (event_results or []) if isinstance(item, dict))


def event_retry_required(event_results: list[dict[str, Any]] | None) -> bool:
    return any(bool(item.get("retry_required"))
               for item in (event_results or []) if isinstance(item, dict))
