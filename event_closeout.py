"""One-off Halloween 2026 closeout sweep and expiry finalizer.

The sweep reuses master_claimer.process_player_promotions_only(), which checks
live Store promotions and configured event rewards but deliberately skips Daily,
standard Store cards, Progression, and Loyalty. The dry-run mode is read-only.

This module keeps pure state helpers importable without importing Selenium or
master_claimer, so its unit tests can run offline.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
PLAYER_FILE = ROOT / "players.csv"
CONFIG_FILE = ROOT / "event_rewards_config.json"
ARTIFACT_DIR = ROOT / "run_artifacts"
EVENT_SLUG = "halloween-2026"
IST = timezone(timedelta(hours=5, minutes=30))


def _epoch(value: Any) -> int | None:
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


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _parse_ist(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=IST)
        return parsed.astimezone(IST)
    except (TypeError, ValueError, OverflowError):
        return None


def load_program(config: dict[str, Any]) -> dict[str, Any]:
    """Return the enabled Halloween programme or fail closed."""
    if not isinstance(config, dict) or not config.get("enabled", False):
        raise ValueError("Event rewards are globally disabled in event_rewards_config.json")
    matches = [
        item for item in config.get("programs", [])
        if isinstance(item, dict)
        and str(item.get("slug", "")).strip().strip("/") == EVENT_SLUG
    ]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one {EVENT_SLUG!r} programme in event_rewards_config.json")
    program = dict(matches[0])
    if not program.get("enabled", False):
        raise ValueError("Halloween programme is disabled in event_rewards_config.json")
    if not str(program.get("program_id") or "").strip():
        raise ValueError("Halloween programme is missing program_id")
    end_at = _epoch(program.get("end_at"))
    if end_at is None:
        raise ValueError("Halloween programme has an invalid or missing end_at timestamp")
    start_at = _epoch(program.get("start_at"))
    program["start_at_epoch"] = start_at
    program["end_at_epoch"] = end_at
    return program


def load_roster(path: str | Path = PLAYER_FILE) -> list[tuple[str, bool]]:
    """Load and validate the configured player roster, preserving CSV order."""
    roster: list[tuple[str, bool]] = []
    with Path(path).open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "player_id" not in reader.fieldnames:
            raise ValueError("players.csv must contain a player_id column")
        for row in reader:
            pid = str(row.get("player_id") or "").strip()
            if not pid:
                continue
            loyalty = str(row.get("has_loyalty") or "").strip().lower() in {"true", "yes", "1"}
            roster.append((pid, loyalty))
    ids = [pid for pid, _ in roster]
    if not ids:
        raise ValueError("players.csv contains no player IDs")
    if len(ids) != len(set(ids)):
        raise ValueError("players.csv contains duplicate player IDs; refusing the closeout")
    return roster


def expiry_label(end_at_epoch: int) -> str:
    return datetime.fromtimestamp(end_at_epoch, timezone.utc).astimezone(IST).strftime("%d %b %Y, %I:%M %p IST")


def eligible_unredeemed_tiers(tiers: list[dict[str, Any]] | None, balance: Any) -> list[dict[str, Any]]:
    """Find tiers whose threshold was met but which were not portal-confirmed Delivered."""
    if not isinstance(balance, (int, float)):
        return []
    outstanding: list[dict[str, Any]] = []
    for tier in tiers or []:
        if not isinstance(tier, dict):
            continue
        threshold = _safe_int(tier.get("threshold"), -1)
        status = str(tier.get("status") or "unknown").strip().lower()
        if threshold >= 0 and threshold <= int(balance) and status != "delivered":
            outstanding.append({
                "threshold": threshold,
                "status": status,
                "reward": list(tier.get("reward", []) or []),
            })
    return outstanding


def build_closeout_snapshot(event: dict[str, Any], checked_at: str, end_at_epoch: int) -> dict[str, Any]:
    """Create a compact, durable pre-expiry snapshot from one live event result."""
    tiers = [dict(x) for x in (event.get("tiers", []) or []) if isinstance(x, dict)]
    balance = event.get("balance")
    outstanding = eligible_unredeemed_tiers(tiers, balance)
    return {
        "checked_at": checked_at,
        "event_end_at": end_at_epoch,
        "balance": balance,
        "next_threshold": event.get("next_threshold"),
        "tiers_total": _safe_int(event.get("tiers_total"), len(tiers)),
        "tiers_delivered": _safe_int(event.get("tiers_delivered"), 0),
        "tiers": tiers,
        "claims_this_run": [dict(x) for x in (event.get("claims_this_run", []) or []) if isinstance(x, dict)],
        "eligible_unredeemed_tiers": outstanding,
        "retry_required": bool(event.get("retry_required")),
        "error": event.get("error"),
        "event_status": str(event.get("status") or "unknown"),
    }


def record_closeout_result(
    history: dict[str, Any],
    player_id: str,
    program: dict[str, Any],
    result: dict[str, Any],
    checked_at: str,
) -> dict[str, Any]:
    """Append this account's sweep attempt without disturbing the claim ledger."""
    player = history.setdefault(player_id, {})
    if not isinstance(player, dict):
        player = history[player_id] = {}
    event_history = player.setdefault("event_rewards", {})
    if not isinstance(event_history, dict):
        event_history = player["event_rewards"] = {}

    program_id = str(program["program_id"])
    entry = event_history.get(program_id, {})
    if not isinstance(entry, dict):
        entry = {}
    attempts = entry.get("closeout_attempts", [])
    attempts = list(attempts) if isinstance(attempts, list) else []

    event_results = result.get("event_rewards", []) if isinstance(result, dict) else []
    event = next((x for x in event_results if isinstance(x, dict) and str(x.get("program_id")) == program_id), None)
    promotions = result.get("promotions_observed", []) if isinstance(result, dict) else []
    promotion_claims = sum(1 for p in promotions if isinstance(p, dict) and p.get("status") == "claimed_this_run")

    if event is not None:
        snapshot = build_closeout_snapshot(event, checked_at, program["end_at_epoch"])
        promotions = [dict(p) for p in (promotions or []) if isinstance(p, dict)]
        unconfirmed_promotions = [
            p for p in promotions
            if str(p.get("status") or "").lower() in {"claim_unconfirmed", "click_failed", "available", "popup_blocked"}
        ]
        promotion_scan_failed = bool(result.get("promotion_detection_failed"))
        snapshot["promotion_scan_failed"] = promotion_scan_failed
        snapshot["unconfirmed_promotions"] = [
            {"title": str(p.get("title") or "Promotion"), "status": str(p.get("status") or "unknown")}
            for p in unconfirmed_promotions
        ]
        snapshot["promotions_claimed_this_run"] = [
            {"title": str(p.get("title") or "Promotion"), "status": str(p.get("status") or "unknown")}
            for p in promotions if p.get("status") == "claimed_this_run"
        ]
        entry["closeout_snapshot"] = snapshot
        attention_required = bool(
            snapshot["retry_required"]
            or snapshot["eligible_unredeemed_tiers"]
            or promotion_scan_failed
            or unconfirmed_promotions
        )
        attempt_status = "attention_required" if attention_required else "checked"
        attempt = {
            "checked_at": checked_at,
            "status": attempt_status,
            "balance": snapshot["balance"],
            "tiers_delivered": snapshot["tiers_delivered"],
            "tiers_total": snapshot["tiers_total"],
            "eligible_unredeemed_count": len(snapshot["eligible_unredeemed_tiers"]),
            "retry_required": snapshot["retry_required"],
            "promotion_claims": promotion_claims,
            "promotion_scan_failed": promotion_scan_failed,
            "unconfirmed_promotions": len(unconfirmed_promotions),
            "error": snapshot["error"] or ("Store promotion scan failed or a temporary promotion was not confirmed" if attention_required and (promotion_scan_failed or unconfirmed_promotions) else None),
        }
        entry["closeout_status"] = attempt_status
    else:
        reason = str((result or {}).get("fail_reason") or (result or {}).get("status") or "Event page was not checked")
        attempt_status = "attention_required"
        attempt = {
            "checked_at": checked_at,
            "status": attempt_status,
            "balance": None,
            "tiers_delivered": None,
            "tiers_total": None,
            "eligible_unredeemed_count": None,
            "retry_required": True,
            "promotion_claims": promotion_claims,
            "error": reason[:240],
        }
        entry["closeout_status"] = attempt_status

    attempts.append(attempt)
    entry["closeout_attempts"] = attempts[-4:]
    entry["closeout_last_checked_at"] = checked_at
    entry["closeout_program_end_at"] = program["end_at_epoch"]
    entry["name"] = event.get("name") if event else entry.get("name", program.get("name"))
    entry["slug"] = program.get("slug")
    entry["end_at"] = program["end_at_epoch"]
    # Do not replace entry["claim_ledger"]: confirmed tier history is authoritative.
    event_history[program_id] = entry
    return attempt


