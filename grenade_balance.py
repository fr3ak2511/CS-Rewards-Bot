"""Read-only Progression Grenade balance capture and minimum tracking.

The portal counter is cumulative for the active monthly Reward Chain. The
minimum is derived from the account's tracking baseline plus three Grenades
per completed daily reset cycle. Purchase bonuses are surplus, never required.
"""

from __future__ import annotations

import copy
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

IST = timezone(timedelta(hours=5, minutes=30))
DAILY_GRENADE_MINIMUM = 3
DEFAULT_RESET_TIME = time(5, 30)

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


def _as_ist(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=IST)
    return value.astimezone(IST)


def daily_reset_date(
    value: datetime,
    *,
    reset_time: time = DEFAULT_RESET_TIME,
) -> date:
    """Return the daily cycle date; the game resets at 05:30 IST."""
    local = _as_ist(value)
    if local.timetz().replace(tzinfo=None) < reset_time:
        return local.date() - timedelta(days=1)
    return local.date()


def eligible_cycle_days(start_date: str | date, end_date: str | date) -> int:
    """Count reset dates inclusively, returning zero for an inverted range."""
    start = date.fromisoformat(start_date) if isinstance(start_date, str) else start_date
    end = date.fromisoformat(end_date) if isinstance(end_date, str) else end_date
    if end < start:
        return 0
    return (end - start).days + 1


def _parse_integer(value: str) -> int:
    return int(value.replace(",", "").strip())


def parse_progression_balance_panel(
    panel_text: str,
    *,
    observed_at: datetime,
) -> dict[str, Any] | None:
    """Parse the Reward Chain banner's collected/next-milestone fraction.

    The caller must pass text scoped to the Reward Chain banner, not the whole
    page. This prevents the unrelated sidebar LP fraction (for example 20/40)
    from being mistaken for the Grenade counter.
    """
    text = re.sub(r"\s+", " ", str(panel_text or "")).strip()
    title_match = re.search(
        r"\b(" + "|".join(_MONTHS) + r")\s+Reward\s+Chain\b",
        text,
        flags=re.IGNORECASE,
    )
    if not title_match:
        return None

    excerpt = text[title_match.end():title_match.end() + 700]
    counter_match = re.search(
        r"(?<![\d,])(\d[\d,]*)\s*/\s*(\d[\d,]*)(?![\d,])",
        excerpt,
    )
    if not counter_match:
        return None

    try:
        current_count = _parse_integer(counter_match.group(1))
        next_milestone = _parse_integer(counter_match.group(2))
    except (TypeError, ValueError):
        return None

    if current_count < 0 or next_milestone <= 0:
        return None

    month = _MONTHS[title_match.group(1).lower()]
    local_observed = _as_ist(observed_at)
    year = local_observed.year
    # A page still showing the previous month's chain during the first few days
    # of a new month belongs to the preceding calendar year only at year rollover.
    if month > local_observed.month and month - local_observed.month > 6:
        year -= 1
    elif local_observed.month > month and local_observed.month - month > 6:
        year += 1

    return {
        "cycle_id": f"{year:04d}-{month:02d}",
        "cycle_title": f"{title_match.group(1).title()} Reward Chain",
        "cycle_start_date": date(year, month, 1).isoformat(),
        "current_count": current_count,
        "next_milestone": next_milestone,
        "observed_at_ist": local_observed.isoformat(),
    }


