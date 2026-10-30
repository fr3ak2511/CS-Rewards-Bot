"""Reusable, UI-driven redemption for temporary point-based event programmes.

The event page uses the same visible Claim / Delivered interaction pattern as the
permanent Progression Program page. This module intentionally keeps event
configuration, event history, and event reporting separate from grenade-based
Progression Program state.

It does not call a guessed redemption API. Claims are triggered through the
portal's own enabled button and are considered successful only after the page
shows the corresponding tier as Delivered.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


DEFAULT_CONFIG_PATH = Path(__file__).with_name("event_rewards_config.json")

# Keep the selector narrow: the Halloween cards inspected in DevTools use this
# card wrapper. A guarded ancestor fallback is included in the browser script.
_SNAPSHOT_SCRIPT = r"""
const buttons = Array.from(document.querySelectorAll('button'));
const cards = [];
for (let i = 0; i < buttons.length; i++) {
  const button = buttons[i];
  const buttonText = (button.innerText || button.textContent || '').trim();
  let card = button.closest('div.relative.rounded-lp-card');
  if (!card) {
    let parent = button.parentElement;
    for (let depth = 0; depth < 7 && parent; depth++, parent = parent.parentElement) {
      const text = (parent.innerText || '').trim();
      const firstLine = (text.split(/\n+/)[0] || '').trim();
      if (/^\d[\d,]*$/.test(firstLine) && parent.querySelector('button')) {
        card = parent;
        break;
      }
    }
  }
  if (!card) continue;
  const cardText = (card.innerText || card.textContent || '').trim();
  const firstLine = (cardText.split(/\n+/)[0] || '').trim();
  if (!/^\d[\d,]*$/.test(firstLine)) continue;
  cards.push({
    buttonIndex: i,
    buttonText,
    disabled: !!button.disabled || button.getAttribute('aria-disabled') === 'true',
    visible: !!(button.getClientRects().length
      && getComputedStyle(button).visibility !== 'hidden'
      && getComputedStyle(button).display !== 'none'),
    cardText
  });
}
return {
  title: document.title || '',
  url: location.href,
  bodyText: (document.body && document.body.innerText) || '',
  cards
};
"""


class EventRewardsError(RuntimeError):
    """Raised only for configuration / page-state failures, not claim failures."""


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


def get_active_event_programs(
    config: dict[str, Any] | None = None,
    now_epoch: int | None = None,
) -> list[dict[str, Any]]:
    """Return enabled, correctly configured programmes whose end time is future."""
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
        start_at = _as_epoch(raw.get("start_at"))
        end_at = _as_epoch(raw.get("end_at"))
        if not program_id or not slug or end_at is None:
            raise EventRewardsError(f"Event programme config is incomplete: {name or 'unnamed event'}")
        if (start_at is not None and now_epoch < start_at) or now_epoch >= end_at:
            continue
        max_claims = raw.get("max_claims_per_run", 20)
        try:
            max_claims = max(1, min(50, int(max_claims)))
        except (TypeError, ValueError):
            max_claims = 20
        active.append({**raw, "program_id": program_id, "slug": slug,
                       "name": name, "end_at_epoch": end_at,
                       "max_claims_per_run": max_claims})
    return active


def _parse_balance(body_text: str) -> tuple[int | None, int | None]:
    # The event banner shows current balance / next tier, e.g. "1,000 / 1,300".
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
        if not line:
            continue
        lower = line.casefold()
        if lower in ignored:
            continue
        if re.fullmatch(r"x\s*[\d,.]+[KMB]?", line, flags=re.IGNORECASE):
            quantity = re.sub(r"\s+", "", line[1:])
            continue
        if quantity:
            names.append(f"{line} ×{quantity}")
            quantity = None
        else:
            if names and names[-1].split(" ×", 1)[0].casefold() == line.casefold():
                continue
            names.append(line)
    return names


def _tier_status(card: dict[str, Any], balance: int | None) -> str:
    button_text = str(card.get("buttonText") or "").strip().casefold()
    card_text = str(card.get("cardText") or "")
    if button_text == "delivered" or re.search(r"(?im)^\s*delivered\s*$", card_text):
        return "delivered"
    threshold = _parse_threshold(card_text)
    if (button_text in {"claim", "claim all"}
            and not card.get("disabled", True)
            and threshold is not None
            and balance is not None
            and balance >= threshold):
        return "available"
    if button_text in {"claim", "claim all"} and not card.get("disabled", True):
        # Do not click a visually enabled tier unless the displayed point balance
        # independently confirms the threshold.
        return "inconsistent_balance"
    return "locked"


def _normalise_snapshot(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise EventRewardsError("Event page returned no DOM snapshot")
    body = str(raw.get("bodyText") or "")
    balance, next_displayed = _parse_balance(body)
    tiers = []
    for card in raw.get("cards", []) or []:
        if not isinstance(card, dict):
            continue
        text = str(card.get("cardText") or "")
        threshold = _parse_threshold(text)
        if threshold is None:
            continue
        tiers.append({
            "threshold": threshold,
            "reward": _parse_reward_names(text),
            "button_text": str(card.get("buttonText") or "").strip(),
            "button_index": int(card.get("buttonIndex", -1)),
            "disabled": bool(card.get("disabled", True)),
            "status": _tier_status(card, balance),
        })
    tiers.sort(key=lambda item: item["threshold"])
    next_threshold = min(
        (tier["threshold"] for tier in tiers
         if tier["status"] != "delivered" and (balance is None or tier["threshold"] > balance)),
        default=next_displayed,
    )
    return {
        "title": str(raw.get("title") or ""),
        "url": str(raw.get("url") or ""),
        "body_text": body,
        "balance": balance,
        "next_threshold": next_threshold,
        "tiers": tiers,
    }


def _read_page_state(driver) -> dict[str, Any]:
    raw = driver.execute_script(_SNAPSHOT_SCRIPT)
    return _normalise_snapshot(raw)


def _tier_by_threshold(state: dict[str, Any], threshold: int) -> dict[str, Any] | None:
    return next((tier for tier in state.get("tiers", [])
                 if tier.get("threshold") == threshold), None)


def _validate_claim_button(driver, button, expected_threshold: int) -> tuple[bool, str]:
    """Revalidate that a fresh enabled Claim control belongs to the expected tier."""
    try:
        state = driver.execute_script(r"""
            // EVENT_CLAIM_BUTTON_CHECK: narrow pre-click guard.
            const button = arguments[0];
            if (!button) return {ok: false, reason: 'button_missing'};
            const norm = value => (value || '').replace(/\s+/g, ' ').trim().toLowerCase();
            const style = window.getComputedStyle(button);
            const rect = button.getBoundingClientRect();
            const visible = !!(button.getClientRects().length && rect.width > 0 && rect.height > 0
              && style.display !== 'none' && style.visibility !== 'hidden');
            const buttonText = norm(button.innerText || button.textContent || button.getAttribute('aria-label'));
            const disabled = !!button.disabled || button.getAttribute('aria-disabled') === 'true';
            let node = button;
            let cardText = '';
            let threshold = null;
            for (let depth = 0; depth < 10 && node; depth++, node = node.parentElement) {
              const text = (node.innerText || node.textContent || '').trim();
              const first = (text.split(/\n+/)[0] || '').trim();
              if (/^\d[\d,]*$/.test(first)) {
                cardText = text;
                threshold = Number(first.replace(/,/g, ''));
                break;
              }
            }
            return {ok: visible && !disabled && ['claim', 'claim all'].includes(buttonText),
              visible, disabled, buttonText, threshold, cardText};
        """, button)
    except Exception as exc:
        return False, f"validation_exception:{type(exc).__name__}"

    if not isinstance(state, dict):
        return False, "validation_no_state"
    if not state.get("visible"):
        return False, "button_not_visible"
    if state.get("disabled"):
        return False, "button_disabled"
    if state.get("buttonText") not in ("claim", "claim all"):
        return False, "button_text_changed"
    try:
        actual_threshold = int(state.get("threshold"))
    except (TypeError, ValueError):
        return False, "tier_threshold_not_found"
    if actual_threshold != int(expected_threshold):
        return False, f"tier_mismatch:{actual_threshold}!={expected_threshold}"
    return True, "validated"


def _claim_one_event_tier(
    driver,
    tier: dict[str, Any],
    physical_click: Callable,
    bypass_cloudflare: Callable,
    close_popup: Callable,
    log: Callable,
) -> tuple[str, dict[str, Any]]:
    index = int(tier.get("button_index", -1))
    # By.TAG_NAME is the literal Selenium locator string "tag name". Using it
    # directly keeps this module importable for offline unit tests without Selenium.
    buttons = driver.find_elements("tag name", "button")
    if index < 0 or index >= len(buttons):
        return "click_failed", tier
    button = buttons[index]
    if getattr(button, "disabled", False):
        return "not_available", tier

    valid, validation_reason = _validate_claim_button(driver, button, int(tier["threshold"]))
    if not valid:
        log(f"⚠️ Refusing event click at {tier['threshold']} points: {validation_reason}")
        return "button_validation_failed", tier

    try:
        clicked = bool(physical_click(driver, button))
    except Exception as exc:  # the account remains eligible for a later safe recheck
        log(f"⚠️ Event reward click raised {type(exc).__name__} at {tier['threshold']} points")
        clicked = False
    if not clicked:
        return "click_failed", tier

    log(f"🎃 Event reward click sent: {tier['threshold']} points — {'; '.join(tier['reward'])}")
    time.sleep(2.5)
    try:
        close_popup(driver)
    except Exception:
        pass

    # First inspect the live DOM. If it has not refreshed, reload once and verify
    # the durable Delivered state; never issue a second claim click blindly.
    try:
        state = _read_page_state(driver)
        updated = _tier_by_threshold(state, tier["threshold"])
        if updated and updated["status"] == "delivered":
            return "claimed_this_run", updated
    except Exception:
        pass

    try:
        driver.refresh()
        bypass_cloudflare(driver)
        time.sleep(2.0)
        close_popup(driver)
        state = _read_page_state(driver)
        updated = _tier_by_threshold(state, tier["threshold"])
        if updated and updated["status"] == "delivered":
            return "claimed_this_run", updated
    except Exception as exc:
        log(f"⚠️ Could not verify event reward at {tier['threshold']} points: {type(exc).__name__}")

    return "claim_unconfirmed", tier


def claim_event_program(
    driver,
    program: dict[str, Any],
    *,
    physical_click: Callable,
    bypass_cloudflare: Callable,
    close_popup: Callable,
    log: Callable,
) -> dict[str, Any]:
    """Inspect and claim every currently eligible tier for one signed-in account."""
    name = str(program["name"])
    slug = str(program["slug"])
    url = f"https://hub.vertigogames.co/{slug}"
    result: dict[str, Any] = {
        "program_id": program["program_id"],
        "name": name,
        "slug": slug,
        "points_label": str(program.get("points_label") or "points"),
        "cycle": int(program.get("cycle", 0) or 0),
        "status": "checking",
        "balance": None,
        "next_threshold": None,
        "tiers_total": 0,
        "tiers_delivered": 0,
        "claims_this_run": [],
        "tiers": [],
        "retry_required": False,
        "error": None,
        "end_at": program["end_at_epoch"],
    }

    driver.get(url)
    bypass_cloudflare(driver)
    time.sleep(2.0)
    close_popup(driver)
    # Scroll once to make lazy-rendered reward cards available to the DOM, then
    # return to the top so subsequent Selenium clicks can scroll to their target.
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    time.sleep(0.5)
    driver.execute_script("window.scrollTo(0, 0);")
    time.sleep(0.3)

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

    inconsistent = next((tier for tier in state["tiers"]
                         if tier["status"] == "inconsistent_balance"), None)
    if inconsistent:
        result["retry_required"] = True
        result["error"] = (
            f"portal shows an enabled Claim button below the displayed balance threshold "
            f"({inconsistent['threshold']} points)"
        )
        log(f"⚠️ Event reward state is inconsistent: {result['error']}")

    max_claims = min(int(program["max_claims_per_run"]), len(state["tiers"]))
    claims = 0
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
                "threshold": candidate["threshold"],
                "reward": candidate["reward"],
                "status": "claimed_this_run",
            })
            state = _read_page_state(driver)
            result["balance"] = state["balance"]
            result["next_threshold"] = state["next_threshold"]
            continue

        candidate["status"] = outcome
        result["retry_required"] = True
        result["error"] = (
            f"{outcome.replace('_', ' ')} at {candidate['threshold']} points"
        )
        log(f"⚠️ Event reward needs review: {result['error']}")
        break

    # Refresh final status once to capture current points / Delivered state.
    final_state = _read_page_state(driver)
    result["balance"] = final_state["balance"]
    result["next_threshold"] = final_state["next_threshold"]
    result["tiers"] = final_state["tiers"]
    result["tiers_total"] = len(final_state["tiers"])
    result["tiers_delivered"] = sum(1 for tier in final_state["tiers"] if tier["status"] == "delivered")
    result["status"] = "checked"
    if result["retry_required"]:
        result["status"] = "attention_required"
    log(
        f"🎃 {name}: {result['balance']} points, "
        f"{len(result['claims_this_run'])} tier(s) claimed this run, "
        f"{result['tiers_delivered']}/{result['tiers_total']} delivered"
    )
    return result


def claim_configured_event_rewards(
    driver,
    pid: str,
    *,
    log: Callable,
    physical_click: Callable,
    bypass_cloudflare: Callable,
    close_popup: Callable,
    config_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Run all active configured event programmes for the currently logged-in ID."""
    config = load_event_config(config_path)
    programs = get_active_event_programs(config)
    results: list[dict[str, Any]] = []
    for program in programs:
        try:
            log(f"🎃 Checking event rewards for {pid}: {program['name']}")
            results.append(claim_event_program(
                driver,
                program,
                physical_click=physical_click,
                bypass_cloudflare=bypass_cloudflare,
                close_popup=close_popup,
                log=log,
            ))
        except Exception as exc:
            error = f"{type(exc).__name__}: {str(exc)[:180]}"
            log(f"❌ Event reward check failed for {pid} / {program['name']}: {error}")
            results.append({
                "program_id": program["program_id"],
                "name": program["name"],
                "slug": program["slug"],
                "status": "error",
                "balance": None,
                "next_threshold": None,
                "tiers_total": 0,
                "tiers_delivered": 0,
                "claims_this_run": [],
                "tiers": [],
                "retry_required": True,
                "error": error,
                "end_at": program["end_at_epoch"],
            })
    return results



def merge_claim_ledger(
    existing: list[dict[str, Any]] | None,
    claims: list[dict[str, Any]] | None,
    *,
    cycle: int = 0,
    claimed_at: str | None = None,
) -> list[dict[str, Any]]:
    """Append newly confirmed tiers without overwriting earlier claim history."""
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
            "cycle": int(cycle),
            "threshold": threshold,
            "reward": list(claim.get("reward", []) or []),
            "claimed_at": claim.get("claimed_at") or claimed_at,
            "verification": "delivered",
        })
        seen.add(key)
    return ledger

def event_claim_count(event_results: list[dict[str, Any]] | None) -> int:
    return sum(
        len(item.get("claims_this_run", []) or [])
        for item in (event_results or []) if isinstance(item, dict)
    )


def event_retry_required(event_results: list[dict[str, Any]] | None) -> bool:
    return any(
        bool(item.get("retry_required"))
        for item in (event_results or []) if isinstance(item, dict)
    )