def finalize_expired_history(
    history: dict[str, Any],
    player_ids: list[str],
    program: dict[str, Any],
    now_iso: str,
    now_epoch: int,
) -> dict[str, Any]:
    """Mark the event expired while preserving all prior claims and closeout evidence."""
    end_at = int(program["end_at_epoch"])
    if now_epoch < end_at:
        raise ValueError(f"Refusing to finalize before event expiry ({expiry_label(end_at)})")

    completed = 0
    missing = 0
    attention = 0
    delivered_total = 0
    outstanding_total = 0
    player_rows: list[dict[str, Any]] = []

    for player_id in player_ids:
        player = history.setdefault(player_id, {})
        if not isinstance(player, dict):
            player = history[player_id] = {}
        events = player.setdefault("event_rewards", {})
        if not isinstance(events, dict):
            events = player["event_rewards"] = {}
        entry = events.get(str(program["program_id"]), {})
        if not isinstance(entry, dict):
            entry = {}

        snapshot = entry.get("closeout_snapshot")
        snapshot_time = _parse_ist(snapshot.get("checked_at")) if isinstance(snapshot, dict) else None
        snapshot_valid = bool(snapshot_time and int(snapshot_time.timestamp()) < end_at)
        attempts = entry.get("closeout_attempts", [])
        latest_attempt = attempts[-1] if isinstance(attempts, list) and attempts else {}
        latest_attempt_attention = (
            isinstance(latest_attempt, dict)
            and latest_attempt.get("status") == "attention_required"
        )
        if snapshot_valid:
            completed += 1
            delivered = _safe_int(snapshot.get("tiers_delivered"), 0)
            total = _safe_int(snapshot.get("tiers_total"), 0)
            balance = snapshot.get("balance")
            outstanding = eligible_unredeemed_tiers(snapshot.get("tiers", []), balance)
            # The final attempt must also be clean. A failed last login/scan cannot
            # be hidden by an older successful snapshot from the first sweep.
            outstanding_total += len(outstanding)
            delivered_total += delivered
            status = "completed" if (
                not outstanding
                and not snapshot.get("retry_required")
                and not snapshot.get("promotion_scan_failed")
                and not snapshot.get("unconfirmed_promotions")
                and not latest_attempt_attention
            ) else "attention_required"
            if status == "attention_required":
                attention += 1
            entry["closeout_status"] = status
            entry["final_balance"] = balance
            entry["final_tiers_total"] = total
            entry["final_tiers_delivered"] = delivered
            entry["unredeemed_tiers_at_expiry"] = outstanding
            entry["final_snapshot_checked_at"] = snapshot.get("checked_at")
        else:
            missing += 1
            attention += 1
            entry["closeout_status"] = "closeout_missing"
            entry["final_balance"] = None
            entry["final_tiers_total"] = None
            entry["final_tiers_delivered"] = None
            entry["unredeemed_tiers_at_expiry"] = []
            entry["final_snapshot_checked_at"] = None

        entry["status_before_expiry_finalization"] = entry.get("status")
        entry["status"] = "expired"
        entry["expired_at"] = now_iso
        entry["event_end_at"] = end_at
        entry["event_end_at_ist"] = expiry_label(end_at)
        # Never delete or rewrite claim_ledger; it remains the durable claim history.
        events[str(program["program_id"])] = entry
        player_rows.append({
            "player_id_last4": player_id[-4:],
            "closeout_status": entry["closeout_status"],
            "balance": entry.get("final_balance"),
            "tiers_delivered": entry.get("final_tiers_delivered"),
            "tiers_total": entry.get("final_tiers_total"),
            "unredeemed_tiers": entry.get("unredeemed_tiers_at_expiry", []),
        })

    return {
        "program_id": str(program["program_id"]),
        "event_name": str(program.get("name") or "Halloween event"),
        "event_end_at": end_at,
        "event_end_at_ist": expiry_label(end_at),
        "finalized_at": now_iso,
        "roster_count": len(player_ids),
        "closeout_snapshots_found": completed,
        "closeout_snapshots_missing": missing,
        "accounts_requiring_attention": attention,
        "tiers_delivered_across_snapshots": delivered_total,
        "eligible_unredeemed_tiers": outstanding_total,
        "players": player_rows,
    }


