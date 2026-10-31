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
_SNAPSHOT_SCRIPT = r"""const buttons = Array.from(document.querySelectorAll('button'));
const cardElements = Array.from(document.querySelectorAll('div.relative.rounded-lp-card'));
const cards = [];

const norm = value => (value || '').replace(/\s+/g, ' ').trim();

for (const card of cardElements) {
  // Read only the leading threshold token. In some headless DOM layouts the
  // threshold and reward quantity are concatenated, e.g. "5,200x20".
  const rawCardText = (card.innerText || card.textContent || '').trim();
  const firstLine = (rawCardText.split(/\n+/)[0] || '').trim();
  const thresholdMatch = firstLine.match(/^(\d[\d,]*)(?=\s*(?:x|\s|$))/i);
  const thresholdText = thresholdMatch ? thresholdMatch[1] : null;
  if (!thresholdText) continue;

  // Inspect the card's own Claim button before reading any reward labels.
  const button = card.querySelector('button');
  const buttonIndex = button ? buttons.indexOf(button) : -1;
  const buttonText = button
    ? norm(button.innerText || button.textContent || button.getAttribute('aria-label')).toLowerCase()
    : '';
  const disabled = !button || !!button.disabled
    || button.getAttribute('aria-disabled') === 'true';
  const rect = button ? button.getBoundingClientRect() : null;
  const visible = !!(button && button.getClientRects().length && rect
    && rect.width > 0 && rect.height > 0
    && getComputedStyle(button).display !== 'none'
    && getComputedStyle(button).visibility !== 'hidden');
  const claimButtonEnabled = visible && !disabled
    && (buttonText === 'claim' || buttonText === 'claim all');

  // Only enabled Claim / Claim all cards get names and quantities extracted.
  let rewardItems = [];
  if (claimButtonEnabled) {
    const rewardRows = Array.from(card.querySelectorAll('div.flex.flex-row.items-center.gap-3'));
    rewardItems = rewardRows.map(row => {
      const nameElement = row.querySelector('span[class*="line-clamp-1"]');
      const quantityElement = row.querySelector('[class*="text-store-card-sku-img-quantity"]');
      return {
        name: norm(nameElement ? nameElement.textContent : ''),
        quantity: norm(quantityElement ? quantityElement.textContent : '')
      };
    }).filter(item => item.name);
  }

  const lines = [thresholdText];
  for (const item of rewardItems) {
    if (item.quantity) lines.push(item.quantity);
    lines.push(item.name);
  }
  if (buttonText) lines.push(buttonText);

  cards.push({
    thresholdText,
    rewardItems,
    claimButtonEnabled,
    buttonIndex,
    buttonText,
    disabled,
    visible,
    cardText: lines.join('\n')
  });
}

return {
  title: document.title || '',
  url: location.href,
  bodyText: (document.body && document.body.innerText) || '',
  cards
};"""


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


def _normalise_reward_items(card: dict[str, Any], card_text: str) -> list[str]:
    """Normalize structured DOM reward items; an explicit empty list stays empty."""
    raw_items = card.get("rewardItems")
    if isinstance(raw_items, list):
        parsed: list[str] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            name = re.sub(r"\s+", " ", str(item.get("name") or "")).strip()
            quantity = re.sub(r"\s+", "", str(item.get("quantity") or "").strip())
            if not name:
                continue
            if quantity[:1].casefold() == "x":
                quantity = quantity[1:]
            parsed.append(f"{name} ×{quantity}" if quantity else name)
        return parsed
    # Compatibility for older snapshots that predate the structured DOM fields.
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
        claim_enabled = bool(card.get("claimButtonEnabled"))
    else:
        claim_enabled = (
            button_text in {"claim", "claim all"}
            and not bool(card.get("disabled", True))
        )

    if not claim_enabled:
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
    seen_thresholds = set()
    for card in raw.get("cards", []) or []:
        if not isinstance(card, dict):
            continue
        text = str(card.get("cardText") or "")
        threshold_text = str(card.get("thresholdText") or "").strip()
        threshold = _parse_threshold(threshold_text) if threshold_text else _parse_threshold(text)
        if threshold is None:
            continue
        if threshold in seen_thresholds:
            raise EventRewardsError(f"Duplicate event tier threshold detected: {threshold}; refusing an ambiguous snapshot")
        seen_thresholds.add(threshold)

        status = _tier_status(card, balance)
        if "claimButtonEnabled" in card:
            should_read_rewards = bool(card.get("claimButtonEnabled"))
        else:
            should_read_rewards = status in {"available", "inconsistent_balance"}
        reward = _normalise_reward_items(card, text) if should_read_rewards else []

        tiers.append({
            "threshold": threshold,
            "reward": reward,
            "button_text": str(card.get("buttonText") or "").strip(),
            "button_index": int(card.get("buttonIndex", -1)),
            "disabled": bool(card.get("disabled", True)),
            "visible": bool(card.get("visible", True)),
            "claim_button_enabled": should_read_rewards,
            "status": status,
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
            const card = button.closest('div.relative.rounded-lp-card');
            const thresholdElement = card && Array.from(card.querySelectorAll('span'))
              .find(el => /^\d[\d,]*$/.test((el.textContent || '').trim()));
            const threshold = thresholdElement
              ? Number(thresholdElement.textContent.trim().replace(/,/g, '')) : null;
            const cardText = card ? (card.innerText || card.textContent || '').trim() : '';
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

    expected_tier_count = program.get("expected_tier_count")
    if expected_tier_count is not None:
        try:
            expected_tier_count = int(expected_tier_count)
        except (TypeError, ValueError) as exc:
            raise EventRewardsError("Configured expected_tier_count is not an integer") from exc
        if len(state["tiers"]) != expected_tier_count:
            detected_count = len(state["tiers"])
            raise EventRewardsError(
                f"Detected {detected_count}/{expected_tier_count} expected reward tiers; "
                "refusing to claim from an incomplete or ambiguous page snapshot"
            )

    missing_reward_labels = [
        tier["threshold"] for tier in state["tiers"]
        if tier["status"] == "available" and not tier.get("reward")
    ]
    if missing_reward_labels:
        raise EventRewardsError(
            "Reward names could not be read for tier(s): " + ", ".join(str(x) for x in missing_reward_labels)
        )

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