def capture_progression_balance(
    driver: Any,
    *,
    observed_at: datetime,
) -> dict[str, Any] | None:
    """Read the visible Reward Chain banner. This function never clicks controls."""
    try:
        payload = driver.execute_script(r"""
            const norm = value => String(value || '').replace(/\s+/g, ' ').trim();
            const visible = el => {
              if (!el) return false;
              const r = el.getBoundingClientRect(), s = getComputedStyle(el);
              return !!(el.getClientRects().length && r.width > 0 && r.height > 0
                && s.display !== 'none' && s.visibility !== 'hidden'
                && Number(s.opacity || '1') > 0);
            };
            const path = String(location.pathname || '').toLowerCase();
            const title = String(document.title || '');
            const bodyText = norm(document.body && document.body.innerText || '');
            const cloudflare = /cloudflare|just a moment|checking your browser|verify you are human/i
              .test(title + ' ' + bodyText.slice(0, 900));
            const validPage = path.includes('/progression-program') && !cloudflare;
            if (!validPage) {
              return {valid_page:false, url:location.href, title, panel_text:''};
            }

            const headings = Array.from(document.querySelectorAll('h1,h2,h3,div,span,p'))
              .filter(visible)
              .filter(el => /\b[A-Za-z]+\s+Reward\s+Chain\b/i.test(
                norm(el.innerText || el.textContent)
              ))
              .sort((a, b) =>
                norm(a.innerText || a.textContent).length
                - norm(b.innerText || b.textContent).length
              );

            let panelText = '';
            for (const heading of headings) {
              let node = heading;
              for (let depth = 0; depth < 10 && node; depth++, node = node.parentElement) {
                const candidate = norm(node.innerText || node.textContent);
                if (candidate.length > 0 && candidate.length <= 1800
                    && /CLAIM YOUR GRENADES/i.test(candidate)
                    && /\d[\d,]*\s*\/\s*\d[\d,]*/.test(candidate)) {
                  panelText = candidate;
                  break;
                }
              }
              if (panelText) break;
            }

            // Bounded fallback around the Reward Chain heading. Never parse the
            // entire page, because the profile sidebar contains another fraction.
            if (!panelText) {
              const lower = bodyText.toLowerCase();
              const index = lower.indexOf('reward chain');
              if (index >= 0) panelText = bodyText.slice(Math.max(0, index - 40), index + 700);
            }

            return {
              valid_page:true,
              url:location.href,
              title,
              panel_text:panelText
            };
        """)
        if not isinstance(payload, dict) or not payload.get("valid_page"):
            return None
        parsed = parse_progression_balance_panel(
            payload.get("panel_text", ""),
            observed_at=observed_at,
        )
        if not parsed:
            return None
        return {
            **parsed,
            "valid_page": True,
            "url": str(payload.get("url") or ""),
            "page_title": str(payload.get("title") or ""),
        }
    except Exception:
        return None


def _cycles_from_state(state: dict[str, Any]) -> dict[str, Any]:
    cycles = state.get("cycles")
    if not isinstance(cycles, dict):
        cycles = {}
        state["cycles"] = cycles
    return cycles


def _players_for_cycle(cycle: dict[str, Any]) -> dict[str, Any]:
    players = cycle.get("players")
    if not isinstance(players, dict):
        players = {}
        cycle["players"] = players
    return players


def _has_prior_cycle_entry(cycles: dict[str, Any], player_id: str, current_cycle: str) -> bool:
    for cycle_id, cycle in cycles.items():
        if cycle_id == current_cycle or not isinstance(cycle, dict):
            continue
        players = cycle.get("players")
        if isinstance(players, dict) and player_id in players:
            return True
    return False