def _read_json(path: Path, default: Any) -> Any:
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def _mask_player(result: dict[str, Any]) -> str:
    display = str(result.get("display_name") or "").strip()
    if display:
        return display[:48]
    pid = str(result.get("pid") or "")
    return f"Player …{pid[-4:]}" if pid else "Unknown player"


def _email_html(title: str, subtitle: str, metrics: list[tuple[str, Any]], details: list[tuple[str, str]]) -> str:
    metric_html = "".join(
        "<div style='background:#f3f6fb;border:1px solid #e2e8f0;border-radius:10px;padding:11px 12px;margin-bottom:8px;width:100%;'>"
        f"<div style='font-size:22px;line-height:1.2;font-weight:700;color:#172554;'>{html.escape(str(value))}</div>"
        f"<div style='font-size:12px;color:#475569;margin-top:5px;'>{html.escape(str(label))}</div></div>"
        for label, value in metrics
    )
    if details:
        detail_html = "".join(
            "<div style='padding:10px 0;border-bottom:1px solid #e5e7eb;'>"
            f"<div style='font-weight:700;color:#111827;'>{html.escape(label)}</div>"
            f"<div style='font-size:13px;color:#475569;margin-top:3px;overflow-wrap:anywhere;'>{html.escape(value)}</div></div>"
            for label, value in details
        )
    else:
        detail_html = "<div style='padding:12px;background:#f0fdf4;border-radius:8px;color:#166534;'>No unresolved accounts were identified.</div>"
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "</head><body style='margin:0;padding:12px;background:#f1f5f9;font-family:Arial,Helvetica,sans-serif;color:#111827;'>"
        "<div style='max-width:680px;margin:0 auto;background:#ffffff;border:1px solid #e2e8f0;border-radius:14px;overflow:hidden;'>"
        "<div style='padding:20px;background:#1e3a8a;color:#ffffff;'>"
        f"<div style='font-size:11px;letter-spacing:1px;font-weight:700;text-transform:uppercase;'>CS Rewards Bot · Halloween 2026</div>"
        f"<h1 style='font-size:21px;line-height:1.25;margin:9px 0 5px;'>{html.escape(title)}</h1>"
        f"<div style='font-size:13px;line-height:1.5;color:#dbeafe;'>{html.escape(subtitle)}</div></div>"
        f"<div style='padding:16px;'>{metric_html}</div>"
        f"<div style='padding:0 16px 16px;'><h2 style='font-size:15px;margin:3px 0 8px;'>Attention / exceptions</h2>{detail_html}</div>"
        "<div style='padding:13px 16px;background:#f8fafc;color:#64748b;font-size:11px;line-height:1.5;'>"
        "This is an event-only closeout report. Daily rewards, standard Store cards, Progression and Loyalty are not part of this run."
        "</div></div></body></html>"
    )


