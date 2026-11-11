#!/usr/bin/env python3
"""Read-only reconstruction of claim-history transitions from Git snapshots."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

IST = timezone(timedelta(hours=5, minutes=30))
HISTORY_PATH = "claim_history.json"
META_PATH = "bot_meta.json"
PLAYERS_PATH = "players.csv"


class AuditError(RuntimeError):
    """Raised when the requested Git evidence cannot be read safely."""


def _git(repo_path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_path), *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        raise AuditError(f"git {' '.join(args[:3])} failed: {detail[:800]}")
    return result.stdout


def _normalise_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IST)
    return parsed.astimezone(IST)


def _iso_utc(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _mask_player_id(player_id: str) -> str:
    value = str(player_id)
    return f"…{value[-4:]}" if len(value) >= 4 else "…"


def _record_snapshots(
    repo_path: Path, ref: str, file_path: str
) -> Iterable[dict[str, Any]]:
    """Yield first-parent snapshots while streaming Git blob data."""
    _git(repo_path, "rev-parse", "--verify", f"{ref}^{{commit}}")
    log_output = _git(
        repo_path,
        "log",
        "--first-parent",
        "--full-history",
        "--reverse",
        "--format=%H%x09%ct%x09%s",
        ref,
        "--",
        file_path,
    )
    commits: list[dict[str, Any]] = []
    for line in log_output.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3 or len(parts[0]) != 40:
            continue
        try:
            commit_epoch = int(parts[1])
        except ValueError:
            continue
        commits.append(
            {
                "commit": parts[0],
                "commit_epoch": commit_epoch,
                "commit_time_utc": _iso_utc(commit_epoch),
                "subject": parts[2],
            }
        )
    if not commits:
        raise AuditError(f"No committed snapshots of {file_path!r} found on {ref!r}.")

    revisions = [f"{item['commit']}:{file_path}" for item in commits]
    resolved = _git(repo_path, "rev-parse", *revisions).splitlines()
    if len(resolved) != len(commits):
        raise AuditError(
            f"Could not resolve every {file_path} blob on {ref}; "
            f"commits={len(commits)}, blobs={len(resolved)}."
        )

    process = subprocess.Popen(
        ["git", "-C", str(repo_path), "cat-file", "--batch"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    assert process.stdin is not None and process.stdout is not None
    try:
        for item, blob_sha in zip(commits, resolved):
            process.stdin.write((blob_sha + "\n").encode("ascii"))
            process.stdin.flush()
            header = process.stdout.readline().decode("ascii", errors="replace").strip()
            fields = header.split()
            if len(fields) != 3 or fields[1] != "blob":
                raise AuditError(
                    f"Unexpected cat-file response for {file_path} at "
                    f"{item['commit'][:12]}: {header!r}"
                )
            try:
                size = int(fields[2])
            except ValueError as exc:
                raise AuditError(f"Invalid blob size in response {header!r}") from exc
            payload = process.stdout.read(size)
            terminator = process.stdout.read(1)
            if len(payload) != size or terminator != b"\n":
                raise AuditError(
                    f"Truncated Git blob for {file_path} at {item['commit'][:12]}."
                )
            try:
                data = json.loads(payload.decode("utf-8-sig"))
                if not isinstance(data, dict):
                    raise ValueError("top-level JSON value is not an object")
                item["data"] = data
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                item["data"] = None
                item["parse_error"] = f"{type(exc).__name__}: {exc}"
            item["blob_sha"] = blob_sha
            yield item
    finally:
        try:
            process.stdin.close()
        except Exception:
            pass
        process.wait()


def _read_roster(repo_path: Path, ref: str) -> list[str]:
    raw = _git(repo_path, "show", f"{ref}:{PLAYERS_PATH}")
    reader = csv.DictReader(io.StringIO(raw))
    if not reader.fieldnames or "player_id" not in reader.fieldnames:
        raise AuditError(f"{PLAYERS_PATH} at {ref} has no player_id column.")
    roster = [
        str(row.get("player_id") or "").strip()
        for row in reader
        if str(row.get("player_id") or "").strip()
    ]
    if len(roster) != len(set(roster)):
        raise AuditError(f"{PLAYERS_PATH} at {ref} contains duplicate player IDs.")
    return roster


def _state_records(history: dict[str, Any]):
    for player_id, player in history.items():
        if not isinstance(player, dict):
            continue
        daily = player.get("daily")
        if isinstance(daily, dict):
            yield player_id, "daily", "daily", daily

        store = player.get("store")
        if isinstance(store, dict):
            for reward_key, record in store.items():
                if not isinstance(record, dict):
                    continue
                if reward_key in {"reward_1", "reward_2", "reward_3"}:
                    category = "store_daily"
                elif reward_key == "reward_4":
                    category = "store_legacy_reward_4"
                else:
                    category = "store_other"
                yield player_id, category, reward_key, record

        for category in ("progression", "loyalty"):
            record = player.get(category)
            if isinstance(record, dict):
                yield player_id, category, category, record

        promotions = player.get("promotions")
        if isinstance(promotions, dict):
            for promotion_key, record in promotions.items():
                if isinstance(record, dict):
                    yield player_id, "promotions", str(promotion_key), record


def _classification(category: str, record: dict[str, Any]) -> tuple[str, str]:
    status = str(record.get("status") or "unknown")
    if status == "claimed":
        return "bot_confirmed", status

    # Older production code recorded Progression success using last_claim and
    # last_count without a status field. Recognise only that exact legacy shape.
    if (
        category == "progression"
        and "status" not in record
        and record.get("last_claim")
        and _safe_int(record.get("last_count")) > 0
    ):
        return "bot_confirmed_legacy_schema", "legacy_claimed_no_status"

    return "recorded_but_not_bot_confirmed", status


def _event_id(*parts: Any) -> str:
    raw = "|".join(str(part) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _summarise_record_events(
    history_snapshots: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    events: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    anomalies: list[dict[str, Any]] = []
    previous: dict[tuple[str, str, str], dict[str, Any]] = {}
    seen_events: set[tuple[str, str, str, str]] = set()
    seen_observations: set[tuple[str, str, str, str]] = set()
    seen_event_ledger: set[tuple[str, str, str, str, str]] = set()
    snapshot_count = 0
    first_snapshot: dict[str, Any] | None = None
    last_snapshot: dict[str, Any] | None = None
    malformed_snapshots = 0

    for snapshot in history_snapshots:
        snapshot_count += 1
        if first_snapshot is None:
            first_snapshot = snapshot
        last_snapshot = snapshot
        history = snapshot.get("data")
        if not isinstance(history, dict):
            malformed_snapshots += 1
            anomalies.append(
                {
                    "type": "malformed_history_snapshot",
                    "commit": snapshot.get("commit"),
                    "commit_time_utc": snapshot.get("commit_time_utc"),
                    "detail": snapshot.get("parse_error", "history snapshot is not an object"),
                }
            )
            continue

        current: dict[tuple[str, str, str], dict[str, Any]] = {}
        first = snapshot_count == 1
        commit = str(snapshot["commit"])
        commit_time = str(snapshot["commit_time_utc"])

        for player_id, category, identity, record in _state_records(history):
            marker = (str(player_id), category, identity)
            current[marker] = record
            old = previous.get(marker, {})
            old_claim = old.get("last_claim") if isinstance(old, dict) else None
            new_claim = record.get("last_claim")
            player_ref = _mask_player_id(str(player_id))
            label = (
                str(record.get("title") or identity)
                if category == "promotions"
                else identity
            )

            if new_claim:
                claim_text = str(new_claim).strip()
                old_text = str(old_claim).strip() if old_claim else None
                if first or claim_text != old_text:
                    classification, status = _classification(category, record)
                    dedupe = (str(player_id), category, identity, claim_text)
                    if dedupe not in seen_events:
                        seen_events.add(dedupe)
                        parsed_claim = _normalise_datetime(claim_text)
                        events.append(
                            {
                                "event_id": _event_id(*dedupe),
                                "account": player_ref,
                                "category": category,
                                "item": label,
                                "recorded_at": claim_text,
                                "recorded_at_valid": parsed_claim is not None,
                                "claim_units": (
                                    max(1, _safe_int(record.get("last_count"), 1))
                                    if category == "progression"
                                    else 1
                                ),
                                "classification": classification,
                                "record_status": status,
                                "first_seen": "baseline_snapshot" if first else "changed_in_snapshot",
                                "first_seen_commit": commit,
                                "first_seen_commit_time_utc": commit_time,
                            }
                        )
                        if category == "promotions":
                            events[-1]["campaign_id"] = record.get("campaign_id")
                        if parsed_claim is None:
                            anomalies.append(
                                {
                                    "type": "invalid_claim_timestamp",
                                    "account": player_ref,
                                    "category": category,
                                    "item": label,
                                    "value": claim_text,
                                    "commit": commit,
                                }
                            )
                        if old_claim and parsed_claim is not None:
                            old_parsed = _normalise_datetime(str(old_claim))
                            if old_parsed and parsed_claim < old_parsed:
                                anomalies.append(
                                    {
                                        "type": "claim_timestamp_regressed",
                                        "account": player_ref,
                                        "category": category,
                                        "item": label,
                                        "previous": str(old_claim),
                                        "current": claim_text,
                                        "commit": commit,
                                    }
                                )

            elif old_claim and not new_claim and not first:
                if str(record.get("status") or "") == "unconfirmed":
                    anomalies.append(
                        {
                            "type": "same_record_claim_cleared_as_unconfirmed",
                            "account": player_ref,
                            "category": category,
                            "item": label,
                            "previous_claim_timestamp": str(old_claim),
                            "commit": commit,
                            "note": "The earlier recorded event remains in the audit; this state change does not erase it.",
                        }
                    )

            old_status = str(old.get("status") or "") if isinstance(old, dict) else ""
            new_status = str(record.get("status") or "")
            if new_status == "unconfirmed" and old_status != "unconfirmed":
                anomalies.append(
                    {
                        "type": "record_became_unconfirmed",
                        "account": player_ref,
                        "category": category,
                        "item": label,
                        "commit": commit,
                        "last_attempt_at": record.get("last_attempt_at")
                        or record.get("last_unconfirmed_at"),
                    }
                )

            portal_observed_at = record.get("portal_observed_at")
            if portal_observed_at:
                observed_text = str(portal_observed_at).strip()
                old_observed = (
                    str(old.get("portal_observed_at")).strip()
                    if isinstance(old, dict) and old.get("portal_observed_at")
                    else None
                )
                if first or observed_text != old_observed:
                    dedupe = (str(player_id), category, identity, observed_text)
                    if dedupe not in seen_observations:
                        seen_observations.add(dedupe)
                        observations.append(
                            {
                                "account": player_ref,
                                "category": category,
                                "item": label,
                                "observed_at": observed_text,
                                "observed_at_valid": _normalise_datetime(observed_text) is not None,
                                "status": new_status or "unknown",
                                "first_seen": "baseline_snapshot" if first else "changed_in_snapshot",
                                "first_seen_commit": commit,
                                "first_seen_commit_time_utc": commit_time,
                                "note": "Portal-observed state; not counted as a bot-made claim.",
                            }
                        )

        for player_id, player in history.items():
            if not isinstance(player, dict):
                continue
            programs = player.get("event_rewards")
            if not isinstance(programs, dict):
                continue
            for program_id, program in programs.items():
                if not isinstance(program, dict):
                    continue
                ledger = program.get("claim_ledger")
                if not isinstance(ledger, list):
                    continue
                for claim in ledger:
                    if not isinstance(claim, dict):
                        continue
                    cycle = str(claim.get("cycle", ""))
                    threshold = str(claim.get("threshold", ""))
                    claimed_at = str(claim.get("claimed_at") or "").strip()
                    reward_text = json.dumps(
                        claim.get("reward", []), sort_keys=True, ensure_ascii=False
                    )
                    dedupe = (
                        str(player_id),
                        str(program_id),
                        cycle,
                        threshold,
                        claimed_at or reward_text,
                    )
                    if dedupe in seen_event_ledger:
                        continue
                    seen_event_ledger.add(dedupe)
                    verification = str(claim.get("verification") or "unknown").lower()
                    classification = (
                        "portal_confirmed_delivered"
                        if verification == "delivered"
                        else "recorded_but_not_portal_confirmed"
                    )
                    recorded_at = claimed_at or commit_time
                    events.append(
                        {
                            "event_id": _event_id(*dedupe),
                            "account": _mask_player_id(str(player_id)),
                            "category": "event_rewards",
                            "item": f"{program.get('name') or program_id} / threshold {threshold or 'unknown'}",
                            "program_id": str(program_id),
                            "cycle": cycle,
                            "threshold": threshold,
                            "recorded_at": recorded_at,
                            "recorded_at_valid": _normalise_datetime(recorded_at) is not None,
                            "claim_units": 1,
                            "classification": classification,
                            "record_status": verification,
                            "first_seen": "baseline_snapshot" if first else "ledger_entry_seen",
                            "first_seen_commit": commit,
                            "first_seen_commit_time_utc": commit_time,
                            "timestamp_quality": "claim_ledger" if claimed_at else "snapshot_commit_fallback",
                        }
                    )

        previous = current

    events.sort(
        key=lambda item: (
            _normalise_datetime(item.get("recorded_at")) or datetime.min.replace(tzinfo=IST),
            item.get("account", ""),
            item.get("category", ""),
            item.get("item", ""),
        )
    )
    observations.sort(key=lambda item: item.get("observed_at", ""))
    coverage = {
        "snapshot_count": snapshot_count,
        "malformed_snapshot_count": malformed_snapshots,
        "first_snapshot_commit": first_snapshot.get("commit") if first_snapshot else None,
        "first_snapshot_time_utc": first_snapshot.get("commit_time_utc") if first_snapshot else None,
        "last_snapshot_commit": last_snapshot.get("commit") if last_snapshot else None,
        "last_snapshot_time_utc": last_snapshot.get("commit_time_utc") if last_snapshot else None,
    }
    return events, observations, anomalies, coverage


def _extract_run_summaries(meta_snapshots: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for snapshot in meta_snapshots:
        meta = snapshot.get("data")
        if not isinstance(meta, dict):
            continue
        run = meta.get("last_run")
        if not isinstance(run, dict):
            continue
        timestamp = str(run.get("timestamp") or "").strip()
        if not timestamp:
            continue
        label = str(run.get("run_label") or "unknown")
        key = (timestamp, label)
        if key in seen:
            continue
        seen.add(key)
        runs.append(
            {
                "timestamp": timestamp,
                "run_label": label,
                "duration_seconds": max(0, _safe_int(run.get("duration_seconds"))),
                "accounts_processed": run.get("accounts_processed"),
                "configured_player_count": run.get("configured_player_count"),
                "claims_made": run.get("claims_made", run.get("total_claimed")),
                "per_type": run.get("per_type") if isinstance(run.get("per_type"), dict) else {},
                "actual_failures": run.get("actual_failures"),
                "unconfirmed_claims": run.get("unconfirmed_claims"),
                "source_commit": snapshot.get("commit"),
                "source_commit_time_utc": snapshot.get("commit_time_utc"),
            }
        )
    runs.sort(
        key=lambda item: _normalise_datetime(item["timestamp"])
        or datetime.min.replace(tzinfo=IST)
    )
    return runs


def _resolve_unconfirmed_flags(
    anomalies: list[dict[str, Any]],
    events: list[dict[str, Any]],
    observations: list[dict[str, Any]],
) -> dict[str, int]:
    """Resolve unconfirmed attempts using later bot claims or portal-observed state."""
    bot_confirmed_count = 0
    portal_observed_count = 0
    open_count = 0
    confirmed_events = [
        event for event in events
        if event.get("classification") in {
            "bot_confirmed", "bot_confirmed_legacy_schema", "portal_confirmed_delivered"
        }
    ]
    terminal_portal_states = {"portal_claimed", "claimed", "cooldown_detected"}
    claimed_observations = [
        observation for observation in observations
        if str(observation.get("status") or "").lower() in terminal_portal_states
    ]

    for anomaly in anomalies:
        if anomaly.get("type") != "record_became_unconfirmed":
            continue
        attempted_at = _normalise_datetime(anomaly.get("last_attempt_at"))
        later_evidence = []
        for event in confirmed_events:
            if event.get("account") != anomaly.get("account") or event.get("category") != anomaly.get("category"):
                continue
            event_at = _normalise_datetime(event.get("recorded_at"))
            if attempted_at and event_at and event_at > attempted_at:
                later_evidence.append((event_at, "bot", event))
        for observation in claimed_observations:
            if observation.get("account") != anomaly.get("account") or observation.get("category") != anomaly.get("category"):
                continue
            observed_at = _normalise_datetime(observation.get("observed_at"))
            if attempted_at and observed_at and observed_at > attempted_at:
                later_evidence.append((observed_at, "portal", observation))

        if later_evidence:
            evidence_at, evidence_type, evidence = min(later_evidence, key=lambda item: item[0])
            anomaly["resolution_lag_hours"] = round(
                (evidence_at - attempted_at).total_seconds() / 3600, 2
            )
            if evidence_type == "bot":
                anomaly["resolution_status"] = "subsequently_bot_confirmed"
                anomaly["resolved_by_claim_at"] = evidence.get("recorded_at")
                bot_confirmed_count += 1
            else:
                anomaly["resolution_status"] = "subsequently_portal_observed_claimed"
                anomaly["resolved_by_portal_observation_at"] = evidence.get("observed_at")
                anomaly["portal_observation_status"] = evidence.get("status")
                portal_observed_count += 1
        else:
            anomaly["resolution_status"] = "open_at_audit_cutoff"
            open_count += 1

    return {
        "unconfirmed_transitions_subsequently_confirmed": bot_confirmed_count,
        "unconfirmed_transitions_subsequently_portal_observed": portal_observed_count,
        "unconfirmed_transitions_open": open_count,
    }


def _run_window_integrity(
    runs: list[dict[str, Any]], events: list[dict[str, Any]]
) -> dict[str, int]:
    """Count overlapping run windows and claim events assigned to more than one run."""
    windows = []
    for index, run in enumerate(runs):
        end = _normalise_datetime(run.get("timestamp"))
        if end is None:
            continue
        start = end - timedelta(seconds=max(0, _safe_int(run.get("duration_seconds"))))
        windows.append((start, end, index))
    windows.sort(key=lambda item: (item[0], item[1]))

    overlap_pairs = 0
    for i, left in enumerate(windows):
        for right in windows[i + 1:]:
            if right[0] >= left[1]:
                break
            if max(left[0], right[0]) < min(left[1], right[1]):
                overlap_pairs += 1

    confirmed_events = [
        event for event in events
        if event.get("classification") in {
            "bot_confirmed", "bot_confirmed_legacy_schema", "portal_confirmed_delivered"
        }
    ]
    multiply_assigned = 0
    for event in confirmed_events:
        event_at = _normalise_datetime(event.get("recorded_at"))
        if event_at is not None:
            matches = sum(1 for start, end, _ in windows if start <= event_at <= end)
            if matches > 1:
                multiply_assigned += 1

    return {
        "overlapping_run_window_pairs": overlap_pairs,
        "claim_events_assigned_to_multiple_run_windows": multiply_assigned,
    }


def _reconcile_runs(
    runs: list[dict[str, Any]], events: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    confirmed = [
        event
        for event in events
        if event.get("classification") in {
            "bot_confirmed", "bot_confirmed_legacy_schema", "portal_confirmed_delivered"
        }
        and _normalise_datetime(event.get("recorded_at")) is not None
    ]
    output: list[dict[str, Any]] = []
    for run in runs:
        end = _normalise_datetime(run.get("timestamp"))
        if end is None:
            continue
        start = end - timedelta(seconds=int(run.get("duration_seconds") or 0))
        assigned = []
        for event in confirmed:
            event_time = _normalise_datetime(event.get("recorded_at"))
            if event_time is not None and start <= event_time <= end:
                assigned.append(event)
        actual = Counter()
        for event in assigned:
            category = event.get("category")
            if category in {
                "daily", "store_daily", "promotions", "progression", "loyalty", "event_rewards"
            }:
                actual[category] += max(1, _safe_int(event.get("claim_units"), 1))

        per_type = run.get("per_type") or {}
        comparisons = {}
        expected_fields = {
            "daily": "daily",
            "store_daily": "store_daily" if "store_daily" in per_type else "store",
            "promotions": "promotions",
            "progression": "progression",
            "loyalty": "loyalty",
            "event_rewards": "event_rewards",
        }
        for category, field in expected_fields.items():
            if field not in per_type:
                continue
            expected = _safe_int(per_type.get(field))
            observed = int(actual.get(category, 0))
            comparisons[category] = {
                "metadata_field": field,
                "metadata_count": expected,
                "history_transition_count": observed,
                "matched": expected == observed,
            }
        mismatches = [
            category for category, result in comparisons.items() if not result["matched"]
        ]
        output.append(
            {
                "timestamp": run["timestamp"],
                "run_label": run["run_label"],
                "duration_seconds": run["duration_seconds"],
                "accounts_processed": run.get("accounts_processed"),
                "metadata_claims_made": run.get("claims_made"),
                "comparisons": comparisons,
                "status": "matched" if not mismatches else "attention_required",
                "mismatches": mismatches,
                "note": (
                    "Compares claim-history transitions recorded within the run window "
                    "with bot_meta counters. This is an audit signal, not proof of "
                    "unrecorded portal activity."
                ),
            }
        )
    return output


def build_account_coverage(
    latest_history: dict[str, Any], roster_ids: list[str]
) -> dict[str, Any]:
    history_ids = {
        str(player_id)
        for player_id, record in latest_history.items()
        if isinstance(record, dict)
    }
    roster_set = set(roster_ids)
    return {
        "history_account_count": len(history_ids),
        "configured_roster_count": len(roster_set),
        "historical_only_count": len(history_ids - roster_set),
        "configured_accounts_missing_from_history_count": len(roster_set - history_ids),
        "historical_only_accounts": sorted(_mask_player_id(pid) for pid in history_ids - roster_set),
        "configured_accounts_missing_from_history": sorted(
            _mask_player_id(pid) for pid in roster_set - history_ids
        ),
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    scope = report["scope"]
    lines = [
        "# CS Rewards Bot — Historical Claim-History Audit",
        "",
        f"- Source ref: {report['source_ref']}",
        f"- Source commit: {report['source_commit_sha']}",
        f"- Generated: {report['generated_at_utc']}",
        f"- History snapshots inspected: **{scope['history_snapshots']['snapshot_count']}**",
        f"- First snapshot: {scope['history_snapshots']['first_snapshot_time_utc']}",
        f"- Latest snapshot: {scope['history_snapshots']['last_snapshot_time_utc']}",
        f"- Configured roster: **{scope['configured_roster_count']}**",
        "",
        "## Summary",
        "",
        "| Measure | Result |",
        "|---|---:|",
        f"| Recorded claim events | {summary['recorded_claim_events']} |",
        f"| Recorded claim units | {summary['recorded_claim_units']} |",
        f"| Portal observations (not claims) | {summary['portal_observations_not_counted_as_claims']} |",
        f"| Run metadata records | {summary['run_metadata_records']} |",
        f"| Run checks matched | {summary['run_checks_matched']} |",
        f"| Run checks requiring attention | {summary['run_checks_attention_required']} |",
        f"| Audit review flags | {summary['anomaly_count']} |",
        f"| Unconfirmed transitions later bot-confirmed | {summary['unconfirmed_transitions_subsequently_confirmed']} |",
        f"| Unconfirmed transitions later portal-observed as claimed | {summary['unconfirmed_transitions_subsequently_portal_observed']} |",
        f"| Unconfirmed transitions still open at cutoff | {summary['unconfirmed_transitions_open']} |",
        f"| Overlapping run-window pairs | {summary['overlapping_run_window_pairs']} |",
        f"| Claim events assigned to multiple runs | {summary['claim_events_assigned_to_multiple_run_windows']} |",
        "",
        "## Claim events by category",
        "",
        "| Category | Events | Claim units |",
        "|---|---:|---:|",
    ]
    categories = sorted(
        set(summary["category_event_counts"]) | set(summary["category_claim_units"])
    )
    for category in categories:
        lines.append(
            f"| {category} | {summary['category_event_counts'].get(category, 0)} "
            f"| {summary['category_claim_units'].get(category, 0)} |"
        )
    lines.extend(
        [
            "",
            "## Run metadata cross-check",
            "",
            "| Run end (IST as recorded) | Run label | Status | Mismatched categories |",
            "|---|---|---|---|",
        ]
    )
    for run in report["run_reconciliation"]:
        mismatch_text = ", ".join(run["mismatches"]) or "—"
        lines.append(
            f"| {run['timestamp']} | {run['run_label']} | {run['status']} | {mismatch_text} |"
        )
    lines.extend(
        [
            "",
            "## Portal observations",
            "",
            "These are live portal states recorded by the bot. They are not bot-made claims and are excluded from claim totals.",
            "",
            f"Total observations: **{len(report['portal_observations'])}**",
            "",
            "## Anomalies and review flags",
            "",
        ]
    )
    if report["anomalies"]:
        lines.extend(
            [
                "| Type | Account | Category | Item / detail | Commit |",
                "|---|---|---|---|---|",
            ]
        )
        for anomaly in report["anomalies"][:100]:
            detail = (
                anomaly.get("item")
                or anomaly.get("detail")
                or anomaly.get("previous_claim_timestamp")
                or anomaly.get("value")
                or "—"
            )
            lines.append(
                f"| {anomaly.get('type', 'unknown')} | {anomaly.get('account', '—')} "
                f"| {anomaly.get('category', '—')} | {str(detail).replace('|', '/')} "
                f"| {str(anomaly.get('commit') or '—')[:12]} |"
            )
        if len(report["anomalies"]) > 100:
            lines.append(f"\nShowing first 100 of {len(report['anomalies'])} anomaly records.")
    else:
        lines.append("No anomalies were detected by the implemented checks.")
    coverage = report.get("account_coverage", {})
    lines.extend(
        [
            "",
            "## Account coverage",
            "",
            f"- Accounts in latest history: **{coverage.get('history_account_count', 0)}**",
            f"- Configured accounts: **{coverage.get('configured_roster_count', 0)}**",
            f"- Historical-only accounts retained: **{coverage.get('historical_only_count', 0)}**",
            f"- Configured accounts absent from history: **{coverage.get('configured_accounts_missing_from_history_count', 0)}**",
            "",
            "## Interpretation limits",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in report["limitations"])
    lines.append("")
    return "\n".join(lines)


def write_reports(report: dict[str, Any], output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "historical_claim_audit.json"
    csv_path = output_dir / "historical_claim_audit.csv"
    markdown_path = output_dir / "historical_claim_audit.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    fields = [
        "event_id", "account", "category", "item", "recorded_at", "claim_units",
        "classification", "record_status", "first_seen", "first_seen_commit",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(report.get("claim_events", []))
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return {"json": json_path, "csv": csv_path, "markdown": markdown_path}


def run_audit(repo_path: Path, ref: str, output_dir: Path) -> dict[str, Any]:
    source_commit_sha = _git(
        repo_path, "rev-parse", "--verify", f"{ref}^{{commit}}"
    ).strip()
    roster_ids = _read_roster(repo_path, ref)
    events, observations, anomalies, coverage = _summarise_record_events(
        _record_snapshots(repo_path, ref, HISTORY_PATH)
    )
    latest_history = json.loads(_git(repo_path, "show", f"{ref}:{HISTORY_PATH}"))
    if not isinstance(latest_history, dict):
        raise AuditError(f"{HISTORY_PATH} at {ref} is not a JSON object.")
    runs = _extract_run_summaries(_record_snapshots(repo_path, ref, META_PATH))
    run_checks = _reconcile_runs(runs, events)
    review_resolution = _resolve_unconfirmed_flags(anomalies, events, observations)
    window_integrity = _run_window_integrity(runs, events)

    by_category = Counter()
    units_by_category = Counter()
    by_classification = Counter()
    for event in events:
        by_category[event["category"]] += 1
        units_by_category[event["category"]] += max(1, _safe_int(event.get("claim_units"), 1))
        by_classification[event["classification"]] += 1

    matched_runs = sum(1 for run in run_checks if run["status"] == "matched")
    account_coverage = build_account_coverage(latest_history, roster_ids)
    all_statuses = Counter()
    for player in latest_history.values():
        if isinstance(player, dict):
            for _, category, _, record in _state_records({"one": player}):
                all_statuses[f"{category}:{record.get('status') or 'unknown'}"] += 1

    report = {
        "report_title": "CS Rewards Bot — Historical Claim-History Audit",
        "source_ref": ref,
        "source_commit_sha": source_commit_sha,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": {
            "history_file": HISTORY_PATH,
            "metadata_file": META_PATH,
            "roster_file": PLAYERS_PATH,
            "history_snapshots": coverage,
            "run_metadata_snapshots": len(runs),
            "configured_roster_count": len(roster_ids),
            "account_masking": "Only the final four characters of each player ID are shown.",
            "first_parent_only": True,
            "read_only": True,
        },
        "summary": {
            "recorded_claim_events": len(events),
            "recorded_claim_units": sum(units_by_category.values()),
            "portal_observations_not_counted_as_claims": len(observations),
            "event_classification_counts": dict(by_classification),
            "category_event_counts": dict(by_category),
            "category_claim_units": dict(units_by_category),
            "run_metadata_records": len(runs),
            "run_checks_matched": matched_runs,
            "run_checks_attention_required": len(run_checks) - matched_runs,
            "anomaly_count": len(anomalies),
            **review_resolution,
            **window_integrity,
            "malformed_history_snapshots": coverage["malformed_snapshot_count"],
        },
        "account_coverage": account_coverage,
        "current_status_counts": dict(all_statuses),
        "claim_events": events,
        "portal_observations": observations,
        "run_reconciliation": run_checks,
        "anomalies": anomalies,
        "limitations": [
            "claim_history.json is a mutable state file, not an append-only event log.",
            "The audit reconstructs transitions visible in committed first-parent Git snapshots.",
            "The first available snapshot is a baseline; events already present there have no earlier transition in the available Git evidence.",
            "A claim absent from every committed snapshot cannot be proven by this audit.",
            "Portal-observed states are reported separately and are never counted as bot-made claims.",
            "Legacy Progression records without a status field are treated as bot-confirmed only when both last_claim and a positive last_count are present; this matches the historical production write path.",
            "Unconfirmed state transitions are linked to later successful records for the same account and reward category when available.",
            "Run-window overlaps are measured explicitly; an overlapping event must be reviewed before interpreting per-run counters.",
            "Run-counter comparisons are evidence checks and may require manual review where snapshots or historical metadata are incomplete.",
            "No production file is changed by this utility.",
        ],
    }
    paths = write_reports(report, output_dir)
    print(
        "Historical claim audit complete: "
        f"{coverage['snapshot_count']} history snapshots, "
        f"{len(events)} recorded claim events, "
        f"{len(observations)} portal observations, "
        f"{len(anomalies)} anomaly flags."
    )
    for kind, path in paths.items():
        print(f"{kind}: {path}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only reconstruction of claim-history Git snapshots."
    )
    parser.add_argument("--ref", default="origin/prod", help="Git ref to audit (default: origin/prod).")
    parser.add_argument("--repo-path", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path, default=Path("historical-audit-output"))
    args = parser.parse_args(argv)
    try:
        run_audit(args.repo_path.resolve(), args.ref, args.output_dir.resolve())
    except (AuditError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"AUDIT FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