def evaluate_progression_balance(
    snapshot: dict[str, Any] | None,
    state: dict[str, Any],
    player_id: str,
    *,
    reset_date: date,
    is_new_account: bool,
    store_rewards_fully_checked: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Update a separate baseline state and return the account's minimum check.

    Existing accounts known at cycle start are seeded with zero and accrue three
    per reset date. New accounts use the observed portal count after the joining
    day's Store check as their baseline, then accrue from the following date.
    Unknown legacy accounts are baselined on their first verified observation,
    never backfilled with guessed pre-joining activity.
    """
    updated = copy.deepcopy(state) if isinstance(state, dict) else {}
    updated.setdefault("schema_version", 1)
    cycles = _cycles_from_state(updated)

    if not isinstance(snapshot, dict) or not snapshot.get("valid_page"):
        return updated, {
            "status": "not_validated",
            "reason": "Progression page or Reward Chain counter was not verified.",
        }

    cycle_id = str(snapshot.get("cycle_id") or "")
    cycle_start = str(snapshot.get("cycle_start_date") or "")
    try:
        current_count = int(snapshot["current_count"])
        next_milestone = int(snapshot["next_milestone"])
        date.fromisoformat(cycle_start)
    except (KeyError, TypeError, ValueError):
        return updated, {
            "status": "not_validated",
            "reason": "Progression counter fields are invalid.",
        }
    if not cycle_id or not player_id or current_count < 0 or next_milestone <= 0:
        return updated, {
            "status": "not_validated",
            "reason": "Progression counter fields are incomplete.",
        }

    cycle = cycles.setdefault(cycle_id, {"players": {}})
    players = _players_for_cycle(cycle)
    entry = players.get(player_id)
    if not isinstance(entry, dict):
        entry = None

    if entry is None and _has_prior_cycle_entry(cycles, player_id, cycle_id) and not is_new_account:
        entry = {
            "baseline_status": "active",
            "baseline_count": 0,
            "baseline_date": cycle_start,
            "tracking_start_date": cycle_start,
            "baseline_source": "existing_account_at_cycle_start",
        }
        players[player_id] = entry

    if entry is None:
        if is_new_account:
            pending_source = "new_account_join_day"
        else:
            pending_source = "first_observation_after_rollout"
        entry = {
            "baseline_status": "pending_baseline",
            "first_seen_date": reset_date.isoformat(),
            "baseline_source": pending_source,
        }
        players[player_id] = entry

    if entry.get("baseline_status") == "pending_baseline":
        if not store_rewards_fully_checked:
            entry["last_pending_observation"] = reset_date.isoformat()
            return updated, {
                "status": "pending_baseline",
                "current_count": current_count,
                "next_milestone": next_milestone,
                "expected_minimum": None,
                "reason": "Waiting for all three daily Store rewards to be portal-confirmed for the baseline.",
            }

        entry.update({
            "baseline_status": "active",
            "baseline_count": current_count,
            "baseline_date": reset_date.isoformat(),
            "tracking_start_date": (reset_date + timedelta(days=1)).isoformat(),
            "baseline_source": (
                "new_account_join_day_post_store_check"
                if is_new_account and entry.get("baseline_source") == "new_account_join_day"
                else "first_verified_observation_after_rollout"
            ),
        })

    if entry.get("baseline_status") != "active":
        return updated, {
            "status": "pending_baseline",
            "current_count": current_count,
            "next_milestone": next_milestone,
            "expected_minimum": None,
        }

    try:
        baseline_count = int(entry.get("baseline_count", 0))
        tracking_start = date.fromisoformat(str(entry.get("tracking_start_date")))
    except (TypeError, ValueError):
        return updated, {
            "status": "not_validated",
            "current_count": current_count,
            "next_milestone": next_milestone,
            "expected_minimum": None,
            "reason": "Stored Grenade baseline is invalid.",
        }

    days_tracked = eligible_cycle_days(tracking_start, reset_date)
    expected_minimum = baseline_count + DAILY_GRENADE_MINIMUM * days_tracked
    previous_count = entry.get("last_observed_count")
    regressed = False
    if previous_count is not None:
        try:
            regressed = current_count < int(previous_count)
        except (TypeError, ValueError):
            regressed = False

    if regressed:
        status = "counter_regressed"
    elif current_count < expected_minimum:
        status = "below_minimum"
    else:
        status = "pass"

    entry["last_observed_count"] = current_count
    entry["last_observed_date"] = reset_date.isoformat()
    entry["last_observed_at_ist"] = snapshot.get("observed_at_ist")

    result = {
        "status": status,
        "cycle_id": cycle_id,
        "current_count": current_count,
        "next_milestone": next_milestone,
        "remaining_to_next_milestone": max(0, next_milestone - current_count),
        "baseline_count": baseline_count,
        "baseline_date": entry.get("baseline_date"),
        "tracking_start_date": tracking_start.isoformat(),
        "baseline_source": entry.get("baseline_source"),
        "eligible_days": days_tracked,
        "expected_minimum": expected_minimum,
        "surplus": max(0, current_count - expected_minimum),
        "deficit": max(0, expected_minimum - current_count),
        "historical_gap": (
            entry.get("baseline_source") == "first_verified_observation_after_rollout"
        ),
        "historical_gap_from": entry.get("join_date"),
    }
    return updated, result


def load_grenade_balance_state(path: str = "grenade_balance_baselines.json") -> dict[str, Any]:
    import json
    from pathlib import Path

    file_path = Path(path)
    if not file_path.exists():
        return {"schema_version": 1, "cycles": {}}
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("cycles", {}), dict):
            data.setdefault("schema_version", 1)
            return data
    except (OSError, json.JSONDecodeError):
        pass
    raise ValueError(f"{path} exists but is not a valid Grenade baseline state file.")


def save_grenade_balance_state(
    state: dict[str, Any],
    path: str = "grenade_balance_baselines.json",
) -> None:
    import json
    from pathlib import Path

    file_path = Path(path)
    file_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