def _write_run_artifacts(name: str, payload: dict[str, Any], email_html: str | None = None) -> None:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    _write_json(ARTIFACT_DIR / f"halloween_closeout_{name}.json", payload)
    if email_html is not None:
        (ARTIFACT_DIR / f"halloween_closeout_{name}.html").write_text(email_html, encoding="utf-8")


def preflight() -> int:
    config = _read_json(CONFIG_FILE, {})
    program = load_program(config)
    roster = load_roster()
    now = int(time.time())
    now_ist = datetime.now(timezone.utc).astimezone(IST)
    if program.get("start_at_epoch") is not None and now < program["start_at_epoch"]:
        raise ValueError("Halloween programme has not started yet")
    state = "ACTIVE" if now < program["end_at_epoch"] else "EXPIRED"
    print("HALLOWEEN_CLOSEOUT_PREFLIGHT=PASS")
    print(f"Programme: {program.get('name')} ({program['program_id']})")
    print(f"Status: {state}")
    print(f"Current IST: {now_ist:%d %b %Y %I:%M:%S %p IST}")
    print(f"Expiry IST: {expiry_label(program['end_at_epoch'])}")
    print(f"Roster entries: {len(roster)}; duplicate IDs: 0")
    print("Live claims: NONE")
    if state != "ACTIVE":
        print("WARNING: the configured Halloween programme has already expired; a live sweep is not allowed.")
        return 2
    return 0


def dry_run(player_id: str | None = None, limit: int = 1) -> int:
    """Read-only browser check: login, inspect Store cards and Halloween state; never click rewards."""
    config = _read_json(CONFIG_FILE, {})
    program = load_program(config)
    now = int(time.time())
    if now >= program["end_at_epoch"]:
        raise ValueError("Dry-run blocked: the Halloween event has expired")
    roster = load_roster()
    if player_id:
        selected = [(pid, loyalty) for pid, loyalty in roster if pid == player_id]
        if not selected:
            raise ValueError("Requested dry-run player_id is not present in players.csv")
    else:
        selected = roster[:max(1, limit)]

    from master_claimer import (
        bypass_cloudflare,
        capture_display_name,
        create_driver,
        discover_promotional_cards,
        login_to_hub,
        log,
    )
    from event_rewards import _dismiss_event_promotion_popup, _read_page_state
    from reward_popup_guard import dismiss_all_safe_popups

    rows: list[dict[str, Any]] = []
    failures = 0
    for pid, _has_loyalty in selected:
        driver = None
        row: dict[str, Any] = {"player_id_last4": pid[-4:], "login": "not_attempted"}
        try:
            driver = create_driver()
            if not login_to_hub(driver, pid):
                row["login"] = "failed"
                row["error"] = "login_not_confirmed"
                failures += 1
                rows.append(row)
                continue
            row["login"] = "success"
            row["display_name"] = capture_display_name(driver)

            driver.get("https://hub.vertigogames.co/store")
            bypass_cloudflare(driver)
            time.sleep(2)
            popup_ok, popup_reason = dismiss_all_safe_popups(driver, log=log)
            if not popup_ok:
                row["store_scan_status"] = "popup_blocked"
                row["store_scan_error"] = popup_reason
                row["store_promotions_seen"] = 0
                row["store_claimable_seen"] = 0
                row["store_promotion_titles"] = []
                failures += 1
            else:
                driver._promotion_discovery_failed = False
                promos = discover_promotional_cards(driver)
                row["store_scan_status"] = "read_only_inspection_passed"
                row["store_promotions_seen"] = len(promos)
                row["store_claimable_seen"] = sum(1 for p in promos if p.get("state") == "available" and not p.get("disabled"))
                row["store_promotion_titles"] = [str(p.get("title") or "Promotion") for p in promos[:12]]
                if bool(getattr(driver, "_promotion_discovery_failed", False)):
                    row["store_scan_status"] = "scan_failed"
                    failures += 1

            driver.get(f"https://hub.vertigogames.co/{str(program['slug']).strip('/')}")
            bypass_cloudflare(driver)
            time.sleep(2)
            popup_ok, popup_reason = _dismiss_event_promotion_popup(driver, log)
            if not popup_ok:
                row["event_status"] = "popup_blocked"
                row["error"] = popup_reason
                failures += 1
            else:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(0.5)
                driver.execute_script("window.scrollTo(0, 0);")
                time.sleep(0.3)
                state = _read_page_state(driver)
                outstanding = eligible_unredeemed_tiers(state.get("tiers", []), state.get("balance"))
                row.update({
                    "event_status": "read_only_inspection_passed",
                    "balance": state.get("balance"),
                    "next_threshold": state.get("next_threshold"),
                    "tiers_total": len(state.get("tiers", [])),
                    "tiers_delivered": sum(1 for t in state.get("tiers", []) if t.get("status") == "delivered"),
                    "currently_eligible_unredeemed": outstanding,
                })
                expected = program.get("expected_tier_count")
                if expected is not None and len(state.get("tiers", [])) != int(expected):
                    row["event_status"] = "tier_count_mismatch"
                    row["error"] = f"Detected {len(state.get('tiers', []))}/{expected} configured tiers"
                    failures += 1
        except Exception as exc:
            row["event_status"] = "error"
            row["error"] = f"{type(exc).__name__}: {str(exc)[:180]}"
            failures += 1
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass
        rows.append(row)
        log(f"🔎 Read-only closeout inspection finished for …{pid[-4:]}")

    payload = {
        "mode": "dry_run",
        "created_at_ist": datetime.now(timezone.utc).astimezone(IST).isoformat(),
        "event": program.get("name"),
        "event_end_at_ist": expiry_label(program["end_at_epoch"]),
        "accounts_inspected": len(rows),
        "failures": failures,
        "claims_attempted": 0,
        "player_results": rows,
    }
    _write_run_artifacts("dry_run", payload)
    print(f"HALLOWEEN_CLOSEOUT_DRY_RUN_ACCOUNTS={len(rows)}")
    print("HALLOWEEN_CLOSEOUT_DRY_RUN_CLAIMS=0")
    print(f"HALLOWEEN_CLOSEOUT_DRY_RUN_FAILURES={failures}")
    return 1 if failures else 0


def run_sweep() -> int:
    """Run an event-only live sweep for the roster, preserving account-level snapshots."""
    config = _read_json(CONFIG_FILE, {})
    program = load_program(config)
    start_epoch = int(time.time())
    if start_epoch >= program["end_at_epoch"]:
        raise ValueError("Live closeout sweep blocked: Halloween has already expired")
    if program.get("start_at_epoch") is not None and start_epoch < program["start_at_epoch"]:
        raise ValueError("Live closeout sweep blocked: Halloween has not started")

    from master_claimer import (
        get_ist_time,
        is_new_id,
        load_bot_meta,
        load_claim_history,
        process_player_promotions_only,
        save_claim_history,
        send_email,
        log,
    )

    roster = load_roster()
    meta = load_bot_meta()
    run_started = get_ist_time().isoformat()
    results: list[dict[str, Any]] = []
    stopped_for_expiry = False

    for index, (pid, _has_loyalty) in enumerate(roster, start=1):
        if int(time.time()) >= program["end_at_epoch"]:
            stopped_for_expiry = True
            log("⛔ Event expiry reached during the sweep; remaining accounts will not be opened for claims.")
            break

        log(f"🎃 Halloween closeout sweep: account {index}/{len(roster)} (…{pid[-4:]})")
        try:
            result = process_player_promotions_only(pid, is_new_id(pid, meta), "Halloween Closeout")
            if not isinstance(result, dict):
                result = {"pid": pid, "status": "Error", "fail_reason": "processor_returned_no_result", "event_rewards": []}
        except Exception as exc:
            result = {
                "pid": pid,
                "status": "Error",
                "fail_reason": f"{type(exc).__name__}: {str(exc)[:180]}",
                "event_rewards": [],
                "promotions_observed": [],
                "retry_required": True,
            }
        result["pid"] = pid
        result["processing_mode"] = "halloween_closeout"
        checked_at = get_ist_time().isoformat()
        # process_player_promotions_only() persists event claim_ledger changes itself.
        # Reload after that call so this metadata update never overwrites those claims.
        history = load_claim_history()
        attempt = record_closeout_result(history, pid, program, result, checked_at)
        save_claim_history(history)
        result["closeout_attempt"] = attempt
        results.append(result)
        log(f"🎃 Closeout status for …{pid[-4:]}: {attempt['status']}")

    finished_at = get_ist_time().isoformat()
    program_id = str(program["program_id"])
    event_claims = sum(
        len((event or {}).get("claims_this_run", []) or [])
        for result in results
        for event in [next((e for e in result.get("event_rewards", []) or []
                            if isinstance(e, dict) and str(e.get("program_id")) == program_id), None)]
    )
    promo_claims = sum(
        1 for result in results
        for promo in result.get("promotions_observed", []) or []
        if isinstance(promo, dict) and promo.get("status") == "claimed_this_run"
    )
    checked = sum(1 for result in results if result.get("closeout_attempt", {}).get("balance") is not None)
    outstanding = sum(
        _safe_int(result.get("closeout_attempt", {}).get("eligible_unredeemed_count"), 0)
        for result in results
    )
    attention_rows: list[tuple[str, str]] = []
    for result in results:
        attempt = result.get("closeout_attempt", {})
        if attempt.get("status") != "checked":
            reason = str(attempt.get("error") or result.get("fail_reason") or attempt.get("status") or "Needs review")
            attention_rows.append((_mask_player(result), reason[:240]))
    if stopped_for_expiry:
        attention_rows.append(("Roster remainder", "Event expiry was reached before all configured accounts could be checked"))

    summary = {
        "mode": "sweep",
        "event": program.get("name"),
        "program_id": program_id,
        "event_end_at_ist": expiry_label(program["end_at_epoch"]),
        "started_at_ist": run_started,
        "finished_at_ist": finished_at,
        "roster_count": len(roster),
        "accounts_processed": len(results),
        "accounts_with_event_snapshot": checked,
        "event_tiers_claimed_this_run": event_claims,
        "temporary_store_promotions_claimed_this_run": promo_claims,
        "eligible_unredeemed_tiers_after_sweep": outstanding,
        "accounts_requiring_attention": len(attention_rows),
        "stopped_for_expiry": stopped_for_expiry,
        "player_results": [
            {
                "player_id_last4": str(r.get("pid", ""))[-4:],
                "display_name": r.get("display_name"),
                "status": r.get("status"),
                "event_balance": (r.get("closeout_attempt") or {}).get("balance"),
                "event_tiers_claimed": len((next((e for e in r.get("event_rewards", []) or []
                    if isinstance(e, dict) and str(e.get("program_id")) == program_id), {}) or {}).get("claims_this_run", []) or []),
                "closeout_status": (r.get("closeout_attempt") or {}).get("status"),
            } for r in results
        ],
    }
    metrics = [
        ("Accounts processed", f"{len(results)}/{len(roster)}"),
        ("Event snapshots", checked),
        ("Halloween tiers claimed", event_claims),
        ("Temporary Store promotions claimed", promo_claims),
        ("Eligible tiers still unredeemed", outstanding),
        ("Accounts needing attention", len(attention_rows)),
    ]
    body = _email_html(
        "Pre-expiry sweep",
        f"Completed {finished_at} · Halloween expiry {expiry_label(program['end_at_epoch'])}",
        metrics,
        attention_rows,
    )
    _write_run_artifacts("sweep", summary, body)
    email_sent = send_email(body, f"🎃 CS Hub | Halloween Closeout Sweep | {len(results)}/{len(roster)} accounts | {event_claims} event tiers")
    summary["email_sent"] = bool(email_sent)
    _write_json(ARTIFACT_DIR / "halloween_closeout_sweep.json", summary)

    print(f"HALLOWEEN_CLOSEOUT_PROCESSED={len(results)}/{len(roster)}")
    print(f"HALLOWEEN_CLOSEOUT_EVENT_CLAIMS={event_claims}")
    print(f"HALLOWEEN_CLOSEOUT_UNREDEEMED={outstanding}")
    print(f"HALLOWEEN_CLOSEOUT_EMAIL={'SUCCESS' if email_sent else 'FAILED'}")
    return 1 if attention_rows or stopped_for_expiry or not email_sent else 0


def run_finalize() -> int:
    """After expiry, mark every roster account's event record expired and report gaps."""
    config = _read_json(CONFIG_FILE, {})
    program = load_program(config)
    now_epoch = int(time.time())
    if now_epoch < program["end_at_epoch"]:
        raise ValueError(f"Finalization is only allowed after {expiry_label(program['end_at_epoch'])}")
    roster = load_roster()

    from master_claimer import load_claim_history, save_claim_history, send_email, log

    history = load_claim_history()
    summary = finalize_expired_history(
        history,
        [pid for pid, _ in roster],
        program,
        datetime.now(timezone.utc).astimezone(IST).isoformat(),
        now_epoch,
    )
    save_claim_history(history)

    attention_rows: list[tuple[str, str]] = []
    for row in summary["players"]:
        if row["closeout_status"] != "completed":
            outstanding = row.get("unredeemed_tiers") or []
            if outstanding:
                detail = "Eligible but not confirmed Delivered: " + ", ".join(str(x.get("threshold")) for x in outstanding)
            elif row["closeout_status"] == "closeout_missing":
                detail = "No valid pre-expiry snapshot was saved for this account"
            else:
                detail = str(row["closeout_status"])
            attention_rows.append((f"Player …{row['player_id_last4']}", detail))

    metrics = [
        ("Roster accounts", summary["roster_count"]),
        ("Pre-expiry snapshots", summary["closeout_snapshots_found"]),
        ("Missing snapshots", summary["closeout_snapshots_missing"]),
        ("Tiers delivered at closeout", summary["tiers_delivered_across_snapshots"]),
        ("Eligible tiers unredeemed", summary["eligible_unredeemed_tiers"]),
        ("Accounts needing attention", summary["accounts_requiring_attention"]),
    ]
    body = _email_html(
        "Event expired · final status recorded",
        f"Expired {summary['event_end_at_ist']} · Finalized {summary['finalized_at']}",
        metrics,
        attention_rows,
    )
    _write_run_artifacts("finalize", summary, body)
    email_sent = send_email(body, f"🎃 CS Hub | Halloween Expired | {summary['closeout_snapshots_found']}/{summary['roster_count']} snapshots | {summary['eligible_unredeemed_tiers']} unredeemed")
    summary["email_sent"] = bool(email_sent)
    _write_json(ARTIFACT_DIR / "halloween_closeout_finalize.json", summary)

    log("🎃 Halloween event finalization saved; prior claim ledgers were preserved.")
    print(f"HALLOWEEN_CLOSEOUT_EXPIRED=TRUE")
    print(f"HALLOWEEN_CLOSEOUT_SNAPSHOTS={summary['closeout_snapshots_found']}/{summary['roster_count']}")
    print(f"HALLOWEEN_CLOSEOUT_UNREDEEMED={summary['eligible_unredeemed_tiers']}")
    print(f"HALLOWEEN_CLOSEOUT_EMAIL={'SUCCESS' if email_sent else 'FAILED'}")
    return 1 if attention_rows or not email_sent else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Halloween 2026 event closeout")
    parser.add_argument("--mode", required=True, choices=("preflight", "dry-run", "sweep", "finalize"))
    parser.add_argument("--player-id", default=None, help="Optional roster ID for one-account read-only dry-run")
    parser.add_argument("--limit", type=int, default=1, help="Maximum accounts for a read-only dry-run; default 1")
    args = parser.parse_args(argv)
    try:
        if args.mode == "preflight":
            return preflight()
        if args.mode == "dry-run":
            return dry_run(args.player_id, args.limit)
        if args.mode == "sweep":
            return run_sweep()
        if args.mode == "finalize":
            return run_finalize()
    except Exception as exc:
        print(f"HALLOWEEN_CLOSEOUT_ERROR={type(exc).__name__}: {str(exc)[:240]}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
