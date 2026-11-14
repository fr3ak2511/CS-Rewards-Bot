# master_claimer.py — CS Rewards Bot v3.3.3
import csv
import time
import os
import json
import hashlib
import html
import smtplib
import re
import subprocess
import sys
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException, StaleElementReferenceException
)

from event_rewards import (
    claim_configured_event_rewards,
    event_claim_count,
    event_retry_required,
    merge_claim_ledger,
)
from reward_popup_guard import dismiss_all_safe_popups
from progression_claim_safety import claim_progression_rewards
from promotion_followups import (
    filter_followup_results, seed_legacy_popup_followups,
    update_promotion_followups, build_promotional_followup_section,
)
from recovery_policy import (
    full_claim_retry_ids,
    daily_only_pending_targets,
    eligible_daily_recovery_ids,
    manual_daily_recovery_targets,
)
from report_export import build_copyable_report
from run_reconciliation import build_run_reconciliation
from email_cycle_summary import update_daily_cycle_summary
from grenade_balance import (
    capture_progression_balance,
    daily_reset_date,
    evaluate_progression_balance,
    load_grenade_balance_state,
    save_grenade_balance_state,
)

# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — CONSTANTS & CONFIG
# ═══════════════════════════════════════════════════════════════════════════════

VERSION        = "v3.3.3"
PLAYER_ID_FILE = "players.csv"
HISTORY_FILE   = "claim_history.json"
PORTAL_CLAIM_AUDIT_FILE = "portal_claim_audit.json"
BOT_META_FILE  = "bot_meta.json"
GRENADE_BALANCE_FILE = "grenade_balance_baselines.json"
HEADLESS       = True
LOGIN_DIAGNOSTICS_DIR = "login_diagnostics"
CLAIM_TEST_ARTIFACTS_DIR = "claim_test_artifacts"
RUN_ARTIFACTS_DIR = "run_artifacts"
DAILY_CLAIM_DIAGNOSTICS_DIR = "daily_claim_diagnostics"
RECOVERY_ONLY_FAILED = os.getenv("RECOVERY_ONLY_FAILED", "").strip().lower() == "true"
DAILY_ONLY_RECOVERY = os.getenv("DAILY_ONLY_RECOVERY", "").strip().lower() == "true"
MANUAL_DAILY_RECOVERY = os.getenv("MANUAL_DAILY_RECOVERY", "").strip().lower() == "true"
MANUAL_DAILY_RECOVERY_PLAYER_ID = os.getenv("MANUAL_DAILY_RECOVERY_PLAYER_ID", "").strip()
_LOGIN_FAILURE_DIAGNOSTIC_CAPTURED = False

DAILY_RESET_HOUR_IST   = 5
DAILY_RESET_MINUTE_IST = 30
LOYALTY_COOLDOWN_HOURS = 24

SMTP_SERVER   = os.getenv("SMTP_SERVER",   "smtp.gmail.com")
SMTP_PORT     = int(os.getenv("SMTP_PORT", "465"))
SMTP_USERNAME = os.getenv("SENDER_EMAIL",  os.getenv("SMTP_USERNAME", ""))
SMTP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", os.getenv("SMTP_PASSWORD", ""))
SMTP_FROM     = os.getenv("SENDER_EMAIL",  os.getenv("SMTP_FROM", ""))
SMTP_TO       = os.getenv("RECIPIENT_EMAIL", os.getenv("SMTP_TO", ""))


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def _player_claim_total(result):
    # Count each confirmed reward category exactly once for one player.
    return (
        int(result.get("daily", 0) or 0)
        + int(result.get("store", 0) or 0)
        + int(result.get("store_temp", 0) or 0)
        + int(result.get("progression", 0) or 0)
        + int(result.get("loyalty", 0) or 0)
        + event_claim_count(result.get("event_rewards"))
    )


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — TIME HELPERS & RUN CLASSIFICATION
# ═══════════════════════════════════════════════════════════════════════════════

def get_ist_time():
    return datetime.utcnow() + timedelta(hours=5, minutes=30)


def enforce_claim_reset_guard():
    """Fail closed before the 05:30 IST reset for every claim-capable entry point.

    Read-only login diagnostics and login-only tests are exempt in main(); this
    guard protects scheduled/full/manual claims, recovery passes, and one-player
    claim tests from accidental pre-reset execution.
    """
    now = get_ist_time()
    reset = now.replace(hour=DAILY_RESET_HOUR_IST, minute=DAILY_RESET_MINUTE_IST,
                        second=0, microsecond=0)
    if now < reset:
        log(f"⛔ CLAIM_GUARD_BLOCKED: claim-capable run refused before 05:30 IST (now {now:%Y-%m-%d %H:%M:%S} IST).")
        return False
    return True


def get_next_daily_reset():
    ist = get_ist_time()
    r = ist.replace(hour=DAILY_RESET_HOUR_IST, minute=DAILY_RESET_MINUTE_IST,
                    second=0, microsecond=0)
    if ist >= r:
        r += timedelta(days=1)
    return r


def get_last_daily_reset():
    ist = get_ist_time()
    r = ist.replace(hour=DAILY_RESET_HOUR_IST, minute=DAILY_RESET_MINUTE_IST,
                    second=0, microsecond=0)
    if ist < r:
        r -= timedelta(days=1)
    return r


def format_time_until(dt):
    delta = dt - get_ist_time()
    if delta.total_seconds() < 0:
        return "Available now"
    h, rem = divmod(int(delta.total_seconds()), 3600)
    m, _   = divmod(rem, 60)
    return f"{h}h {m}m" if h > 0 else f"{m}m"


def parse_timer_text(text):
    try:
        h = m = s = 0
        mh = re.search(r'(\d+)\s*h', text, re.IGNORECASE)
        mm = re.search(r'(\d+)\s*m', text, re.IGNORECASE)
        mc = re.search(r'(\d{1,2}):(\d{2}):(\d{2})', text)
        if mh: h = int(mh.group(1))
        if mm: m = int(mm.group(1))
        if mc: h, m, s = int(mc.group(1)), int(mc.group(2)), int(mc.group(3))
        if h > 0 or m > 0 or s > 0:
            return timedelta(hours=h, minutes=m, seconds=s)
    except:
        pass
    return None


# Scheduled claim windows are defined in .github/workflows/schedule.yml.
# The primary release remains date-seeded within 05:35–06:00 IST. Backup labels
# are mapped from their unique cron expressions, never from actual start time.
_RUN_SLOTS = [(5, 35), (9, 35), (13, 35), (17, 35), (21, 35)]
_PROMOTIONAL_BACKUP_CRONS = {
    "5 4 * * *": ("Promotional Check #1", 1),
    "5 8 * * *": ("Promotional Check #2", 2),
    "5 12 * * *": ("Promotional Check #3", 3),
    "5 16 * * *": ("Promotional Check #4", 4),
}


def determine_run_context():
    if RECOVERY_ONLY_FAILED:
        return "Recovery Run", -2
    if os.getenv("EXTERNAL_PRIMARY", "").strip().lower() == "true":
        return "Primary Run", 0
    event = os.getenv("GITHUB_EVENT_NAME") or "local"
    if event == "workflow_dispatch":
        return "Manual Run", -1
    if event == "repository_dispatch":
        return "Recovery Run", -2
    if event == "schedule":
        cron = os.getenv("SCHEDULE_CRON", "").strip()
        if cron in ("30 21 * * *", "0-30/5 0 * * *"):
            return "Primary Run", 0
        if cron in _PROMOTIONAL_BACKUP_CRONS:
            return _PROMOTIONAL_BACKUP_CRONS[cron]
        return "Unknown Scheduled Run", -3
    return "Manual Run", -1


def next_scheduled_runs_ist():
    return [
        ("Primary Run", "05:35–06:00 IST (randomized)"),
        ("Promotional Check #1", "09:35 IST"),
        ("Promotional Check #2", "13:35 IST"),
        ("Promotional Check #3", "17:35 IST"),
        ("Promotional Check #4", "21:35 IST"),
    ]


def should_skip_duplicate_scheduled_retry(meta, window_minutes=35):
    """Skip offset cron retries if a full run completed recently.

    GitHub scheduled events are best-effort. The primary dispatcher retains its
    randomized post-reset wait, and five-minute fallback events run during the
    05:30–06:00 IST window. Committed run state suppresses redundant fallbacks.
    Manual workflow_dispatch runs are never skipped by this guard.
    """
    if os.getenv("GITHUB_EVENT_NAME") != "schedule":
        return False
    last_run = meta.get("last_run") or {}
    raw = last_run.get("timestamp")
    if not raw:
        return False
    try:
        last = datetime.fromisoformat(raw)
        if last.tzinfo is not None:
            # All current stored timestamps are naive IST; support offsets safely too.
            from datetime import timezone
            last = last.astimezone(timezone(timedelta(hours=5, minutes=30))).replace(tzinfo=None)
        age_seconds = (get_ist_time() - last).total_seconds()
        recent = 0 <= age_seconds <= window_minutes * 60
        # A recent run suppresses a scheduled recovery only when email delivery
        # was explicitly confirmed. Otherwise the retry should re-check live state
        # and try to deliver the report again.
        return recent and last_run.get("email_sent") is True
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — BOT META (streak tracking)
# ═══════════════════════════════════════════════════════════════════════════════

_META_DEFAULT = {
    "streak": {
        "current": 0,
        "best": 0,
        "last_success_date": None,
        "last_checked_date": None
    },
    "last_run": None,
    "new_ids_seen": []
}


def load_bot_meta():
    if os.path.exists(BOT_META_FILE):
        try:
            with open(BOT_META_FILE, 'r') as f:
                data = json.load(f)
            # Migrate old flat schema
            if "streak" not in data:
                data["streak"] = {
                    "current": data.pop("streak_days", 0),
                    "best": 0,
                    "last_success_date": None,
                    "last_checked_date": None
                }
            if "new_ids_seen" not in data:
                data["new_ids_seen"] = data.pop("known_ids", [])
            if "last_run" not in data:
                data["last_run"] = None
            return data
        except Exception as e:
            log(f"⚠️ Could not load {BOT_META_FILE}: {e}")
    import copy
    return copy.deepcopy(_META_DEFAULT)


def save_bot_meta(meta):
    try:
        with open(BOT_META_FILE, 'w') as f:
            json.dump(meta, f, indent=2)
    except Exception as e:
        log(f"⚠️ Could not save {BOT_META_FILE}: {e}")


def is_new_id(pid, meta):
    return pid not in meta.get("new_ids_seen", [])


def mark_id_seen(pid, meta):
    if pid not in meta.get("new_ids_seen", []):
        meta.setdefault("new_ids_seen", []).append(pid)


def _current_reset_date():
    return get_last_daily_reset().strftime("%Y-%m-%d")


def has_complete_full_claim_coverage(pid, meta):
    """True only after this ID's full claim path completed cleanly after today's reset."""
    coverage = meta.get("full_claim_coverage", {})
    entry = coverage.get(pid, {}) if isinstance(coverage, dict) else {}
    return bool(
        isinstance(entry, dict)
        and entry.get("reset_date") == _current_reset_date()
        and entry.get("status") == "complete"
        and not entry.get("retry_required", False)
    )


def needs_standard_backup_audit(pid, meta):
    """True until this player's standard Daily/Store cards were audited this reset."""
    coverage = meta.get("full_claim_coverage", {})
    entry = coverage.get(pid, {}) if isinstance(coverage, dict) else {}
    return not (
        isinstance(entry, dict)
        and entry.get("standard_rewards_audited_reset") == _current_reset_date()
    )


def _result_needs_recovery(result):
    """Return whether this result has an unresolved, mode-appropriate follow-up."""
    mode = str(result.get("processing_mode") or "full_claim")
    if mode == "promotional_only":
        # Promotional-only checks are revisited by the promotional schedule.
        # Their Store popup state must not trigger a normal full-claim retry.
        return (
            str(result.get("status") or "") in ("Login Failed", "Error", "Failed")
            or bool(result.get("event_rewards_retry_required"))
        )

    status = str(result.get("status") or "")
    if status in ("Login Failed", "Error", "Failed"):
        return True
    if (result.get("retry_required") or result.get("promotion_detection_failed")
            or result.get("daily_unconfirmed") or result.get("store_unconfirmed_indices")
            or result.get("progression_unconfirmed")
            or event_retry_required(result.get("event_rewards"))):
        return True
    if status in ("Partial", "No Rewards"):
        return True
    possible = max(0, int(result.get("possible", 0) or 0))
    confirmed = (
        int(result.get("daily", 0) or 0)
        + int(result.get("store", 0) or 0)
        + int(result.get("store_temp", 0) or 0)
        + int(result.get("progression", 0) or 0)
        + int(result.get("loyalty", 0) or 0)
    )
    return possible > confirmed


def _full_claim_recovery_candidate(result):
    """Return true only for unresolved full-claim/standard-reconciliation work."""
    mode = str(result.get("processing_mode") or "full_claim")
    if mode not in ("full_claim", "backup_reconciliation"):
        return False
    if str(result.get("status") or "") in ("Login Failed", "Error", "Failed"):
        return True
    if (result.get("daily_unconfirmed")
            or result.get("store_unconfirmed_indices")
            or result.get("progression_unconfirmed")
            or result.get("event_rewards_retry_required")
            or event_retry_required(result.get("event_rewards"))):
        return True
    return False


def _record_full_claim_coverage(meta, pid, result, run_label):
    """Persist full-claim coverage; promotional-only scans must never replace it."""
    if str(result.get("processing_mode") or "full_claim") == "promotional_only":
        return False

    retry_required = _full_claim_recovery_candidate(result)
    reason = result.get("fail_reason") if retry_required else None
    if not reason and retry_required:
        reason = "One or more full-claim rewards remained unconfirmed"
    previous = meta.get("full_claim_coverage", {}).get(pid, {})
    preserve_daily_slots = (
        previous.get("reset_date") == _current_reset_date()
        and isinstance(previous.get("daily_recovery_slots_attempted"), list)
    )
    entry = {
        "reset_date": _current_reset_date(),
        "status": "pending" if retry_required else "complete",
        "last_full_attempt_at": get_ist_time().isoformat(),
        "last_run_label": run_label,
        "last_status": str(result.get("status") or "Unknown"),
        "retry_required": bool(retry_required),
        "unresolved_reason": reason,
    }
    if preserve_daily_slots:
        attempted_slots = list(dict.fromkeys(
            item for item in previous.get("daily_recovery_slots_attempted", [])
            if isinstance(item, str) and item.strip()
        ))
        entry["daily_recovery_slots_attempted"] = attempted_slots
        entry["daily_recovery_attempts"] = len(attempted_slots)
        if previous.get("daily_recovery_last_attempt_at"):
            entry["daily_recovery_last_attempt_at"] = previous["daily_recovery_last_attempt_at"]
        if previous.get("daily_recovery_last_slot"):
            entry["daily_recovery_last_slot"] = previous["daily_recovery_last_slot"]
    standard_checked = bool(result.get("standard_rewards_checked"))
    standard_unconfirmed = bool(
        result.get("daily_unconfirmed")
        or result.get("store_unconfirmed_indices")
    )
    if standard_checked and not standard_unconfirmed:
        entry["standard_rewards_audited_reset"] = _current_reset_date()
    meta.setdefault("full_claim_coverage", {})[pid] = entry
    return True


def _mark_daily_recovery_slot_attempt(meta, pid, slot_label):
    """Persist an at-most-once marker for this account and scheduled backup slot."""
    if slot_label not in {
        "Promotional Check #1", "Promotional Check #2",
        "Promotional Check #3", "Promotional Check #4",
    }:
        return False
    coverage = meta.setdefault("full_claim_coverage", {})
    entry = coverage.get(pid)
    if not isinstance(entry, dict) or entry.get("reset_date") != _current_reset_date():
        return False
    attempted = entry.get("daily_recovery_slots_attempted")
    if not isinstance(attempted, list):
        attempted = []
    attempted = list(dict.fromkeys(
        item for item in attempted if isinstance(item, str) and item.strip()
    ))
    if slot_label in attempted or len(attempted) >= 4:
        return False
    attempted.append(slot_label)
    entry["daily_recovery_slots_attempted"] = attempted
    entry["daily_recovery_attempts"] = len(attempted)
    entry["daily_recovery_last_slot"] = slot_label
    entry["daily_recovery_last_attempt_at"] = get_ist_time().isoformat()
    coverage[pid] = entry
    meta["full_claim_coverage"] = coverage
    # The workflow's always-run state-commit step persists this marker even if
    # the browser attempt subsequently fails or the portal does not confirm it.
    save_bot_meta(meta)
    return True


def _deferred_daily_recovery_result(pid, targets, reason):
    """Represent a bounded retry that was intentionally not clicked this slot."""
    result = _recovery_result_from_saved_targets(pid, targets)
    result["status"] = "Partial"
    result["recovery_deferred"] = True
    result["fail_reason"] = reason
    result["daily_only_recovery"] = True
    result["immediate_recovery"] = {
        "attempted": False,
        "status": "deferred",
        "categories": ["Daily Reward"],
        "resolved_categories": [],
        "unresolved_categories": ["Daily Reward"],
        "reason": reason,
        "click_limit_per_target": 1,
    }
    return result




def _promotion_detail_worthy(promo):
    """Return True only for free, actionable Store promotion outcomes worth emailing.

    Paid/discounted cards are filtered using the live card text captured for reporting.
    This is a presentation filter only: it does not change discovery or click behaviour.
    """
    status = str(promo.get("status") or "unknown")
    if status == "popup_blocked":
        # The generic guard result has no individual offer/card details. Keep it
        # reportable so the reader can see that the Store was obstructed.
        # An identified offer must pass the normal free/actionable checks below.
        is_generic_popup_blocker = (
            str(promo.get("key") or "").strip() == "store-popup-blocked"
            and not str(promo.get("card_text") or "").strip()
            and not str(promo.get("button_text") or "").strip()
        )
        if is_generic_popup_blocker:
            return True
        # Without captured card text we cannot establish that an identified
        # blocked offer is free. Fail closed for email presentation.
        if not str(promo.get("card_text") or "").strip():
            return False
    if status not in ("claimed_this_run", "claim_unconfirmed", "click_failed", "available", "popup_blocked"):
        return False

    card_text = _normalize_promotion_text(promo.get("card_text") or "")
    button_text = _normalize_promotion_text(promo.get("button_text") or "")

    # Suppress prices and promotional sales language. The bot still scans the Store;
    # these checks decide only whether a result belongs in the email.
    paid_or_discounted_patterns = (
        # Currency symbols/codes before the amount (₹499, Rs. 499, USD 4.99).
        r"(?:₹|rs\.?|inr|usd|eur|gbp|aud|cad|\$|€|£)\s*[1-9][0-9,]*(?:\.[0-9]{1,2})?",
        # Currency codes after the amount (499 INR, 4.99 USD).
        r"\b[1-9][0-9,]*(?:\.[0-9]{1,2})?\s*(?:inr|usd|eur|gbp|aud|cad)\b",
        # Explicit percentage-off language.
        r"\b[1-9][0-9]*(?:\.[0-9]+)?\s*%\s*(?:off|discount|discounted|saving)\b",
        r"\bsave\s+[1-9][0-9]*(?:\.[0-9]+)?\s*%",
        # Commercial wording. 'Bundle' alone is deliberately not treated as paid.
        r"\b(?:discount|discounted|sale|buy\s+now|purchase|paid|price)\b",
    )
    combined_text = f"{card_text} {button_text}"
    if any(re.search(pattern, combined_text, flags=re.IGNORECASE) for pattern in paid_or_discounted_patterns):
        return False

    # The live scanner recognizes these explicit claim controls. Unknown CTA labels
    # remain out of the email rather than being presented as free/redeemable.
    allowed_actions = {"free", "claim", "claim reward", "claim now"}
    return button_text in allowed_actions


def _promotion_offer_type_key(promo):
    """Use the normalized visible title for a human-facing distinct-offer count."""
    title = str(promo.get("title") or "").strip()
    return re.sub(r"\s+", " ", title).casefold() if title else ""


def _summary_counts(results):
    """Shared report counters; routine promotional unavailability is tracked separately."""
    hard_failure_statuses = {"Login Failed", "Error", "Failed"}
    standard_unavailable = 0
    promotional_unavailable = 0
    distinct_offer_types = set()
    promotional_claims_made = 0
    actionable_promotion_results = 0
    unconfirmed = 0
    promotion_detection_failures = 0
    event_reward_claims_made = 0
    event_reward_failures = 0
    partial = 0
    actual_failures = 0
    for result in results:
        status = str(result.get("status") or "")
        is_promo_only = result.get("processing_mode") == "promotional_only"
        actual_failures += int(status in hard_failure_statuses)
        partial += int(status in ("Partial", "No Rewards") or (bool(result.get("retry_required")) and status not in hard_failure_statuses))
        for promo in (result.get("promotions_observed", []) or []):
            promo_status = str(promo.get("status") or "unknown")
            offer_type_key = _promotion_offer_type_key(promo)
            if offer_type_key:
                distinct_offer_types.add(offer_type_key)
            if promo_status == "unavailable":
                promotional_unavailable += 1
            elif promo_status == "claimed_this_run" and _promotion_detail_worthy(promo):
                promotional_claims_made += max(1, int(promo.get("claim_count", 0) or 0))
            elif _promotion_detail_worthy(promo):
                actionable_promotion_results += 1
            unconfirmed += int(
                promo_status in ("claim_unconfirmed", "click_failed", "available")
                or promo_status not in ("claimed_this_run", "portal_claimed", "unavailable", "claim_unconfirmed", "click_failed", "available")
            )
        unconfirmed += int(bool(result.get("daily_unconfirmed")))
        unconfirmed += len(result.get("store_unconfirmed_indices", []) or [])
        unconfirmed += int(bool(result.get("progression_unconfirmed")))
        event_results = result.get("event_rewards", []) or []
        event_reward_claims_made += event_claim_count(event_results)
        event_reward_failures += sum(1 for item in event_results if item.get("retry_required"))
        unconfirmed += sum(1 for item in event_results if item.get("retry_required"))
        if not is_promo_only and status not in hard_failure_statuses:
            standard_unavailable += int(bool(result.get("daily_skipped", False)))
            store_skipped = result.get("store_skipped", [False, False, False])
            if isinstance(store_skipped, list):
                standard_unavailable += sum(1 for item in store_skipped[:3] if item)
            if result.get("has_loyalty"):
                standard_unavailable += int(bool(result.get("loyalty_skipped", False)))
        promotion_detection_failures += int(bool(result.get("promotion_detection_failed")))
    claims_made = sum(
        int(result.get("daily", 0) or 0)
        + int(result.get("store", 0) or 0)
        + int(result.get("store_temp", 0) or 0)
        + int(result.get("progression", 0) or 0)
        + int(result.get("loyalty", 0) or 0)
        + event_claim_count(result.get("event_rewards"))
        for result in results
    )
    full_claim_accounts = sum(1 for result in results if result.get("processing_mode", "full_claim") == "full_claim")
    promotional_only_accounts = sum(1 for result in results if result.get("processing_mode") == "promotional_only")
    backup_reconciliation_accounts = sum(1 for result in results if result.get("processing_mode") == "backup_reconciliation")
    return {
        "accounts_processed": len(results),
        "claims_made": claims_made,
        # Preserve the legacy total-observation counter for state/report compatibility.
        # The email uses standard_rewards_on_cooldown so promotional noise cannot dominate it.
        "unavailable_rewards": standard_unavailable + promotional_unavailable,
        "standard_rewards_on_cooldown": standard_unavailable,
        "promotional_unavailable_observations": promotional_unavailable,
        "distinct_promotional_offer_types": len(distinct_offer_types),
        "promotional_claims_made": promotional_claims_made,
        "actionable_promotion_results": actionable_promotion_results,
        "partial_results": partial,
        "actual_failures": actual_failures,
        "unconfirmed_claims": unconfirmed,
        "promotion_detection_failures": promotion_detection_failures,
        "event_reward_claims_made": event_reward_claims_made,
        "event_reward_failures": event_reward_failures,
        "promotional_only_accounts": promotional_only_accounts,
        "backup_reconciliation_accounts": backup_reconciliation_accounts,
        "full_claim_accounts": full_claim_accounts,
    }


def update_streak_day_level(meta, all_ok_today):
    """
    Increment streak when all eligible rewards are claimed for the day.
    Loyalty is excluded from the streak requirement when it is LP-locked
    (i.e. never claimed) — only daily + store must be done to count.
    """
    streak   = meta.setdefault("streak", _META_DEFAULT["streak"].copy())
    ist_date = get_ist_time().strftime("%Y-%m-%d")

    if all_ok_today:
        last_ok = streak.get("last_success_date")
        if last_ok != ist_date:
            # Only increment if this is a new success date (not already counted today)
            streak["current"] = streak.get("current", 0) + 1
            streak["best"]    = max(streak.get("best", 0), streak["current"])
            streak["last_success_date"] = ist_date
            log(f"🔥 Streak: Day {streak['current']} (Best: {streak['best']})")
        else:
            log(f"🔥 Streak already counted today: Day {streak['current']}")
    else:
        # Check if streak should be broken (missed a day)
        last_ok = streak.get("last_success_date")
        if last_ok and last_ok != ist_date:
            yesterday = (
                datetime.strptime(ist_date, "%Y-%m-%d") - timedelta(days=1)
            ).strftime("%Y-%m-%d")
            if last_ok < yesterday:
                if streak.get("current", 0) > 0:
                    log(f"💔 Streak broken at Day {streak['current']}")
                streak["current"] = 0

    streak["last_checked_date"] = ist_date
    meta["streak"] = streak


def compute_all_ok_today(players):
    """
    Returns True if daily + store have been claimed for ALL players today.
    Loyalty is NOT required — LP-locked players would permanently block the
    streak otherwise. Loyalty is a bonus metric, not a streak blocker.
    """
    h  = load_claim_history()
    lr = get_last_daily_reset()

    for pid, has_loyalty in players:
        if pid not in h:
            return False
        ph = h[pid]

        # Daily — claimed since last 05:30 IST reset
        daily = ph.get("daily", {})
        lc = daily.get("last_claim")
        observed = daily.get("portal_observed_at") if daily.get("status") == "portal_claimed" else None
        recent = any(value and datetime.fromisoformat(value) >= lr for value in (lc, observed))
        if not recent:
            return False

        # Promotional rewards are independent of the daily-reset streak requirement.
        for i in range(1, 4):
            rec = ph.get("store", {}).get(f"reward_{i}", {})
            lc = rec.get("last_claim")
            observed = rec.get("portal_observed_at") if rec.get("status") == "portal_claimed" else None
            recent = any(value and datetime.fromisoformat(value) >= lr for value in (lc, observed))
            if not recent:
                return False

    return True


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — CLAIM HISTORY
# ═══════════════════════════════════════════════════════════════════════════════

def load_claim_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            log(f"⚠️ Error reading {HISTORY_FILE}: {e}")
    return {}


def save_claim_history(h):
    try:
        with open(HISTORY_FILE, 'w') as f:
            json.dump(h, f, indent=2)
    except Exception as e:
        log(f"⚠️ Error saving {HISTORY_FILE}: {e}")


def record_player_display_name(meta, player_id, display_name, observed_at=None):
    """Maintain current IGN and all previously authenticated names for a stable Player ID."""
    if not isinstance(meta, dict):
        return None
    player_id = str(player_id or "").strip()
    display_name = " ".join(str(display_name or "").split())
    if not player_id or not display_name:
        return None

    observed = observed_at or get_ist_time()
    if isinstance(observed, str):
        try:
            observed = datetime.fromisoformat(observed)
        except (TypeError, ValueError):
            observed = get_ist_time()
    observed_text = observed.isoformat()

    current_cache = meta.setdefault("player_display_names", {})
    if not isinstance(current_cache, dict):
        current_cache = {}
        meta["player_display_names"] = current_cache
    history_store = meta.setdefault("player_display_name_history", {})
    if not isinstance(history_store, dict):
        history_store = {}
        meta["player_display_name_history"] = history_store

    legacy_name = " ".join(str(current_cache.get(player_id) or "").split())
    player_history = history_store.setdefault(player_id, {"current_name": None, "names": []})
    if not isinstance(player_history, dict):
        player_history = {"current_name": None, "names": []}
        history_store[player_id] = player_history
    names = player_history.setdefault("names", [])
    if not isinstance(names, list):
        names = []
        player_history["names"] = names

    # Migrate the old latest-name cache without inventing a timestamp for the old IGN.
    if legacy_name and not any(
        isinstance(item, dict) and item.get("name") == legacy_name for item in names
    ):
        names.append({
            "name": legacy_name,
            "first_seen_at_ist": None,
            "last_seen_at_ist": None,
            "observation_count": 0,
            "source": "legacy_name_cache",
            "timestamp_precision": "unknown",
        })

    entry = next(
        (item for item in names if isinstance(item, dict) and item.get("name") == display_name),
        None,
    )
    if entry is None:
        entry = {
            "name": display_name,
            "first_seen_at_ist": observed_text,
            "last_seen_at_ist": observed_text,
            "observation_count": 1,
            "source": "authenticated_profile_capture",
            "timestamp_precision": "run_observation",
        }
        names.append(entry)
    else:
        if not entry.get("first_seen_at_ist") and entry.get("timestamp_precision") != "unknown":
            entry["first_seen_at_ist"] = observed_text
        entry["last_seen_at_ist"] = observed_text
        entry["observation_count"] = int(entry.get("observation_count", 0) or 0) + 1
        entry.setdefault("source", "authenticated_profile_capture")

    previous_name = str(player_history.get("current_name") or legacy_name or "").strip()
    player_history["current_name"] = display_name
    player_history["last_observed_at_ist"] = observed_text
    current_cache[player_id] = display_name
    return {
        "current_name": display_name,
        "previous_name": previous_name if previous_name and previous_name != display_name else None,
        "changed": bool(previous_name and previous_name != display_name),
        "names": names,
    }


def load_portal_claim_audit():
    """Load the durable pre-existing portal-claim audit without overwriting corruption."""
    if not os.path.exists(PORTAL_CLAIM_AUDIT_FILE):
        return {"schema_version": 1, "observations": {}}
    try:
        with open(PORTAL_CLAIM_AUDIT_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict) or not isinstance(data.get("observations"), dict):
            raise ValueError("invalid portal-claim audit structure")
        data.setdefault("schema_version", 1)
        return data
    except Exception as exc:
        log(f"⚠️ Cannot safely read {PORTAL_CLAIM_AUDIT_FILE}; audit write skipped ({type(exc).__name__})")
        return None


def save_portal_claim_audit(data):
    """Atomically persist portal-claim evidence; never replace the source on a failed write."""
    if not isinstance(data, dict) or not isinstance(data.get("observations"), dict):
        return False
    temp_path = PORTAL_CLAIM_AUDIT_FILE + ".tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(temp_path, PORTAL_CLAIM_AUDIT_FILE)
        return True
    except Exception as exc:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception:
            pass
        log(f"⚠️ Could not persist {PORTAL_CLAIM_AUDIT_FILE}: {type(exc).__name__}")
        return False


def _timestamp_is_in_reset_cycle(value, reset_at):
    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is not None:
            parsed = parsed.replace(tzinfo=None)
        return parsed >= reset_at
    except (TypeError, ValueError, OverflowError):
        return False


def record_preexisting_portal_claim(driver, pid, reward_category, reward_key, reward_name, source):
    """Record a portal Claimed/cooldown state only when it predates this bot attempt.

    The portal does not identify the actor. These records are review evidence, not
    proof that a player manually claimed the reward. Standard Daily/Store incidents
    are deduplicated once per player/reward/reset date. Promotional campaign
    observations are deduplicated once per player/campaign.
    """
    if not bool(getattr(driver, "_portal_claim_audit_enabled", False)):
        return None
    player_id = str(pid or "").strip()
    if not player_id:
        return None

    category = str(reward_category or "").strip().lower()
    reward_key = str(reward_key or "").strip()
    if category not in ("daily", "store", "promotion") or not reward_key:
        return None

    reset_at = get_last_daily_reset()
    reset_date = reset_at.strftime("%Y-%m-%d")
    history = load_claim_history()
    player_history = history.get(player_id, {}) if isinstance(history, dict) else {}

    # If the bot already confirmed this reward, or has an unresolved attempt in
    # the current reset cycle, a later Claimed state is ambiguous and is not
    # attributed as pre-existing/manual evidence.
    if category == "daily":
        reward_history = player_history.get("daily", {})
        if any(_timestamp_is_in_reset_cycle(reward_history.get(field), reset_at)
               for field in ("last_claim", "last_attempt_at")):
            return None
    elif category == "store":
        reward_history = (player_history.get("store", {}) or {}).get(reward_key, {})
        if any(_timestamp_is_in_reset_cycle(reward_history.get(field), reset_at)
               for field in ("last_claim", "last_attempt_at")):
            return None
    else:
        reward_history = (player_history.get("promotions", {}) or {}).get(reward_key, {})
        # A prior bot-confirmed claim or an unconfirmed bot click makes a later
        # Claimed state ambiguous until a fresh live read shows the offer available.
        if (
            reward_history.get("last_claim")
            or reward_history.get("last_unconfirmed_attempt_at")
            or reward_history.get("status") in ("claim_unconfirmed", "click_failed")
        ):
            return None

    audit = load_portal_claim_audit()
    if audit is None:
        return None
    observations = audit.setdefault("observations", {})
    cycle_scope = "campaign_once" if category == "promotion" else "daily_reset"
    cycle_token = "campaign" if category == "promotion" else reset_date
    raw_key = "|".join((player_id, category, reward_key, cycle_token))
    incident_id = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    now = get_ist_time()
    now_text = now.isoformat()
    run_id = str(os.getenv("GITHUB_RUN_ID") or "").strip()
    run_label = str(getattr(driver, "_portal_claim_run_label", "") or "")

    existing = observations.get(incident_id)
    if isinstance(existing, dict):
        existing["last_observed_at_ist"] = now_text
        existing["observation_count"] = int(existing.get("observation_count", 1) or 1) + 1
        if getattr(driver, "_portal_claim_display_name", None):
            latest_name = " ".join(str(driver._portal_claim_display_name).split())
            existing["last_observed_display_name"] = latest_name
            existing.setdefault("display_name_at_first_observation", existing.get("display_name"))
        run_ids = list(existing.get("run_ids") or [])
        if run_id and run_id not in run_ids:
            run_ids.append(run_id)
        existing["run_ids"] = run_ids
        save_portal_claim_audit(audit)
        return None

    event = {
        "incident_id": incident_id,
        "player_id": player_id,
        "display_name": " ".join(str(getattr(driver, "_portal_claim_display_name", "") or "").split()) or None,
        "display_name_at_first_observation": " ".join(str(getattr(driver, "_portal_claim_display_name", "") or "").split()) or None,
        "last_observed_display_name": " ".join(str(getattr(driver, "_portal_claim_display_name", "") or "").split()) or None,
        "reward_category": category,
        "reward_key": reward_key,
        "reward_name": str(reward_name or reward_key).strip(),
        "classification": "pre_existing_portal_claim",
        "actor": "unknown_portal_actor",
        "source": str(source or "pre_attempt_portal_state"),
        "cycle_scope": cycle_scope,
        "reset_date": reset_date,
        "first_observed_at_ist": now_text,
        "last_observed_at_ist": now_text,
        "observation_count": 1,
        "run_label": run_label,
        "run_ids": [run_id] if run_id else [],
    }
    observations[incident_id] = event
    if not save_portal_claim_audit(audit):
        return None

    events = list(getattr(driver, "_preexisting_portal_claims", []) or [])
    events.append(dict(event))
    driver._preexisting_portal_claims = events
    log(f"🧾 Pre-existing portal claim recorded for {player_id}: {event['reward_name']} ({reset_date})")
    return event


def _portal_claim_reset_day_count(player_id, audit=None):
    audit = audit if isinstance(audit, dict) else load_portal_claim_audit()
    if not isinstance(audit, dict):
        return 0
    days = {
        str(item.get("reset_date") or "")
        for item in (audit.get("observations") or {}).values()
        if isinstance(item, dict)
        and str(item.get("player_id") or "") == str(player_id or "")
        and item.get("reset_date")
    }
    return len(days)


def init_player_history(pid):
    h = load_claim_history()
    if pid not in h:
        h[pid] = {
            "daily":       {"last_claim": None, "next_available": None, "status": "unknown"},
            "store": {
                f"reward_{i}": {"last_claim": None, "next_available": None, "status": "unknown"}
                for i in range(1, 5)
            },
            "progression": {"last_claim": None, "last_count": 0, "last_visit": None},
            "loyalty":     {"last_claim": None, "next_available": None, "status": "unknown"},
            "promotions":  {},
            "event_rewards": {}
        }
        save_claim_history(h)
    else:
        changed = False
        h[pid].setdefault("store", {})
        if "promotions" not in h[pid]:
            h[pid]["promotions"] = {}
            changed = True
        if "event_rewards" not in h[pid]:
            h[pid]["event_rewards"] = {}
            changed = True
        if "loyalty" not in h[pid]:
            h[pid]["loyalty"] = {"last_claim": None, "next_available": None, "status": "unknown"}
            changed = True
        if "last_visit" not in h[pid].get("progression", {}):
            h[pid].setdefault("progression", {})["last_visit"] = None
            changed = True
        for rk in ("reward_1", "reward_2", "reward_3", "reward_4"):
            if rk not in h[pid]["store"]:
                h[pid]["store"][rk] = {
                    "last_claim": None, "next_available": None, "status": "unknown"
                }
                changed = True
            elif "status" not in h[pid]["store"][rk]:
                h[pid]["store"][rk]["status"] = "unknown"
                changed = True
        if changed:
            save_claim_history(h)
    return h


def update_claim_history(pid, reward_type, claimed_count=0,
                         reward_index=None, detected_cooldown=None, attempted=False,
                         unconfirmed=False):
    h = init_player_history(pid)
    ist_now = get_ist_time()
    nr = get_next_daily_reset()

    def mark_unconfirmed(record):
        # A live Free/Claim control disproves a same-reset history entry that claimed
        # success. Preserve older historical claims, but remove today's false green.
        raw_last_claim = record.get("last_claim")
        if raw_last_claim:
            try:
                if datetime.fromisoformat(raw_last_claim) >= get_last_daily_reset():
                    record["last_claim"] = None
            except (TypeError, ValueError):
                record["last_claim"] = None
        record["next_available"] = None
        record["status"] = "unconfirmed"
        record["last_attempt_at"] = ist_now.isoformat()
        record["unconfirmed_attempts"] = int(record.get("unconfirmed_attempts", 0) or 0) + 1
        record.pop("portal_observed_at", None)

    if reward_type == "daily":
        record = h[pid]["daily"]
        if claimed_count > 0:
            record["last_claim"] = ist_now.isoformat()
            record["next_available"] = nr.isoformat()
            record["status"] = "claimed"
            record.pop("portal_observed_at", None)
            log(f"📝 Daily claim confirmed → next reset {nr.strftime('%I:%M %p IST')}")
        elif detected_cooldown is not None:
            # A live timer/Claimed state is portal evidence, not a bot click claim.
            record["next_available"] = nr.isoformat()
            record["status"] = "portal_claimed"
            record["portal_observed_at"] = ist_now.isoformat()
            log(f"📝 Daily portal state confirmed; suppressed until {nr.strftime('%I:%M %p IST')}")
        elif unconfirmed:
            mark_unconfirmed(record)
            log("⚠️ Daily claim remains unconfirmed; eligible for the next live recheck")
        elif attempted:
            lc = record.get("last_claim")
            if lc and datetime.fromisoformat(lc) >= get_last_daily_reset():
                log("📝 Daily — preserving previously confirmed claim")
            else:
                record["status"] = "unavailable"
                log("📝 Daily unavailable")

    elif reward_type == "store" and reward_index is not None:
        rk = f"reward_{reward_index}"
        record = h[pid]["store"].setdefault(
            rk, {"last_claim": None, "next_available": None, "status": "unknown"}
        )

        if reward_index == LEGACY_TEMP_STORE_REWARD_INDEX:
            # Legacy reward_4 state is retained for compatibility only.
            if claimed_count > 0:
                record["last_claim"] = ist_now.isoformat()
                record["next_available"] = None
                record["status"] = "claimed"
                log("📝 Legacy reward_4 history updated; dynamic promotions are stored independently")
            elif attempted or unconfirmed:
                log("📝 Temporary Store reward is not confirmed; it will be rechecked")
        else:
            if claimed_count > 0:
                record["last_claim"] = ist_now.isoformat()
                record["next_available"] = nr.isoformat()
                record["status"] = "claimed"
                record.pop("portal_observed_at", None)
                log(f"📝 Store {reward_index} claim confirmed → next reset {nr.strftime('%I:%M %p IST')}")
            elif detected_cooldown is not None:
                record["next_available"] = nr.isoformat()
                record["status"] = "portal_claimed"
                record["portal_observed_at"] = ist_now.isoformat()
                log(f"📝 Store {reward_index} portal state confirmed; suppressed until daily reset")
            elif unconfirmed:
                mark_unconfirmed(record)
                log(f"⚠️ Store reward_{reward_index} remains unconfirmed; eligible for the next live recheck")
            elif attempted:
                lc = record.get("last_claim")
                if lc and datetime.fromisoformat(lc) >= get_last_daily_reset():
                    log(f"📝 Store {reward_index} — preserving previously confirmed claim")
                else:
                    record["status"] = "unavailable"

    elif reward_type == "progression":
        record = h[pid]["progression"]
        record["last_visit"] = ist_now.isoformat()
        if claimed_count > 0:
            record["last_claim"] = ist_now.isoformat()
            record["last_count"] = claimed_count
            record["status"] = "claimed"
            record.pop("last_unconfirmed_at", None)
            log(f"📝 Progression claims portal-confirmed: {claimed_count}; last_visit updated")
        elif unconfirmed:
            record["status"] = "unconfirmed"
            record["last_unconfirmed_at"] = ist_now.isoformat()
            log("⚠️ Progression claim could not be confirmed; eligible for the next live recheck")
        else:
            record["status"] = "checked_no_claimable_reward"

    elif reward_type == "loyalty":
        if claimed_count > 0:
            na = ist_now + timedelta(hours=LOYALTY_COOLDOWN_HOURS)
            h[pid]["loyalty"]["last_claim"] = ist_now.isoformat()
            h[pid]["loyalty"]["next_available"] = na.isoformat()
            h[pid]["loyalty"]["status"] = "claimed"
            log(f"📝 Loyalty claimed {claimed_count}, next in {LOYALTY_COOLDOWN_HOURS}h")
        elif detected_cooldown is not None:
            na = ist_now + detected_cooldown
            h[pid]["loyalty"]["next_available"] = na.isoformat()
            h[pid]["loyalty"]["status"] = "cooldown_detected"
            log(f"📝 Loyalty cooldown → {format_time_until(na)}")
        elif attempted:
            lc = h[pid]["loyalty"].get("last_claim")
            if lc and ist_now < datetime.fromisoformat(lc) + timedelta(hours=LOYALTY_COOLDOWN_HOURS):
                log("📝 Loyalty — preserving (within 24h cooldown)")
            else:
                h[pid]["loyalty"]["status"] = "unavailable"
                log("📝 Loyalty unavailable")

    save_claim_history(h)
    return h


def get_reward_status(pid):
    h       = load_claim_history()
    ist_now = get_ist_time()
    lr      = get_last_daily_reset()
    nr      = get_next_daily_reset()

    if pid not in h:
        return {
            "daily_available": True, "daily_next": None, "daily_status": "unknown",
            "store_available": [True, True, True],
            "store_next": [None, None, None],
            "store_status": ["unknown", "unknown", "unknown"],
            "temp_store_available": True,
            "temp_store_status": "unknown",
            "loyalty_available": True, "loyalty_next": None, "loyalty_status": "unknown",
        }

    ph = h[pid]

    # Daily
    d_avail, d_next = True, None
    d_status = ph.get("daily", {}).get("status", "unknown")
    lc_d = ph.get("daily", {}).get("last_claim")
    na_d = ph.get("daily", {}).get("next_available")
    if d_status == "unconfirmed":
        d_avail, d_next = True, None
    elif lc_d and datetime.fromisoformat(lc_d) >= lr:
        d_avail, d_next, d_status = False, format_time_until(nr), "claimed"
    elif na_d:
        nt = datetime.fromisoformat(na_d)
        if ist_now < nt:
            d_avail, d_next = False, format_time_until(nt)

    # Established daily Store rewards — indices 1..3 reset at 05:30 IST.
    s_avail  = [True, True, True]
    s_next   = [None, None, None]
    s_status = ["unknown", "unknown", "unknown"]
    store_history = ph.get("store", {})
    for i in range(3):
        rk = f"reward_{i+1}"
        rd = store_history.get(rk, {})
        s_status[i] = rd.get("status", "unknown")
        lc_s = rd.get("last_claim")
        na_s = rd.get("next_available")
        if s_status[i] == "unconfirmed":
            s_avail[i], s_next[i] = True, None
        elif lc_s and datetime.fromisoformat(lc_s) >= lr:
            s_avail[i], s_next[i], s_status[i] = False, format_time_until(nr), "claimed"
        elif na_s:
            nt = datetime.fromisoformat(na_s)
            if ist_now < nt:
                s_avail[i], s_next[i] = False, format_time_until(nt)

    # Legacy reward_4 state is preserved for compatibility only. Dynamic promotions
    # are discovered from the live page and never use this record to suppress scanning.
    temp_record = store_history.get("reward_4", {})
    temp_status = temp_record.get("status", "unknown")
    temp_claimed = bool(temp_record.get("last_claim")) or temp_status in ("claimed", "portal_claimed")
    temp_available = not temp_claimed
    if temp_claimed:
        temp_status = "claimed" if temp_status != "portal_claimed" else "portal_claimed"

    # Loyalty (rolling 24h)
    l_avail, l_next = True, None
    ld = ph.get("loyalty", {})
    l_status = ld.get("status", "unknown")
    lc_l = ld.get("last_claim")
    na_l = ld.get("next_available")
    if lc_l:
        cd_end = datetime.fromisoformat(lc_l) + timedelta(hours=LOYALTY_COOLDOWN_HOURS)
        if ist_now < cd_end:
            l_avail, l_next, l_status = False, format_time_until(cd_end), "claimed"
    if l_avail and na_l:
        nt = datetime.fromisoformat(na_l)
        if ist_now < nt:
            l_avail, l_next = False, format_time_until(nt)

    return {
        "daily_available": d_avail, "daily_next": d_next, "daily_status": d_status,
        "store_available": s_avail, "store_next": s_next, "store_status": s_status,
        "temp_store_available": temp_available, "temp_store_status": temp_status,
        "loyalty_available": l_avail, "loyalty_next": l_next, "loyalty_status": l_status,
    }


def all_claimable_on_cooldown(pid, has_loyalty):
    """
    Returns True only when every claimable reward is on cooldown AND
    progression was visited within the last 4 hours.
    Loyalty uses only last_claim — never next_available alone (can be poisoned).
    """
    PROGRESSION_CHECK_WINDOW_HOURS = 4

    s = get_reward_status(pid)
    # Never smart-skip an ID whose temporary reward has not been claimed; its
    # availability can only be confirmed by checking that account's live Store card.
    if (not s["daily_available"] and not any(s["store_available"])
            and not s.get("temp_store_available", True)):
        h   = load_claim_history()
        ph  = h.get(pid, {})
        ist = get_ist_time()

        # Progression: must have been visited within 4h
        last_visit = ph.get("progression", {}).get("last_visit")
        if last_visit:
            age_h = (ist - datetime.fromisoformat(last_visit)).total_seconds() / 3600
            if age_h >= PROGRESSION_CHECK_WINDOW_HOURS:
                log(f"🔄 {pid}: progression not checked in {age_h:.1f}h — opening browser")
                return False
        else:
            return False  # never visited — must open browser

        # Loyalty: only skip if we actually claimed it (last_claim within 24h)
        if not has_loyalty:
            return True
        lc_l = ph.get("loyalty", {}).get("last_claim")
        if lc_l:
            cd_end = datetime.fromisoformat(lc_l) + timedelta(hours=LOYALTY_COOLDOWN_HOURS)
            if ist < cd_end:
                return True
        return False  # loyalty uncertain — do NOT skip

    return False


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — CHROME DRIVER
# ═══════════════════════════════════════════════════════════════════════════════

def get_chrome_major_version():
    for binary in ["google-chrome", "google-chrome-stable", "chromium-browser", "chromium"]:
        try:
            res = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=5)
            if res.stdout.strip():
                v = int(res.stdout.strip().split()[-1].split(".")[0])
                log(f"🔍 Chrome v{v} detected via {binary}")
                return v
        except:
            continue
    log("⚠️ Chrome version detection failed — using uc auto-detect")
    return None


def create_driver():
    chrome_v = get_chrome_major_version()
    for attempt in range(3):
        try:
            opts = uc.ChromeOptions()
            if HEADLESS:
                opts.add_argument("--headless=new")
            opts.add_argument("--window-size=1920,1080")
            opts.add_argument("--no-sandbox")
            opts.add_argument("--disable-dev-shm-usage")
            opts.add_argument("--disable-blink-features=AutomationControlled")
            opts.add_argument("--disable-gpu")
            opts.add_argument("--disable-logging")
            opts.add_argument("--disable-notifications")
            opts.add_argument("--disable-popup-blocking")
            opts.add_argument("--remote-debugging-port=0")
            opts.add_experimental_option("prefs", {
                "profile.default_content_setting_values": {
                    "images": 2, "notifications": 2, "popups": 2
                }
            })
            kwargs = {"version_main": chrome_v} if chrome_v else {}
            driver = uc.Chrome(options=opts, use_subprocess=True, **kwargs)
            driver.set_page_load_timeout(30)
            driver.set_script_timeout(30)
            log(f"✅ Driver ready (Chrome v{chrome_v or 'auto'})")
            return driver
        except Exception as e:
            log(f"⚠️ Driver init attempt {attempt+1} failed: {str(e)[:100]}")
            time.sleep(2)
            if attempt == 2:
                raise


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — BROWSER HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def bypass_cloudflare(driver):
    try:
        title  = driver.title.lower()
        source = driver.page_source.lower()
        if "just a moment" not in title and "verifying" not in source:
            return
        log("🛡️ Cloudflare detected — waiting...")
        time.sleep(5)
        try:
            driver.find_elements(By.XPATH, "//input[@type='checkbox']")[0].click()
            time.sleep(3)
        except:
            pass
        for _ in range(10):
            if ("hub.vertigogames.co" in driver.current_url
                    and "verifying" not in driver.page_source.lower()):
                log("✅ Cloudflare cleared")
                return
            time.sleep(1)
    except:
        pass


def accept_cookies(driver):
    try:
        WebDriverWait(driver, 3).until(EC.element_to_be_clickable((
            By.XPATH,
            "//button[normalize-space()='Accept All' or contains(text(),'Accept') "
            "or contains(text(),'Allow') or contains(text(),'Consent')]"
        ))).click()
        time.sleep(0.3)
        log("✅ Cookies accepted")
    except:
        pass


_DISPLAY_NAME_CAPTURE_SCRIPT = r"""
/* CS_HUB_DISPLAY_NAME_CAPTURE_V2 */
const norm = value => String(value || '').replace(/\s+/g, ' ').trim();
const blocked = /^(?:critical strike(?: cs)?|online fps|official page|home|store|daily rewards|progression program|loyalty program|redeem code|content creator program|news|updates|events|achievements|about|faq|customer support|operated by aghanim|claim your daily rewards|iron|rp\s*\d+|menu)$/i;
const validName = value => {
  const text = norm(value);
  return !!(text.length >= 2 && text.length < 40 && !blocked.test(text)
    && !/^[0-9\s]+$/.test(text) && !/[\n\r/:]/.test(text) && /[\p{L}]/u.test(text));
};
const visible = el => {
  if (!el) return false;
  const r = el.getBoundingClientRect(), st = getComputedStyle(el);
  return !!(el.getClientRects().length && r.width > 0 && r.height > 0
    && st.display !== 'none' && st.visibility !== 'hidden' && Number(st.opacity || '1') > 0);
};
const directSelectors = [
  '[data-testid="username"]', '[data-testid="display-name"]',
  '[data-testid*="player-name"]', '[class*="username"]',
  '[class*="display-name"]', '[class*="player-name"]',
  '[class*="user-name"]', '[class*="nickname"]'
];
for (const selector of directSelectors) {
  for (const el of document.querySelectorAll(selector)) {
    if (!visible(el)) continue;
    const value = norm(el.innerText || el.textContent);
    if (validName(value)) return {name:value, source:'profile_name_selector', selector};
  }
}
const avatars = Array.from(document.querySelectorAll(
  'img[src*="avatar" i], img[src*="profile" i], img[alt*="avatar" i], img[alt*="profile" i]'
)).filter(visible);
const candidateSelector = 'h1,h2,h3,p,span,strong,bdi,div';
for (const avatar of avatars) {
  const ar = avatar.getBoundingClientRect();
  let root = avatar;
  for (let depth = 0; depth < 7 && root; depth++, root = root.parentElement) {
    const rr = root.getBoundingClientRect();
    if (rr.width > window.innerWidth * 0.85 || rr.height > window.innerHeight * 0.75) continue;
    const candidates = Array.from(root.querySelectorAll(candidateSelector)).filter(el => {
      if (!visible(el)) return false;
      const value = norm(el.innerText || el.textContent);
      if (!validName(value)) return false;
      const r = el.getBoundingClientRect();
      const dx = Math.abs((r.left + r.width/2) - (ar.left + ar.width/2));
      return dx <= Math.max(240, window.innerWidth * 0.18)
        && r.top >= ar.top - 24 && r.top <= ar.bottom + 190;
    }).map(el => {
      const r = el.getBoundingClientRect(), value = norm(el.innerText || el.textContent);
      const dx = Math.abs((r.left + r.width/2) - (ar.left + ar.width/2));
      const dy = Math.abs(r.top - (ar.bottom + 36));
      return {name:value, score:dx + dy * 0.8, depth};
    }).sort((a,b) => a.score - b.score || a.name.length - b.name.length);
    if (candidates.length) {
      return {name:candidates[0].name, source:'text_near_profile_avatar', depth};
    }
  }
}
// Last-resort scan is restricted to the profile area in the top-right corner.
const fallback = Array.from(document.querySelectorAll(candidateSelector)).filter(el => {
  if (!visible(el)) return false;
  const value = norm(el.innerText || el.textContent);
  if (!validName(value)) return false;
  const r = el.getBoundingClientRect();
  return r.left >= window.innerWidth * 0.66 && r.top >= 0
    && r.top <= window.innerHeight * 0.48
    && r.width <= Math.max(320, window.innerWidth * 0.28);
}).map(el => {
  const r = el.getBoundingClientRect();
  return {name:norm(el.innerText || el.textContent),
    score:Math.abs((r.left + r.width/2) - window.innerWidth * 0.84)
      + Math.abs(r.top - window.innerHeight * 0.16)};
}).sort((a,b) => a.score - b.score || a.name.length - b.name.length);
return fallback.length ? {name:fallback[0].name, source:'top_right_profile_fallback'} : null;
"""


def _normalize_display_name(value):
    """Reject blank or obvious portal labels while preserving legitimate IGN punctuation."""
    name = " ".join(str(value or "").split())
    if not (2 <= len(name) < 40) or name.isdigit():
        return None
    if any(char in name for char in ("/", ":", "\n", "\r")):
        return None
    blocked = {
        "critical strike", "critical strike cs", "online fps", "official page",
        "home", "store", "daily rewards", "progression program", "loyalty program",
        "redeem code", "content creator program", "news", "updates", "events",
        "achievements", "about", "faq", "customer support", "operated by aghanim",
        "claim your daily rewards", "iron", "menu",
    }
    folded = name.casefold()
    if folded in blocked or re.fullmatch(r"rp\s*\d+", folded):
        return None
    return name if any(char.isalpha() for char in name) else None


def capture_display_name(driver, *, attempts=5, poll_interval=0.35):
    """Poll the authenticated profile header briefly; return None if no credible IGN is visible."""
    try:
        attempts = max(1, min(8, int(attempts)))
    except (TypeError, ValueError, OverflowError):
        attempts = 5
    try:
        poll_interval = max(0.05, min(1.0, float(poll_interval)))
    except (TypeError, ValueError, OverflowError):
        poll_interval = 0.35

    last_error = None
    for attempt in range(attempts):
        try:
            result = driver.execute_script(_DISPLAY_NAME_CAPTURE_SCRIPT)
            candidate = result.get("name") if isinstance(result, dict) else result
            source = str(result.get("source") or "profile") if isinstance(result, dict) else "profile"
            display_name = _normalize_display_name(candidate)
            if display_name:
                log(f"👤 Display name captured: {display_name} (source={source})")
                return display_name
        except Exception as exc:
            last_error = type(exc).__name__
        if attempt + 1 < attempts:
            time.sleep(poll_interval)

    if last_error:
        log(f"⚠️ Display name capture unavailable after bounded polling ({last_error}); using cached name or masked ID")
    else:
        log("⚠️ Display name not yet present in the profile header after bounded polling; using cached name or masked ID")
    return None


def _find_player_id_element(driver):
    """Find the actual CS Hub player/User ID input, not unrelated text fields."""
    xpath = (
        "//input[not(translate(@type,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')='hidden') "
        "and ("
        "contains(translate(normalize-space(@placeholder), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'user id') "
        "or contains(translate(normalize-space(@placeholder), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'player id') "
        "or translate(normalize-space(@name), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')='playerid' "
        "or translate(normalize-space(@id), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')='playerid' "
        "or contains(translate(normalize-space(@aria-label), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'user id') "
        "or contains(translate(normalize-space(@aria-label), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'player id')"
        ")]"
    )
    try:
        for element in driver.find_elements(By.XPATH, xpath):
            try:
                if element.is_displayed() and element.is_enabled():
                    return element
            except StaleElementReferenceException:
                continue
    except Exception:
        pass
    return None


def _find_exact_login_trigger(driver):
    """Find a visibly-labelled Login trigger. Never fall back to arbitrary buttons."""
    xpaths = [
        "//button[normalize-space(.)='Login' or normalize-space(.)='Log in' or normalize-space(.)='Sign in']",
        "//a[normalize-space(.)='Login' or normalize-space(.)='Log in' or normalize-space(.)='Sign in']",
        "//*[@role='button' and (normalize-space(.)='Login' or normalize-space(.)='Log in' or normalize-space(.)='Sign in')]",
    ]
    for xpath in xpaths:
        try:
            for element in driver.find_elements(By.XPATH, xpath):
                try:
                    if element.is_displayed() and element.is_enabled():
                        return element
                except StaleElementReferenceException:
                    continue
        except Exception:
            continue
    return None


def login_to_hub(driver, pid):
    """Open the real login UI, enter the supplied player ID, and verify submission."""
    log("🔐 Opening CS Hub login...")
    try:
        driver.get("https://hub.vertigogames.co/daily-rewards")
        bypass_cloudflare(driver)
        try:
            WebDriverWait(driver, 15).until(
                lambda d: d.execute_script("return document.readyState") in ("interactive", "complete")
            )
        except TimeoutException:
            log("⚠️ Page readiness wait timed out; inspecting the current page")

        accept_cookies(driver)

        # Some portal sessions show the User ID modal automatically. Check before clicking.
        id_field = _find_player_id_element(driver)
        if id_field is None:
            login_button = _find_exact_login_trigger(driver)
            if login_button is None:
                log("❌ No exact, visible Login control found; refusing to click an unrelated button")
                capture_login_diagnostics(driver, "exact_login_trigger_not_found", force=True)
                return False

            try:
                driver.execute_script(
                    "arguments[0].scrollIntoView({block:'center', inline:'nearest'});", login_button
                )
                login_button.click()
            except Exception as click_error:
                log(f"❌ Could not activate the Login control: {type(click_error).__name__}")
                capture_login_diagnostics(driver, "login_trigger_click_failed", force=True)
                return False

            try:
                id_field = WebDriverWait(driver, 12).until(
                    lambda d: _find_player_id_element(d) or False
                )
            except TimeoutException:
                log("❌ Login control was clicked, but the User ID form did not appear")
                capture_login_diagnostics(driver, "login_form_not_opened", force=True)
                return False

        try:
            id_field.clear()
            id_field.send_keys(pid)
        except Exception as input_error:
            log(f"❌ Could not enter the Player ID: {type(input_error).__name__}")
            capture_login_diagnostics(driver, "player_id_input_failed", force=True)
            return False
        log("✅ Player ID entered")  # Do not write the raw ID into logs.

        submit_xpath = (
            "//button[normalize-space(.)='Login' or normalize-space(.)='Submit' "
            "or normalize-space(.)='Continue' or normalize-space(.)='Log in']"
        )
        submitted = False
        try:
            buttons = driver.find_elements(By.XPATH, submit_xpath)
        except Exception:
            buttons = []
        for button in buttons:
            try:
                if button.is_displayed() and button.is_enabled():
                    button.click()
                    submitted = True
                    break
            except Exception:
                continue

        if not submitted:
            try:
                id_field.send_keys(Keys.RETURN)
                submitted = True
            except Exception:
                log("❌ Could not submit the Player ID form")
                capture_login_diagnostics(driver, "login_form_submit_failed", force=True)
                return False

        # Login should close the ID form and remove the signed-out Login trigger.
        # Do not infer success merely because a click or form submission was attempted.
        try:
            WebDriverWait(driver, 15).until(
                lambda d: _find_player_id_element(d) is None
                and _find_exact_login_trigger(d) is None
            )
        except TimeoutException:
            log("❌ Login state did not change after submitting the Player ID")
            capture_login_diagnostics(driver, "login_not_confirmed", force=True)
            return False

        log("✅ Login state changed; continuing to the rewards pages")
        return True

    except Exception as exc:
        log(f"❌ Login error: {type(exc).__name__}: {str(exc)[:180]}")
        capture_login_diagnostics(driver, "login_exception", force=True)
        return False

def close_popup(driver):
    try:
        for sel in [
            "//button[contains(text(),'Close') or contains(text(),'×') "
            "or contains(@class,'close')]",
            "//div[contains(@class,'modal')]//button",
            "//*[@aria-label='Close' or @title='Close']",
        ]:
            try:
                for btn in driver.find_elements(By.XPATH, sel):
                    if btn.is_displayed():
                        btn.click()
                        time.sleep(0.3)
                        return
            except:
                continue
        driver.execute_script(
            "document.querySelectorAll('[class*=\"modal\"],[class*=\"overlay\"]')"
            ".forEach(m=>{ if(m.offsetParent!==null) m.click(); });"
        )
    except:
        pass

def _dismiss_store_promotion_popups(driver):
    """Use the conservative shared guard before interacting with Store promotions."""
    if not callable(getattr(driver, "execute_script", None)):
        return False, "webdriver_popup_inspection_unavailable"
    return dismiss_all_safe_popups(driver, log=log, max_popups=8, timeout=2.5)
    
def physical_click(driver, el, *, allow_js_fallback=True):
    """Scroll and click; preserve the route used for optional diagnostics."""
    trace = {"method": "not_dispatched", "attempted": []}

    def store_trace():
        try:
            driver._last_physical_click_trace = dict(trace)
        except Exception:
            pass

    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({behavior:'smooth',block:'center'});", el)
        time.sleep(0.5)
        trace["attempted"].append("webdriver_element_click")
        el.click()
        trace["method"] = "webdriver_element_click"
        store_trace()
        return True
    except Exception as webdriver_exc:
        trace["webdriver_error"] = type(webdriver_exc).__name__
        try:
            trace["attempted"].append("action_chains_click")
            ActionChains(driver).move_to_element(el).click().perform()
            trace["method"] = "action_chains_click"
            store_trace()
            return True
        except Exception as action_exc:
            trace["action_chains_error"] = type(action_exc).__name__
            if not allow_js_fallback:
                store_trace()
                return False
            try:
                trace["attempted"].append("javascript_click_fallback")
                driver.execute_script("arguments[0].click();", el)
                trace["method"] = "javascript_click_fallback"
                store_trace()
                return True
            except Exception as js_exc:
                trace["javascript_error"] = type(js_exc).__name__
                store_trace()
                return False



# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 7 — TIMER DETECTION (JS DOM)
# ═══════════════════════════════════════════════════════════════════════════════

def detect_daily_timer_js(driver):
    try:
        res = driver.execute_script("""
            function getNum(el){return parseInt((el.innerText||el.textContent||'').trim())||0;}
            let leafs=Array.from(document.querySelectorAll('*')).filter(e=>e.children.length===0);
            for(let el of leafs){
                let t=(el.innerText||'').trim().toLowerCase();
                if(t==='next reward in'||t==='next in'||t==='next reward'){
                    let c=el.parentElement;
                    for(let d=0;d<6&&c;d++){
                        let nums=Array.from(c.querySelectorAll('*')).filter(e=>{
                            let tx=(e.innerText||'').trim();
                            return /^\\d+$/.test(tx)&&e.children.length===0;
                        });
                        if(nums.length>=2){
                            let h=getNum(nums[0]),m=getNum(nums[1]);
                            if(h>0||m>0) return h+'h '+m+'m';
                        }
                        c=c.parentElement;
                    }
                }
            }
            return null;
        """)
        if res:
            d = parse_timer_text(res)
            if d and d.total_seconds() > 60:
                log(f"🔍 Daily timer: {res}")
                return d
    except Exception as e:
        log(f"⚠️ Daily timer JS error: {e}")
    return None


def detect_store_timers_js(driver):
    result = {}
    try:
        res = driver.execute_script("""
            var anchors={
                1:['gold (daily)','gold(daily)','5 gold','gold daily'],
                2:['cash (daily)','cash(daily)','500 cash','cash daily'],
                3:['luckyloon (daily)','luckyloon(daily)','10 luckyloon','luckyloon daily']
            };
            function findCard(kws){
                var els=Array.from(document.querySelectorAll('*'));
                var lbl=null;
                for(var i=0;i<els.length;i++){
                    var own=Array.from(els[i].childNodes)
                        .filter(n=>n.nodeType===3).map(n=>n.textContent).join('').trim().toLowerCase();
                    if(kws.some(k=>own.includes(k))&&own.length<35){lbl=els[i];break;}
                }
                if(!lbl)return 'not_found';
                var node=lbl;
                for(var d=0;d<15;d++){
                    node=node.parentElement;
                    if(!node||node===document.body)break;
                    if((node.innerText||'').toLowerCase().includes('next in')){
                        var ch=Array.from(node.querySelectorAll('*'));
                        for(var j=0;j<ch.length;j++){
                            var own2=Array.from(ch[j].childNodes)
                                .filter(n=>n.nodeType===3).map(n=>n.textContent).join('').trim();
                            if(own2.toLowerCase().includes('next in')&&own2.length<50)
                                return 'timer:'+(ch[j].innerText||ch[j].textContent||'').trim();
                        }
                        return 'timer:unknown';
                    }
                    if(d>=4){
                        var btns=node.querySelectorAll('button');
                        for(var b=0;b<btns.length;b++)
                            if((btns[b].innerText||'').trim().toLowerCase()==='free')return 'free';
                    }
                }
                return 'free';
            }
            var r={};
            for(var k in anchors)r[k]=findCard(anchors[k]);
            return r;
        """)
        if res:
            NAMES = {1:"Gold", 2:"Cash", 3:"Luckyloon"}
            for k, status in res.items():
                n = int(k)
                if status.startswith('timer:'):
                    txt = status[6:]
                    if txt != 'unknown':
                        d = parse_timer_text(txt)
                        if d and d.total_seconds() > 60:
                            result[n] = d
                            log(f"🔍 Store {NAMES[n]}: cooldown ({txt})")
                elif status == 'free':
                    log(f"🔍 Store {NAMES.get(n,n)}: Free")
    except Exception as e:
        log(f"⚠️ Store timer JS error: {e}")
    return result


def detect_loyalty_timer_js(driver):
    """
    Detects a real loyalty TIER cooldown timer.
    Searches inside [data-slider-item-id] tier cards first to avoid
    picking up Store Bonus 'Next in' timers from the page bottom.
    """
    try:
        res = driver.execute_script("""
            // First pass: tier card containers only
            var cards = Array.from(document.querySelectorAll('[data-slider-item-id]'));
            for(var i=0; i<cards.length; i++){
                var cardText = (cards[i].innerText||'').toLowerCase();
                if(cardText.includes('next in') && !cardText.includes('claim')){
                    var spans = Array.from(cards[i].querySelectorAll('*'));
                    for(var j=0; j<spans.length; j++){
                        var own = Array.from(spans[j].childNodes)
                            .filter(function(n){return n.nodeType===3;})
                            .map(function(n){return n.textContent;}).join('').trim();
                        if(own.toLowerCase().includes('next in') && own.length < 60)
                            return own;
                    }
                }
            }
            // Second pass: page-wide, skip Store Bonus context
            var els = Array.from(document.querySelectorAll('*'));
            for(var k=0; k<els.length; k++){
                var ownText = Array.from(els[k].childNodes)
                    .filter(function(n){return n.nodeType===3;})
                    .map(function(n){return n.textContent;}).join('').trim();
                if(ownText.toLowerCase().includes('next in') && ownText.length < 60){
                    var node = els[k].parentElement, isStore = false;
                    for(var d=0; d<6&&node; d++){
                        if((node.innerText||'').toLowerCase().includes('store bonus')){
                            isStore=true; break;
                        }
                        node = node.parentElement;
                    }
                    if(!isStore) return ownText;
                }
            }
            return null;
        """)
        if res:
            d = parse_timer_text(res)
            if d and d.total_seconds() > 60:
                log(f"🔍 Loyalty timer (tier): {res}")
                return d
    except Exception as e:
        log(f"⚠️ Loyalty timer JS error: {e}")
    return None


def detect_page_cooldowns(driver, pid, page_type):
    if page_type == "daily":
        d = detect_daily_timer_js(driver)
        if d and d.total_seconds() > 60:
            update_claim_history(pid, "daily", detected_cooldown=d)
    elif page_type == "store":
        tmap = detect_store_timers_js(driver)
        for card_n, d in tmap.items():
            if d is not None:
                update_claim_history(pid, "store", reward_index=card_n, detected_cooldown=d)
    elif page_type == "loyalty":
        d = detect_loyalty_timer_js(driver)
        if d and d.total_seconds() > 60:
            update_claim_history(pid, "loyalty", detected_cooldown=d)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 8 — CLAIMING FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def _capture_daily_claim_diagnostic(driver, stage, attempt=None, click_trace=None):
    """Capture browser evidence in memory; files are written only for unconfirmed Daily claims."""
    metadata = {
        "stage": str(stage),
        "attempt": attempt,
        "captured_at_utc": datetime.utcnow().isoformat(timespec="milliseconds") + "Z",
        "url": "",
        "title": "",
    }
    try:
        metadata["url"] = str(getattr(driver, "current_url", "") or "")
    except Exception as exc:
        metadata["url_error"] = type(exc).__name__
    try:
        metadata["title"] = str(getattr(driver, "title", "") or "")
    except Exception as exc:
        metadata["title_error"] = type(exc).__name__
    if click_trace:
        metadata["click_trace"] = click_trace
    for attribute, key in (
        ("_last_daily_target_trace", "daily_target"),
        ("_last_daily_popup_guard", "popup_guard"),
    ):
        try:
            value = getattr(driver, attribute, None)
            if isinstance(value, dict):
                metadata[key] = value
        except Exception:
            pass

    try:
        page_snapshot = driver.execute_script(r"""
            const controls = Array.from(document.querySelectorAll('button,[role="button"]'));
            const norm = value => (value || '').replace(/\s+/g, ' ').trim();
            const visible = el => {
                if (!el) return false;
                const style = getComputedStyle(el), rect = el.getBoundingClientRect();
                return !!(el.getClientRects().length && style.display !== 'none'
                    && style.visibility !== 'hidden' && Number(style.opacity || '1') > 0
                    && rect.width > 0 && rect.height > 0);
            };
            const bodyText = norm(document.body && document.body.innerText);
            const lines = (document.body && document.body.innerText || '')
                .split(/\n+/).map(line => norm(line)).filter(Boolean);
            const dailyButtons = Array.from(document.querySelectorAll(
                'button[data-variant="component-daily-reward"],[role="button"][data-variant="component-daily-reward"]'
            ));
            const dailyClaims = dailyButtons.filter(el =>
                norm(el.innerText || el.textContent).toLowerCase() === 'claim'
            );
            const inViewport = rect => rect.bottom > 0 && rect.right > 0
                && rect.top < window.innerHeight && rect.left < window.innerWidth;
            const dialogs = Array.from(document.querySelectorAll(
                '[role="dialog"],[aria-modal="true"],[class*="modal"],[class*="Modal"]'
            )).filter(visible).slice(0, 5).map(el => ({
                text:norm(el.innerText || el.textContent).slice(0, 220),
                class_name:String(el.className || '').slice(0, 120),
                role:el.getAttribute('role'),
                aria_modal:el.getAttribute('aria-modal')
            }));
            return {
                total_button_count:controls.length,
                claim_button_count:controls.filter(el =>
                    norm(el.innerText || el.textContent).toLowerCase() === 'claim').length,
                daily_component_button_count:dailyButtons.length,
                daily_claim_candidate_count:dailyClaims.length,
                daily_claim_candidates:dailyClaims.slice(0, 8).map(el => {
                    const rect = el.getBoundingClientRect();
                    const x = Math.max(0, Math.min(window.innerWidth - 1, rect.left + rect.width/2));
                    const y = Math.max(0, Math.min(window.innerHeight - 1, rect.top + rect.height/2));
                    const top = document.elementFromPoint(x, y);
                    return {
                        text:norm(el.innerText || el.textContent),
                        enabled:!el.disabled && el.getAttribute('aria-disabled') !== 'true',
                        in_viewport:inViewport(rect),
                        unobstructed:!!(top && (top === el || el.contains(top))),
                        bounding_box:{
                            x:Math.round(rect.x), y:Math.round(rect.y),
                            width:Math.round(rect.width), height:Math.round(rect.height)
                        },
                        outer_html_excerpt:(el.outerHTML || '').slice(0, 1000)
                    };
                }),
                dialogs,
                viewport:{
                    width:window.innerWidth, height:window.innerHeight,
                    scroll_x:window.scrollX, scroll_y:window.scrollY
                },
                buttons:controls.slice(0, 50).map((el,index) => {
                    const rect = el.getBoundingClientRect(), label = norm(el.innerText || el.textContent);
                    return {
                        index, tag:el.tagName.toLowerCase(), role:el.getAttribute('role'),
                        text:label.slice(0,240), visible:visible(el), in_viewport:inViewport(rect),
                        enabled:!el.disabled && el.getAttribute('aria-disabled') !== 'true',
                        disabled_property:!!el.disabled, aria_disabled:el.getAttribute('aria-disabled'),
                        bounding_box:{
                            x:Math.round(rect.x), y:Math.round(rect.y),
                            width:Math.round(rect.width), height:Math.round(rect.height)
                        },
                        outer_html_excerpt:(el.outerHTML || '').slice(0,1200)
                    };
                }),
                visible_page_text:bodyText.slice(0,5000),
                timer_text_lines:lines.filter(line =>
                    /daily|next in|next reward|cooldown|claimed|claim/i.test(line)
                ).slice(0,40)
            };
        """)
        if isinstance(page_snapshot, dict):
            metadata.update(page_snapshot)
        else:
            metadata["dom_capture_error"] = "unexpected_snapshot_type"
    except Exception as exc:
        metadata["dom_capture_error"] = type(exc).__name__

    screenshot = None
    try:
        screenshot = driver.get_screenshot_as_png()
        if not isinstance(screenshot, (bytes, bytearray)):
            screenshot = None
            metadata["screenshot_error"] = "unexpected_screenshot_type"
    except Exception as exc:
        metadata["screenshot_error"] = type(exc).__name__
    metadata["screenshot_captured"] = screenshot is not None
    return {"metadata": metadata, "screenshot": bytes(screenshot) if screenshot is not None else None}


def _persist_daily_claim_diagnostics(pid, evidence, failure_reason):
    """Persist collected evidence only after a Daily claim remains unconfirmed."""
    if not evidence:
        return None
    try:
        os.makedirs(DAILY_CLAIM_DIAGNOSTICS_DIR, exist_ok=True)
        player_last4 = str(pid)[-4:]
        run_id = re.sub(r"[^A-Za-z0-9_-]", "", os.getenv("GITHUB_RUN_ID", "local")) or "local"
        run_attempt = re.sub(r"[^A-Za-z0-9_-]", "", os.getenv("GITHUB_RUN_ATTEMPT", "1")) or "1"
        stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S%fZ")
        basename = f"daily-{player_last4}-{run_id}-a{run_attempt}-{stamp}"
        stages = []

        for index, item in enumerate(evidence, start=1):
            metadata = dict(item.get("metadata") or {})
            screenshot = item.get("screenshot")
            stage = re.sub(r"[^A-Za-z0-9_-]", "_", str(metadata.get("stage") or f"stage-{index}"))
            screenshot_name = None
            if isinstance(screenshot, (bytes, bytearray)):
                screenshot_name = f"{basename}-{index:02d}-{stage}.png"
                screenshot_path = os.path.join(DAILY_CLAIM_DIAGNOSTICS_DIR, screenshot_name)
                with open(screenshot_path, "wb") as image_file:
                    image_file.write(bytes(screenshot))
            metadata["screenshot_file"] = screenshot_name
            stages.append(metadata)

        payload = {
            "schema_version": 1,
            "player_id_last4": player_last4,
            "run_id": run_id,
            "run_attempt": run_attempt,
            "failure_reason": str(failure_reason)[:300],
            "captured_at_utc": datetime.utcnow().isoformat(timespec="milliseconds") + "Z",
            "stages": stages,
        }
        json_path = os.path.join(DAILY_CLAIM_DIAGNOSTICS_DIR, f"{basename}.json")
        with open(json_path, "w", encoding="utf-8") as json_file:
            json.dump(payload, json_file, indent=2, ensure_ascii=False)
        return json_path
    except Exception as exc:
        log(f"⚠️ Could not persist Daily claim diagnostics: {type(exc).__name__}: {str(exc)[:120]}")
        return None


def _daily_claim_is_already_visible_as_claimed(driver):
    """Recognize a manually-claimed Daily card even if its timer is unavailable."""
    try:
        return bool(driver.execute_script(r"""
            const norm = s => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
            const visible = el => {
                if (!el) return false;
                const st = getComputedStyle(el), r = el.getBoundingClientRect();
                return !!(el.getClientRects().length && st.display !== 'none'
                    && st.visibility !== 'hidden' && r.width > 0 && r.height > 0);
            };
            const buttons = Array.from(document.querySelectorAll('button,[role="button"]'));
            for (const button of buttons) {
                const label = norm(button.innerText || button.textContent);
                if (!visible(button) || !['claimed', 'already claimed'].includes(label)) continue;
                let node = button;
                for (let depth = 0; depth < 6 && node; depth++, node = node.parentElement) {
                    const text = norm(node.innerText || node.textContent);
                    if (text.length > 900) continue;
                    if (text.includes('daily reward') || text.includes('next reward in')) return true;
                }
            }
            return false;
        """))
    except Exception as exc:
        log(f"⚠️ Could not inspect Daily Claimed button: {type(exc).__name__}")
        return False


def _find_daily_claim_button(driver):
    """Find only an enabled Daily-reward component button, never Store Bonus controls."""
    try:
        result = driver.execute_script(r"""
            const norm = s => (s || '').replace(/\s+/g, ' ').trim();
            const visible = el => {
                if (!el) return false;
                const st = getComputedStyle(el), r = el.getBoundingClientRect();
                return !!(el.isConnected && el.getClientRects().length
                    && st.display !== 'none' && st.visibility !== 'hidden'
                    && Number(st.opacity || '1') > 0 && r.width > 0 && r.height > 0);
            };
            const text = el => norm(el && (el.innerText || el.textContent));
            const blockers = Array.from(document.querySelectorAll(
                '[role="dialog"],[aria-modal="true"],[class*="modal"],[class*="Modal"]'
            )).filter(visible);
            if (blockers.length) {
                return {status:'blocked', reason:'visible_dialog_present', blocker_count:blockers.length};
            }
            const dailyButtons = Array.from(document.querySelectorAll(
                'button[data-variant="component-daily-reward"],[role="button"][data-variant="component-daily-reward"]'
            )).filter(visible);
            const available = dailyButtons.filter(b =>
                text(b).toLowerCase() === 'claim'
                && !b.disabled && b.getAttribute('aria-disabled') !== 'true'
            );
            const cardText = button => {
                let node = button;
                for (let depth = 0; depth < 8 && node; depth++, node = node.parentElement) {
                    const value = text(node);
                    if (value && value.length <= 500
                        && (/extra reward/i.test(value) || /\bday\s+\d+\b/i.test(value))) {
                        return value.slice(0, 420);
                    }
                }
                return '';
            };
            const targetTrace = (button, extraPriority) => ({
                selector:'button[data-variant="component-daily-reward"]',
                index:dailyButtons.indexOf(button),
                card_text:cardText(button),
                button_text:text(button),
                button_html:(button.outerHTML || '').slice(0, 1200),
                available_count:available.length,
                extra_reward_priority:!!extraPriority
            });
            if (available.length > 1) {
                const extra = available.filter(b => /extra reward/i.test(cardText(b)));
                if (extra.length === 1) {
                    return {status:'available', button:extra[0], target:targetTrace(extra[0], true)};
                }
                return {status:'ambiguous', reason:'multiple_daily_claim_buttons', available_count:available.length};
            }
            if (available.length === 1) {
                const button = available[0];
                return {
                    status:'available', button,
                    target:targetTrace(button, /extra reward/i.test(cardText(button)))
                };
            }
            const allDailyClaimed = dailyButtons.length > 0 && dailyButtons.every(b =>
                ['claimed','already claimed'].includes(text(b).toLowerCase())
            );
            return {
                status:allDailyClaimed ? 'claimed' : 'not_found',
                daily_button_count:dailyButtons.length,
                claimed_button_count:dailyButtons.filter(b =>
                    ['claimed','already claimed'].includes(text(b).toLowerCase())
                ).length
            };
        """)
        if not isinstance(result, dict):
            return None, "error"
        trace = result.get("target") if isinstance(result.get("target"), dict) else result
        try:
            driver._last_daily_target_trace = dict(trace)
        except Exception:
            pass
        return result.get("button"), str(result.get("status") or "error")
    except Exception as exc:
        log(f"⚠️ Daily claim button lookup failed: {type(exc).__name__}")
        return None, "error"


def _dismiss_daily_reward_popups(driver):
    """Clear safely dismissible portal dialogs; fail closed if any blocker is ambiguous."""
    if not callable(getattr(driver, "execute_script", None)):
        # Test doubles are not live portal pages.
        return True, "non-browser test double"
    ok, reason = dismiss_all_safe_popups(
        driver, log=log, max_popups=8, timeout=3.0, poll_interval=0.15
    )
    try:
        driver._last_daily_popup_guard = {"ok":bool(ok), "reason":str(reason)[:1800]}
    except Exception:
        pass
    if not ok:
        log(f"⚠️ Daily page is blocked by a popup that could not be safely dismissed: {reason}")
    return bool(ok), str(reason)


_DAILY_TARGET_READY_SCRIPT = r"""
/* CS_HUB_DAILY_TARGET_READY_V1 */
const el = arguments[0];
if (!el || !el.isConnected) return {ready:false, reason:'target_detached'};
const r = el.getBoundingClientRect(), s = getComputedStyle(el);
const visible = !!(el.getClientRects().length && r.width > 0 && r.height > 0
  && s.display !== 'none' && s.visibility !== 'hidden'
  && Number(s.opacity || '1') > 0);
const fullyInViewport = r.top >= 0 && r.left >= 0
  && r.bottom <= window.innerHeight && r.right <= window.innerWidth;
const x = Math.max(0, Math.min(window.innerWidth - 1, r.left + r.width/2));
const y = Math.max(0, Math.min(window.innerHeight - 1, r.top + r.height/2));
const top = visible ? document.elementFromPoint(x, y) : null;
const unobstructed = !!(top && (top === el || el.contains(top)));
const dialogs = Array.from(document.querySelectorAll(
  '[role="dialog"],[aria-modal="true"],[class*="modal"],[class*="Modal"]'
)).filter(node => {
  const rr = node.getBoundingClientRect(), st = getComputedStyle(node);
  return !!(node.getClientRects().length && rr.width > 0 && rr.height > 0
    && st.display !== 'none' && st.visibility !== 'hidden'
    && Number(st.opacity || '1') > 0);
});
return {
  ready:visible && fullyInViewport && unobstructed && dialogs.length === 0
    && !el.disabled && el.getAttribute('aria-disabled') !== 'true',
  reason:!visible ? 'target_not_visible'
    : dialogs.length ? 'blocking_dialog_present'
    : !fullyInViewport ? 'target_outside_viewport'
    : !unobstructed ? 'target_obscured_at_click_point'
    : (el.disabled || el.getAttribute('aria-disabled') === 'true')
      ? 'target_disabled' : 'ready',
  rect:{x:Math.round(r.x),y:Math.round(r.y),width:Math.round(r.width),height:Math.round(r.height)},
  viewport:{width:window.innerWidth,height:window.innerHeight,scroll_x:window.scrollX,scroll_y:window.scrollY},
  hit_test:top ? {tag:top.tagName.toLowerCase(),text:(top.innerText||top.textContent||'').replace(/\s+/g,' ').trim().slice(0,100)} : null,
  blocker_count:dialogs.length
};
"""


def _prepare_daily_claim_target(driver, button, *, timeout=3.5):
    """Instant-scroll the exact Daily CTA and wait for an unobstructed viewport hit test."""
    if not callable(getattr(driver, "execute_script", None)):
        return True, {"ready":True,"reason":"non-browser test double"}
    trace = {"scroll_behavior":"auto","method":"scroll_into_view"}
    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({behavior:'auto',block:'center',inline:'nearest'});",
            button,
        )
        deadline = time.monotonic() + max(0.2, min(6.0, float(timeout)))
        last = None
        while time.monotonic() < deadline:
            last = driver.execute_script(_DAILY_TARGET_READY_SCRIPT, button)
            if isinstance(last, dict):
                trace.update(last)
                try:
                    driver._last_daily_target_trace = {
                        **dict(getattr(driver, "_last_daily_target_trace", {}) or {}),
                        "viewport_check":trace
                    }
                except Exception:
                    pass
                if last.get("ready"):
                    return True, trace
                if last.get("reason") in ("blocking_dialog_present","target_detached","target_disabled"):
                    return False, trace
            time.sleep(0.12)
        if isinstance(last, dict):
            trace.update(last)
        trace.setdefault("reason","viewport_wait_timeout")
        return False, trace
    except Exception as exc:
        trace["error"] = type(exc).__name__
        trace["reason"] = "viewport_preparation_failed"
        return False, trace


def _click_daily_claim_target(driver, button):
    """Native click only. Daily claims never use ActionChains or JavaScript click fallbacks."""
    trace = {"method":"not_dispatched","attempted":[]}
    try:
        trace["attempted"].append("webdriver_element_click")
        button.click()
        trace["method"] = "webdriver_element_click"
        try:
            driver._last_physical_click_trace = dict(trace)
        except Exception:
            pass
        return True
    except Exception as exc:
        trace["webdriver_error"] = type(exc).__name__
        try:
            driver._last_physical_click_trace = dict(trace)
        except Exception:
            pass
        return False


def _daily_portal_confirms_claim(driver):
    """Confirm Daily completion from its own live CTA; unrelated blockers never count as claimed."""
    button, state = _find_daily_claim_button(driver)
    if state == "available" and button is not None:
        return False
    if state == "claimed":
        return True
    if state in ("blocked", "ambiguous"):
        return False
    try:
        timer = detect_daily_timer_js(driver)
        if timer and timer.total_seconds() > 60:
            return True
    except Exception:
        pass
    return _daily_claim_is_already_visible_as_claimed(driver)


def _wait_for_daily_claim_confirmation(driver, timeout=6.0, poll_interval=0.5):
    deadline = time.monotonic() + max(0.0, float(timeout))
    while True:
        if _daily_portal_confirms_claim(driver):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(max(0.05, float(poll_interval)))


def _capture_daily_failure_stage(driver, evidence, stage, attempt, reason=None, click_trace=None):
    captured = _capture_daily_claim_diagnostic(
        driver, stage, attempt=attempt, click_trace=click_trace
    )
    if reason:
        captured.setdefault("metadata", {})["guard_reason"] = str(reason)[:1800]
    evidence.append(captured)
    return captured


def claim_daily_rewards(driver, pid, *, max_attempts=2):
    """Claim the exact Daily reward CTA only after popup and viewport safety checks."""
    log("🎁 Checking Daily Rewards against live portal state...")
    diagnostic_evidence = []
    try:
        max_attempts = max(1, min(2, int(max_attempts)))
    except (TypeError, ValueError, OverflowError):
        max_attempts = 2
    try:
        driver.get("https://hub.vertigogames.co/daily-rewards")
        bypass_cloudflare(driver)
        time.sleep(2)

        popup_ok, popup_reason = _dismiss_daily_reward_popups(driver)
        if not popup_ok:
            _capture_daily_failure_stage(
                driver, diagnostic_evidence, "initial_popup_blocked", 0, popup_reason
            )
            log("🚩 Daily claim deferred: popup could not be safely dismissed")
            update_claim_history(pid, "daily", unconfirmed=True)
            _persist_daily_claim_diagnostics(pid, diagnostic_evidence, "daily_initial_popup_blocked")
            return 0, False, True

        # Do not trust local history as proof. The live Daily page is authoritative.
        if _daily_portal_confirms_claim(driver):
            record_preexisting_portal_claim(
                driver, pid, "daily", "daily", "Daily Reward", "daily_initial_portal_state"
            )
            update_claim_history(pid, "daily", detected_cooldown=timedelta(seconds=61))
            log("⏩ Daily: portal confirms already claimed/on cooldown")
            driver.save_screenshot(f"daily_{pid}.png")
            return 0, True, False

        button, state = _find_daily_claim_button(driver)
        if state != "available" or button is None:
            _capture_daily_failure_stage(
                driver, diagnostic_evidence, "initial_state_unconfirmed", 0, state
            )
            update_claim_history(pid, "daily", unconfirmed=True)
            log(f"⚠️ Daily state could not be established (portal_state={state}); leaving it retryable")
            _persist_daily_claim_diagnostics(pid, diagnostic_evidence, f"initial_portal_state_unconfirmed:{state}")
            driver.save_screenshot(f"daily_{pid}.png")
            return 0, False, True

        for attempt in range(max_attempts):
            # Check/dismiss any overlay before resolving the actual Daily component CTA.
            popup_ok, popup_reason = _dismiss_daily_reward_popups(driver)
            if not popup_ok:
                _capture_daily_failure_stage(
                    driver, diagnostic_evidence, "popup_blocked_before_click", attempt + 1,
                    popup_reason
                )
                break

            button, state = _find_daily_claim_button(driver)
            if state == "claimed" or _daily_portal_confirms_claim(driver):
                update_claim_history(pid, "daily", detected_cooldown=timedelta(seconds=61))
                driver.save_screenshot(f"daily_{pid}.png")
                return 0, True, False
            if state != "available" or button is None:
                _capture_daily_failure_stage(
                    driver, diagnostic_evidence, "target_not_found_before_click", attempt + 1, state
                )
                break

            target_ready, target_trace = _prepare_daily_claim_target(driver, button)
            if not target_ready:
                _capture_daily_failure_stage(
                    driver, diagnostic_evidence, "target_not_ready", attempt + 1,
                    target_trace.get("reason")
                )
                log(f"🚩 Daily click deferred: target not safely interactable ({target_trace.get('reason')})")
                break

            # Scrolling may reveal a delayed popup. Clear it, then re-resolve/re-check
            # the exact Daily component CTA; never use a stale or covered element.
            popup_ok, popup_reason = _dismiss_daily_reward_popups(driver)
            if not popup_ok:
                _capture_daily_failure_stage(
                    driver, diagnostic_evidence, "popup_blocked_after_scroll", attempt + 1,
                    popup_reason
                )
                break

            button, state = _find_daily_claim_button(driver)
            if state != "available" or button is None:
                if state == "claimed" or _daily_portal_confirms_claim(driver):
                    update_claim_history(pid, "daily", detected_cooldown=timedelta(seconds=61))
                    driver.save_screenshot(f"daily_{pid}.png")
                    return 0, True, False
                _capture_daily_failure_stage(
                    driver, diagnostic_evidence, "target_changed_after_scroll", attempt + 1, state
                )
                break

            target_ready, target_trace = _prepare_daily_claim_target(driver, button)
            if not target_ready:
                _capture_daily_failure_stage(
                    driver, diagnostic_evidence, "target_not_ready_after_popup_check", attempt + 1,
                    target_trace.get("reason")
                )
                break

            pre_click_evidence = _capture_daily_claim_diagnostic(
                driver, "pre_click", attempt=attempt + 1
            )
            diagnostic_evidence.append(pre_click_evidence)
            clicked = bool(_click_daily_claim_target(driver, button))
            click_trace = dict(getattr(driver, "_last_physical_click_trace", {}) or {})
            pre_click_evidence["metadata"]["click_trace"] = click_trace
            if clicked:
                log(f"🖱️ Daily native click dispatched (attempt {attempt + 1}); awaiting portal confirmation")
            else:
                log(f"⚠️ Daily native click was not dispatched (attempt {attempt + 1}); no fallback click attempted")

            if _wait_for_daily_claim_confirmation(driver):
                if clicked:
                    update_claim_history(pid, "daily", claimed_count=1)
                    log("✅ Daily claim confirmed by portal")
                    driver.save_screenshot(f"daily_{pid}.png")
                    return 1, False, False
                update_claim_history(pid, "daily", detected_cooldown=timedelta(seconds=61))
                log("✅ Daily portal state changed to claimed; native dispatch result was ambiguous")
                driver.save_screenshot(f"daily_{pid}.png")
                return 0, True, False

            diagnostic_evidence.append(
                _capture_daily_claim_diagnostic(
                    driver, "post_click_unconfirmed", attempt=attempt + 1,
                    click_trace=click_trace,
                )
            )

            # A second attempt is permitted only after a fresh page read, a cleared
            # popup stack, exact target resolution, and another viewport verification.
            if attempt + 1 < max_attempts:
                try:
                    driver.refresh()
                    bypass_cloudflare(driver)
                    time.sleep(1.5)
                except Exception as refresh_exc:
                    log(f"⚠️ Daily refresh before retry failed: {type(refresh_exc).__name__}")
                    _capture_daily_failure_stage(
                        driver, diagnostic_evidence, "refresh_error", attempt + 1,
                        type(refresh_exc).__name__,
                        click_trace={"refresh_error": type(refresh_exc).__name__},
                    )
                    break

                popup_ok, popup_reason = _dismiss_daily_reward_popups(driver)
                if not popup_ok:
                    _capture_daily_failure_stage(
                        driver, diagnostic_evidence, "popup_blocked_after_refresh", attempt + 1,
                        popup_reason
                    )
                    break

                _capture_daily_failure_stage(driver, diagnostic_evidence, "post_refresh", attempt + 1)
                fresh_button, fresh_state = _find_daily_claim_button(driver)
                if fresh_state == "claimed" or _daily_portal_confirms_claim(driver):
                    update_claim_history(pid, "daily", detected_cooldown=timedelta(seconds=61))
                    driver.save_screenshot(f"daily_{pid}.png")
                    return 0, True, False
                if fresh_state == "available" and fresh_button is not None:
                    fresh_ready, fresh_trace = _prepare_daily_claim_target(driver, fresh_button)
                    if fresh_ready:
                        log("⚠️ Daily remains claimable after refresh and popup clearance; one retry will follow")
                        continue
                    _capture_daily_failure_stage(
                        driver, diagnostic_evidence, "retry_target_not_ready", attempt + 1,
                        fresh_trace.get("reason")
                    )
            break

        update_claim_history(pid, "daily", unconfirmed=True)
        log("⚠️ Daily claim was not confirmed by the portal; exact target remains retryable")
        diagnostic_path = _persist_daily_claim_diagnostics(
            pid, diagnostic_evidence, "daily_click_not_confirmed_after_allowed_attempts"
        )
        if diagnostic_path:
            log(f"🧪 Daily failure evidence saved: {diagnostic_path}")
        try:
            driver.save_screenshot(f"daily_{pid}.png")
        except Exception:
            pass
        return 0, False, True

    except Exception as exc:
        log(f"❌ Daily error: {type(exc).__name__}: {str(exc)[:160]}")
        try:
            update_claim_history(pid, "daily", unconfirmed=True)
        except Exception:
            pass
        diagnostic_evidence.append(
            _capture_daily_claim_diagnostic(
                driver, "exception", attempt=None,
                click_trace={"exception": type(exc).__name__},
            )
        )
        _persist_daily_claim_diagnostics(pid, diagnostic_evidence, f"exception:{type(exc).__name__}")
        return 0, False, True


STORE_DAILY_LABELS = {
    1: "Gold (Daily)",
    2: "Cash (Daily)",
    3: "Luckyloon (Daily)",
}

LEGACY_TEMP_STORE_REWARD_INDEX = 4  # Legacy state is preserved, never used to suppress live discovery.


def _find_store_daily_button(driver, reward_index):
    """Find a claim button inside one named daily Store card only.

    Returns (button, status) where status is available, claimed,
    label_not_found, button_not_found, or error. Ambiguous timers are not proof.
    The daily Store cards are
    resolved independently so a promotional card cannot be mistaken for one.
    """
    target = STORE_DAILY_LABELS[reward_index].lower()
    others = [label.lower() for idx, label in STORE_DAILY_LABELS.items() if idx != reward_index]
    try:
        result = driver.execute_script(r"""
            const target = (arguments[0] || '').toLowerCase();
            const others = arguments[1] || [];
            const norm = s => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
            const visible = el => {
                if (!el) return false;
                const st = window.getComputedStyle(el), r = el.getBoundingClientRect();
                return !!(el.getClientRects().length && st.visibility !== 'hidden'
                    && st.display !== 'none' && r.width > 0 && r.height > 0);
            };
            const leaves = Array.from(document.querySelectorAll('*')).filter(el =>
                el.children.length === 0 && norm(el.innerText || el.textContent).includes(target)
            );
            leaves.sort((a, b) => norm(a.innerText || a.textContent).length - norm(b.innerText || b.textContent).length);
            if (!leaves.length) return {status: 'label_not_found'};

            let node = leaves[0];
            for (let depth = 0; depth < 12 && node && node !== document.body; depth++, node = node.parentElement) {
                const text = norm(node.innerText || node.textContent);
                if (!text.includes(target)) continue;
                // Do not climb into a parent containing another daily reward card.
                if (others.some(label => text.includes(label))) continue;
                const buttons = Array.from(node.querySelectorAll('button, [role="button"]')).filter(visible);
                const states = buttons.map(b => ({
                    el: b,
                    text: norm(b.innerText || b.textContent || b.getAttribute('aria-label')),
                    disabled: !!b.disabled || b.getAttribute('aria-disabled') === 'true'
                }));
                if (states.some(x => ['claimed', 'already claimed'].includes(x.text)))
                    return {status: 'claimed'};
                // Some Store Bonus labels have their own "Next in" timer even when
                // the daily reward button is still Free. An enabled reward button wins.
                const btn = states.find(x => ['free', 'claim'].includes(x.text) && !x.disabled);
                if (btn) return {status: 'available', button: btn.el};
                // Do not infer the daily reward is claimed from a generic "Next in" timer.
                // Store Bonus badges can have their own timer while this card is still Free.
            }
            return {status: 'button_not_found'};
        """, target, others)
        if not result:
            return None, "error"
        return result.get("button"), result.get("status", "error")
    except Exception as exc:
        log(f"⚠️ Store card lookup failed for {STORE_DAILY_LABELS[reward_index]}: {type(exc).__name__}")
        return None, "error"


def _normalize_promotion_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def _is_standard_daily_store_card(title, card_text=""):
    """Exclude the three known daily Store rewards from promotional discovery."""
    text = _normalize_promotion_text(f"{title} {card_text}")
    patterns = (
        r"\bgold\s*\(daily\)", r"\bcash\s*\(daily\)", r"\bluckyloon\s*\(daily\)",
        r"\bgold\s+daily\b", r"\bcash\s+daily\b", r"\bluckyloon\s+daily\b",
    )
    return any(re.search(pattern, text) for pattern in patterns)


def _promotion_campaign_key(card):
    """Use a stable portal ID/URL when available; otherwise use a normalized title.

    A live enabled claim button remains authoritative even if a previous campaign
    with the same title is present in local history.
    """
    import hashlib
    stable_id = str(card.get("campaign_id") or card.get("href") or "").strip()
    title = _normalize_promotion_text(card.get("title"))
    source = f"id:{stable_id}|title:{title}" if stable_id else f"title:{title}"
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:20]
    return f"promo_{digest}"


def discover_promotional_cards(driver):
    """Discover promotion cards from the live Store DOM without hardcoded names.

    Only visible cards with a recognizable action state are surfaced. Daily Gold,
    Cash and Luckyloon cards are excluded. A card is never clicked unless its own
    visible action is explicitly Free/Claim and enabled.
    """
    script = r"""
        const norm = s => (s || '').replace(/\s+/g, ' ').trim().toLowerCase();
        const visible = el => {
            if (!el) return false;
            const st = getComputedStyle(el), r = el.getBoundingClientRect();
            return !!(el.getClientRects().length && st.display !== 'none'
                && st.visibility !== 'hidden' && r.width > 0 && r.height > 0);
        };
        const actionState = el => {
            const t = norm(el.innerText || el.textContent || el.getAttribute('aria-label'));
            if (['free', 'claim', 'claim reward', 'claim now'].includes(t))
                return {state: (el.disabled || el.getAttribute('aria-disabled') === 'true') ? 'unavailable' : 'available', action: t};
            if (['claimed', 'already claimed'].includes(t)) return {state: 'portal_claimed', action: t};
            if (['unavailable', 'not available'].includes(t)) return {state: 'unavailable', action: t};
            return null;
        };
        const standardDaily = text => {
            const t = norm(text);
            return /\bgold\s*\(daily\)/.test(t) || /\bcash\s*\(daily\)/.test(t)
                || /\bluckyloon\s*\(daily\)/.test(t)
                || /\bgold\s+daily\b/.test(t) || /\bcash\s+daily\b/.test(t)
                || /\bluckyloon\s+daily\b/.test(t);
        };
        const actionNodes = Array.from(document.querySelectorAll('button,[role="button"],a')).filter(visible);
        const candidates = [];
        for (const button of actionNodes) {
            const initialState = actionState(button);
            if (!initialState) continue;
            let node = button.parentElement, fallback = null, chosen = null;
            for (let depth = 0; depth < 14 && node && node !== document.body; depth++, node = node.parentElement) {
                const text = (node.innerText || node.textContent || '').trim();
                const normalized = norm(text);
                if (text.length < 5 || text.length > 1800) continue;
                if (standardDaily(text)) { chosen = null; fallback = null; break; }
                const nodeActions = Array.from(node.querySelectorAll('button,[role="button"],a')).filter(visible);
                const recognizedActions = nodeActions.filter(el => !!actionState(el));
                if (recognizedActions.length !== 1) continue;
                if (!fallback && normalized.length >= 12) fallback = node;
                const cls = typeof node.className === 'string' ? node.className.toLowerCase() : '';
                const hasHeading = !!node.querySelector('h1,h2,h3,h4,h5,h6,[class*="title"],[class*="name"]');
                const hasImage = !!node.querySelector('img');
                if (/(card|reward|offer|promo|product|item|campaign)/.test(cls) || (hasHeading && hasImage)) {
                    chosen = node;
                    break;
                }
            }
            const root = chosen || fallback;
            if (!root) continue;
            const rootText = (root.innerText || root.textContent || '').trim();
            if (rootText.length > 1800 || standardDaily(rootText)) continue;
            const lines = (root.innerText || '').split(/\n+/).map(x => x.trim()).filter(Boolean);
            const generic = new Set(['free','claim','claim reward','claim now','claimed','already claimed','unavailable','not available','store','rewards','reward']);
            let title = '';
            const headingNodes = Array.from(root.querySelectorAll('h1,h2,h3,h4,h5,h6,[class*="title"],[class*="name"],[data-testid*="title"]'))
                .filter(visible).map(el => (el.innerText || el.textContent || '').trim())
                .filter(t => t.length >= 3 && t.length <= 120 && !generic.has(norm(t)) && !standardDaily(t));
            if (headingNodes.length) {
                headingNodes.sort((a,b) => a.length - b.length);
                title = headingNodes[0];
            }
            if (!title) {
                title = lines.find(line => line.length >= 3 && line.length <= 120
                    && !generic.has(norm(line)) && !['sale','limited time','new'].includes(norm(line))
                    && !standardDaily(line)) || '';
            }
            if (!title || standardDaily(title, rootText)) continue;
            const stateInfo = actionState(button);
            if (!stateInfo) continue;
            const idParts = [
                root.getAttribute('data-promotion-id'), root.getAttribute('data-reward-id'),
                root.getAttribute('data-campaign-id'), root.getAttribute('data-offer-id'),
                root.getAttribute('data-testid'), root.id
            ].filter(Boolean);
            const link = root.matches('a[href]') ? root.href : (root.querySelector('a[href]') || {}).href || '';
            const image = root.querySelector('img');
            const imageRef = image ? (image.currentSrc || image.src || image.alt || '') : '';
            candidates.push({
                title: title,
                state: stateInfo.state,
                button_text: stateInfo.action,
                disabled: !!button.disabled || button.getAttribute('aria-disabled') === 'true',
                campaign_id: idParts[0] || '',
                href: link,
                image_ref: imageRef,
                button: button,
                card_text: rootText.slice(0, 500)
            });
        }
        const seen = new Set();
        return candidates.filter(c => {
            const k = norm(c.title) + '|' + (c.campaign_id || c.href || c.image_ref);
            if (seen.has(k)) return false;
            seen.add(k);
            return true;
        });
    """
    try:
        raw = driver.execute_script(script) or []
    except Exception as exc:
        try:
            driver._promotion_discovery_failed = True
        except Exception:
            pass
        log(f"⚠️ Dynamic promotion discovery failed: {type(exc).__name__}")
        return []

    found = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        card_text = str(item.get("card_text") or "")
        if not title or _is_standard_daily_store_card(title, card_text):
            continue
        state = str(item.get("state") or "unknown")
        if state not in ("available", "portal_claimed", "unavailable"):
            continue
        card = {
            "title": title,
            "state": state,
            "button_text": str(item.get("button_text") or ""),
            "button": item.get("button"),
            "disabled": bool(item.get("disabled")),
            "campaign_id": str(item.get("campaign_id") or ""),
            "href": str(item.get("href") or ""),
            "image_ref": str(item.get("image_ref") or ""),
            "card_text": card_text,
        }
        card["key"] = _promotion_campaign_key(card)
        if card["key"] in seen:
            continue
        seen.add(card["key"])
        found.append(card)
    return found


def _record_promotion_history(pid, card, status, claimed=False):
    """Record dynamic promotions independently; never overwrite legacy reward_4."""
    h = init_player_history(pid)
    history = h[pid].setdefault("promotions", {})
    key = card["key"]
    now = get_ist_time().isoformat()
    record = history.get(key, {}) if isinstance(history.get(key), dict) else {}
    previous_status = str(record.get("status") or "")
    if previous_status in ("claim_unconfirmed", "click_failed") and not record.get("last_unconfirmed_attempt_at"):
        # Preserve a trace of legacy unconfirmed bot clicks before overwriting their status.
        record["last_unconfirmed_attempt_at"] = record.get("last_seen_at") or now
    record.update({
        "title": card.get("title", "Unknown promotion"),
        "campaign_id": card.get("campaign_id") or None,
        "href": card.get("href") or None,
        "status": "claimed" if claimed else status,
        "last_seen_at": now,
    })
    if claimed:
        record["last_claim"] = now
        record.pop("last_unconfirmed_attempt_at", None)
    elif status in ("claim_unconfirmed", "click_failed"):
        record["last_unconfirmed_attempt_at"] = now
    elif status == "portal_claimed":
        record["portal_observed_at"] = now
    history[key] = record
    save_claim_history(h)


def _clear_promotion_unconfirmed_attempt(pid, card):
    """A live enabled Available card disproves an earlier pending bot click."""
    key = str((card or {}).get("key") or "").strip()
    if not key:
        return False
    history = init_player_history(pid)
    record = (history.get(pid, {}).get("promotions", {}) or {}).get(key)
    if not isinstance(record, dict):
        return False
    changed = False
    if record.pop("last_unconfirmed_attempt_at", None) is not None:
        changed = True
    if record.get("status") in ("claim_unconfirmed", "click_failed"):
        record["status"] = "available"
        changed = True
    if changed:
        record["last_available_confirmed_at"] = get_ist_time().isoformat()
        save_claim_history(history)
    return changed


def _same_promotion(candidate, expected):
    return candidate.get("key") == expected.get("key") or (
        _normalize_promotion_text(candidate.get("title")) == _normalize_promotion_text(expected.get("title"))
        and (not expected.get("campaign_id") or candidate.get("campaign_id") == expected.get("campaign_id"))
    )


def discover_and_claim_promotions(driver, pid):
    """Discover and claim eligible Store promotions using fail-closed popup handling.

    Every claim is preceded by a fresh safe-popup check. After a click, the same
    guard must clear any resulting dialog before the portal state is inspected.
    Promotion clicks never use the JavaScript fallback. An unknown popup stops
    processing immediately so no later offer is clicked through an obstruction.
    """
    popup_ok, popup_reason = _dismiss_store_promotion_popups(driver)
    if not popup_ok:
        log(f"⛔ Store promotions blocked by an unsafe popup; no promotion buttons clicked: {popup_reason}")
        return [{
            "key": "store-popup-blocked",
            "title": "Store promotion popup",
            "campaign_id": None,
            "status": "popup_blocked",
            "claim_count": 0,
            "claimable_observed": 0,
            "error": popup_reason,
        }]

    cards = discover_promotional_cards(driver)
    results = []
    for card in cards:
        if card.get("state") == "available" and not card.get("disabled"):
            _clear_promotion_unconfirmed_attempt(pid, card)
        entry = {
            "key": card["key"], "title": card["title"],
            "campaign_id": card.get("campaign_id") or None,
            "status": card["state"], "claim_count": 0,
            "claimable_observed": 1 if card["state"] == "available" and not card.get("disabled") else 0,
            "button_text": card.get("button_text", ""),
            "card_text": card.get("card_text", ""),
        }
        if card["state"] == "portal_claimed":
            entry["status"] = "portal_claimed"
            record_preexisting_portal_claim(
                driver, pid, "promotion", str(card.get("key") or ""),
                str(card.get("title") or "Store promotion"),
                "promotion_initial_portal_state",
            )
            _record_promotion_history(pid, card, "portal_claimed")
            results.append(entry)
            log(f"🎁 Promotion observed as already claimed: {card['title']}")
            continue
        if card["state"] == "unavailable" or card.get("disabled"):
            entry["status"] = "unavailable"
            _record_promotion_history(pid, card, "unavailable")
            results.append(entry)
            log(f"🎁 Promotion observed but unavailable: {card['title']}")
            continue

        confirmed = False
        click_failed = False
        popup_blocked = False
        for attempt in range(2):
            popup_ok, popup_reason = _dismiss_store_promotion_popups(driver)
            if not popup_ok:
                entry["status"] = "popup_blocked"
                entry["error"] = popup_reason
                popup_blocked = True
                log(f"⛔ Store promotion blocked before click ({card['title']}): {popup_reason}")
                break

            # Re-resolve the visible card/button immediately before clicking to avoid stale DOM references.
            current_cards = discover_promotional_cards(driver)
            current = next((x for x in current_cards if _same_promotion(x, card)), None)
            if not current:
                log(f"⚠️ Promotion disappeared before click; leaving unconfirmed: {card['title']}")
                break
            if current["state"] == "portal_claimed":
                entry["status"] = "portal_claimed"
                _record_promotion_history(pid, card, "portal_claimed")
                confirmed = True
                break
            button = current.get("button")
            if current["state"] != "available" or button is None or current.get("disabled"):
                entry["status"] = "unavailable"
                _record_promotion_history(pid, card, "unavailable")
                confirmed = True
                break
            try:
                click_ok = physical_click(driver, button, allow_js_fallback=False)
            except Exception as exc:
                log(f"⚠️ Promotion click attempt {attempt + 1} failed: {type(exc).__name__}")
                click_ok = False
            if not click_ok:
                click_failed = True
                time.sleep(1)
                continue

            log(f"🎁 Promotion claim clicked without JavaScript fallback: {card['title']} (attempt {attempt + 1})")
            time.sleep(2.5)
            popup_ok, popup_reason = _dismiss_store_promotion_popups(driver)
            if not popup_ok:
                entry["status"] = "popup_blocked"
                entry["error"] = popup_reason
                popup_blocked = True
                log(f"⛔ Store promotion result could not be safely inspected ({card['title']}): {popup_reason}")
                break

            time.sleep(1)
            after_cards = discover_promotional_cards(driver)
            after = next((x for x in after_cards if _same_promotion(x, card)), None)
            if after and after["state"] == "portal_claimed":
                entry["status"] = "claimed_this_run"
                entry["claim_count"] = 1
                _record_promotion_history(pid, card, "claimed_this_run", claimed=True)
                confirmed = True
                log(f"✅ Promotion claim confirmed by portal: {card['title']}")
                break
            if after and after["state"] == "available" and attempt == 0:
                log(f"⚠️ Promotion still appears claimable; retrying once: {card['title']}")
                continue
            log(f"⚠️ Promotion click was not confirmed by portal: {card['title']}")
            break

        if not confirmed:
            if not popup_blocked:
                entry["status"] = "claim_unconfirmed" if not click_failed else "click_failed"
            _record_promotion_history(pid, card, entry["status"])
        results.append(entry)
        if popup_blocked:
            log("⛔ Remaining Store promotions skipped because the popup guard did not establish a clear page")
            break
    return results

def _wait_for_store_daily_state(driver, reward_index, timeout=6.0, poll_interval=0.5):
    """Wait for a live Store card to become Claimed/on cooldown or remain Free."""
    deadline = time.monotonic() + max(0.0, float(timeout))
    last_state = "error"
    while True:
        _button, state = _find_store_daily_button(driver, reward_index)
        last_state = state or "error"
        if last_state in ("claimed", "cooldown"):
            return last_state
        if time.monotonic() >= deadline:
            return last_state
        time.sleep(max(0.05, float(poll_interval)))


def claim_store_rewards(driver, pid, *, reward_indices=None, max_attempts=2, scan_promotions=True):
    """Claim and verify the three daily Store cards plus dynamically discovered offers.

    Every run checks the live portal, even when local history says the card was
    claimed. Returns (confirmed_count, skip_flags, promotions, unconfirmed_indices,
    claimed_indices). A dispatched click is never counted without portal evidence.
    """
    try:
        max_attempts = max(1, min(2, int(max_attempts)))
    except (TypeError, ValueError, OverflowError):
        max_attempts = 2
    selected_indices = list(STORE_DAILY_LABELS)
    if reward_indices is not None:
        requested_indices = set()
        for raw_index in reward_indices:
            try:
                requested_indices.add(int(raw_index))
            except (TypeError, ValueError, OverflowError):
                continue
        selected_indices = [index for index in STORE_DAILY_LABELS if index in requested_indices]
    skip_flags = [False, False, False]
    claimed = 0
    unconfirmed_indices = []
    claimed_indices = []
    recovered_indices = []
    processed_indices = set()
    promotions = []
    if not selected_indices:
        return claimed, skip_flags, promotions, unconfirmed_indices, claimed_indices

    log("🏪 Reconciling live Store cards: Gold, Cash, Luckyloon and promotions")
    try:
        driver.get("https://hub.vertigogames.co/store")
        bypass_cloudflare(driver)
        time.sleep(2)
        close_popup(driver)

        for reward_index, label in STORE_DAILY_LABELS.items():
            if reward_index not in selected_indices:
                continue
            confirmed = False
            for attempt in range(max_attempts):
                if "store" not in str(getattr(driver, "current_url", "")).lower():
                    driver.get("https://hub.vertigogames.co/store")
                    bypass_cloudflare(driver)
                    time.sleep(2)
                    close_popup(driver)

                button, button_status = _find_store_daily_button(driver, reward_index)

                if button_status in ("claimed", "cooldown"):
                    record_preexisting_portal_claim(
                        driver, pid, "store", f"reward_{reward_index}", label,
                        "store_initial_portal_state",
                    )
                    update_claim_history(
                        pid, "store", reward_index=reward_index,
                        detected_cooldown=timedelta(seconds=61)
                    )
                    skip_flags[reward_index - 1] = True
                    confirmed = True
                    processed_indices.add(reward_index)
                    log(f"⏩ Store {label}: live portal confirms already claimed/on cooldown")
                    break

                if button_status != "available" or button is None:
                    log(f"⚠️ Store {label}: live card state is inconclusive (state={button_status})")
                    if attempt + 1 < max_attempts:
                        time.sleep(1)
                        continue
                    break

                clicked = bool(physical_click(driver, button))
                if clicked:
                    log(f"🖱️ Store {label}: click dispatched (attempt {attempt + 1}); verifying portal state")
                else:
                    log(f"⚠️ Store {label}: click helper returned false (attempt {attempt + 1}); checking state")
                time.sleep(1.5)
                close_popup(driver)
                portal_state = _wait_for_store_daily_state(driver, reward_index)

                if portal_state in ("claimed", "cooldown"):
                    # This card was freshly observed as available immediately before
                    # this attempt, so a confirmed state transition is a recovered
                    # reward even if the click helper raised after dispatch.
                    recovered_indices.append(reward_index)
                    if clicked:
                        update_claim_history(pid, "store", claimed_count=1, reward_index=reward_index)
                        claimed += 1
                        claimed_indices.append(reward_index)
                        log(f"✅ Store {label}: claim confirmed by portal")
                    else:
                        update_claim_history(
                            pid, "store", reward_index=reward_index,
                            detected_cooldown=timedelta(seconds=61)
                        )
                        log(f"✅ Store {label}: portal now confirms claimed; click dispatch was ambiguous")
                    # If our click helper returned false, the card is still portal-confirmed
                    # but must not be counted as a bot-verified claim made this run.
                    skip_flags[reward_index - 1] = not clicked
                    confirmed = True
                    processed_indices.add(reward_index)
                    break

                if portal_state == "available" and attempt + 1 < max_attempts:
                    # Force a server-backed page read before the one permitted retry.
                    refreshed = False
                    try:
                        driver.refresh()
                        bypass_cloudflare(driver)
                        time.sleep(1.5)
                        close_popup(driver)
                        refreshed = True
                    except Exception as refresh_exc:
                        log(f"⚠️ Store {label} refresh before retry failed: {type(refresh_exc).__name__}")
                    if not refreshed:
                        log(f"⚠️ Store {label}: refresh failed; not retrying against a potentially stale page")
                        break
                    fresh_button, fresh_state = _find_store_daily_button(driver, reward_index)
                    if fresh_state in ("claimed", "cooldown"):
                        recovered_indices.append(reward_index)
                        update_claim_history(
                            pid, "store", reward_index=reward_index,
                            detected_cooldown=timedelta(seconds=61)
                        )
                        skip_flags[reward_index - 1] = True
                        confirmed = True
                        processed_indices.add(reward_index)
                        log(f"✅ Store {label}: fresh page read confirms claimed/cooldown after the click")
                        break
                    if fresh_state == "available" and fresh_button is not None:
                        log(f"⚠️ Store {label} remains Free after refresh; one fresh retry will follow")
                        continue
                    log(f"⚠️ Store {label}: refresh did not establish a safe retry state ({fresh_state})")
                    break

                log(f"⚠️ Store {label}: claim was not confirmed (final portal state={portal_state})")
                break

            if not confirmed:
                skip_flags[reward_index - 1] = False
                unconfirmed_indices.append(reward_index)
                processed_indices.add(reward_index)
                update_claim_history(pid, "store", reward_index=reward_index, unconfirmed=True)
                log(f"🚩 Store {label}: marked unconfirmed and queued for a later live recheck")

        promotions = discover_and_claim_promotions(driver, pid) if scan_promotions else []
        log(f"📊 Daily Store: {claimed}/3 confirmed claims made this pass")
        if unconfirmed_indices:
            labels = ", ".join(STORE_DAILY_LABELS[i] for i in unconfirmed_indices)
            log(f"🚩 Daily Store items requiring attention: {labels}")
        log(f"🎁 Live promotional cards detected for this ID: {len(promotions)}")
        driver.save_screenshot(f"store_{pid}.png")
    except Exception as exc:
        log(f"❌ Store error: {type(exc).__name__}: {str(exc)[:180]}")
        try:
            driver._promotion_discovery_failed = True
        except Exception:
            pass
        # A Store page/navigation failure means all cards are unverified, not claimed.
        for reward_index in selected_indices:
            if reward_index not in processed_indices:
                unconfirmed_indices.append(reward_index)
                skip_flags[reward_index - 1] = False
                try:
                    update_claim_history(pid, "store", reward_index=reward_index, unconfirmed=True)
                except Exception:
                    pass
        promotions = []

    try:
        driver._store_daily_unconfirmed = list(unconfirmed_indices)
        driver._store_daily_claimed_indices = list(claimed_indices)
        driver._store_daily_recovered_indices = list(recovered_indices)
    except Exception:
        pass
    return claimed, skip_flags, promotions, unconfirmed_indices, claimed_indices


def _progression_claim_snapshot(driver):
    """Return visible Progression claim controls with card-level identity."""
    try:
        snapshot = driver.execute_script(r"""
            /* PROGRESSION_CARD_SNAPSHOT_V1 */
            const norm = value => (value || '').replace(/\s+/g, ' ').trim();
            const lower = value => norm(value).toLowerCase();
            const visible = el => {
              if (!el) return false;
              const r = el.getBoundingClientRect(), s = getComputedStyle(el);
              return !!(el.getClientRects().length && r.width > 0 && r.height > 0
                && s.display !== 'none' && s.visibility !== 'hidden'
                && Number(s.opacity || '1') > 0);
            };
            const cta = new Set(['claim', 'claim all']);
            const terminal = new Set([
              'delivered', 'already delivered', 'claimed', 'already claimed',
              'collected', 'redeemed'
            ]);
            const controls = Array.from(document.querySelectorAll('button,[role="button"]'));
            const isInDialog = el => !!el.closest(
              '[role="dialog"],[aria-modal="true"],[class*="modal"],[class*="Modal"]'
            );
            const cardInfo = button => {
              const buttonLabel = lower(button.innerText || button.textContent
                || button.getAttribute('aria-label'));
              let node = button.parentElement, best = null, bestText = '';
              for (let depth = 0; depth < 10 && node; depth++, node = node.parentElement) {
                const raw = norm(node.innerText || node.textContent || '');
                const childActions = Array.from(node.querySelectorAll('button,[role="button"]'))
                  .filter(visible)
                  .filter(b => cta.has(lower(b.innerText || b.textContent
                    || b.getAttribute('aria-label')))
                    || terminal.has(lower(b.innerText || b.textContent
                    || b.getAttribute('aria-label'))));
                if (childActions.length > 1) break;
                const rawLines = raw.split(/\n+/).map(norm).filter(Boolean);
                const hasTerminalLine = rawLines.some(line => terminal.has(line.toLowerCase()));
                if (((childActions.length === 1) || (childActions.length === 0 && hasTerminalLine))
                    && raw.length >= buttonLabel.length + 3 && raw.length <= 1800) {
                  best = node;
                  bestText = raw;
                }
              }
              best = best || button.parentElement || button;
              bestText = bestText || norm(best.innerText || best.textContent || '');
              const stableLines = bestText.split(/\n+/).map(norm).filter(line => line
                && !cta.has(line.toLowerCase()) && !terminal.has(line.toLowerCase())
                && !/^\d{1,2}:\d{2}(?::\d{2})?$/.test(line)
                && !/^next\s+(reward\s+)?in\b/i.test(line));
              const className = String(best.className || '').replace(/\s+/g, ' ').trim();
              const dataId = best.getAttribute('data-testid') || best.getAttribute('data-id') || '';
              const signature = (stableLines.join(' | ') || dataId || className || best.tagName || '')
                .slice(0, 900);
              return {card:best, card_text:bestText.slice(0, 1400), card_signature:signature};
            };
            const pointIsTopmost = button => {
              const r = button.getBoundingClientRect();
              const top = document.elementFromPoint(r.left + r.width/2, r.top + r.height/2);
              return !!(top && (top === button || button.contains(top)));
            };
            const claimable = controls.filter(button => visible(button) && !isInDialog(button)
              && cta.has(lower(button.innerText || button.textContent
                || button.getAttribute('aria-label')))
              && !button.disabled && button.getAttribute('aria-disabled') !== 'true'
              && pointIsTopmost(button));
            const deliveredControls = controls.filter(button => visible(button) && !isInDialog(button)
              && terminal.has(lower(button.innerText || button.textContent
                || button.getAttribute('aria-label'))));
            const claimableCards = claimable.map(button => {
              const info = cardInfo(button);
              return {button, button_text:lower(button.innerText || button.textContent),
                card_signature:info.card_signature, card_text:info.card_text};
            });
            const terminalTextElements = Array.from(document.querySelectorAll('span,div,p'))
              .filter(el => visible(el) && terminal.has(lower(el.innerText || el.textContent)));
            const deliveredSignatures = deliveredControls.map(button => cardInfo(button).card_signature)
              .concat(terminalTextElements.map(el => cardInfo(el).card_signature))
              .filter(Boolean);
            const path = (location.pathname || '').toLowerCase();
            const title = lower(document.title || '');
            const leadText = lower((document.body && document.body.innerText || '').slice(0, 900));
            const cloudflare = /cloudflare|just a moment|checking your browser|verify you are human/.test(title + ' ' + leadText);
            const validPage = path.includes('/progression-program') && !cloudflare;
            return {
              valid_page:validPage, url:location.href, title:document.title || '',
              claimable_count:claimableCards.length,
              delivered_count:deliveredControls.length,
              button:claimableCards.length ? claimableCards[0].button : null,
              button_text:claimableCards.length ? claimableCards[0].button_text : null,
              card_signature:claimableCards.length ? claimableCards[0].card_signature : null,
              card_text:claimableCards.length ? claimableCards[0].card_text : null,
              claimable_cards:claimableCards,
              delivered_signatures:deliveredSignatures,
              terminal_card_signatures:deliveredSignatures
            };
        """)
        if not isinstance(snapshot, dict):
            return None
        return snapshot
    except Exception as exc:
        log(f"⚠️ Progression state inspection failed: {type(exc).__name__}")
        return None


def _dismiss_progression_popups(driver):
    """Clear all safely dismissible layers; unknown blockers stop the claim."""
    # Small unit-test fakes without the WebDriver browser API are not live pages.
    # A real Selenium WebDriver always exposes execute_script; never fall back to
    # the generic production close_popup() when inspection fails.
    if not callable(getattr(driver, "execute_script", None)):
        return True, "non-browser test double"
    return dismiss_all_safe_popups(driver, log=log, max_popups=8, timeout=2.5)


def _scroll_progression_carousel(driver):
    """Advance horizontal reward carousels without dispatching any claim click."""
    try:
        moved = driver.execute_script(r"""
            const visible = el => {
              const r = el.getBoundingClientRect(), s = getComputedStyle(el);
              return !!(el.getClientRects().length && r.width > 0 && r.height > 0
                && s.display !== 'none' && s.visibility !== 'hidden');
            };
            const candidates = Array.from(document.querySelectorAll('div,section,ul'))
              .filter(el => visible(el) && el.scrollWidth > el.clientWidth + 40
                && el.clientWidth > 180 && el.clientHeight > 80
                && !el.closest('[role="dialog"],[aria-modal="true"]'))
              .sort((a,b) => (b.clientWidth*b.clientHeight) - (a.clientWidth*a.clientHeight));
            if (!candidates.length) return false;
            let changed = false;
            for (const el of candidates.slice(0, 4)) {
              const before = el.scrollLeft;
              const step = Math.max(260, Math.floor(el.clientWidth * 0.72));
              el.scrollLeft = Math.min(el.scrollWidth - el.clientWidth, before + step);
              if (el.scrollLeft > before) changed = true;
            }
            return changed;
        """)
        return bool(moved)
    except Exception as exc:
        log(f"⚠️ Progression carousel scan could not advance: {type(exc).__name__}")
        return False


def claim_progression_program_rewards(driver, pid, *, target_card_signature=None, max_claims_per_visit=6):
    """Claim card-bound Progression rewards with popup and portal verification."""
    log("🎯 Checking Progression Program...")
    result = claim_progression_rewards(
        driver,
        pid,
        log=log,
        bypass_cloudflare=bypass_cloudflare,
        snapshot_fn=_progression_claim_snapshot,
        click_fn=lambda d, button: physical_click(d, button, allow_js_fallback=False),
        update_history=update_claim_history,
        dismiss_popups=_dismiss_progression_popups,
        scroll_carousel=_scroll_progression_carousel,
        sleep=time.sleep,
        verify_polls=16,
        poll_seconds=0.5,
        max_claims_per_visit=max_claims_per_visit,
        target_card_signature=target_card_signature,
    )
    return result


def _record_grenade_balance_for_player(driver, pid, *, is_new_account, stats):
    """Read the cumulative portal counter and update only its separate baseline file."""
    observed_at = get_ist_time()
    snapshot = capture_progression_balance(driver, observed_at=observed_at)
    if not snapshot:
        log(f"⚠️ GRENADE_BALANCE=NOT_VALIDATED for {pid}: Reward Chain counter was not readable.")
        return {
            "status": "not_validated",
            "reason": "Authenticated Progression Reward Chain counter was not readable.",
        }

    claimed_indices = set()
    for value in stats.get("store_claimed_indices", []) or []:
        try:
            claimed_indices.add(int(value))
        except (TypeError, ValueError):
            continue
    for index, skipped in enumerate(stats.get("store_skipped", []) or [], start=1):
        if skipped:
            claimed_indices.add(index)
    store_fully_checked = bool(
        stats.get("standard_rewards_checked")
        and not (stats.get("store_unconfirmed_indices") or [])
        and claimed_indices.issuperset({1, 2, 3})
    )

    try:
        state = load_grenade_balance_state(GRENADE_BALANCE_FILE)
        updated, result = evaluate_progression_balance(
            snapshot,
            state,
            pid,
            reset_date=daily_reset_date(observed_at),
            is_new_account=bool(is_new_account),
            store_rewards_fully_checked=store_fully_checked,
        )
        save_grenade_balance_state(updated, GRENADE_BALANCE_FILE)
    except Exception as exc:
        log(f"⚠️ GRENADE_BALANCE=NOT_VALIDATED for {pid}: tracker state error ({type(exc).__name__}).")
        return {
            "status": "not_validated",
            "reason": f"Separate Grenade baseline state could not be read or saved ({type(exc).__name__}).",
        }

    result["store_rewards_fully_checked"] = store_fully_checked
    status = result.get("status")
    if status in {"below_minimum", "counter_regressed"}:
        log(
            f"🚩 GRENADE_BALANCE=ATTENTION for {pid}: "
            f"observed={result.get('current_count')} minimum={result.get('expected_minimum')} "
            f"deficit={result.get('deficit', 0)} status={status}"
        )
    elif status == "pass":
        log(
            f"✅ GRENADE_BALANCE=PASS for {pid}: "
            f"observed={result.get('current_count')} minimum={result.get('expected_minimum')} "
            f"next_milestone={result.get('next_milestone')}"
        )
    elif status == "pending_baseline":
        log(f"⏳ GRENADE_BALANCE=BASELINE_PENDING for {pid}: three Store rewards are not yet fully verified.")
    return result


def run_event_reward_checks(driver, pid, *, only_program_ids=None, retry_thresholds_by_program=None, max_claims_per_program=None):
    """Check active temporary point programmes for the currently logged-in ID."""
    try:
        event_results = claim_configured_event_rewards(
            driver,
            pid,
            log=log,
            physical_click=physical_click,
            bypass_cloudflare=bypass_cloudflare,
            close_popup=close_popup,
            only_program_ids=only_program_ids,
            retry_thresholds_by_program=retry_thresholds_by_program,
            max_claims_per_program=max_claims_per_program,
        )
    except Exception as exc:
        error = f"{type(exc).__name__}: {str(exc)[:180]}"
        log(f"❌ Event reward configuration/check failed for {pid}: {error}")
        event_results = [{
            "program_id": "event-config",
            "name": "Event rewards configuration",
            "slug": "",
            "status": "error",
            "balance": None,
            "next_threshold": None,
            "tiers_total": 0,
            "tiers_delivered": 0,
            "claims_this_run": [],
            "tiers": [],
            "retry_required": True,
            "error": error,
            "end_at": None,
        }]

    if event_results:
        history = init_player_history(pid)
        event_history = history[pid].setdefault("event_rewards", {})
        for event in event_results:
            program_id = str(event.get("program_id") or "unknown")
            previous = event_history.get(program_id, {})
            previous = previous if isinstance(previous, dict) else {}
            cycle = int(event.get("cycle", 0) or 0)
            now_iso = get_ist_time().isoformat()
            claim_ledger = merge_claim_ledger(
                previous.get("claim_ledger", []),
                event.get("claims_this_run", []),
                cycle=cycle,
                claimed_at=now_iso,
            )

            event_history[program_id] = {
                **previous,
                "name": event.get("name"),
                "slug": event.get("slug"),
                "points_label": event.get("points_label") or previous.get("points_label") or "points",
                "cycle": cycle,
                "checked_at": now_iso,
                "balance": event.get("balance"),
                "next_threshold": event.get("next_threshold"),
                "tiers_total": event.get("tiers_total", 0),
                "tiers_delivered": event.get("tiers_delivered", 0),
                "tiers": event.get("tiers", []),
                "claims_this_run": event.get("claims_this_run", []),
                "claim_ledger": claim_ledger,
                "status": event.get("status"),
                "retry_required": bool(event.get("retry_required")),
                "retry_thresholds": list(event.get("retry_thresholds") or []),
                "error": event.get("error"),
                "end_at": event.get("end_at"),
            }
        save_claim_history(history)
    return event_results


def claim_loyalty_program(driver, pid):
    """Returns (count_claimed, was_skipped)."""
    h    = load_claim_history()
    ld   = h.get(pid, {}).get("loyalty", {})
    lc_l = ld.get("last_claim")

    # Gate: only skip if we actually claimed within 24h
    if lc_l:
        cd_end = datetime.fromisoformat(lc_l) + timedelta(hours=LOYALTY_COOLDOWN_HOURS)
        if get_ist_time() < cd_end:
            log(f"⏩ Loyalty claimed recently. Next: {format_time_until(cd_end)}")
            return 0, True

    # Self-heal: corrupt next_available (set without real claim) → clear it
    if ld.get("next_available") and not lc_l:
        log(f"🔧 Healing corrupt loyalty cooldown for {pid}")
        history = load_claim_history()
        if pid in history and "loyalty" in history[pid]:
            history[pid]["loyalty"]["next_available"] = None
            history[pid]["loyalty"]["status"]         = "unknown"
            save_claim_history(history)

    log("🏆 Claiming Loyalty Program...")
    claimed = 0
    try:
        driver.get("https://hub.vertigogames.co/loyalty-program")
        bypass_cloudflare(driver)
        time.sleep(2)
        close_popup(driver)
        detect_page_cooldowns(driver, pid, "loyalty")

        for attempt in range(5):
            ok = driver.execute_script("""
                // Primary: tier card containers [data-slider-item-id]
                var cards = Array.from(document.querySelectorAll('[data-slider-item-id]'));
                for(var i=0; i<cards.length; i++){
                    var card = cards[i];
                    var cardText = (card.innerText||'').toLowerCase();
                    if(cardText.includes('delivered')) continue;
                    if(cardText.includes('next in')) continue;
                    var btns = card.querySelectorAll('button');
                    for(var j=0; j<btns.length; j++){
                        var btn = btns[j];
                        if(btn.disabled) continue;
                        var t=(btn.innerText||btn.textContent||'').trim().toLowerCase();
                        if(t!=='claim'&&t!=='free') continue;
                        btn.scrollIntoView({behavior:'smooth',block:'center',inline:'center'});
                        btn.click();
                        return 'card';
                    }
                }
                // Fallback: page-wide, exclude Store Bonus
                var allBtns = Array.from(document.querySelectorAll('button'));
                for(var k=0; k<allBtns.length; k++){
                    var btn2 = allBtns[k];
                    if(btn2.disabled) continue;
                    var t2=(btn2.innerText||btn2.textContent||'').trim().toLowerCase();
                    if(t2!=='claim'&&t2!=='free') continue;
                    var node=btn2.parentElement, cd=false;
                    for(var d=0; d<3&&node; d++){
                        var nt=(node.innerText||'').toLowerCase();
                        if(nt.includes('next in')||nt.includes('delivered')
                           ||nt.includes('store bonus')){cd=true;break;}
                        node=node.parentElement;
                    }
                    if(!cd){
                        btn2.scrollIntoView({behavior:'smooth',block:'center',inline:'center'});
                        btn2.click();
                        return 'fallback';
                    }
                }
                return false;
            """)
            if ok:
                log(f"✅ Loyalty Claimed (via {ok})")
                claimed += 1
                time.sleep(2)
                close_popup(driver)
                time.sleep(1)
            else:
                log(f"ℹ️  No claimable loyalty (attempt {attempt+1})")
                break

        if claimed > 0:
            update_claim_history(pid, "loyalty", claimed_count=claimed)
            time.sleep(1)
            detect_page_cooldowns(driver, pid, "loyalty")
        else:
            update_claim_history(pid, "loyalty", attempted=True)
            # LP-locked: no claimable tier — don't count in possible, no "Partial" alert
            log("🔒 Loyalty: no claimable tier — LP-locked (not counted in possible)")
            driver.save_screenshot(f"loyalty_{pid}.png")
            return 0, True

        driver.save_screenshot(f"loyalty_{pid}.png")
        log(f"📊 Loyalty: {claimed}")
    except Exception as e:
        log(f"❌ Loyalty error: {e}")
        return claimed, False

    return claimed, False


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 9 — PLAYER PROCESSING
# ═══════════════════════════════════════════════════════════════════════════════

def process_player(pid, has_loyalty, is_new, run_label):
    start = get_ist_time()
    stats = {
        "pid":             pid,
        "display_name":    None,   # captured after login; replaces raw ID in email
        "preexisting_portal_claims": [],
        "is_new":          is_new,
        "has_loyalty":     has_loyalty,
        "daily":           0,
        "store":           0,          # the 3 established daily Store cards
        "store_temp":      0,          # compatibility count: verified promotional claims this run
        "store_possible":  0,
        "store_temp_possible": 0,       # compatibility count: live claimable promotions seen
        "store_temp_skipped": True,
        "store_temp_status": "not_found",
        "promotions_observed": [],
        "promotion_scan_completed": False,
        "promotion_popup_blocked": False,
        "event_rewards": [],
        "event_reward_claims": 0,
        "event_rewards_retry_required": False,
        "processing_mode": "full_claim",
        "promotion_detection_failed": False,
        "retry_required": False,
        "progression":     0,
        "loyalty":         0,
        "daily_skipped":   False,
        "store_skipped":   [False, False, False],
        "loyalty_skipped": False,
        "skipped_all":     False,
        "status":          "Failed",
        "fail_reason":     None,
        "duration_s":      0,
        "possible":        0,
        "daily_portal_status": "unknown",
        "store_portal_status": ["unknown", "unknown", "unknown"],
        "daily_unconfirmed": False,
        "store_unconfirmed_indices": [],
        "store_claimed_indices": [],
        "progression_unconfirmed": False,
        "progression_deferred": False,
        "progression_deferred_reason": None,
        "progression_claim_attempted": False,
        "grenade_balance": None,
        "standard_rewards_checked": False,
        "progression_checked": True,
    }

    init_player_history(pid)

    # Do not smart-skip any ID: a future promotion may appear even when all daily
    # rewards are on cooldown. Every full-roster cycle inspects the live Store page.

    driver = None
    try:
        log(f"\n🚀 {pid}" + (" 🆕 NEW ID" if is_new else "") + f"  [{run_label}]")
        driver = create_driver()

        if not login_to_hub(driver, pid):
            stats["status"]      = "Login Failed"
            stats["fail_reason"] = "Could not authenticate"
            stats["duration_s"] = int((get_ist_time() - start).total_seconds())
            return stats

        # Capture display name right after login — used in email instead of raw player ID
        stats["display_name"] = capture_display_name(driver)
        stats["display_name_captured_at_ist"] = get_ist_time().isoformat() if stats["display_name"] else None
        driver._portal_claim_audit_enabled = True
        driver._portal_claim_display_name = stats["display_name"]
        driver._portal_claim_run_label = run_label
        driver._preexisting_portal_claims = []

        # Daily
        d, d_skip, d_unconfirmed = claim_daily_rewards(driver, pid)
        stats["daily"] = d
        stats["daily_skipped"] = d_skip
        stats["daily_unconfirmed"] = bool(d_unconfirmed)
        if not d_skip:
            stats["possible"] += 1

        # Store — each established daily reward is targeted by its visible card label.
        # Reset the live-detection flag for this account; the dynamic scanner sets it
        # when the live Store DOM could not be inspected reliably.
        driver._promotion_discovery_failed = False
        (store_count, store_skips, promotions,
         store_unconfirmed, store_claimed_indices) = claim_store_rewards(driver, pid)
        stats["store"] = store_count
        stats["store_skipped"] = store_skips
        stats["store_unconfirmed_indices"] = list(store_unconfirmed)
        stats["store_claimed_indices"] = list(store_claimed_indices)
        stats["standard_rewards_checked"] = True
        stats["promotions_observed"] = promotions
        stats["promotion_scan_completed"] = True
        stats["promotion_popup_blocked"] = any(promo.get("status") == "popup_blocked" for promo in promotions)
        stats["promotion_detection_failed"] = bool(getattr(driver, "_promotion_discovery_failed", False))
        stats["store_temp"] = sum(1 for promo in promotions if promo.get("status") == "claimed_this_run")
        stats["store_temp_skipped"] = not bool(promotions)
        stats["store_temp_possible"] = sum(int(promo.get("claimable_observed", 0) or 0) for promo in promotions)
        stats["store_temp_status"] = "not_found" if not promotions else "observed"
        stats["store_possible"] = sum(1 for sk in stats["store_skipped"] if not sk)
        stats["possible"] += stats["store_possible"] + stats["store_temp_possible"]

        if stats["store"] > 0 or stats["store_temp"] > 0:
            log("⏳ Waiting for server to process Store/promotion claims...")
            time.sleep(3)

        # Progression
        driver._progression_unconfirmed = False
        p = claim_progression_program_rewards(driver, pid)
        stats["progression"] = p
        stats["progression_unconfirmed"] = bool(getattr(driver, "_progression_unconfirmed", False))
        stats["progression_unconfirmed_card_signature"] = getattr(driver, "_progression_unconfirmed_card_signature", None)
        stats["progression_deferred"] = bool(getattr(driver, "_progression_deferred", False))
        stats["progression_deferred_reason"] = getattr(driver, "_progression_deferred_reason", None)
        stats["progression_claim_attempted"] = bool(getattr(driver, "_progression_claim_attempted", False))
        stats["progression_checked"] = bool(getattr(driver, "_progression_checked", not stats["progression_deferred"]))
        stats["grenade_balance"] = _record_grenade_balance_for_player(
            driver, pid, is_new_account=is_new, stats=stats
        )

        # Loyalty
        if has_loyalty:
            l, l_skip = claim_loyalty_program(driver, pid)
            stats["loyalty"]         = l
            stats["loyalty_skipped"] = l_skip
            if not l_skip:
                stats["possible"] += 1
        else:
            stats["loyalty_skipped"] = True
            log("ℹ️  Loyalty not enrolled for this ID")

        # Limited-time point programmes run last, using the existing authenticated browser session.
        stats["event_rewards"] = run_event_reward_checks(driver, pid)
        stats["event_reward_claims"] = event_claim_count(stats["event_rewards"])
        stats["event_rewards_retry_required"] = event_retry_required(stats["event_rewards"])
        if stats["event_rewards_retry_required"]:
            stats["retry_required"] = True
            stats["fail_reason"] = "One or more limited-time event reward claims could not be confirmed"

        # Determine status
        claimed_now = (stats["daily"] + stats["store"] + stats.get("store_temp", 0)
                       + stats.get("loyalty", 0) + stats.get("event_reward_claims", 0))
        possible    = stats["possible"]
        if possible == 0:
            stats["status"] = "Success" if stats["event_reward_claims"] > 0 else "All Skipped (Cooldown)"
        elif claimed_now >= possible:
            stats["status"] = "Success"
        elif claimed_now > 0:
            stats["status"] = "Partial"
        else:
            stats["status"] = "No Rewards"

        standard_claim_unconfirmed = bool(
            stats.get("daily_unconfirmed")
            or stats.get("store_unconfirmed_indices")
            or stats.get("progression_unconfirmed")
        )
        if standard_claim_unconfirmed:
            stats["retry_required"] = True
            if stats["status"] not in ("Login Failed", "Error", "Failed"):
                stats["status"] = "Partial"
            stats["fail_reason"] = "One or more standard rewards were not confirmed by the live portal"

        promotion_unconfirmed = any(
            promo.get("status") in ("claim_unconfirmed", "click_failed", "available")
            for promo in promotions
        )
        if (stats["promotion_detection_failed"] or promotion_unconfirmed
                or stats["event_rewards_retry_required"]):
            stats["retry_required"] = True
            if stats["status"] not in ("Login Failed", "Error", "Failed"):
                stats["status"] = "Partial"
            if not stats.get("fail_reason"):
                stats["fail_reason"] = (
                    "Dynamic promotional-card detection was inconclusive"
                    if stats["promotion_detection_failed"]
                    else ("At least one promotional claim was not confirmed by the portal"
                          if promotion_unconfirmed else "One or more event reward claims could not be confirmed")
                )

        total_inc_prog = _player_claim_total(stats)
        log(f"🎉 {pid}: {total_inc_prog} claimed "
            f"(D:{stats['daily']} S:{stats['store']} Promotions:{stats['store_temp']} "
            f"P:{stats['progression']} L:{stats['loyalty']} Event:{stats['event_reward_claims']})")

    except Exception as e:
        log(f"❌ Error: {e}")
        stats["status"]      = "Error"
        stats["fail_reason"] = str(e)[:120]
    finally:
        if driver:
            stats["preexisting_portal_claims"] = list(
                getattr(driver, "_preexisting_portal_claims", []) or []
            )
            try:
                driver.quit()
            except:
                pass

    stats["duration_s"] = int((get_ist_time() - start).total_seconds())

    snap = get_reward_status(pid)
    stats["store_next"]   = snap["store_next"]
    stats["daily_next"]   = snap["daily_next"]
    stats["loyalty_next"] = snap.get("loyalty_next")
    stats["daily_portal_status"] = snap.get("daily_status", "unknown")
    stats["store_portal_status"] = snap.get("store_status", ["unknown", "unknown", "unknown"])

    return stats



def _event_retry_targets(event_results):
    """Return exact programme/tier targets that failed portal verification this pass."""
    targets = {}
    for event in event_results or []:
        if not isinstance(event, dict) or not event.get("retry_required"):
            continue
        program_id = str(event.get("program_id") or "").strip()
        if not program_id:
            continue
        thresholds = []
        for raw in (event.get("retry_thresholds") or []):
            try:
                threshold = int(raw)
            except (TypeError, ValueError, OverflowError):
                continue
            if threshold >= 0 and threshold not in thresholds:
                thresholds.append(threshold)
        if thresholds:
            targets[program_id] = thresholds
    return targets



def _recovery_targets_from_result(result):
    """Serialize only the unresolved target identities needed by one later recovery pass."""
    result = result if isinstance(result, dict) else {}
    status = str(result.get("status") or "")
    store_indices = []
    for raw in result.get("store_unconfirmed_indices") or []:
        try:
            index = int(raw)
        except (TypeError, ValueError, OverflowError):
            continue
        if index in STORE_DAILY_LABELS and index not in store_indices:
            store_indices.append(index)

    event_results = [
        dict(item) for item in (result.get("event_rewards") or [])
        if isinstance(item, dict) and item.get("retry_required")
    ]
    event_targets = _event_retry_targets(event_results)
    store_indices.sort()
    return {
        "login_retry": status == "Login Failed",
        "processing_error": status in ("Error", "Failed") and status != "Login Failed",
        "daily_unconfirmed": bool(result.get("daily_unconfirmed")),
        "store_unconfirmed_indices": store_indices,
        "progression_unconfirmed": bool(result.get("progression_unconfirmed")),
        "progression_card_signature": str(
            result.get("progression_unconfirmed_card_signature") or ""
        ).strip() or None,
        "event_retry_required": bool(
            result.get("event_rewards_retry_required") or event_results
        ),
        "event_thresholds_by_program": event_targets,
        "event_rewards": event_results,
    }


def _recovery_result_from_saved_targets(pid, targets):
    """Reconstruct a minimal result object from persisted, exact reward targets."""
    targets = targets if isinstance(targets, dict) else {}
    event_results = [
        {**dict(item), "claims_this_run": []}
        for item in (targets.get("event_rewards") or [])
        if isinstance(item, dict)
    ]
    event_thresholds = targets.get("event_thresholds_by_program")
    if isinstance(event_thresholds, dict):
        known_programs = {
            str(item.get("program_id") or "") for item in event_results
        }
        for program_id, thresholds in event_thresholds.items():
            program_id = str(program_id or "").strip()
            if not program_id or program_id in known_programs:
                continue
            event_results.append({
                "program_id": program_id,
                "retry_required": True,
                "retry_thresholds": list(thresholds or []),
                "claims_this_run": [],
                "tiers": [],
            })

    store_indices = []
    for raw in targets.get("store_unconfirmed_indices") or []:
        try:
            index = int(raw)
        except (TypeError, ValueError, OverflowError):
            continue
        if index in STORE_DAILY_LABELS and index not in store_indices:
            store_indices.append(index)

    login_retry = bool(targets.get("login_retry"))
    processing_error = bool(targets.get("processing_error"))
    daily_pending = bool(targets.get("daily_unconfirmed"))
    progression_pending = bool(targets.get("progression_unconfirmed"))
    progression_signature = str(targets.get("progression_card_signature") or "").strip() or None
    event_pending = bool(targets.get("event_retry_required") or any(
        item.get("retry_required") for item in event_results
    ))
    return {
        "pid": pid,
        "display_name": None,
        "preexisting_portal_claims": [],
        "is_new": False,
        "has_loyalty": False,
        "processing_error": processing_error,
        "processing_mode": "full_claim",
        "status": "Login Failed" if login_retry else ("Error" if processing_error else "Partial"),
        "fail_reason": "Targeted post-run recovery from the saved failure identities",
        "daily": 0,
        "daily_skipped": False,
        "daily_unconfirmed": daily_pending,
        "store": 0,
        "store_skipped": [False, False, False],
        "store_unconfirmed_indices": store_indices,
        "store_claimed_indices": [],
        "store_possible": len(store_indices),
        "store_temp": 0,
        "store_temp_possible": 0,
        "store_temp_skipped": True,
        "store_temp_status": "not_found",
        "promotions_observed": [],
        "promotion_scan_completed": False,
        "promotion_detection_failed": False,
        "progression": 0,
        "progression_unconfirmed": progression_pending,
        "progression_unconfirmed_card_signature": progression_signature,
        "progression_deferred": False,
        "progression_deferred_reason": None,
        "progression_claim_attempted": False,
        "progression_checked": True,
        "loyalty": 0,
        "loyalty_skipped": True,
        "event_rewards": event_results,
        "event_reward_claims": event_claim_count(event_results),
        "event_rewards_retry_required": event_pending,
        "retry_required": True,
        "standard_rewards_checked": True,
        "possible": (
            int(daily_pending) + len(store_indices) + int(progression_pending)
            + sum(
                len(item.get("retry_thresholds") or [])
                for item in event_results if isinstance(item, dict)
            )
        ),
        "duration_s": 0,
        "daily_portal_status": "unknown",
        "store_portal_status": ["unknown", "unknown", "unknown"],
    }


def recover_player_from_saved_targets(pid, has_loyalty, is_new, run_label, targets):
    """Run post-primary recovery only from the target identities saved by that run.

    Returns (result, coverage_was_rechecked). Missing/invalid target metadata is
    explicitly deferred; it never falls back to process_player() or a full claim.
    """
    if not isinstance(targets, dict):
        result = {
            "pid": pid,
            "display_name": None,
            "preexisting_portal_claims": [],
            "is_new": is_new,
            "has_loyalty": has_loyalty,
            "processing_mode": "full_claim",
            "status": "Partial",
            "fail_reason": "Post-run recovery deferred: exact failed reward targets were not saved",
            "retry_required": True,
            "recovery_deferred": True,
            "daily": 0,
            "store": 0,
            "store_temp": 0,
            "progression": 0,
            "loyalty": 0,
            "daily_unconfirmed": False,
            "store_unconfirmed_indices": [],
            "progression_unconfirmed": False,
            "event_rewards": [],
            "event_reward_claims": 0,
            "event_rewards_retry_required": False,
            "promotions_observed": [],
            "duration_s": 0,
            "immediate_recovery": {
                "attempted": False,
                "status": "deferred",
                "categories": ["Saved target metadata missing"],
                "resolved_categories": [],
                "unresolved_categories": ["Saved target metadata missing"],
                "reason": "The recovery worker refused to run the normal full-account claim path because exact failed targets were unavailable.",
                "click_limit_per_target": 1,
            },
        }
        log(f"⏭️ Post-run recovery deferred for {pid}: no saved target identities; no reward clicks attempted")
        return result, False

    target_result = _recovery_result_from_saved_targets(pid, targets)
    has_safe_target = bool(
        target_result.get("status") == "Login Failed"
        or target_result.get("processing_error")
        or target_result.get("daily_unconfirmed")
        or target_result.get("store_unconfirmed_indices")
        or target_result.get("progression_unconfirmed")
        or target_result.get("event_rewards_retry_required")
    )
    if not has_safe_target:
        target_result["status"] = "Partial"
        target_result["recovery_deferred"] = True
        target_result["immediate_recovery"] = {
            "attempted": False,
            "status": "deferred",
            "categories": ["No actionable saved target"],
            "resolved_categories": [],
            "unresolved_categories": ["No actionable saved target"],
            "reason": "Saved recovery metadata did not contain a recognized reward target; no reward clicks attempted.",
            "click_limit_per_target": 1,
        }
        log(f"⏭️ Post-run recovery deferred for {pid}: saved target metadata contained no actionable target")
        return target_result, False

    # This invokes the same target-scoped fresh-session recovery as the immediate
    # in-run pass. Login failure is the sole permitted full-account retry because
    # the original attempt never reached a reward page.
    recovered = immediate_recover_player_claims(
        pid, has_loyalty, is_new, run_label, target_result
    )
    return recovered, True


def _merge_immediate_event_results(original_results, recovered_results):
    """Merge targeted event results while preserving claims made earlier in this run."""
    merged = {
        str(item.get("program_id") or ""): dict(item)
        for item in (original_results or [])
        if isinstance(item, dict) and str(item.get("program_id") or "")
    }
    for recovered in recovered_results or []:
        if not isinstance(recovered, dict):
            continue
        program_id = str(recovered.get("program_id") or "")
        if not program_id:
            continue
        previous = merged.get(program_id, {})
        old_claims = list(previous.get("claims_this_run") or [])
        new_claims = list(recovered.get("claims_this_run") or [])
        seen = set()
        combined_claims = []
        for claim in old_claims + new_claims:
            if not isinstance(claim, dict):
                continue
            key = (str(claim.get("cycle", recovered.get("cycle", 0))), str(claim.get("threshold")))
            if key in seen:
                continue
            seen.add(key)
            combined_claims.append(claim)
        combined = dict(previous)
        combined.update(recovered)
        combined["claims_this_run"] = combined_claims
        merged[program_id] = combined
    return list(merged.values())


def _refresh_player_status_after_immediate_recovery(result):
    """Recalculate account status after live recovery changes its outcome."""
    if str(result.get("status") or "") in ("Login Failed", "Error", "Failed"):
        return
    event_results = result.get("event_rewards") or []
    result["event_reward_claims"] = event_claim_count(event_results)
    result["event_rewards_retry_required"] = event_retry_required(event_results)
    promotion_unconfirmed = any(
        str(promo.get("status") or "") in ("claim_unconfirmed", "click_failed", "available")
        for promo in (result.get("promotions_observed") or [])
        if isinstance(promo, dict)
    )
    standard_unconfirmed = bool(
        result.get("daily_unconfirmed")
        or result.get("store_unconfirmed_indices")
        or result.get("progression_unconfirmed")
    )
    other_attention = bool(
        result.get("promotion_detection_failed")
        or result.get("event_rewards_retry_required")
        or promotion_unconfirmed
    )
    result["retry_required"] = bool(standard_unconfirmed or other_attention)
    if standard_unconfirmed:
        result["status"] = "Partial"
        result["fail_reason"] = "One or more standard rewards remain unconfirmed after immediate recovery"
    elif other_attention:
        result["status"] = "Partial"
        if result.get("event_rewards_retry_required"):
            result["fail_reason"] = "One or more event reward claims remain unconfirmed after immediate recovery"
        elif result.get("promotion_detection_failed"):
            result["fail_reason"] = "Dynamic promotional-card detection remains inconclusive"
        else:
            result["fail_reason"] = "One or more promotional claims remain unconfirmed"
    elif (result.get("immediate_recovery") or {}).get("status") == "resolved":
        result["status"] = "Success"
        result["fail_reason"] = None
    else:
        possible = max(0, int(result.get("possible", 0) or 0))
        claimed_now = (
            int(result.get("daily", 0) or 0)
            + int(result.get("store", 0) or 0)
            + int(result.get("store_temp", 0) or 0)
            + int(result.get("loyalty", 0) or 0)
            + int(result.get("event_reward_claims", 0) or 0)
        )
        if possible == 0:
            result["status"] = "Success" if result.get("event_reward_claims", 0) else "All Skipped (Cooldown)"
        elif claimed_now >= possible:
            result["status"] = "Success"
        elif claimed_now > 0:
            result["status"] = "Partial"
        else:
            result["status"] = "No Rewards"
        result["fail_reason"] = None


def immediate_recover_player_claims(pid, has_loyalty, is_new, run_label, result):
    """Retry only unresolved targets in one fresh, sequential browser session.

    A login failure may receive one normal account pass because the failed first
    attempt never reached reward pages. Otherwise, each identified reward target
    gets at most one additional click. Unidentified targets remain for the
    post-run recovery observer rather than clicking an unrelated reward.
    """
    result = dict(result or {})
    mode = str(result.get("processing_mode") or "full_claim")
    if mode == "promotional_only":
        return result

    login_retry = str(result.get("status") or "") == "Login Failed"
    daily_pending = bool(result.get("daily_unconfirmed"))
    store_indices = []
    for raw_index in (result.get("store_unconfirmed_indices") or []):
        try:
            index = int(raw_index)
        except (TypeError, ValueError, OverflowError):
            continue
        if index in STORE_DAILY_LABELS and index not in store_indices:
            store_indices.append(index)

    progression_pending = bool(result.get("progression_unconfirmed"))
    progression_signature = str(result.get("progression_unconfirmed_card_signature") or "").strip()
    event_targets = _event_retry_targets(result.get("event_rewards"))
    deferred_categories = []
    if str(result.get("status") or "") in ("Error", "Failed") and not login_retry:
        deferred_categories.append("General processing error (no safe reward target)")
    if progression_pending and not progression_signature:
        deferred_categories.append("Progression (failed card identity unavailable)")
    if result.get("event_rewards_retry_required") and not event_targets:
        deferred_categories.append("Event rewards (failed tier identity unavailable)")

    categories = []
    if login_retry:
        categories.append("Login / full account")
    else:
        if daily_pending:
            categories.append("Daily Reward")
        categories.extend(f"Store {STORE_DAILY_LABELS[index]}" for index in store_indices)
        if progression_pending and progression_signature:
            categories.append("Progression reward")
        categories.extend(f"Event {program_id}" for program_id in event_targets)

    if not categories and not deferred_categories:
        return result

    recovery = {
        "attempted": False,
        "status": "deferred" if deferred_categories else "not_required",
        "categories": categories + deferred_categories,
        "resolved_categories": [],
        "unresolved_categories": list(deferred_categories),
        "deferred_checks": [],
        "attempted_at": None,
        "finished_at": None,
        "recovery_seconds": 0,
        "click_limit_per_target": 1,
        "reason": None,
    }

    # The original login failure occurred before any reward page interaction.
    # A single standard retry is therefore safe and is deliberately non-recursive.
    if login_retry:
        started = get_ist_time()
        recovery["attempted"] = True
        recovery["status"] = "running"
        recovery["attempted_at"] = started.isoformat()
        log(f"🔁 Immediate recovery: one fresh login retry for {pid} after the initial authentication failure")
        try:
            retried = process_player(pid, has_loyalty, is_new, run_label)
            resolved = not _full_claim_recovery_candidate(retried)
            recovery["resolved_categories"] = ["Login / full account"] if resolved else []
            if not resolved:
                recovery["unresolved_categories"].append("Login / full account")
            recovery["reason"] = "Fresh login retry completed" if resolved else "Fresh login retry still has unresolved claims"
            recovery["status"] = "resolved" if resolved and not recovery["unresolved_categories"] else "unresolved"
            recovery["finished_at"] = get_ist_time().isoformat()
            recovery["recovery_seconds"] = max(0, int((get_ist_time() - started).total_seconds()))
            recovery["recovered_claims"] = (
                int(retried.get("daily", 0) or 0) + int(retried.get("store", 0) or 0)
                + int(retried.get("store_temp", 0) or 0) + int(retried.get("progression", 0) or 0)
                + int(retried.get("loyalty", 0) or 0) + event_claim_count(retried.get("event_rewards"))
            )
            retried["immediate_recovery"] = recovery
            retried["duration_s"] = int(retried.get("duration_s", 0) or 0) + recovery["recovery_seconds"]
            log(f"{'✅' if resolved else '🚩'} Immediate login recovery for {pid}: {recovery['status']}")
            return retried
        except Exception as exc:
            recovery["unresolved_categories"].append("Login / full account")
            recovery["reason"] = f"Fresh login retry raised {type(exc).__name__}"
            recovery["status"] = "unresolved"
            recovery["finished_at"] = get_ist_time().isoformat()
            recovery["recovery_seconds"] = max(0, int((get_ist_time() - started).total_seconds()))
            result["immediate_recovery"] = recovery
            return result

    if not categories:
        recovery["reason"] = "No safe reward target could be isolated; post-run recovery remains the backstop."
        result["immediate_recovery"] = recovery
        log(f"⏭️ Immediate recovery deferred for {pid}: {recovery['reason']}")
        return result

    started = get_ist_time()
    recovery["attempted"] = True
    recovery["status"] = "running"
    recovery["attempted_at"] = started.isoformat()
    driver = None
    unresolved = []
    recovered_claims = 0
    recovered_store_claims = 0
    try:
        driver = create_driver()
        if not login_to_hub(driver, pid):
            unresolved.extend(categories)
            recovery["reason"] = "Fresh recovery session could not authenticate; no reward clicks were attempted"
            log(f"🚩 Immediate recovery login failed for {pid}; no claim controls were clicked")
        else:
            recovered_name = capture_display_name(driver)
            if recovered_name:
                result["display_name"] = recovered_name
                result["display_name_captured_at_ist"] = get_ist_time().isoformat()
            driver._portal_claim_audit_enabled = False
            driver._portal_claim_display_name = result.get("display_name")
            driver._portal_claim_run_label = run_label
            driver._preexisting_portal_claims = []

            if daily_pending:
                try:
                    daily_count, daily_skipped, daily_unconfirmed = claim_daily_rewards(
                        driver, pid, max_attempts=1
                    )
                    result["daily"] = int(result.get("daily", 0) or 0) + int(daily_count or 0)
                    recovered_claims += int(daily_count or 0)
                    result["daily_skipped"] = bool(daily_skipped)
                    result["daily_unconfirmed"] = bool(daily_unconfirmed)
                    if daily_skipped and not daily_unconfirmed and not daily_count:
                        result["possible"] = max(0, int(result.get("possible", 0) or 0) - 1)
                    if daily_unconfirmed:
                        unresolved.append("Daily Reward")
                    else:
                        recovery["resolved_categories"].append("Daily Reward")
                    log(f"{'🚩' if daily_unconfirmed else '✅'} Immediate Daily recovery for {pid}: {'still unresolved' if daily_unconfirmed else 'portal verified'}")
                except Exception as exc:
                    unresolved.append("Daily Reward")
                    log(f"🚩 Immediate Daily recovery raised {type(exc).__name__} for {pid}")

            if store_indices:
                try:
                    old_store_possible = int(result.get("store_possible", 0) or 0)
                    old_skipped = list(result.get("store_skipped") or [False, False, False])
                    old_unconfirmed = set(int(x) for x in (result.get("store_unconfirmed_indices") or []))
                    store_count, store_skips, _promotions, store_unconfirmed, store_claimed = claim_store_rewards(
                        driver, pid,
                        reward_indices=store_indices,
                        max_attempts=1,
                        scan_promotions=False,
                    )
                    recovered_store_claims = int(store_count or 0)
                    result["store"] = int(result.get("store", 0) or 0) + recovered_store_claims
                    recovered_claims += recovered_store_claims
                    for index in store_indices:
                        old_skipped[index - 1] = bool(store_skips[index - 1])
                    result["store_skipped"] = old_skipped
                    current_unconfirmed = set(int(x) for x in (store_unconfirmed or []))
                    result["store_unconfirmed_indices"] = sorted(
                        (old_unconfirmed - set(store_indices)) | current_unconfirmed
                    )
                    result["store_claimed_indices"] = sorted(set(
                        int(x) for x in (result.get("store_claimed_indices") or [])
                    ) | {int(x) for x in (store_claimed or [])})
                    resolved_cooldowns = [
                        index for index in store_indices
                        if index not in current_unconfirmed
                        and bool(store_skips[index - 1])
                        and index not in (store_claimed or [])
                    ]
                    if resolved_cooldowns:
                        result["possible"] = max(0, int(result.get("possible", 0) or 0) - len(resolved_cooldowns))
                        result["store_possible"] = max(0, old_store_possible - len(resolved_cooldowns))
                    if current_unconfirmed:
                        unresolved.extend(f"Store {STORE_DAILY_LABELS[index]}" for index in sorted(current_unconfirmed))
                    for index in store_indices:
                        if index not in current_unconfirmed:
                            recovery["resolved_categories"].append(f"Store {STORE_DAILY_LABELS[index]}")
                    log(f"{'🚩' if current_unconfirmed else '✅'} Immediate Store recovery for {pid}: {len(current_unconfirmed)} target card(s) remain unresolved")
                except Exception as exc:
                    unresolved.extend(f"Store {STORE_DAILY_LABELS[index]}" for index in store_indices)
                    log(f"🚩 Immediate Store recovery raised {type(exc).__name__} for {pid}")

            if progression_pending and progression_signature:
                try:
                    progression_count = claim_progression_program_rewards(
                        driver, pid,
                        target_card_signature=progression_signature,
                        max_claims_per_visit=1,
                    )
                    result["progression"] = int(result.get("progression", 0) or 0) + int(progression_count or 0)
                    recovered_claims += int(progression_count or 0)
                    progression_unconfirmed = bool(getattr(driver, "_progression_unconfirmed", True))
                    result["progression_unconfirmed"] = progression_unconfirmed
                    result["progression_unconfirmed_card_signature"] = getattr(
                        driver, "_progression_unconfirmed_card_signature", None
                    )
                    result["progression_deferred"] = bool(getattr(driver, "_progression_deferred", False))
                    result["progression_deferred_reason"] = getattr(driver, "_progression_deferred_reason", None)
                    result["progression_claim_attempted"] = bool(getattr(driver, "_progression_claim_attempted", False))
                    result["progression_checked"] = bool(getattr(driver, "_progression_checked", not result["progression_deferred"]))
                    if progression_unconfirmed:
                        unresolved.append("Progression reward")
                    else:
                        recovery["resolved_categories"].append("Progression reward")
                    log(f"{'🚩' if progression_unconfirmed else '✅'} Immediate Progression recovery for {pid}: {'still unresolved' if progression_unconfirmed else 'target card verified'}")
                except Exception as exc:
                    unresolved.append("Progression reward")
                    log(f"🚩 Immediate Progression recovery raised {type(exc).__name__} for {pid}")
            elif recovered_store_claims > 0:
                # A newly confirmed Store card can unlock Progression rewards.
                try:
                    progression_count = claim_progression_program_rewards(
                        driver, pid, max_claims_per_visit=6
                    )
                    result["progression"] = int(result.get("progression", 0) or 0) + int(progression_count or 0)
                    recovered_claims += int(progression_count or 0)
                    result["progression_unconfirmed"] = bool(getattr(driver, "_progression_unconfirmed", False))
                    result["progression_unconfirmed_card_signature"] = getattr(
                        driver, "_progression_unconfirmed_card_signature", None
                    )
                    result["progression_deferred"] = bool(getattr(driver, "_progression_deferred", False))
                    result["progression_deferred_reason"] = getattr(driver, "_progression_deferred_reason", None)
                    result["progression_claim_attempted"] = bool(getattr(driver, "_progression_claim_attempted", False))
                    result["progression_checked"] = bool(getattr(driver, "_progression_checked", not result["progression_deferred"]))
                    if result["progression_unconfirmed"]:
                        unresolved.append("Progression reward")
                    elif result["progression_deferred"]:
                        recovery["deferred_checks"].append("Progression eligibility after Store recovery")
                    else:
                        recovery["resolved_categories"].append("Progression eligibility after Store recovery")
                except Exception as exc:
                    result["progression_unconfirmed"] = True
                    unresolved.append("Progression reward")
                    log(f"🚩 Progression recheck after Store recovery raised {type(exc).__name__} for {pid}")

            if event_targets:
                try:
                    recovered_events = run_event_reward_checks(
                        driver, pid,
                        only_program_ids=set(event_targets),
                        retry_thresholds_by_program=event_targets,
                        max_claims_per_program=max(1, max(len(v) for v in event_targets.values())),
                    )
                    result["event_rewards"] = _merge_immediate_event_results(
                        result.get("event_rewards") or [], recovered_events
                    )
                    result["event_reward_claims"] = event_claim_count(result["event_rewards"])
                    result["event_rewards_retry_required"] = event_retry_required(result["event_rewards"])
                    recovered_by_id = {
                        str(item.get("program_id") or ""): item
                        for item in recovered_events if isinstance(item, dict)
                    }
                    for program_id in event_targets:
                        updated = recovered_by_id.get(program_id)
                        label = f"Event {program_id}"
                        if updated and not updated.get("retry_required"):
                            recovery["resolved_categories"].append(label)
                        else:
                            unresolved.append(label)
                    recovered_claims += event_claim_count(recovered_events)
                    log(f"✅ Immediate event recovery checked {len(recovered_events)} targeted programme(s) for {pid}")
                except Exception as exc:
                    unresolved.extend(f"Event {program_id}" for program_id in event_targets)
                    log(f"🚩 Immediate event recovery raised {type(exc).__name__} for {pid}")
    except Exception as exc:
        unresolved.extend(category for category in categories if category not in recovery["resolved_categories"])
        recovery["reason"] = f"Fresh recovery session failed ({type(exc).__name__})"
        log(f"🚩 Immediate recovery session failed for {pid}: {type(exc).__name__}")
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

    recovery["unresolved_categories"] = list(dict.fromkeys(
        recovery["unresolved_categories"] + unresolved
    ))
    recovery["resolved_categories"] = list(dict.fromkeys(recovery["resolved_categories"]))
    recovery["status"] = "resolved" if not recovery["unresolved_categories"] else "unresolved"
    recovery["recovery_seconds"] = max(0, int((get_ist_time() - started).total_seconds()))
    recovery["finished_at"] = get_ist_time().isoformat()
    recovery["recovered_claims"] = recovered_claims
    if not recovery["reason"]:
        recovery["reason"] = (
            "All immediate targets were portal-confirmed"
            if recovery["status"] == "resolved"
            else "One or more targets remain unresolved for post-run recovery"
        )
    result["immediate_recovery"] = recovery
    result["duration_s"] = int(result.get("duration_s", 0) or 0) + recovery["recovery_seconds"]
    _refresh_player_status_after_immediate_recovery(result)
    snap = get_reward_status(pid)
    result["daily_next"] = snap.get("daily_next")
    result["store_next"] = snap.get("store_next")
    result["daily_portal_status"] = snap.get("daily_status", "unknown")
    result["store_portal_status"] = snap.get("store_status", ["unknown", "unknown", "unknown"])
    if recovery["status"] == "resolved":
        log(f"✅ Immediate recovery complete for {pid}: {', '.join(recovery['resolved_categories'])}")
    else:
        log(f"🚩 Immediate recovery incomplete for {pid}: {', '.join(recovery['unresolved_categories'])}")
    return result


def process_player_promotions_only(pid, is_new, run_label):
    """Check live Store offers and configured event rewards; skip daily/standard Store/progression/loyalty claims."""
    start = get_ist_time()
    stats = {
        "pid": pid,
        "display_name": None,
        "preexisting_portal_claims": [],
        "is_new": is_new,
        "has_loyalty": False,
        "processing_mode": "promotional_only",
        "daily": 0,
        "store": 0,
        "store_temp": 0,
        "store_possible": 0,
        "store_temp_possible": 0,
        "store_temp_skipped": True,
        "store_temp_status": "not_found",
        "promotions_observed": [],
        "event_rewards": [],
        "event_reward_claims": 0,
        "event_rewards_retry_required": False,
        "promotion_detection_failed": False,
        "promotion_scan_completed": False,
        "promotion_popup_blocked": False,
        "retry_required": False,
        "progression": 0,
        "loyalty": 0,
        "daily_skipped": False,
        "store_skipped": [False, False, False],
        "loyalty_skipped": False,
        "skipped_all": False,
        "status": "Error",
        "fail_reason": None,
        "duration_s": 0,
        "possible": 0,
        "daily_portal_status": "not_checked",
        "store_portal_status": ["not_checked", "not_checked", "not_checked"],
    }
    init_player_history(pid)
    driver = None
    try:
        log(f"\n🎁 Promotional-only check: {pid}" + (" 🆕 NEW ID" if is_new else "") + f"  [{run_label}]")
        driver = create_driver()
        driver._promotion_discovery_failed = False
        if not login_to_hub(driver, pid):
            stats["status"] = "Login Failed"
            stats["fail_reason"] = "Could not authenticate for the promotional check"
            stats["duration_s"] = int((get_ist_time() - start).total_seconds())
            return stats

        stats["display_name"] = capture_display_name(driver)
        stats["display_name_captured_at_ist"] = get_ist_time().isoformat() if stats["display_name"] else None
        driver._portal_claim_audit_enabled = True
        driver._portal_claim_display_name = stats["display_name"]
        driver._portal_claim_run_label = run_label
        driver._preexisting_portal_claims = []
        driver.get("https://hub.vertigogames.co/store")
        bypass_cloudflare(driver)
        time.sleep(2)
        # discover_and_claim_promotions() performs the fail-closed popup check.
        # Do not run the legacy generic close_popup() before that safety check.
        promotions = discover_and_claim_promotions(driver, pid)
        stats["promotions_observed"] = promotions
        stats["promotion_scan_completed"] = True
        stats["promotion_popup_blocked"] = any(promo.get("status") == "popup_blocked" for promo in promotions)
        if any(promo.get("status") == "claimed_this_run" for promo in promotions):
            log("⏳ Waiting for Trick & Treat points to settle before checking event tiers...")
            time.sleep(3)
        stats["event_rewards"] = run_event_reward_checks(driver, pid)
        stats["event_reward_claims"] = event_claim_count(stats["event_rewards"])
        stats["event_rewards_retry_required"] = event_retry_required(stats["event_rewards"])
        stats["store_temp"] = sum(1 for promo in promotions if promo.get("status") == "claimed_this_run")
        stats["store_temp_possible"] = sum(int(promo.get("claimable_observed", 0) or 0) for promo in promotions)
        stats["possible"] = stats["store_temp_possible"]
        stats["store_temp_skipped"] = not bool(promotions)
        stats["store_temp_status"] = "not_found" if not promotions else "observed"
        stats["promotion_detection_failed"] = bool(getattr(driver, "_promotion_discovery_failed", False))
        popup_blocked = any(promo.get("status") == "popup_blocked" for promo in promotions)
        stats["promotion_popup_blocked"] = popup_blocked
        unconfirmed = any(
            promo.get("status") in ("claim_unconfirmed", "click_failed", "available", "popup_blocked")
            for promo in promotions
        )
        if (stats["promotion_detection_failed"] or unconfirmed
                or stats["store_temp"] < stats["possible"] or stats["event_rewards_retry_required"]):
            stats["status"] = "Partial"
            stats["retry_required"] = True
            stats["fail_reason"] = (
                "A Store promotion popup could not be safely dismissed; remaining promotions were skipped"
                if popup_blocked
                else ("Dynamic promotional-card detection was inconclusive"
                      if stats["promotion_detection_failed"]
                      else ("At least one observed promotional claim was not confirmed by the portal"
                            if unconfirmed or stats["store_temp"] < stats["possible"]
                            else "One or more limited-time event reward claims could not be confirmed"))
            )
        elif stats["store_temp"] > 0 or stats["event_reward_claims"] > 0:
            stats["status"] = "Promotional / Event Claims Made"
        else:
            # No available offer is a completed check, not a failure.
            stats["status"] = "Promotional Check Complete"
        try:
            driver.save_screenshot(f"store_{pid}.png")
        except Exception:
            pass
    except Exception as exc:
        stats["status"] = "Error"
        stats["fail_reason"] = f"{type(exc).__name__}: {str(exc)[:120]}"
        log(f"❌ Promotional-only check failed: {type(exc).__name__}: {str(exc)[:160]}")
    finally:
        if driver:
            stats["preexisting_portal_claims"] = list(
                getattr(driver, "_preexisting_portal_claims", []) or []
            )
            try:
                driver.quit()
            except Exception:
                pass
    stats["duration_s"] = int((get_ist_time() - start).total_seconds())
    return stats


def process_player_backup_reconciliation(pid, is_new, run_label):
    """Perform the once-per-reset standard-reward audit plus promotions/events.

    Daily and all three standard Store cards are read from the live portal so stale
    history cannot hide a missed claim. Progression is reopened when a Store Bonus
    changes from available to portal-confirmed.
    """
    start = get_ist_time()
    stats = {
        "pid": pid,
        "display_name": None,
        "preexisting_portal_claims": [],
        "is_new": is_new,
        "has_loyalty": False,
        "processing_mode": "backup_reconciliation",
        "standard_rewards_checked": False,
        "progression_checked": False,
        "daily": 0,
        "store": 0,
        "store_temp": 0,
        "store_possible": 0,
        "store_temp_possible": 0,
        "store_temp_skipped": True,
        "store_temp_status": "not_found",
        "promotions_observed": [],
        "event_rewards": [],
        "event_reward_claims": 0,
        "event_rewards_retry_required": False,
        "promotion_detection_failed": False,
        "promotion_scan_completed": False,
        "promotion_popup_blocked": False,
        "retry_required": False,
        "progression": 0,
        "progression_unconfirmed": False,
        "grenade_balance": None,
        "daily_unconfirmed": False,
        "store_unconfirmed_indices": [],
        "store_claimed_indices": [],
        "loyalty": 0,
        "daily_skipped": False,
        "store_skipped": [False, False, False],
        "loyalty_skipped": True,
        "skipped_all": False,
        "status": "Error",
        "fail_reason": None,
        "duration_s": 0,
        "possible": 0,
        "daily_portal_status": "unknown",
        "store_portal_status": ["unknown", "unknown", "unknown"],
    }
    init_player_history(pid)
    driver = None
    try:
        log(f"\n🔎 Backup reconciliation: {pid}" + (" 🆕 NEW ID" if is_new else "") + f"  [{run_label}]")
        driver = create_driver()
        driver._promotion_discovery_failed = False
        driver._progression_unconfirmed = False
        if not login_to_hub(driver, pid):
            stats["status"] = "Login Failed"
            stats["fail_reason"] = "Could not authenticate for backup reconciliation"
            stats["duration_s"] = int((get_ist_time() - start).total_seconds())
            return stats

        stats["display_name"] = capture_display_name(driver)
        stats["display_name_captured_at_ist"] = get_ist_time().isoformat() if stats["display_name"] else None
        driver._portal_claim_audit_enabled = True
        driver._portal_claim_display_name = stats["display_name"]
        driver._portal_claim_run_label = run_label
        driver._preexisting_portal_claims = []

        # Recheck Daily live; local history is not allowed to suppress this portal read.
        daily_count, daily_skipped, daily_unconfirmed = claim_daily_rewards(driver, pid)
        stats["daily"] = daily_count
        stats["daily_skipped"] = daily_skipped
        stats["daily_unconfirmed"] = bool(daily_unconfirmed)

        # Recheck all three standard Store cards live, regardless of today's local history.
        store_count, store_skips, promotions, store_unconfirmed, store_claimed_indices = claim_store_rewards(driver, pid)
        stats["store"] = store_count
        stats["store_skipped"] = store_skips
        stats["store_unconfirmed_indices"] = list(store_unconfirmed)
        stats["store_claimed_indices"] = list(store_claimed_indices)
        stats["standard_rewards_checked"] = True
        stats["store_recovered_indices"] = list(getattr(driver, "_store_daily_recovered_indices", []) or [])
        stats["promotions_observed"] = promotions
        stats["promotion_scan_completed"] = True
        stats["promotion_popup_blocked"] = any(promo.get("status") == "popup_blocked" for promo in promotions)
        stats["promotion_detection_failed"] = bool(getattr(driver, "_promotion_discovery_failed", False))
        stats["store_temp"] = sum(1 for promo in promotions if promo.get("status") == "claimed_this_run")
        stats["store_temp_possible"] = sum(int(promo.get("claimable_observed", 0) or 0) for promo in promotions)
        stats["store_temp_skipped"] = not bool(promotions)
        stats["store_temp_status"] = "not_found" if not promotions else "observed"
        stats["store_possible"] = sum(1 for skipped in store_skips if not skipped)
        stats["possible"] = (0 if daily_skipped else 1) + stats["store_possible"] + stats["store_temp_possible"]

        if daily_count > 0 or store_count > 0 or stats["store_temp"] > 0:
            log("⏳ Waiting for confirmed Store bonuses/rewards to settle before downstream checks...")
            time.sleep(3)

        # A newly recovered daily Store bonus may unlock a Progression milestone.
        recovered_store_indices = list(getattr(driver, "_store_daily_recovered_indices", []) or [])
        if recovered_store_indices:
            stats["progression_checked"] = True
            p = claim_progression_program_rewards(driver, pid)
            stats["progression"] = p
            stats["progression_unconfirmed"] = bool(getattr(driver, "_progression_unconfirmed", False))
            stats["grenade_balance"] = _record_grenade_balance_for_player(
                driver, pid, is_new_account=is_new, stats=stats
            )
        else:
            log("ℹ️ Progression not reopened: no standard Store bonus changed from available to portal-confirmed")

        stats["event_rewards"] = run_event_reward_checks(driver, pid)
        stats["event_reward_claims"] = event_claim_count(stats["event_rewards"])
        stats["event_rewards_retry_required"] = event_retry_required(stats["event_rewards"])

        unconfirmed_promotion = any(
            promo.get("status") in ("claim_unconfirmed", "click_failed", "available")
            for promo in promotions
        )
        standard_unconfirmed = bool(
            stats["daily_unconfirmed"]
            or stats["store_unconfirmed_indices"]
            or stats["progression_unconfirmed"]
        )
        if (standard_unconfirmed or stats["promotion_detection_failed"]
                or unconfirmed_promotion or stats["event_rewards_retry_required"]):
            stats["status"] = "Partial"
            stats["retry_required"] = True
            if standard_unconfirmed:
                labels = []
                if stats["daily_unconfirmed"]:
                    labels.append("Daily reward")
                labels.extend(STORE_DAILY_LABELS[i] for i in stats["store_unconfirmed_indices"])
                if stats["progression_unconfirmed"]:
                    labels.append("Progression reward")
                stats["fail_reason"] = "Portal confirmation pending for: " + ", ".join(labels)
            elif stats["promotion_detection_failed"]:
                stats["fail_reason"] = "Dynamic promotional-card detection was inconclusive"
            elif unconfirmed_promotion:
                stats["fail_reason"] = "At least one observed promotional claim was not confirmed by the portal"
            else:
                stats["fail_reason"] = "One or more limited-time event reward claims could not be confirmed"
        elif (daily_count + store_count + stats["store_temp"] + stats["event_reward_claims"]
              + stats["progression"]) > 0:
            stats["status"] = "Claims Made"
        else:
            stats["status"] = "Backup Reconciliation Complete"

        try:
            driver.save_screenshot(f"store_{pid}.png")
        except Exception:
            pass
    except Exception as exc:
        stats["status"] = "Error"
        stats["retry_required"] = True
        stats["fail_reason"] = f"{type(exc).__name__}: {str(exc)[:120]}"
        log(f"❌ Backup reconciliation failed: {type(exc).__name__}: {str(exc)[:160]}")
    finally:
        if driver:
            stats["preexisting_portal_claims"] = list(
                getattr(driver, "_preexisting_portal_claims", []) or []
            )
            try:
                driver.quit()
            except Exception:
                pass

    stats["duration_s"] = int((get_ist_time() - start).total_seconds())
    snap = get_reward_status(pid)
    stats["daily_next"] = snap.get("daily_next")
    stats["store_next"] = snap.get("store_next")
    stats["loyalty_next"] = snap.get("loyalty_next")
    stats["daily_portal_status"] = snap.get("daily_status", "unknown")
    stats["store_portal_status"] = snap.get("store_status", ["unknown", "unknown", "unknown"])
    return stats




# SECTION 10 — EMAIL (Light Theme, Mobile-First)
# ═══════════════════════════════════════════════════════════════════════════════

_CSS = """
/* ═══ CS Hub Rewards Dashboard — Light Theme v3.0 ═══
   Mobile-first: stacked cards ≤640px, table >640px.
   !important overrides prevent Gmail dark-mode hijack.
═══════════════════════════════════════════════════ */
*{box-sizing:border-box;margin:0;padding:0;}
body{background:#f3f4f6 !important;font-family:'Segoe UI',Arial,sans-serif;
     padding:16px;color:#111827 !important;}
.wrap{max-width:980px;margin:0 auto;background:#f3f4f6 !important;}

/* ── Hero ── */
.hero{background:linear-gradient(135deg,#1e3a5f 0%,#1e40af 100%) !important;
      border-radius:14px;padding:22px 24px;margin-bottom:14px;color:#ffffff !important;}
.badge{display:inline-block;padding:4px 14px;border-radius:20px;font-size:11px;
       font-weight:700;letter-spacing:1.2px;text-transform:uppercase;margin-bottom:12px;}
.bp{background:#dcfce7 !important;color:#166534 !important;border:1px solid #86efac;}
.bb{background:#dbeafe !important;color:#1e40af !important;border:1px solid #93c5fd;}
.bm{background:#fff7ed !important;color:#c2410c !important;border:1px solid #fed7aa;}
.hero-grid{display:flex;justify-content:space-between;align-items:center;
           gap:16px;flex-wrap:wrap;}
.hero-left h1{font-size:18px;font-weight:700;color:#ffffff !important;margin-bottom:6px;}
.hero-left p{font-size:12px;color:#bfdbfe !important;}
.hero-nums{display:flex;gap:24px;flex-wrap:wrap;}
.hnum{text-align:center;}
.hv{display:block;font-size:30px;font-weight:800;line-height:1;}
.g{color:#86efac !important;}.b{color:#bfdbfe !important;}.a{color:#fde68a !important;}
.hl{font-size:10px;color:#bfdbfe !important;text-transform:uppercase;
    letter-spacing:.8px;margin-top:4px;}

/* ── KPI Row ── */
.kpi-row{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:14px;}
.kpi{background:#ffffff !important;border:1px solid #e5e7eb;border-radius:10px;
     padding:14px 16px;box-shadow:0 1px 3px rgba(0,0,0,.06);}
.kl{font-size:10px;color:#6b7280 !important;text-transform:uppercase;
    letter-spacing:.8px;margin-bottom:8px;font-weight:600;}
.kv{font-size:24px;font-weight:700;color:#111827 !important;}
.kv span{font-size:13px;color:#9ca3af !important;}
.ks{font-size:11px;color:#6b7280 !important;margin-top:4px;}
.du{color:#16a34a !important;font-weight:600;}
.dd{color:#dc2626 !important;font-weight:600;}
.de{color:#9ca3af !important;}
.pb{background:#e5e7eb !important;border-radius:3px;height:4px;margin-top:10px;}
.pf{height:4px;border-radius:3px;}
.pg{background:#22c55e !important;}.pb2{background:#3b82f6 !important;}
.pp{background:#a855f7 !important;}.pa{background:#f59e0b !important;}

/* ── Run strip ── */
.strip{background:#ffffff !important;border:1px solid #e5e7eb;border-radius:8px;
       padding:11px 16px;margin-bottom:14px;display:flex;gap:16px;
       flex-wrap:wrap;align-items:center;}
.si{font-size:12px;color:#6b7280 !important;}
.si strong{color:#111827 !important;}

/* ── Status badges & icons ── */
.sb{display:inline-block;padding:3px 9px;border-radius:10px;
    font-size:10px;font-weight:700;white-space:nowrap;}
.ss{background:#dcfce7 !important;color:#166534 !important;}
.sp{background:#fff7ed !important;color:#c2410c !important;}
.sf{background:#fee2e2 !important;color:#991b1b !important;}
.sk{background:#f3f4f6 !important;color:#6b7280 !important;}
.sn{background:#f3f4f6 !important;color:#9ca3af !important;}
.ic-ok{color:#16a34a !important;font-size:15px;}
.ic-cd{color:#d97706 !important;font-size:15px;}
.ic-fl{color:#dc2626 !important;font-size:15px;}
.ic-lk{color:#9ca3af !important;font-size:15px;}
.ic-pd{color:#2563eb !important;font-size:15px;}
.ic-na{color:#d1d5db !important;font-size:15px;}

/* ═══ DESKTOP TABLE — visible above 640px ═══ */
.tbx{background:#ffffff !important;border:1px solid #e5e7eb;border-radius:10px;
     margin-bottom:14px;overflow:visible;box-shadow:0 1px 3px rgba(0,0,0,.06);}
.tbh{background:#f9fafb !important;padding:13px 18px;border-bottom:1px solid #e5e7eb;
     font-size:13px;font-weight:600;color:#111827 !important;display:flex;
     justify-content:space-between;align-items:center;border-radius:10px 10px 0 0;}
.tbh small{font-size:11px;color:#6b7280 !important;}
.tsc{overflow-x:auto;-webkit-overflow-scrolling:touch;border-radius:0 0 10px 10px;}
table{width:100%;border-collapse:collapse;font-size:12px;
      min-width:760px;background:#ffffff !important;}
th{background:#f9fafb !important;color:#6b7280 !important;font-size:10px;
   text-transform:uppercase;letter-spacing:.8px;padding:10px;text-align:center;
   border-bottom:1px solid #e5e7eb;white-space:nowrap;font-weight:600;}
th.idh{text-align:left;padding-left:14px;min-width:185px;}
td{padding:9px 10px;text-align:center;border-bottom:1px solid #f3f4f6;
   vertical-align:middle;color:#374151 !important;}
td.idc{text-align:left;padding-left:14px;font-family:'Courier New',monospace;
       font-size:11px;color:#1d4ed8 !important;white-space:nowrap;font-weight:600;}
tr:last-child td{border-bottom:none;}
tr.rs{background:#f0fdf4 !important;}
tr.rp{background:#fff7ed !important;}
tr.rf{background:#fef2f2 !important;}
tr.rk{background:#fafafa !important;}
/* Hide mobile cards on desktop */
.mob-cards{display:none;}

/* ═══ MOBILE STACKED CARDS — active below 640px ═══
   • Desktop table hidden
   • Each player = self-contained card
   • No horizontal scroll, no swipe-action conflicts     */
@media(max-width:640px){
  body{padding:10px;}
  .hero{padding:16px 14px;}
  .hero-left h1{font-size:15px;}
  .hv{font-size:24px;}
  .kpi-row{grid-template-columns:1fr 1fr;gap:8px;}
  .kpi{padding:12px;}
  .kv{font-size:20px;}
  .strip{flex-direction:column;align-items:flex-start;gap:8px;}
  .tbx{display:none;}
  .mob-cards{display:block;margin-bottom:14px;}
  .mc-head{background:#f9fafb !important;border:1px solid #e5e7eb;
           border-radius:10px 10px 0 0;padding:12px 14px;font-size:13px;
           font-weight:600;color:#111827 !important;display:flex;
           justify-content:space-between;align-items:center;}
  .mc-head small{font-size:11px;color:#6b7280 !important;}
  .mpc{background:#ffffff !important;border:1px solid #e5e7eb;border-radius:8px;
       margin-bottom:8px;overflow:hidden;box-shadow:0 1px 2px rgba(0,0,0,.05);}
  .mpc.rs{border-left:3px solid #22c55e;}
  .mpc.rp{border-left:3px solid #f59e0b;}
  .mpc.rf{border-left:3px solid #ef4444;}
  .mpc.rk{border-left:3px solid #d1d5db;}
  .mpc-id{background:#f9fafb !important;padding:9px 14px;
          font-family:'Courier New',monospace;font-size:12px;
          color:#1d4ed8 !important;font-weight:700;display:flex;
          justify-content:space-between;align-items:center;
          border-bottom:1px solid #f3f4f6;}
  .mpc-id .mpc-st{font-family:'Segoe UI',Arial,sans-serif;}
  .mpc-row{display:flex;justify-content:space-between;align-items:center;
           padding:8px 14px;border-bottom:1px solid #f9fafb;font-size:12px;}
  .mpc-row:last-child{border-bottom:none;}
  .mpc-lbl{color:#6b7280 !important;font-size:11px;font-weight:500;}
  .mpc-val{color:#374151 !important;display:flex;gap:8px;align-items:center;}
  .nr-grid{grid-template-columns:1fr 1fr;gap:6px;}
}


/* ── Standard reward attention: readable amber with dark text ── */
.attn-box{background:#fffbeb !important;border:1px solid #f59e0b;border-left:5px solid #d97706;
          border-radius:9px;padding:12px 15px;margin:0 0 14px;}
.attn-title{font-size:13px;font-weight:800;color:#78350f !important;margin:0 0 8px;}
.attn-row{background:#fff7ed !important;border:1px solid #fed7aa;border-radius:6px;
          padding:8px 10px;margin:6px 0;color:#7c2d12 !important;font-size:12px;}
.attn-row strong{color:#7c2d12 !important;}

/* ── Detail cards ── */
.dcs{margin-bottom:14px;}
.dct{font-size:11px;font-weight:600;color:#6b7280 !important;
     text-transform:uppercase;letter-spacing:.8px;margin-bottom:10px;padding:0 4px;}
.dc{background:#ffffff !important;border:1px solid #e5e7eb;border-radius:8px;
    padding:14px 18px;margin-bottom:10px;box-shadow:0 1px 2px rgba(0,0,0,.05);}
.dcf{border-left:3px solid #ef4444;}.dcp{border-left:3px solid #f59e0b;}
.dcid{font-family:'Courier New',monospace;font-size:13px;
      color:#1d4ed8 !important;font-weight:700;margin-bottom:10px;}
.dcr{display:flex;justify-content:space-between;font-size:12px;
     padding:6px 0;border-bottom:1px solid #f3f4f6;}
.dcr:last-child{border-bottom:none;}
.dcl{color:#6b7280 !important;font-weight:500;}
.dcv{color:#374151 !important;}
.dce{color:#dc2626 !important;font-size:11px;font-style:italic;margin-bottom:8px;}

/* ── Footer ── */
.foot{background:#ffffff !important;border:1px solid #e5e7eb;border-radius:10px;
      padding:18px 20px;box-shadow:0 1px 3px rgba(0,0,0,.04);}
.ft{font-size:11px;color:#6b7280 !important;text-transform:uppercase;
    letter-spacing:.8px;margin-bottom:12px;font-weight:600;}
.nr-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:14px;}
.nr{background:#f9fafb !important;border:1px solid #e5e7eb;
    border-radius:6px;padding:8px 12px;text-align:center;}
.nrl{font-size:10px;color:#6b7280 !important;margin-bottom:4px;font-weight:500;}
.nrt{font-size:12px;font-family:'Courier New',monospace;color:#374151 !important;}
.nr.nrp .nrt{color:#16a34a !important;font-weight:700;}
.leg{display:flex;flex-wrap:wrap;gap:12px;padding-top:12px;
     border-top:1px solid #e5e7eb;margin-top:12px;}
.leg span{font-size:11px;color:#6b7280 !important;}
.ver{text-align:center;font-size:10px;color:#9ca3af !important;
     margin-top:12px;padding-top:10px;border-top:1px solid #e5e7eb;}
"""


def _badge_cls(run_label):
    if run_label == "Primary Run": return "bp"
    if run_label == "Manual Run":  return "bm"
    return "bb"


def _display_label(r):
    """
    Privacy-safe display string: display_name captured from hub,
    or masked fallback showing only last 4 chars of ID.
    Raw player IDs are NEVER rendered in the email.
    """
    dn = r.get("display_name")
    if dn:
        return dn
    pid = r.get("pid", "")
    return f"Player …{pid[-4:]}" if len(pid) >= 4 else "Player"


def _issue_identity_label(result, known_display_names=None):
    """Show an actionable issue identity: full player ID plus captured in-game name."""
    result = result if isinstance(result, dict) else {}
    player_id = str(result.get("pid") or "").strip()
    display_name = str(result.get("display_name") or "").strip()
    if not display_name and isinstance(known_display_names, dict) and player_id:
        display_name = str(known_display_names.get(player_id) or "").strip()
    display_name = display_name or "IGN unavailable"
    return f"{display_name} · Player ID {player_id}" if player_id else display_name


def build_preexisting_portal_claim_section(results):
    """List newly observed pre-existing claims with full ID, IGN and repeat-reset evidence."""
    incidents = []
    seen = set()
    for result in results or []:
        if not isinstance(result, dict):
            continue
        for event in (result.get("preexisting_portal_claims") or []):
            if not isinstance(event, dict):
                continue
            incident_id = str(event.get("incident_id") or "")
            key = incident_id or "|".join((
                str(event.get("player_id") or result.get("pid") or ""),
                str(event.get("reward_key") or ""),
                str(event.get("reset_date") or ""),
            ))
            if key in seen:
                continue
            seen.add(key)
            incidents.append((result, event))

    if not incidents:
        return ""

    audit = load_portal_claim_audit()
    account_ids = {str(event.get("player_id") or result.get("pid") or "") for result, event in incidents}
    repeated_accounts = {
        pid for pid in account_ids if _portal_claim_reset_day_count(pid, audit) > 1
    }
    rows = []
    for result, event in incidents:
        player_id = str(event.get("player_id") or result.get("pid") or "")
        first_name = str(
            event.get("display_name_at_first_observation")
            or event.get("display_name")
            or result.get("display_name")
            or "IGN unavailable"
        ).strip()
        current_name = str(
            event.get("last_observed_display_name")
            or result.get("display_name")
            or first_name
        ).strip()
        identity = html.escape(_issue_identity_label({"pid": player_id, "display_name": current_name}))
        previous_note = (
            f" · Name at first observation: {html.escape(first_name)}"
            if first_name and current_name and first_name != current_name else ""
        )
        reward = html.escape(str(event.get("reward_name") or "Reward"))
        category = html.escape(str(event.get("reward_category") or "Reward").title())
        observed = str(event.get("first_observed_at_ist") or "")
        try:
            observed_text = datetime.fromisoformat(observed).strftime("%d %b %Y · %I:%M %p IST")
        except (TypeError, ValueError):
            observed_text = observed or "Time unavailable"
        reset_days = _portal_claim_reset_day_count(player_id, audit)
        recurrence = (
            f"Observed on {reset_days} separate reset days"
            if reset_days > 1 else "First recorded reset-day observation"
        )
        run_id = str(event.get("run_ids", [""])[0] if event.get("run_ids") else "")
        run_note = f" · Run #{html.escape(run_id)}" if run_id else ""
        rows.append(
            "<div class='v2-item v2-attention'>"
            f"<div><strong>{identity}</strong>"
            f"<small>{category} · {reward} · First observed {html.escape(observed_text)}{run_note}{previous_note}</small></div>"
            f"<span class='v2-result'>{html.escape(recurrence)}</span></div>"
        )

    repeated_count = len(repeated_accounts)
    account_word = "account" if len(account_ids) == 1 else "accounts"
    reward_word = "reward" if len(incidents) == 1 else "rewards"
    subtitle = f"{len(account_ids)} {account_word} · {len(incidents)} {reward_word} observed before bot interaction"
    repeated_note = f" · {repeated_count} repeat account(s)" if repeated_count else ""
    return (
        "<section class='v2-section v2-portal-claims'>"
        + _email_section_header("Pre-existing Portal Claims — Review", subtitle + repeated_note,
                                "Review Required", "v2-state-warning")
        + "<p class='v2-muted-note'>The portal already showed these rewards as claimed before this bot attempt. The portal does not identify who made the claim; treat this as supporting evidence, not proof of the actor.</p>"
        + "".join(rows)
        + "</section>"
    )


def _row_cls(status):
    return {"Success":"rs", "Claims Made":"rs", "Promotional Claims Made":"rs",
            "Partial":"rp", "Login Failed":"rf", "Error":"rf", "Failed":"rf"}.get(status, "rk")


def _sb_html(status):
    m = {
        "Success":               ("ss", "✅ Success"),
        "Claims Made":            ("ss", "✅ Claims Confirmed"),
        "Backup Reconciliation Complete": ("sk", "🔎 Backup Check Complete"),
        "Promotional Claims Made": ("ss", "🎁 Promotions Claimed"),
        "Promotional Check Complete": ("sk", "🔎 Promo Check Complete"),
        "Partial":               ("sp", "⚠️ Partial"),
        "All Skipped (Cooldown)":("sk", "⏩ Skipped"),
        "No Rewards":            ("sn", "⏳ No Rewards"),
        "Login Failed":          ("sf", "🔐 Login Failed"),
        "Error":                 ("sf", "❌ Error"),
        "Failed":                ("sf", "❌ Failed"),
    }
    cls, lbl = m.get(status, ("sk", status))
    return f'<span class="sb {cls}">{lbl}</span>'


def _delta_html(cur, prev, unit=""):
    if prev is None or (prev == 0 and cur == 0):
        return '<span class="de">first run</span>'
    d = cur - prev
    if d > 0: return f'<span class="du">▲{d}{unit}</span>'
    if d < 0: return f'<span class="dd">▼{abs(d)}{unit}</span>'
    return '<span class="de">✓ same</span>'


def _pbar(pct, cls):
    p = min(int(pct), 100)
    return f'<div class="pb"><div class="pf {cls}" style="width:{p}%;"></div></div>'


def _drow(lbl, val):
    return (f'<div class="dcr">'
            f'<span class="dcl">{lbl}</span>'
            f'<span class="dcv">{val}</span></div>')


def _promotion_status_label(status):
    return {
        "claimed_this_run": "✅ Claimed this run",
        "portal_claimed": "☑️ Already claimed on portal",
        "unavailable": "⏸ Present but unavailable",
        "claim_unconfirmed": "⚠️ Claim attempted; not confirmed",
        "click_failed": "⚠️ Claim click failed",
        "available": "⚠️ Claimable; not confirmed",
        "popup_blocked": "⛔ Popup safety check blocked the claim",
    }.get(str(status or "unknown"), "⚠️ Status unconfirmed")


def build_standard_attention_section(results):
    """Render standard Daily/Store/Progression claims that lack portal confirmation."""
    rows = []
    for result in results:
        player = html.escape(_issue_identity_label(result))
        if result.get("daily_unconfirmed"):
            rows.append((player, "Daily reward", "Portal did not confirm a Claimed/cooldown state"))
        for reward_index in (result.get("store_unconfirmed_indices", []) or []):
            label = STORE_DAILY_LABELS.get(int(reward_index), f"Store reward {reward_index}")
            rows.append((player, label, "Still available or portal state could not be established"))
        if result.get("progression_unconfirmed"):
            if result.get("progression_deferred"):
                reason = "A previously attempted Progression claim remains unconfirmed; this recheck was deferred and no new click was attempted"
            elif result.get("progression_claim_attempted"):
                reason = "Claim click was dispatched, but the portal did not confirm the same card as Delivered"
            else:
                reason = "Progression target remains unconfirmed; safe click dispatch could not be verified"
            rows.append((player, "Progression reward", reason))

    if not rows:
        return ""
    row_html = "".join(
        f"<div class='attn-row'><strong>{player}</strong> · {html.escape(reward)} — {html.escape(reason)}</div>"
        for player, reward, reason in rows
    )
    return (
        "<div class='attn-box'>"
        f"<div class='attn-title'>⚠️ Attention Required — {len(rows)} unconfirmed standard reward(s)</div>"
        f"{row_html}"
        "</div>"
    )


def build_deferred_checks_section(results):
    """Render safe checks that could not be completed without implying a failed claim."""
    rows = []
    for result in results:
        if not result.get("progression_deferred"):
            continue
        player = html.escape(_issue_identity_label(result))
        reason = str(
            result.get("progression_deferred_reason")
            or "Progression inspection was blocked by a safety check"
        )
        if result.get("progression_unconfirmed"):
            detail = (
                "A previously attempted claim remains unconfirmed; this recheck was "
                "deferred, and no additional claim click was attempted. "
                + reason
            )
        else:
            detail = (
                "Progression inspection was deferred; no claim click was attempted. "
                + reason
            )
        rows.append(
            "<div class='v2-item'>"
            f"<div><strong>{player}</strong><small>Progression Program</small></div>"
            f"<span class='v2-result'>{html.escape(detail)}</span></div>"
        )
    if not rows:
        return ""
    count = len(rows)
    account_word = "account" if count == 1 else "accounts"
    return (
        "<section class='v2-section v2-deferred'>"
        "<div class='v2-section-head'><h2>Deferred Checks</h2>"
        f"<span>{count} {account_word} · no new claim click attempted</span></div>"
        + "".join(rows)
        + "</section>"
    )


def _promotion_unavailable_is_reportable_free(promo):
    """Show unavailable offers only when the captured card proves they are free."""
    if str(promo.get("status") or "") not in ("unavailable", "portal_claimed"):
        return False
    card_text = _normalize_promotion_text(promo.get("card_text") or "")
    button_text = _normalize_promotion_text(promo.get("button_text") or "")
    paid_patterns = (
        r"(?:₹|rs\.?|inr|usd|eur|gbp|aud|cad|\$|€|£)\s*[1-9][0-9,]*(?:\.[0-9]{1,2})?",
        r"\b[1-9][0-9,]*(?:\.[0-9]{1,2})?\s*(?:inr|usd|eur|gbp|aud|cad)\b",
        r"\b[1-9][0-9]*(?:\.[0-9]+)?\s*%\s*(?:off|discount|discounted|saving)\b",
        r"\bsave\s+[1-9][0-9]*(?:\.[0-9]+)?\s*%",
        r"\b(?:discount|discounted|sale|buy\s+now|purchase|paid|price)\b",
    )
    if any(re.search(pattern, f"{card_text} {button_text}", flags=re.IGNORECASE) for pattern in paid_patterns):
        return False
    return "free" in card_text or button_text in ("free", "claim", "claim reward", "claim now", "claimed")


def build_promotional_section(results):
    """Render grouped, free Store offer outcomes and only the actionable details."""
    offers = {}
    detail_rows = []
    scan_accounts = sum(1 for result in results if result.get("promotion_scan_completed"))

    for result in results:
        player = html.escape(_issue_identity_label(result))
        for promo in (result.get("promotions_observed", []) or []):
            status = str(promo.get("status") or "unknown")
            detail_worthy = _promotion_detail_worthy(promo)
            summary_worthy = detail_worthy or _promotion_unavailable_is_reportable_free(promo)
            if summary_worthy:
                title_raw = str(promo.get("title") or "Store promotion").strip()
                title = html.escape(title_raw)
                key = _promotion_offer_type_key(promo) or title_raw.casefold()
                item = offers.setdefault(key, {
                    "title": title, "players": set(), "unavailable": 0,
                    "already_claimed": 0, "actionable": 0, "claimed": 0,
                })
                pid = str(result.get("pid") or "").strip()
                item["players"].add(pid or player)
                if status == "unavailable":
                    item["unavailable"] += 1
                elif status == "portal_claimed":
                    item["already_claimed"] += 1
                elif status == "claimed_this_run":
                    item["claimed"] += max(1, int(promo.get("claim_count", 0) or 0))
                elif status in ("claim_unconfirmed", "click_failed", "available", "popup_blocked"):
                    item["actionable"] += 1

            # Confirmed offers are already represented by aggregate counts above.
            # Per-account rows are reserved for outcomes that need inspection.
            if detail_worthy and status != "claimed_this_run":
                title = html.escape(str(promo.get("title") or "Store promotion"))
                label = html.escape(_promotion_status_label(status))
                detail_rows.append(
                    "<div class='v2-item'>"
                    f"<div><strong>{title}</strong><small>{player}</small></div>"
                    f"<span class='v2-result'>{label}</span></div>"
                )

        if result.get("promotion_detection_failed"):
            reason = html.escape(str(result.get("fail_reason") or "Live Store scan did not complete"))
            detail_rows.append(
                "<div class='v2-item v2-attention'>"
                f"<div><strong>Promotion scan</strong><small>{player}</small></div>"
                f"<span class='v2-result'>⚠️ {reason}</span></div>"
            )

    if not scan_accounts and not offers and not detail_rows:
        return ""

    summary_rows = []
    for item in offers.values():
        people = len(item["players"])
        seen_label = f"Seen on {people} account" + ("" if people == 1 else "s")
        counts = []
        if item["unavailable"]:
            counts.append(f"{item['unavailable']} unavailable")
        if item["actionable"]:
            counts.append(f"{item['actionable']} actionable")
        if item["claimed"]:
            counts.append(f"{item['claimed']} claimed")
        if item["already_claimed"]:
            counts.append(f"{item['already_claimed']} already claimed")
        outcome = " · ".join(counts) if counts else "No claimable outcome recorded"
        summary_rows.append(
            "<div class='v2-offer-row'>"
            f"<div><strong>{item['title']}</strong><small>{seen_label}</small></div>"
            f"<span>{html.escape(outcome)}</span></div>"
        )

    if not offers and scan_accounts:
        summary_rows.append(
            "<p class='v2-muted-note'>No reportable free promotional offers were observed during these scans.</p>"
        )

    scan_word = "account scan" if scan_accounts == 1 else "account scans"
    return (
        "<section class='v2-section v2-promotions'>"
        "<div class='v2-section-head'><h2>Promotional Offers</h2>"
        f"<span>{scan_accounts} {scan_word} · {len(offers)} offer types</span></div>"
        + "".join(summary_rows)
        + ("<div class='v2-promo-details'>" + "".join(detail_rows) + "</div>" if detail_rows else "")
        + "</section>"
    )



def build_event_rewards_section(results):
    """Render active limited-time event progress separately from Store Promotions."""
    rows = []
    claimed_total = 0
    retry_total = 0

    for result in results:
        player = html.escape(_issue_identity_label(result))
        for event in (result.get("event_rewards", []) or []):
            name = html.escape(str(event.get("name") or "Limited-time event"))
            balance = event.get("balance")
            points_label = html.escape(str(event.get("points_label") or "points"))
            balance_text = f"{int(balance):,} {points_label}" if isinstance(balance, (int, float)) else "Points unavailable"
            next_threshold = event.get("next_threshold")
            next_text = f"Next tier: {int(next_threshold):,}" if isinstance(next_threshold, (int, float)) else "No next tier shown"
            delivered = int(event.get("tiers_delivered", 0) or 0)
            total = int(event.get("tiers_total", 0) or 0)
            rows.append(
                "<div class='v2-item'>"
                f"<div><strong>{name} — progress</strong><small>{player}</small></div>"
                f"<span class='v2-result'>{html.escape(balance_text)} · {html.escape(next_text)} · {delivered}/{total} delivered</span></div>"
            )
            for claim in (event.get("claims_this_run", []) or []):
                threshold = int(claim.get("threshold", 0) or 0)
                rewards = ", ".join(str(item) for item in (claim.get("reward", []) or [])) or "Reward details unavailable"
                claimed_total += 1
                rows.append(
                    "<div class='v2-item'>"
                    f"<div><strong>{name} — {threshold:,} points</strong><small>{player}</small></div>"
                    f"<span class='v2-result v2-good'>✅ Confirmed · {html.escape(rewards)}</span></div>"
                )
            if event.get("retry_required"):
                retry_total += 1
                reason = html.escape(str(event.get("error") or "Claim state could not be confirmed"))
                rows.append(
                    "<div class='v2-item v2-attention'>"
                    f"<div><strong>{name} — attention required</strong><small>{player}</small></div>"
                    f"<span class='v2-result'>⚠️ {reason}</span></div>"
                )

    if not rows:
        return ""
    return (
        "<section class='v2-section'>"
        "<div class='v2-section-head'><h2>Active Event Rewards</h2>"
        f"<span>{claimed_total} tiers claimed · {retry_total} needing attention</span></div>"
        + "".join(rows)
        + "</section>"
    )


def build_mobile_cards(results, n):
    """
    Builds the mobile stacked-card HTML block.
    Hidden on desktop via CSS .mob-cards{display:none}.
    Each player gets one card with icon rows — no horizontal scroll.
    Uses _display_label() so raw IDs are never rendered.
    """
    mob_html = ""
    for r in results:
        display_lbl = _display_label(r)   # privacy-safe
        status      = r["status"]
        rc          = _row_cls(status)
        new_mark    = " 🆕" if r.get("is_new") else ""
        if (r.get("processing_mode") == "promotional_only"
                and not r.get("standard_rewards_checked", False)):
            promotion_rows = "".join(
                f'<div class="mpc-row"><span class="mpc-lbl">🎁 {html.escape(str(promo.get("title") or "Promotion"))}</span>'
                f'<span class="mpc-val">{html.escape(_promotion_status_label(promo.get("status")))}</span></div>'
                for promo in (r.get("promotions_observed", []) or [])
                if _promotion_detail_worthy(promo)
            )
            d_s2 = r.get("duration_s", 0)
            tm2 = f"{d_s2//60}m{d_s2%60}s" if d_s2 else "—"
            mob_html += (
                f'<div class="mpc {_row_cls(status)}">'
                f'<div class="mpc-id">{display_lbl}{new_mark}<span class="mpc-st">{_sb_html(status)}</span></div>'
                f'<div class="mpc-row"><span class="mpc-lbl">Mode</span><span class="mpc-val">Promotional-only; other rewards not checked</span></div>'
                f'{promotion_rows}'
                f'<div class="mpc-row"><span class="mpc-lbl">⏱️ Time</span><span class="mpc-val" style="color:#6b7280 !important;">{tm2}</span></div>'
                f'</div>'
            )
            continue
        d_s2        = r.get("duration_s", 0)
        tm2         = f"{d_s2//60}m{d_s2%60}s" if d_s2 else "—"

        sk2 = r.get("store_skipped", [False, False, False])
        if not isinstance(sk2, list) or len(sk2) < 3:
            sk2 = [False, False, False]
        cc2 = [False, False, False]
        explicit_claimed_indices2 = r.get("store_claimed_indices")
        if isinstance(explicit_claimed_indices2, list):
            for reward_index in explicit_claimed_indices2:
                if isinstance(reward_index, int) and 1 <= reward_index <= 3:
                    cc2[reward_index - 1] = True
        else:
            free_slots2 = [i for i in range(3) if not sk2[i]]
            for j2, idx2 in enumerate(free_slots2):
                if j2 < r["store"]:
                    cc2[idx2] = True

        is_fail2 = status in ("Login Failed", "Error", "Failed")

        def _mi(val, skipped, fail):
            if skipped: return "⏰"
            if val > 0: return "✅"
            if fail:    return "❌"
            return "⏳"

        d_ic  = _mi(r["daily"], r["daily_skipped"], is_fail2)
        g_ic  = "⏰" if sk2[0] else ("✅" if cc2[0] else ("❌" if is_fail2 else "⏳"))
        c_ic  = "⏰" if sk2[1] else ("✅" if cc2[1] else ("❌" if is_fail2 else "⏳"))
        l_ic  = "⏰" if sk2[2] else ("✅" if cc2[2] else ("❌" if is_fail2 else "⏳"))
        p_ic = ("—" if (r.get("processing_mode") == "backup_reconciliation"
                         and not r.get("progression_checked"))
                else ("✅" if r["progression"] > 0 else ("❌" if is_fail2 else "⏳")))

        if not r.get("has_loyalty"):
            ly_ic = "—"
        elif r.get("loyalty_skipped"):
            ly_ic = "⏰"
        elif r.get("loyalty", 0) > 0:
            ly_ic = "✅"
        elif is_fail2:
            ly_ic = "❌"
        else:
            ly_ic = "🔒"

        promotion_rows = "".join(
            f'<div class="mpc-row">'
            f'<span class="mpc-lbl">🎁 {html.escape(str(promo.get("title") or "Promotion"))}</span>'
            f'<span class="mpc-val">{html.escape(_promotion_status_label(promo.get("status")))}</span></div>'
            for promo in (r.get("promotions_observed", []) or [])
            if _promotion_detail_worthy(promo)
        )

        mob_html += (
            f'<div class="mpc {rc}">'
            f'<div class="mpc-id">{display_lbl}{new_mark}'
            f'<span class="mpc-st">{_sb_html(status)}</span></div>'
            f'<div class="mpc-row">'
            f'<span class="mpc-lbl">🎁 Daily &nbsp;🥇 Gold &nbsp;💵 Cash &nbsp;🍀 Lucky</span>'
            f'<span class="mpc-val">{d_ic} &nbsp;{g_ic} &nbsp;{c_ic} &nbsp;{l_ic}</span></div>'
            f'{promotion_rows}'
            f'<div class="mpc-row">'
            f'<span class="mpc-lbl">🎯 Progression</span>'
            f'<span class="mpc-val">{p_ic}'
            f'{" " + str(r["progression"]) if r["progression"] > 0 else ""}'
            f'</span></div>'
            f'<div class="mpc-row">'
            f'<span class="mpc-lbl">🏆 Loyalty</span>'
            f'<span class="mpc-val">{ly_ic}</span></div>'
            f'<div class="mpc-row">'
            f'<span class="mpc-lbl">⏱️ Time</span>'
            f'<span class="mpc-val" style="color:#6b7280 !important;">{tm2}</span></div>'
            f'</div>'
        )

    return (
        f"<div class='mob-cards'>"
        f"<div class='mc-head'>"
        f"<span>👥 {n} Accounts Processed</span>"
        f"<small>🆕 = new ID this run</small></div>"
        f"{mob_html}"
        f"</div>"
    )


def build_grenade_balance_email_section(results):
    """Render a clear balance status; absence of a snapshot is never called a pass."""
    records = []
    for result in results:
        balance = result.get("grenade_balance")
        if isinstance(balance, dict):
            records.append((result, balance, str(balance.get("status") or "not_validated")))

    if not records:
        modes = {str(result.get("processing_mode") or "full_claim") for result in results}
        if modes and modes <= {"promotional_only"}:
            note = "Not performed for this promotional-only run; core reward balances were not checked."
        elif not results:
            note = "No account balance snapshots were available in this run."
        else:
            note = "No portal balance snapshot was captured. This is not a validation pass."
        return (
            "<section class='v2-section v2-grenade v2-validation-card'>"
            "<div class='v2-section-head v2-grenade-head'><h2>Grenade Balance Check</h2>"
            "<span class='v2-state v2-state-neutral'>Not performed</span></div>"
            f"<p class='v2-muted-note'>{html.escape(note)}</p>"
            "</section>"
        )

    passed = sum(1 for _, _, status in records if status == "pass")
    below = sum(1 for _, _, status in records if status == "below_minimum")
    regressed = sum(1 for _, _, status in records if status == "counter_regressed")
    pending = sum(1 for _, _, status in records if status == "pending_baseline")
    not_validated = sum(1 for _, _, status in records if status == "not_validated")
    captured = sum(1 for _, balance, _ in records if balance.get("current_count") is not None)
    account_word = "account" if len(records) == 1 else "accounts"
    balance_word = "balance" if captured == 1 else "balances"
    summary = (
        f"{len(records)} {account_word} checked · {captured} {balance_word} captured · "
        f"{passed} passed"
    )
    exceptions = []
    for result, balance, status in records:
        if status == "pass":
            continue
        label = html.escape(_issue_identity_label(result))
        observed = balance.get("current_count")
        minimum = balance.get("expected_minimum")
        observed_text = "—" if observed is None else str(int(observed))
        minimum_text = "Pending baseline" if minimum is None else str(int(minimum))
        if status == "below_minimum":
            detail = f"{observed_text} / {minimum_text} · short {int(balance.get('deficit', 0) or 0)}"
            status_text = "Below minimum"
        elif status == "counter_regressed":
            detail = f"{observed_text} observed · expected at least {minimum_text}"
            status_text = "Counter regressed"
        elif status == "pending_baseline":
            detail = f"{observed_text} observed · baseline pending"
            status_text = "Baseline pending"
        else:
            detail = str(balance.get("reason") or "Balance could not be validated")
            status_text = "Not validated"
        exceptions.append(
            "<div class='v2-item v2-attention'>"
            f"<div><strong>{label}</strong><small>{html.escape(status_text)}</small></div>"
            f"<span class='v2-result'>{html.escape(detail)}</span></div>"
        )

    flags = []
    if below:
        flags.append(f"{below} below minimum")
    if regressed:
        flags.append(f"{regressed} regressed")
    if pending:
        flags.append(f"{pending} baseline pending")
    if not_validated:
        flags.append(f"{not_validated} not validated")
    state = "Attention Required" if below or regressed or pending or not_validated else "Passed"
    state_class = "v2-state-warning" if state == "Attention Required" else "v2-state-success"
    if flags:
        summary += " · " + " · ".join(flags)

    return (
        "<section class='v2-section v2-grenade v2-validation-card'>"
        "<div class='v2-section-head v2-grenade-head'><h2>Grenade Balance Check</h2>"
        f"<span class='v2-state {state_class}'>{state}</span></div>"
        f"<div class='v2-grenade-summary'>{html.escape(summary)}</div>"
        + ("".join(exceptions) if exceptions else
           "<p class='v2-all-clear'>All captured Grenade balances meet their expected minimum.</p>")
        + "</section>"
    )




def _email_section_header(title, subtitle="", badge_text=None, badge_class=""):
    """Render an email-safe stacked section heading; avoid flex/grid positioning."""
    badge_html = (
        f"<span class='v2-state {html.escape(str(badge_class or 'v2-state-neutral'))}'>"
        f"{html.escape(str(badge_text))}</span>"
        if badge_text else ""
    )
    subtitle_html = (
        f"<p class='v2-section-subtitle'>{html.escape(str(subtitle))}</p>"
        if subtitle else ""
    )
    return (
        "<div class='v2-section-head'>"
        f"<h2>{html.escape(str(title))}</h2>"
        + badge_html + subtitle_html
        + "</div>"
    )


def build_immediate_recovery_email_section(results, run_label=''):
    """Summarise fresh-session, account-scoped recovery attempts."""
    records = [
        result for result in results
        if isinstance(result.get("immediate_recovery"), dict)
        and (result["immediate_recovery"].get("attempted") or result["immediate_recovery"].get("status") == "deferred")
    ]
    if not records:
        return ""
    attempted = sum(1 for item in records if item["immediate_recovery"].get("attempted"))
    resolved = sum(1 for item in records if item["immediate_recovery"].get("status") == "resolved")
    deferred = sum(1 for item in records if item["immediate_recovery"].get("status") == "deferred")
    unresolved = sum(1 for item in records if item["immediate_recovery"].get("attempted") and item["immediate_recovery"].get("status") != "resolved")
    summary = f"{attempted} attempted · {resolved} resolved · {deferred} deferred · {unresolved} still pending"
    section_title = "Post-run Recovery" if run_label == "Recovery Run" else "In-run Recovery"
    rows = []
    for result in records:
        recovery = result["immediate_recovery"]
        label = html.escape(_issue_identity_label(result))
        is_resolved = recovery.get("status") == "resolved"
        details = []
        if recovery.get("resolved_categories"):
            details.append("Recovered: " + ", ".join(str(x) for x in recovery["resolved_categories"]))
        if recovery.get("unresolved_categories"):
            details.append("Pending: " + ", ".join(str(x) for x in recovery["unresolved_categories"]))
        rows.append(
            "<div class='v2-item'>"
            f"<div><strong>{label}</strong><small class='{'v2-good' if is_resolved else 'v2-status'}'>{'Recovered' if is_resolved else 'Still pending'}</small></div>"
            f"<span class='v2-result'>{html.escape(' · '.join(details) or str(recovery.get('reason') or 'Recovery checked'))}</span>"
            "</div>"
        )
    return (
        "<section class='v2-section v2-recovery'>"
        + _email_section_header(section_title, summary)

        + "".join(rows)
        + "</section>"
    )


def build_daily_cycle_email_section(summary, results=None, player_display_names=None):
    """Show the overall reset-cycle outcome in recovery emails, not only the subset."""
    if not isinstance(summary, dict):
        return ""
    configured = int(summary.get("configured_player_count", 0) or 0)
    complete = int(summary.get("accounts_complete", 0) or 0)
    pending = int(summary.get("accounts_pending", 0) or 0)
    if configured <= 0:
        return ""
    recovery_claims = int(summary.get("recovery_claims_added", 0) or 0)
    recovery_accounts = int(summary.get("recovery_accounts_count", 0) or 0)
    total_claims = int(summary.get("total_claims", 0) or 0)
    if pending:
        state = f"{complete} / {configured} accounts complete · {pending} pending"
    else:
        state = f"{complete} / {configured} accounts complete"
    recovery_account_word = "account" if recovery_accounts == 1 else "accounts"
    recovery_claim_word = "claim" if recovery_claims == 1 else "claims"
    recovery_note = (
        f"Recovery checked {recovery_accounts} {recovery_account_word} and confirmed "
        f"{recovery_claims} additional {recovery_claim_word}."
        if recovery_accounts or recovery_claims else
        "No additional recovery claims were needed."
    )
    pending_ids = summary.get("pending_player_last4")
    pending_ids = [str(value).strip()[-4:] for value in pending_ids if str(value).strip()] if isinstance(pending_ids, list) else []
    result_by_suffix = {}
    for result in (results or []):
        pid = str(result.get("pid") or "").strip()
        if pid:
            result_by_suffix.setdefault(pid[-4:].upper(), []).append(result)
    roster_by_suffix = {}
    if pending_ids:
        try:
            with open(PLAYER_ID_FILE, "r", newline="", encoding="utf-8-sig") as handle:
                for row in csv.DictReader(handle):
                    pid = str(row.get("player_id") or "").strip()
                    if pid:
                        roster_by_suffix.setdefault(pid[-4:].upper(), []).append(pid)
        except Exception as exc:
            log(f"⚠️ Could not resolve full IDs for recovery-cycle pending list ({type(exc).__name__})")
    pending_rows = []
    for suffix in pending_ids[:12]:
        matches = result_by_suffix.get(suffix.upper(), [])
        if len(matches) == 1:
            label = _issue_identity_label(matches[0], player_display_names)
        else:
            roster_matches = roster_by_suffix.get(suffix.upper(), [])
            if len(roster_matches) == 1:
                pid = roster_matches[0]
                fake = {"pid": pid, "display_name": (player_display_names or {}).get(pid)}
                label = _issue_identity_label(fake, player_display_names)
            else:
                label = f"Player ID suffix …{suffix} (full ID not uniquely resolved)"
        pending_rows.append(f"<div class='attn-row'>{html.escape(label)}</div>")
    pending_note = (
        "<div class='v2-cycle-pending'><strong>Still pending:</strong>"
        + "".join(pending_rows) + "</div>"
        if pending_rows else ""
    )
    return (
        "<section class='v2-section v2-cycle'>"
        + _email_section_header("Today's Reward Cycle", f"{total_claims} total confirmed claims")
        + f"<strong>{html.escape(state)}</strong>"
        f"<p>{html.escape(recovery_note)}</p>{pending_note}"
        "</section>"
    )


def build_processing_overview_section(counts, results=None, run_label=""):
    """Show actual processing scope using table markup supported by email clients."""
    results = results if isinstance(results, list) else []
    rows = []

    if run_label == "Recovery Run":
        targets = []
        for result in results:
            recovery = result.get("immediate_recovery") or {}
            for target in (recovery.get("categories") or []):
                value = str(target).strip()
                if value and value not in targets:
                    targets.append(value)
        target_text = ", ".join(targets) if targets else "Saved unresolved reward target(s)"
        account_count = len(results)
        account_word = "account" if account_count == 1 else "accounts"
        rows.append((
            "Targeted Recovery",
            account_count,
            f"Replayed exact saved target(s): {target_text}. Unrelated reward categories were not checked.",
            "blue",
            "#EAF2FF",
        ))
        subtitle = f"{account_count} target {account_word} · recovery-only execution"
    else:
        modes = [
            ("Full Claim Path", int(counts.get("full_claim_accounts", 0) or 0),
             "Daily, Store and eligible Progression / Loyalty / Event checks",
             "blue", "#EAF2FF"),
            ("Backup Reconciliation", int(counts.get("backup_reconciliation_accounts", 0) or 0),
             "Targeted Daily / Store checks; Progression only when reopened",
             "purple", "#F2ECFF"),
            ("Promotional-only", int(counts.get("promotional_only_accounts", 0) or 0),
             "Store promotional scans; core rewards not checked",
             "teal", "#E8F8F5"),
        ]
        total = sum(value for _, value, _, _, _ in modes)
        active_paths = sum(1 for _, value, _, _, _ in modes if value)
        account_word = "account" if total == 1 else "accounts"
        path_word = "path" if active_paths == 1 else "paths"
        subtitle = f"{total} {account_word} · {active_paths} active {path_word}"
        rows = modes

    row_html = []
    for label, value, note, tone, background in rows:
        row_html.append(
            "<tr><td class='v2-mode-card' "
            f"bgcolor='{background}' style='background-color:{background};border:1px solid #DCE5F0;border-radius:9px;padding:10px 12px;'>"
            "<table role='presentation' width='100%' cellpadding='0' cellspacing='0' border='0'>"
            "<tr>"
            "<td valign='middle' style='padding:0 8px 0 0;'>"
            f"<strong class='v2-mode-title'>{html.escape(label)}</strong>"
            f"<small class='v2-mode-note'>{html.escape(note)}</small>"
            "</td>"
            f"<td width='58' align='right' valign='middle'><strong class='v2-mode-count'>{value}</strong></td>"
            "</tr></table></td></tr>"
        )

    return (
        "<section class='v2-section v2-processing'>"
        + _email_section_header("Processing Overview", subtitle)
        + "<table role='presentation' class='v2-mode-table' width='100%' cellpadding='0' cellspacing='0' border='0'>"
        + "".join(row_html)
        + "</table></section>"
    )



def build_run_reconciliation_email_section(results, run_label, configured_player_count):
    """Use the same read-only reconciliation logic as run_summary.json."""
    daily = sum(int(result.get("daily", 0) or 0) for result in results)
    store = sum(int(result.get("store", 0) or 0) for result in results)
    promotions = sum(int(result.get("store_temp", 0) or 0) for result in results)
    progression = sum(int(result.get("progression", 0) or 0) for result in results)
    loyalty = sum(int(result.get("loyalty", 0) or 0) for result in results)
    events = sum(event_claim_count(result.get("event_rewards")) for result in results)
    totals = {
        "daily_claimed_this_run": daily,
        "standard_store_claimed_this_run": store,
        "promotional_claims_this_run": promotions,
        "event_reward_claims_this_run": events,
        "progression_claimed_this_run": progression,
        "loyalty_claimed_this_run": loyalty,
        "all_claims_this_run": daily + store + promotions + progression + loyalty + events,
    }
    counters = _summary_counts(results)
    reconciliation = build_run_reconciliation(
        results,
        run_label=run_label,
        configured_player_count=configured_player_count,
        totals=totals,
        counters=counters,
    )
    checks = reconciliation.get("checks") or {}
    evaluated_checks = [value for value in checks.values() if value is not None]
    passed = sum(1 for value in evaluated_checks if value is True)
    checked = len(evaluated_checks)
    status = str(reconciliation.get("status") or "attention_required")
    matched = status == "matched"
    label = "Matched" if matched else "Attention Required"
    badge_class = "v2-state-success" if matched else "v2-state-warning"

    if run_label == "Recovery Run":
        title = "Recovery Run Consistency"
        scope = "Targeted recovery subset"
        detail = (
            f"{passed}/{checked} consistency checks passed · "
            f"{int(reconciliation.get('accounts_processed', 0) or 0)} targeted account(s) represented. "
            "Full-roster cycle completion is shown above."
        )
    else:
        title = "Run Reconciliation"
        scope = (
            "Full-roster primary run" if reconciliation.get("scope") == "full_roster_primary"
            else "Partial / mixed run"
        )
        detail = (
            f"{passed}/{checked} consistency checks passed · "
            f"{int(reconciliation.get('accounts_processed', 0) or 0)} / "
            f"{int(reconciliation.get('configured_player_count', configured_player_count) or 0)} accounts represented"
        )
        if matched and reconciliation.get("scope") != "full_roster_primary":
            detail += " · This does not certify a full-roster reward cycle"

    warnings = reconciliation.get("warnings") or []
    if warnings:
        detail += " · " + ", ".join(str(item).replace("_", " ") for item in warnings)
    return (
        "<section class='v2-section v2-reconciliation v2-validation-card'>"
        + _email_section_header(title, scope, label, badge_class)
        + f"<p class='v2-muted-note'>{html.escape(detail)}</p>"
        + "</section>"
    )



def build_email(results, run_label, run_index, job_start, meta, followup_summary=None, configured_player_count=None):
    """Build the compact Email V2 report; streak remains internal in bot_meta.json."""
    ist_now = get_ist_time()
    duration_seconds = max(0, int((ist_now - job_start).total_seconds()))
    duration_text = f"{duration_seconds // 60}m {duration_seconds % 60}s"
    account_count = len(results)
    try:
        configured_count = max(account_count, int(configured_player_count if configured_player_count is not None else account_count))
    except (TypeError, ValueError, OverflowError):
        configured_count = account_count
    cycle_summary = meta.get("daily_cycle_email_summary") if isinstance(meta.get("daily_cycle_email_summary"), dict) else None
    display_name_map = meta.get("player_display_names", {}) if isinstance(meta.get("player_display_names"), dict) else {}
    for result in results:
        pid = str(result.get("pid") or "").strip()
        if pid and not result.get("display_name") and display_name_map.get(pid):
            result["display_name"] = str(display_name_map[pid])
    followup_summary = followup_summary if isinstance(followup_summary, dict) else {}
    for collection_name in ("open_items", "updated_items"):
        for item in (followup_summary.get(collection_name) or []):
            if not isinstance(item, dict):
                continue
            player_id = str(item.get("player_key") or "").strip()
            if player_id:
                item["player_label"] = _issue_identity_label(
                    {"pid": player_id, "display_name": display_name_map.get(player_id)},
                    display_name_map,
                )


    is_recovery_rollup = bool(
        run_label == "Recovery Run"
        and cycle_summary
        and cycle_summary.get("reset_date") == _current_reset_date()
        and int(cycle_summary.get("configured_player_count", 0) or 0) == configured_count
    )
    counts = _summary_counts(results)
    hard_failures = {"Login Failed", "Error", "Failed"}

    def _is_hard_failure(result):
        return str(result.get("status") or "") in hard_failures

    # Category totals are scoped to accounts that actually checked that reward
    # type. Promo-only and backup-reconciliation runs must not imply unchecked
    # categories returned zero claims.
    standard_checked_results = [
        result for result in results
        if (bool(result.get("standard_rewards_checked"))
            or (result.get("processing_mode", "full_claim") == "full_claim"
                and not _is_hard_failure(result)))
    ]
    progression_checked_results = [
        result for result in results
        if (
            (bool(result.get("progression_checked")) and not result.get("progression_deferred"))
            or (result.get("processing_mode", "full_claim") == "full_claim"
                and not _is_hard_failure(result) and not result.get("progression_deferred"))
        )
    ]
    full_claim_results = [
        result for result in results
        if result.get("processing_mode", "full_claim") == "full_claim"
        and not _is_hard_failure(result)
    ]
    loyalty_checked_results = [
        result for result in full_claim_results if bool(result.get("has_loyalty"))
    ]

    daily_claims = sum(int(result.get("daily", 0) or 0) for result in standard_checked_results)
    store_claims = sum(int(result.get("store", 0) or 0) for result in standard_checked_results)
    progression_claims = sum(int(result.get("progression", 0) or 0) for result in progression_checked_results)
    loyalty_claims = sum(int(result.get("loyalty", 0) or 0) for result in loyalty_checked_results)
    # The email headline follows the same visibility policy as the detail sections:
    # paid/discounted promotions remain in internal run telemetry but are not counted
    # in this user-facing report total. Standard and active-event claims remain included.
    reportable_promotion_claims = int(counts.get("promotional_claims_made", 0) or 0)
    event_claims = sum(event_claim_count(result.get("event_rewards")) for result in results)
    confirmed_claims = (
        daily_claims + store_claims + progression_claims + loyalty_claims
        + reportable_promotion_claims + event_claims
    )
    if is_recovery_rollup:
        confirmed_claims = int(cycle_summary.get("total_claims", confirmed_claims) or 0)

    attention_player_ids = set()
    # Recovery summaries retain only masked last-four identifiers. Build a lookup
    # against this run's actual player IDs so the same account is not counted once
    # by its full ID and again by its pending suffix.
    result_pids_by_suffix = {}
    for result in results:
        result_pid = str(result.get("pid") or "").strip()
        if result_pid:
            result_pids_by_suffix.setdefault(result_pid[-4:], set()).add(result_pid)
    exception_cards = []
    for result in results:
        status = str(result.get("status") or "")
        promotions = result.get("promotions_observed", []) or []
        promo_attention = any(
            str(promo.get("status") or "") in ("claim_unconfirmed", "click_failed", "available", "popup_blocked")
            and _promotion_detail_worthy(promo)
            for promo in promotions
        )
        hidden_promo_unconfirmed = any(
            str(promo.get("status") or "") in ("claim_unconfirmed", "click_failed", "available", "popup_blocked")
            and not _promotion_detail_worthy(promo)
            for promo in promotions
        )
        event_attention = any(bool(event.get("retry_required")) for event in (result.get("event_rewards", []) or []))
        standard_attention = bool(
            result.get("daily_unconfirmed")
            or result.get("store_unconfirmed_indices")
            or result.get("progression_unconfirmed")
        )
        grenade_balance = result.get("grenade_balance")
        grenade_attention = bool(
            isinstance(grenade_balance, dict)
            and str(grenade_balance.get("status") or "not_validated") != "pass"
        )
        # The claim engine retains retry/status telemetry for every observed offer.
        # The email, however, must not expose a filtered paid/discounted offer
        # indirectly through a generic Partial/retry warning. Suppress that warning
        # only when the known promotional-confirmation reason is the sole issue.
        hidden_promo_only_failure = bool(
            hidden_promo_unconfirmed
            and not standard_attention
            and not result.get("promotion_detection_failed")
            and not promo_attention
            and not event_attention
            and status not in hard_failures
            and str(result.get("fail_reason") or "").strip()
                == "At least one observed promotional claim was not confirmed by the portal"
        )
        needs_attention = (
            status in hard_failures
            or (status in ("Partial", "No Rewards") and not hidden_promo_only_failure)
            or (bool(result.get("retry_required")) and not hidden_promo_only_failure)
            or standard_attention
            or bool(result.get("promotion_detection_failed"))
            or promo_attention
            or event_attention
            or grenade_attention
        )
        if needs_attention:
            attention_player_ids.add(str(result.get("pid") or _display_label(result)))
            has_specific_detail = bool(
                standard_attention
                or result.get("promotion_detection_failed")
                or promo_attention
                or event_attention
                or grenade_attention
            )
            if status in hard_failures or not has_specific_detail:
                reason = str(result.get("fail_reason") or "One or more items need review")
                exception_cards.append(
                    "<div class='v2-exception'>"
                    f"<div><strong>{html.escape(_issue_identity_label(result))}</strong>"
                    f"<span class='v2-status'>{html.escape(status or 'Needs review')}</span></div>"
                    f"<p>{html.escape(reason)}</p></div>"
                )

    for item in (followup_summary or {}).get("open_items", []):
        player_id = str(item.get("player_key") or "").strip()
        attention_player_ids.add(player_id or f"followup:{item.get('key', item.get('title', 'unknown'))}")
    if is_recovery_rollup:
        for raw_suffix in (cycle_summary.get("pending_player_last4") or []):
            suffix = str(raw_suffix).strip()[-4:]
            if not suffix:
                continue
            matching_pids = result_pids_by_suffix.get(suffix, set())
            if len(matching_pids) == 1:
                attention_player_ids.add(next(iter(matching_pids)))
            elif not matching_pids or not matching_pids.intersection(attention_player_ids):
                # When a suffix is ambiguous or was not processed in this run,
                # retain one masked pending identity instead of double-counting it.
                attention_player_ids.add("pending:" + suffix)
    attention_accounts = len(attention_player_ids)

    actual_failures = int(counts.get("actual_failures", 0) or 0)
    if account_count == 0 or (actual_failures and actual_failures == account_count):
        health_label, health_class = "Failed", "v2-failed"
    elif attention_accounts:
        health_label, health_class = "Attention Required", "v2-warn"
    else:
        health_label, health_class = "Completed", "v2-healthy"

    standard_attention_html = build_standard_attention_section(results)
    deferred_checks_html = build_deferred_checks_section(results)
    promotion_html = build_promotional_section(results)
    followup_html = build_promotional_followup_section(followup_summary or {})
    event_html = build_event_rewards_section(results)
    grenade_html = build_grenade_balance_email_section(results)
    reconciliation_html = build_run_reconciliation_email_section(results, run_label, configured_count)
    validation_html = grenade_html + reconciliation_html
    # A run-level reconciliation mismatch is an execution attention state even
    # when no individual account was attributed as the cause.
    if "v2-state-warning" in reconciliation_html and health_label != "Failed":
        health_label, health_class = "Attention Required", "v2-warn"
    immediate_recovery_html = build_immediate_recovery_email_section(results, run_label)
    preexisting_portal_claim_html = build_preexisting_portal_claim_section(results)
    daily_cycle_html = build_daily_cycle_email_section(
        cycle_summary, results, display_name_map
    ) if is_recovery_rollup else ""
    exception_details = standard_attention_html + "".join(exception_cards)
    if exception_details:
        attention_html = (
            "<section class='v2-section v2-exceptions'>"
            f"<div class='v2-section-head'><h2>Attention &amp; Exceptions</h2><span>{attention_accounts} accounts</span></div>"
            + exception_details + "</section>"
        )
    elif attention_accounts:
        attention_html = (
            "<section class='v2-section v2-exceptions'>"
            f"<div class='v2-section-head'><h2>Attention &amp; Exceptions</h2><span>{attention_accounts} accounts</span></div>"
            "<p class='v2-muted-note'>Review the highlighted items in the reward, validation, promotion or event sections above.</p>"
            "</section>"
        )
    else:
        # The zero-attention state is already clear from the KPI; omit a redundant section.
        attention_html = ""

    def _category_card(label, checked_results, claim_key, cycle_key, tone, empty_note="Not checked in this run"):
        if is_recovery_rollup:
            value = int((cycle_summary.get("claim_totals") or {}).get(cycle_key, 0) or 0)
            note = "confirmed in today's cycle"
        elif not checked_results:
            value, note = "—", empty_note
        else:
            value = sum(int(result.get(claim_key, 0) or 0) for result in checked_results)
            account_word = "account" if len(checked_results) == 1 else "accounts"
            note = f"{len(checked_results)} {account_word} checked"
        return (
            f"<td class='v2-category-cell' width='25%'><div class='v2-category v2-category-{tone}'>"
            f"<span>{label}</span><strong>{value}</strong><small>{html.escape(note)}</small></div></td>"
        )

    promotions_checked_results = [
        result for result in results
        if result.get("promotion_scan_completed") or result.get("promotions_observed")
    ]
    event_checked_results = [
        result for result in results
        if isinstance(result.get("event_rewards"), list) and result.get("event_rewards")
    ]
    category_cards = [
        _category_card("Daily Rewards", standard_checked_results, "daily", "daily", "daily"),
        _category_card("Store Rewards", standard_checked_results, "store", "store", "store"),
        _category_card("Progression Rewards", progression_checked_results, "progression", "progression", "progression"),
        _category_card(
            "Loyalty Rewards", loyalty_checked_results, "loyalty", "loyalty", "loyalty",
            "No enrolled accounts" if full_claim_results else "Not checked in this run",
        ),
    ]
    category_html = (
        "<table role='presentation' class='v2-category-table' width='100%' cellpadding='0' cellspacing='0' border='0'><tr>"
        + "".join(category_cards)
        + "</tr></table>"
    )
    generated = ist_now.strftime("%d %b %Y · %I:%M %p IST")
    safe_run_label = html.escape(str(run_label or "Reward Run"))
    confirmed_note = "Total confirmed in today's cycle" if is_recovery_rollup else "Portal-confirmed during this run"
    accounts_kpi_label = "Accounts complete" if is_recovery_rollup else "Accounts processed"
    accounts_kpi_value = (
        f"{int(cycle_summary.get('accounts_complete', 0) or 0)} / {configured_count}"
        if is_recovery_rollup else f"{account_count} / {configured_count}"
    )
    accounts_kpi_note = "Today's full-roster cycle" if is_recovery_rollup else "Of configured roster"

    immediate_attempted = sum(
        1 for result in results
        if isinstance(result.get("immediate_recovery"), dict)
        and result["immediate_recovery"].get("attempted")
    )
    immediate_resolved = sum(
        1 for result in results
        if isinstance(result.get("immediate_recovery"), dict)
        and result["immediate_recovery"].get("attempted")
        and result["immediate_recovery"].get("status") == "resolved"
    )
    immediate_unresolved = sum(
        1 for result in results
        if isinstance(result.get("immediate_recovery"), dict)
        and result["immediate_recovery"].get("attempted")
        and result["immediate_recovery"].get("status") != "resolved"
    )
    if is_recovery_rollup:
        recovery_kpi_value = int(cycle_summary.get("recovery_accounts_count", 0) or 0)
        recovery_kpi_label = "Recovery accounts"
        recovery_kpi_note = (
            f"{int(cycle_summary.get('recovery_claims_added', 0) or 0)} additional claims confirmed"
        )
    else:
        recovery_kpi_value = immediate_attempted
        recovery_kpi_label = "In-run recovery"
        recovery_kpi_note = (
            f"{immediate_resolved} resolved · {immediate_unresolved} pending"
            if immediate_attempted else "No in-run retries were needed"
        )
    kpi_items = [
        (accounts_kpi_label, accounts_kpi_value, accounts_kpi_note, "accounts", "👥"),
        ("Confirmed claims", confirmed_claims, confirmed_note, "claims", "🎁"),
        (recovery_kpi_label, recovery_kpi_value, recovery_kpi_note, "recovery", "🔄"),
        ("Need attention", attention_accounts, "Accounts with item-level exceptions", "attention", "⚠️"),
    ]
    kpi_cells = "".join(
        "<td class='v2-kpi-cell' width='25%' valign='top'>"
        f"<div class='v2-kpi v2-kpi-{tone}'>"
        f"<span>{icon} {html.escape(str(label))}</span>"
        f"<strong>{html.escape(str(value))}</strong>"
        f"<small>{html.escape(str(note))}</small>"
        "</div></td>"
        for label, value, note, tone, icon in kpi_items
    )
    kpi_html = (
        "<table role='presentation' class='v2-kpi-table' width='100%' cellpadding='0' cellspacing='0' border='0'><tr>"
        + kpi_cells
        + "</tr></table>"
    )
    processing_html = build_processing_overview_section(counts, results, run_label)
    rewards_heading = "Reward Claims (Today's Cycle)" if is_recovery_rollup else "Reward Claims (This Run)"
    rewards_subtitle = (
        "Cumulative portal-confirmed totals from the primary and recovery runs"
        if is_recovery_rollup else
        "Portal-confirmed counts for reward categories checked in this run"
    )

    run_id = str(os.getenv("GITHUB_RUN_ID") or "").strip()
    repository = str(os.getenv("GITHUB_REPOSITORY") or "").strip()
    commit_sha = str(os.getenv("GITHUB_SHA") or "").strip()
    run_link_html = ""
    if run_id.isdigit() and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        run_url = f"https://github.com/{repository}/actions/runs/{run_id}"
        safe_url = html.escape(run_url, quote=True)
        run_link_html = (
            f"<a href='{safe_url}'>View run details &amp; logs</a> · "
            f"<a href='{safe_url}#artifacts'>Artifacts</a>"
        )
    run_meta = []
    if run_id.isdigit():
        run_meta.append(f"Run #{html.escape(run_id)}")
    if re.fullmatch(r"[0-9a-fA-F]{7,40}", commit_sha):
        run_meta.append(f"Commit {html.escape(commit_sha[:7])}")
    run_meta.append(f"Duration {duration_text}")
    footer_meta = " · ".join(run_meta)

    return (
        "<!DOCTYPE html><html lang='en'><head><meta charset='UTF-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1.0'>"
        "<style>"
        "*{box-sizing:border-box}"
        "body{margin:0;padding:16px;background:#f2f5fa;color:#172033;font-family:Arial,Helvetica,sans-serif}"
        ".v2-shell{width:100%;max-width:780px;margin:0 auto}"
        ".v2-hero{padding:22px 24px;border-radius:14px;background-color:#173968;color:#fff}"
        ".v2-brand{font-size:11px;letter-spacing:1.25px;text-transform:uppercase;color:#dbeafe}"
        ".v2-hero h1{font-size:24px;margin:8px 0 6px;line-height:1.2;color:#fff}"
        ".v2-meta{font-size:12px;color:#dbeafe;line-height:1.5}"
        ".v2-health{display:inline-block;margin-top:12px;padding:6px 11px;border-radius:20px;font-size:11px;font-weight:700;background-color:#fff}"
        ".v2-healthy{color:#166534;background-color:#dcfce7}"
        ".v2-warn{color:#92400e;background-color:#fef3c7}"
        ".v2-failed{color:#991b1b;background-color:#fee2e2}"
        ".v2-kpi-table,.v2-category-table{width:100%;table-layout:fixed;border-collapse:separate;border-spacing:6px 0;margin:14px 0}"
        ".v2-kpi-cell,.v2-category-cell{width:25%;vertical-align:top;padding:0 2px}"
        ".v2-kpi,.v2-category{border:1px solid #dbe4ef;border-radius:12px;padding:13px 11px;min-height:104px}"
        ".v2-kpi-accounts{background-color:#eaf2ff;border-color:#bfd5ff}"
        ".v2-kpi-claims{background-color:#f1eafe;border-color:#dac8ff}"
        ".v2-kpi-recovery{background-color:#e8f8ef;border-color:#b7e6c8}"
        ".v2-kpi-attention{background-color:#fff3e4;border-color:#f4d2a4}"
        ".v2-kpi span,.v2-category span{display:block;color:#52647d;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.35px;line-height:1.4}"
        ".v2-kpi strong{display:block;font-size:26px;margin:7px 0 3px;color:#172b4d;line-height:1.1;overflow-wrap:anywhere}"
        ".v2-kpi small,.v2-category small{font-size:11px;color:#52647d;line-height:1.4;display:block}"
        ".v2-section{background-color:#fff;border:1px solid #e1e7ef;border-radius:12px;padding:15px;margin:0 0 12px}"
        ".v2-section-head{display:block;margin:0 0 10px}"
        ".v2-section-head h2{display:block;font-size:15px;margin:0 0 4px;line-height:1.35;color:#172b4d}"
        ".v2-section-head>span{display:block;font-size:11px;color:#64748b;line-height:1.45;text-align:left;max-width:100%;white-space:normal;margin:3px 0 0}"
        ".v2-section-subtitle{display:block;font-size:11px;color:#64748b;line-height:1.45;margin:4px 0 0}"
        ".v2-mode-table{width:100%;border-collapse:separate;border-spacing:0 6px;table-layout:fixed}"
        ".v2-mode-card{border-radius:9px;padding:10px 12px;border:1px solid #dce5f0}"
        ".v2-mode-title{display:block;font-size:12px;font-weight:700;color:#253b5b;line-height:1.35}"
        ".v2-mode-note{display:block;font-size:11px;color:#61738d;line-height:1.4;margin-top:3px}"
        ".v2-mode-count{font-size:22px;color:#173968;line-height:1.1}"
        ".v2-category{padding:13px 11px;min-height:100px}"
        ".v2-category-daily{background-color:#eaf2ff;border-color:#bfd5ff}"
        ".v2-category-store{background-color:#f1eafe;border-color:#dac8ff}"
        ".v2-category-progression{background-color:#e8f8ef;border-color:#b7e6c8}"
        ".v2-category-loyalty{background-color:#fff3e4;border-color:#f4d2a4}"
        ".v2-category strong{display:block;font-size:24px;margin:7px 0 3px;color:#172b4d;line-height:1.1}"
        ".v2-item{display:table;width:100%;table-layout:fixed;border-collapse:collapse;border-top:1px solid #edf1f6;font-size:12px}"
        ".v2-item:first-of-type{border-top:0}"
        ".v2-item>div{display:table-cell;width:42%;vertical-align:top;padding:10px 10px 10px 0}"
        ".v2-item>.v2-result{display:table-cell;width:58%;vertical-align:top;padding:10px 0 10px 8px;max-width:none;text-align:right;color:#475569;font-size:11px;line-height:1.45;overflow-wrap:anywhere}"
        ".v2-item strong{display:block;overflow-wrap:anywhere;color:#253b5b}"
        ".v2-item small{display:block;color:#64748b;font-size:11px;margin-top:3px;line-height:1.4}"
        ".v2-good{color:#166534}"
        ".v2-attention{background-color:#fffbeb;padding:10px;border:1px solid #fde68a;border-radius:8px;margin:6px 0}"
        ".v2-exceptions{background-color:#fffbeb;border-color:#fcd34d}"
        ".v2-deferred{background-color:#f8fafc;border-color:#cbd5e1}"
        ".v2-exception{background-color:#fff;border:1px solid #e5e7eb;border-left:4px solid #d97706;border-radius:8px;padding:10px;margin:7px 0}"
        ".v2-exception div{display:block;margin-bottom:4px}"
        ".v2-exception p{margin:7px 0 0;color:#64748b;font-size:12px;line-height:1.45;overflow-wrap:anywhere}"
        ".v2-status{font-size:11px;color:#92400e}"
        ".v2-all-clear{background-color:#f0fdf4;border:1px solid #bbf7d0;border-radius:9px;padding:11px;color:#166534;font-size:12px;line-height:1.45}"
        ".v2-all-clear-section{background-color:#fff}"
        ".v2-cycle p{margin:7px 0 0;color:#475569;font-size:12px;line-height:1.5}"
        ".v2-cycle small{display:block;margin-top:7px;color:#92400e;font-size:11px}"
        ".v2-state{display:inline-block;padding:4px 8px;border-radius:20px;font-size:11px;font-weight:700;line-height:1.35;white-space:normal}"
        ".v2-state-success{background-color:#dcfce7;color:#166534}"
        ".v2-state-warning{background-color:#fef3c7;color:#92400e}"
        ".v2-state-neutral{background-color:#eef2f7;color:#475569}"
        ".v2-muted-note{margin:0;color:#64748b;font-size:12px;line-height:1.5;overflow-wrap:anywhere}"
        ".v2-grenade-summary{display:block;margin:6px 0 8px;padding:9px 10px;background-color:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;color:#166534;font-size:12px;line-height:1.5;overflow-wrap:anywhere}"
        ".v2-grenade .v2-item{margin:5px 0}"
        ".v2-grenade .v2-item small{font-size:11px;color:#64748b}"
        ".v2-offer-row{display:table;width:100%;table-layout:fixed;border-collapse:collapse;border-top:1px solid #edf1f6;font-size:12px}"
        ".v2-offer-row:first-of-type{border-top:0}"
        ".v2-offer-row>div,.v2-offer-row>span{display:table-cell;width:50%;vertical-align:top;padding:9px 0}"
        ".v2-offer-row strong{display:block;color:#253b5b;overflow-wrap:anywhere}"
        ".v2-offer-row small{display:block;color:#64748b;font-size:11px;margin-top:3px;line-height:1.4}"
        ".v2-offer-row>span{text-align:right;color:#475569;line-height:1.45;overflow-wrap:anywhere}"
        ".v2-promo-details{margin-top:4px}"
        ".v2-muted-note + .v2-item{border-top:0}"
        ".attn-box{padding:10px;background-color:#fff7ed;border:1px solid #fed7aa;border-radius:9px}"
        ".attn-title{font-weight:700;color:#9a3412;font-size:12px;margin-bottom:6px}"
        ".attn-row{padding:7px 0;border-top:1px solid #fed7aa;font-size:12px;line-height:1.45;color:#7c2d12}"
        ".v2-footer{text-align:center;color:#64748b;font-size:11px;line-height:1.55;padding:14px 8px}"
        ".v2-footer a{color:#1d4ed8;text-decoration:underline}"
        "@media screen and (max-width:640px){"
        " body{padding:8px}"
        " .v2-shell{max-width:100%}"
        " .v2-hero{padding:18px 16px}"
        " .v2-hero h1{font-size:21px}"
        " .v2-kpi-cell,.v2-category-cell{display:inline-block !important;width:48% !important;vertical-align:top;padding:0 2px 6px}"
        " .v2-kpi{min-height:94px;padding:11px 9px}"
        " .v2-kpi strong{font-size:22px}"
        " .v2-category{min-height:91px;padding:11px 9px}"
        " .v2-category strong{font-size:22px}"
        " .v2-section{padding:12px}"
        " .v2-section-head h2{font-size:15px}"
        " .v2-item{display:block;width:100%}"
        " .v2-item>div,.v2-item>.v2-result{display:block;width:100%;text-align:left;padding:6px 0;max-width:none}"
        " .v2-offer-row{display:block;width:100%}"
        " .v2-offer-row>div,.v2-offer-row>span{display:block;width:100%;text-align:left;padding:6px 0}"
        " .v2-footer{font-size:11px}"
        "}"
        "</style></head><body><div class='v2-shell'>"
        "<header class='v2-hero'>"
        "<div class='v2-brand'>🎮 CS Rewards Bot · Automated Rewards Report</div>"
        "<h1>Daily Rewards Report</h1>"
        f"<div class='v2-meta'>{safe_run_label} · Started {job_start.strftime('%d %b %Y, %I:%M %p IST')} · Completed {generated} · Duration {duration_text}</div>"
        f"<span class='v2-health {health_class}'>{health_label}</span>"
        "</header>"
        + kpi_html
        + processing_html
        + "<section class='v2-section v2-rewards'>"
        + _email_section_header(rewards_heading, rewards_subtitle)
        + category_html
        + "</section>"
        + (daily_cycle_html or "")
        + validation_html
        + (deferred_checks_html or "")
        + (immediate_recovery_html or "")
        + attention_html
        + (preexisting_portal_claim_html or "")
        + (promotion_html or "")
        + (followup_html or "")
        + (event_html or "")
        + f"<footer class='v2-footer'>CS Rewards Bot {html.escape(str(VERSION))} · {html.escape(footer_meta)}"
        + (f"<div>{run_link_html}</div>" if run_link_html else "")
        + "</footer></div></body></html>"
    )


def _is_cloudflare_interstitial(driver):
    """Detect an actually visible Cloudflare challenge, not incidental page source.

    Normal portal pages can load Cloudflare-related scripts or contain challenge
    strings in bundled JavaScript. Searching the entire HTML source therefore
    creates false positives. This check relies on the title, visible page text,
    and visible challenge-specific UI. It does not interact with or solve a challenge.
    """
    try:
        signals = driver.execute_script(r"""
            function visible(el) {
                if (!el) return false;
                const s = window.getComputedStyle(el);
                const r = el.getBoundingClientRect();
                return !!(el.getClientRects().length && s.visibility !== 'hidden'
                    && s.display !== 'none' && r.width > 0 && r.height > 0);
            }
            const title = (document.title || '').trim();
            const bodyText = (document.body ? document.body.innerText : '')
                .replace(/\s+/g, ' ').trim().slice(0, 1400).toLowerCase();
            const challengePhrases = [
                'checking your browser',
                'verify you are human',
                'verification required',
                'performing security verification',
                'complete the security check'
            ];
            const visibleTextSignals = challengePhrases.filter(p => bodyText.includes(p));
            const selectors = [
                '#challenge-form', '#challenge-running', '#challenge-stage',
                '#cf-challenge-running', '[id^="cf-chl-"]',
                'iframe[src*="challenges.cloudflare.com"]'
            ];
            const visibleElementSignals = [];
            for (const selector of selectors) {
                try {
                    if (Array.from(document.querySelectorAll(selector)).some(visible)) {
                        visibleElementSignals.push(selector);
                    }
                } catch (_) {}
            }
            return {
                title: title,
                visibleTextSignals: visibleTextSignals,
                visibleElementSignals: visibleElementSignals
            };
        """) or {}

        title = str(signals.get('title', driver.title or '')).strip().lower()
        title_signal = 'just a moment' in title
        text_signals = signals.get('visibleTextSignals') or []
        element_signals = signals.get('visibleElementSignals') or []
        detected = bool(title_signal or text_signals or element_signals)

        if detected:
            reasons = []
            if title_signal:
                reasons.append('challenge title')
            if text_signals:
                reasons.append('visible text: ' + ', '.join(text_signals))
            if element_signals:
                reasons.append('visible challenge UI: ' + ', '.join(element_signals))
            log('🛡️ Visible challenge indicators: ' + '; '.join(reasons))
        else:
            log(f"ℹ️ No visible Cloudflare challenge detected (title={signals.get('title', driver.title or '')!r})")
        return detected
    except Exception as exc:
        # A detector error must not be treated as proof that a challenge exists.
        log(f"⚠️ Challenge detection was inconclusive: {type(exc).__name__}; continuing with read-only UI inspection")
        return False


def _dom_snapshot_current_context(driver):
    """Collect bounded, non-secret DOM metadata from the current document."""
    return driver.execute_script(r"""
        function visible(el) {
            const s = window.getComputedStyle(el);
            return !!(el.getClientRects().length && s.visibility !== 'hidden' && s.display !== 'none');
        }
        function txt(el) {
            return (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 120);
        }
        function safePath(raw) {
            try { const u = new URL(raw, location.href); return u.origin + u.pathname; }
            catch (_) { return ''; }
        }
        const inputs = Array.from(document.querySelectorAll('input, textarea, select')).slice(0, 80).map(el => ({
            tag: el.tagName.toLowerCase(),
            type: (el.getAttribute('type') || '').slice(0, 40),
            placeholder: (el.getAttribute('placeholder') || '').slice(0, 120),
            name: (el.getAttribute('name') || '').slice(0, 120),
            id: (el.id || '').slice(0, 120),
            aria_label: (el.getAttribute('aria-label') || '').slice(0, 120),
            autocomplete: (el.getAttribute('autocomplete') || '').slice(0, 80),
            visible: visible(el),
            disabled: !!el.disabled,
            value_present: !!(el.value)
        }));
        const buttons = Array.from(document.querySelectorAll('button, a, [role="button"]')).slice(0, 100).map(el => ({
            tag: el.tagName.toLowerCase(),
            text: txt(el),
            type: (el.getAttribute('type') || '').slice(0, 40),
            aria_label: (el.getAttribute('aria-label') || '').slice(0, 120),
            title: (el.getAttribute('title') || '').slice(0, 120),
            visible: visible(el),
            disabled: !!el.disabled
        }));
        const iframes = Array.from(document.querySelectorAll('iframe')).slice(0, 30).map(el => ({
            title: (el.getAttribute('title') || '').slice(0, 120),
            name: (el.getAttribute('name') || '').slice(0, 120),
            id: (el.id || '').slice(0, 120),
            src_path: safePath(el.src || ''),
            visible: visible(el)
        }));
        const bodyText = (document.body ? document.body.innerText : '').replace(/\s+/g, ' ').trim();
        return {
            title: document.title || '',
            url: location.origin + location.pathname,
            ready_state: document.readyState,
            body_text_excerpt: bodyText.slice(0, 1800),
            input_count: document.querySelectorAll('input, textarea, select').length,
            visible_input_count: inputs.filter(x => x.visible).length,
            buttons,
            inputs,
            iframes
        };
    """)


def capture_login_diagnostics(driver, stage, force=False):
    """Write one small screenshot + JSON snapshot; never includes raw player IDs."""
    global _LOGIN_FAILURE_DIAGNOSTIC_CAPTURED
    if _LOGIN_FAILURE_DIAGNOSTIC_CAPTURED and not force:
        return None

    try:
        os.makedirs(LOGIN_DIAGNOSTICS_DIR, exist_ok=True)
        timestamp = get_ist_time().strftime('%Y%m%dT%H%M%S')
        safe_stage = re.sub(r'[^A-Za-z0-9_-]+', '_', stage)[:48]
        base = os.path.join(LOGIN_DIAGNOSTICS_DIR, f'{timestamp}_{safe_stage}')

        try:
            driver.switch_to.default_content()
        except Exception:
            pass

        snapshot = {
            'captured_at_ist': get_ist_time().isoformat(),
            'stage': stage,
            'window_handle_count': len(getattr(driver, 'window_handles', []) or []),
            'current_window_handle_present': bool(getattr(driver, 'current_window_handle', None)),
        }
        try:
            snapshot['dom'] = _dom_snapshot_current_context(driver)
        except Exception as exc:
            snapshot['dom_error'] = f'{type(exc).__name__}: {str(exc)[:240]}'

        try:
            screenshot_path = base + '.png'
            snapshot['screenshot_saved'] = bool(driver.save_screenshot(screenshot_path))
            if not snapshot['screenshot_saved']:
                snapshot['screenshot_error'] = 'WebDriver returned false from save_screenshot'
        except Exception as exc:
            snapshot['screenshot_saved'] = False
            snapshot['screenshot_error'] = f'{type(exc).__name__}: {str(exc)[:240]}'

        json_path = base + '.json'
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(snapshot, f, indent=2, ensure_ascii=False)

        _LOGIN_FAILURE_DIAGNOSTIC_CAPTURED = True
        log(f'🧪 Login diagnostic saved: {base}.[png/json]')
        log('🧪 Diagnostic metadata excludes input values and raw player IDs.')
        return base
    except Exception as exc:
        log(f'⚠️ Could not save login diagnostic: {type(exc).__name__}: {str(exc)[:200]}')
        return None


def _find_visible_login_input(driver):
    """Return metadata for visible input controls in the current document."""
    try:
        return driver.execute_script(r"""
            const visible = el => {
                const s = getComputedStyle(el);
                return !!(el.getClientRects().length && s.visibility !== 'hidden' && s.display !== 'none');
            };
            return Array.from(document.querySelectorAll('input, textarea')).filter(visible).map(el => ({
                tag: el.tagName.toLowerCase(),
                type: (el.getAttribute('type') || '').toLowerCase(),
                placeholder: (el.getAttribute('placeholder') || '').trim(),
                name: (el.getAttribute('name') || '').trim(),
                id: (el.id || '').trim(),
                aria_label: (el.getAttribute('aria-label') || '').trim(),
                disabled: !!el.disabled
            }));
        """) or []
    except Exception:
        return []


def _login_input_is_present(input_metadata):
    for item in input_metadata:
        descriptor = ' '.join(str(item.get(k, '')) for k in ('placeholder', 'name', 'id', 'aria_label')).lower()
        if 'user id' in descriptor or 'player id' in descriptor or item.get('name', '').lower() == 'playerid':
            return True
    return False


def _wait_for_cloudflare_to_clear_naturally(driver, seconds=12):
    """Diagnostic mode waits for a challenge to resolve; it never clicks/interacts with it."""
    if not _is_cloudflare_interstitial(driver):
        return True
    log('🛡️ Challenge/interstitial detected. Waiting for it to resolve naturally; no challenge interaction will be attempted.')
    for _ in range(seconds):
        time.sleep(1)
        if not _is_cloudflare_interstitial(driver):
            log('✅ Interstitial is no longer detected.')
            return True
    log('⚠️ Interstitial is still present after the diagnostic wait.')
    return False


def run_login_diagnostic():
    """Single-player-free diagnostic: inspect login UI only; no ID submission or rewards."""
    driver = None
    try:
        log('=' * 60)
        log('CS HUB LOGIN DIAGNOSTIC — NO REWARD CLAIMS')
        log('This mode does not type a player ID, submit the login form, or claim rewards.')
        log('=' * 60)

        driver = create_driver()
        driver.get('https://hub.vertigogames.co/daily-rewards')
        try:
            WebDriverWait(driver, 15).until(
                lambda d: d.execute_script('return document.readyState') in ('interactive', 'complete')
            )
        except Exception:
            pass
        time.sleep(2)
        capture_login_diagnostics(driver, 'initial_page', force=True)

        if not _wait_for_cloudflare_to_clear_naturally(driver):
            capture_login_diagnostics(driver, 'challenge_persisted', force=True)
            log('DIAGNOSTIC_RESULT=CHALLENGE_PERSISTED')
            return 2

        accept_cookies(driver)
        time.sleep(1)
        inputs = _find_visible_login_input(driver)
        if _login_input_is_present(inputs):
            capture_login_diagnostics(driver, 'login_input_present_initially', force=True)
            log('DIAGNOSTIC_RESULT=LOGIN_INPUT_PRESENT')
            log('The User ID field is present before clicking any login control.')
            return 0

        # Only click a clearly labelled login trigger. Do not use generic .btn/.button selectors.
        clicked = False
        exact_login_xpaths = [
            "//button[normalize-space()='Login' or normalize-space()='Log in' or normalize-space()='Sign in']",
            "//a[normalize-space()='Login' or normalize-space()='Log in' or normalize-space()='Sign in']",
            "//*[@role='button' and (normalize-space()='Login' or normalize-space()='Log in' or normalize-space()='Sign in')]",
        ]
        prior_handles = set(driver.window_handles)
        for xpath in exact_login_xpaths:
            try:
                candidates = driver.find_elements(By.XPATH, xpath)
            except Exception:
                candidates = []
            for element in candidates:
                try:
                    if element.is_displayed() and element.is_enabled():
                        element.click()
                        clicked = True
                        log('🧪 Clicked an exact-label Login control to reveal the form.')
                        break
                except Exception:
                    continue
            if clicked:
                break

        if clicked:
            time.sleep(2)
            new_handles = [h for h in driver.window_handles if h not in prior_handles]
            if new_handles:
                driver.switch_to.window(new_handles[0])
                time.sleep(1)

        # Inspect top-level context and accessible iframe documents; do not type or submit.
        inputs = _find_visible_login_input(driver)
        frame_summaries = []
        try:
            driver.switch_to.default_content()
            frames = driver.find_elements(By.TAG_NAME, 'iframe')[:20]
        except Exception:
            frames = []
        for index, frame in enumerate(frames):
            frame_info = {'index': index, 'visible_inputs': []}
            try:
                driver.switch_to.default_content()
                driver.switch_to.frame(frame)
                frame_info['visible_inputs'] = _find_visible_login_input(driver)
            except Exception as exc:
                frame_info['inspection_error'] = f'{type(exc).__name__}: {str(exc)[:160]}'
            finally:
                try:
                    driver.switch_to.default_content()
                except Exception:
                    pass
            frame_summaries.append(frame_info)

        top_found = _login_input_is_present(inputs)
        frame_found = any(_login_input_is_present(x.get('visible_inputs', [])) for x in frame_summaries)
        # Force a final snapshot after the modal/control interaction, if any.
        base = capture_login_diagnostics(driver, 'after_login_ui_inspection', force=True)
        if frame_summaries and base:
            json_path = base + '.json'
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    snapshot = json.load(f)
                snapshot['frame_input_scan'] = frame_summaries
                with open(json_path, 'w', encoding='utf-8') as f:
                    json.dump(snapshot, f, indent=2, ensure_ascii=False)
            except Exception as exc:
                log(f'⚠️ Could not append frame scan: {type(exc).__name__}: {str(exc)[:160]}')

        if top_found or frame_found:
            log('DIAGNOSTIC_RESULT=LOGIN_INPUT_PRESENT_AFTER_UI_INTERACTION')
            log(f'Visible top-level inputs: {len(inputs)}; frames inspected: {len(frame_summaries)}; matching input found in frame: {frame_found}')
            return 0

        log('DIAGNOSTIC_RESULT=LOGIN_INPUT_NOT_FOUND')
        log(f'Exact-label login trigger clicked: {clicked}; visible top-level inputs: {len(inputs)}; frames inspected: {len(frame_summaries)}')
        log('Download the login diagnostics artifact and inspect the PNG + JSON snapshots.')
        return 1
    except Exception as exc:
        log(f'❌ Diagnostic failed: {type(exc).__name__}: {str(exc)[:240]}')
        if driver:
            capture_login_diagnostics(driver, 'diagnostic_exception', force=True)
        return 1
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


def run_login_only_test():
    """Submit one configured player ID and verify login; never claim rewards."""
    driver = None
    try:
        players = []
        with open(PLAYER_ID_FILE, "r", newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                pid = (row.get("player_id") or "").strip()
                if pid:
                    players.append(pid)

        if not players:
            log(f"❌ LOGIN_ONLY_RESULT=FAILED — no player_id entries in {PLAYER_ID_FILE}")
            return 2

        pid = players[0]
        log("=" * 60)
        log("CS HUB LOGIN-ONLY TEST — NO REWARD CLAIMS")
        log("One configured player ID will be submitted; the ID itself will not be logged.")
        log("This mode does not call any reward-claiming functions or update claim history.")
        log("=" * 60)

        driver = create_driver()
        success = login_to_hub(driver, pid)
        if success:
            log("✅ LOGIN_ONLY_RESULT=SUCCESS")
            log("Authentication state changed as expected. No rewards were claimed.")
            return 0

        log("❌ LOGIN_ONLY_RESULT=FAILED")
        log("Login could not be confirmed. No rewards were claimed.")
        return 1
    except Exception as exc:
        log(f"❌ LOGIN_ONLY_RESULT=ERROR — {type(exc).__name__}: {str(exc)[:200]}")
        return 1
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 11 — ONE-PLAYER CLAIM TEST
# ═══════════════════════════════════════════════════════════════════════════════

def run_claim_test_one_player():
    """Run the normal claim flow for only the first CSV player, then stop.

    This intentionally writes real per-reward claim history so GitHub Actions can
    commit it back; otherwise a successful test claim could be repeated later.
    It does not run the all-player loop, streak update, or aggregate email.
    """
    log("=" * 68)
    log("CS HUB ONE-PLAYER CLAIM TEST — REAL CLAIMS, NO FULL ROSTER RUN")
    log("Only the first configured player ID will be processed.")
    log("Claim history will be updated for rewards actually processed.")
    log("Promotional cards are discovered live; only visible, recognized cards can be claimed.")
    log("=" * 68)

    try:
        with open(PLAYER_ID_FILE, "r", newline="", encoding="utf-8-sig") as handle:
            players = []
            for row in csv.DictReader(handle):
                pid = (row.get("player_id") or "").strip()
                if pid:
                    loyalty = (row.get("has_loyalty") or "").strip().lower() in ("true", "yes", "1")
                    players.append((pid, loyalty))
                    break
    except Exception as exc:
        log(f"❌ CLAIM_TEST_RESULT=ERROR — cannot read {PLAYER_ID_FILE}: {type(exc).__name__}")
        return 1

    if not players:
        log(f"❌ CLAIM_TEST_RESULT=ERROR — no player_id rows found in {PLAYER_ID_FILE}")
        return 1

    pid, has_loyalty = players[0]
    meta = load_bot_meta()
    is_new = is_new_id(pid, meta)
    start = get_ist_time()
    try:
        result = process_player(pid, has_loyalty, is_new, "ONE-PLAYER CLAIM TEST")
    except Exception as exc:
        log(f"❌ CLAIM_TEST_RESULT=ERROR — {type(exc).__name__}: {str(exc)[:180]}")
        return 1

    event_reward_claims = event_claim_count(result.get("event_rewards"))
    total_claimed = _player_claim_total(result)
    test_status = str(result.get("status", "Unknown"))
    end = get_ist_time()

    # Move this run's screenshots into a downloadable artifact and rename them
    # so the raw player ID is not exposed in artifact filenames. The Store shot
    # captures the live state of any promotional cards found during the test.
    os.makedirs(CLAIM_TEST_ARTIFACTS_DIR, exist_ok=True)
    screenshot_map = {
        f"daily_{pid}.png": "daily.png",
        f"store_{pid}.png": "store.png",
        f"loyalty_{pid}.png": "loyalty.png",
    }
    for source_name, target_name in screenshot_map.items():
        if os.path.isfile(source_name):
            target_path = os.path.join(CLAIM_TEST_ARTIFACTS_DIR, target_name)
            try:
                if os.path.exists(target_path):
                    os.remove(target_path)
                os.replace(source_name, target_path)
                log(f"🧪 Saved claim-test screenshot: {target_path}")
            except Exception as exc:
                log(f"⚠️ Could not move {source_name} into test artifacts: {type(exc).__name__}")

    summary = {
        "version": VERSION,
        "run_type": "one_player_claim_test",
        "started_at_ist": start.isoformat(),
        "finished_at_ist": end.isoformat(),
        "player_id_last4": pid[-4:],
        "has_loyalty": has_loyalty,
        "status": test_status,
        "daily_claimed": int(result.get("daily", 0) or 0),
        "store_daily_rewards_claimed": int(result.get("store", 0) or 0),
        "promotional_claims_this_run": int(result.get("store_temp", 0) or 0),
        "promotions_observed": result.get("promotions_observed", []),
        "promotional_claimable_cards_seen": int(result.get("store_temp_possible", 0) or 0),
        "progression_claimed": int(result.get("progression", 0) or 0),
        "loyalty_claimed": int(result.get("loyalty", 0) or 0),
        "event_reward_claims": event_reward_claims,
        "event_rewards": result.get("event_rewards", []),
        "total_claimed": total_claimed,
        "duration_seconds": int(result.get("duration_s", 0) or (end - start).total_seconds()),
        "daily_skipped": bool(result.get("daily_skipped", False)),
        "store_skipped": result.get("store_skipped", [False, False, False]),
        "store_temp_skipped": bool(result.get("store_temp_skipped", True)),
        "loyalty_skipped": bool(result.get("loyalty_skipped", False)),
        "legacy_reward_4_history_preserved": True,
        "note": "Live promotional cards are discovered independently of the three standard daily Store cards. The existing click helper is reused, and a promotion is counted as claimed only after the portal confirms it.",
    }
    summary_path = os.path.join(CLAIM_TEST_ARTIFACTS_DIR, "claim_test_summary.json")
    with open(summary_path, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, ensure_ascii=False)

    log("=" * 68)
    log(f"CLAIM_TEST_STATUS={test_status}")
    log(f"CLAIM_TEST_COUNTS=daily:{summary['daily_claimed']},store_daily:{summary['store_daily_rewards_claimed']},promotions:{summary['promotional_claims_this_run']},progression:{summary['progression_claimed']},loyalty:{summary['loyalty_claimed']},event_rewards:{summary['event_reward_claims']}")
    log(f"CLAIM_TEST_TOTAL={total_claimed}")
    log("CLAIM_TEST_RESULT=SUCCESS" if total_claimed > 0 and test_status not in ("Login Failed", "Error", "Failed") else "CLAIM_TEST_RESULT=NO_CLAIMS_OBSERVED")
    log("Claim history will be committed by the workflow if it changed.")
    log("No other player IDs were processed; no aggregate email was sent.")
    log("=" * 68)

    if test_status in ("Login Failed", "Error", "Failed"):
        return 1
    # No claimable rewards may be available for this player now. That is not a
    # script exception, but the summary marker makes it clear claims were untested.
    return 0





def _full_claim_efficiency(results):
    """Efficiency for full-claim accounts only; N/A when a run is promotional-only."""
    full_results = [r for r in results if r.get("processing_mode", "full_claim") == "full_claim"]
    if not full_results:
        return None
    possible = sum(max(0, int(r.get("possible", 0) or 0)) for r in full_results)
    unresolved = any(_result_needs_recovery(r) for r in full_results)
    confirmed = sum(
        int(r.get("daily", 0) or 0) + int(r.get("store", 0) or 0)
        + int(r.get("store_temp", 0) or 0) + int(r.get("loyalty", 0) or 0)
        for r in full_results
    )
    if possible == 0:
        return 0.0 if unresolved else 100.0
    return round(confirmed / possible * 100, 1)


def write_run_summary(results, run_label, started_at, finished_at, email_sent, configured_player_count=None, promotional_followups=None):
    """Write a privacy-minimized per-player report, including only live promotions observed this run."""
    os.makedirs(RUN_ARTIFACTS_DIR, exist_ok=True)
    daily = sum(int(r.get("daily", 0) or 0) for r in results)
    store_daily = sum(int(r.get("store", 0) or 0) for r in results)
    promotion_claims = sum(int(r.get("store_temp", 0) or 0) for r in results)
    event_reward_claims = sum(event_claim_count(r.get("event_rewards")) for r in results)
    promotions_seen = {}
    for r in results:
        for promo in (r.get("promotions_observed", []) or []):
            key = str(promo.get("key") or promo.get("title") or "").strip()
            if not key:
                continue
            item = promotions_seen.setdefault(key, {"title": promo.get("title", "Promotion"), "observed_player_count": 0, "claimed_this_run": 0, "statuses": {}})
            item["observed_player_count"] += 1
            if promo.get("status") == "claimed_this_run":
                item["claimed_this_run"] += 1
            status = str(promo.get("status") or "unknown")
            item["statuses"][status] = int(item["statuses"].get(status, 0)) + 1
    progression = sum(int(r.get("progression", 0) or 0) for r in results)
    loyalty = sum(int(r.get("loyalty", 0) or 0) for r in results)
    retry_ids = []
    hard_failed_ids = []
    for r in results:
        status = r.get("status")
        if status in ("Login Failed", "Error", "Failed") and r.get("pid"):
            hard_failed_ids.append(str(r["pid"]))
        if r.get("pid") and _full_claim_recovery_candidate(r):
            retry_ids.append(str(r["pid"]))

    counters = _summary_counts(results)
    recovery_records = [
        r.get("immediate_recovery") for r in results
        if isinstance(r.get("immediate_recovery"), dict)
    ]
    immediate_recovery_summary = {
        "accounts_attempted": sum(1 for item in recovery_records if item.get("attempted")),
        "accounts_resolved": sum(1 for item in recovery_records if item.get("attempted") and item.get("status") == "resolved"),
        "accounts_unresolved": sum(1 for item in recovery_records if item.get("attempted") and item.get("status") != "resolved"),
        "recovered_claims": sum(int(item.get("recovered_claims", 0) or 0) for item in recovery_records),
    }
    summary = {
        "version": VERSION,
        "run_label": run_label,
        "started_at_ist": started_at.isoformat(),
        "finished_at_ist": finished_at.isoformat(),
        "configured_player_count": len(results) if configured_player_count is None else int(configured_player_count),
        **counters,
        "success_count": sum(1 for r in results if r.get("status") == "Success"),
        "partial_count": sum(1 for r in results if r.get("status") == "Partial"),
        "no_rewards_count": sum(1 for r in results if r.get("status") == "No Rewards"),
        "login_failed_count": sum(1 for r in results if r.get("status") == "Login Failed"),
        "error_count": sum(1 for r in results if r.get("status") in ("Error", "Failed")),
        "hard_failed_count": len(set(hard_failed_ids)),
        "retry_candidate_count": len(set(retry_ids)),
        "totals": {
            "daily_claimed_this_run": daily,
            "standard_store_claimed_this_run": store_daily,
            "promotional_claims_this_run": promotion_claims,
            "event_reward_claims_this_run": event_reward_claims,
            "progression_claimed_this_run": progression,
            "loyalty_claimed_this_run": loyalty,
            "all_claims_this_run": daily + store_daily + promotion_claims + progression + loyalty + event_reward_claims,
        },
        "promotions_observed_this_run": list(promotions_seen.values()),
        "promotional_followups": {
            "open_count": int((promotional_followups or {}).get("open_count", 0) or 0),
            "still_blocked_count": int((promotional_followups or {}).get("still_blocked_count", 0) or 0),
            "pending_recheck_count": int((promotional_followups or {}).get("pending_recheck_count", 0) or 0),
            "resolved_this_run_count": int((promotional_followups or {}).get("resolved_this_run_count", 0) or 0),
            "not_retryable_this_run_count": int((promotional_followups or {}).get("not_retryable_this_run_count", 0) or 0),
            "legacy_seeded_count": int((promotional_followups or {}).get("legacy_seeded_count", 0) or 0),
            "open_items": [{
                "player_id_last4": str(item.get("player_key", ""))[-4:],
                "title": item.get("title"), "status": item.get("status"),
                "reason": item.get("reason"), "attempts": int(item.get("attempts", 0) or 0),
                "first_seen_at": item.get("first_seen_at"), "last_checked_at": item.get("last_checked_at"),
            } for item in (promotional_followups or {}).get("open_items", [])],
        },
        "full_claim_efficiency": _full_claim_efficiency(results),
        "immediate_recovery": immediate_recovery_summary,
        "preexisting_portal_claims": [
            dict(event)
            for result in results
            for event in (result.get("preexisting_portal_claims") or [])
            if isinstance(event, dict)
        ],
        "email_sent": bool(email_sent),
        "player_results": [
            {
                "player_id_last4": str(r.get("pid", ""))[-4:],
                "display_name": r.get("display_name"),
                "status": r.get("status"),
                "processing_mode": r.get("processing_mode", "full_claim"),
                "retry_required": bool(_result_needs_recovery(r)),
                "full_claim_recovery_candidate": bool(_full_claim_recovery_candidate(r)),
                "fail_reason": r.get("fail_reason"),
                "daily_claimed_this_run": int(r.get("daily", 0) or 0),
                "daily_portal_status": r.get("daily_portal_status", "unknown"),
                "daily_unconfirmed": bool(r.get("daily_unconfirmed", False)),
                "standard_store_claimed_this_run": int(r.get("store", 0) or 0),
                "standard_store_unconfirmed_indices": list(r.get("store_unconfirmed_indices", []) or []),
                "standard_store_claimed_indices": list(r.get("store_claimed_indices", []) or []),
                "progression_unconfirmed": bool(r.get("progression_unconfirmed", False)),
                "progression_deferred": bool(r.get("progression_deferred", False)),
                "progression_deferred_reason": r.get("progression_deferred_reason"),
                "progression_claim_attempted": bool(r.get("progression_claim_attempted", False)),
                "progression_checked": bool(r.get("progression_checked", False)),
                "immediate_recovery": r.get("immediate_recovery"),
                "grenade_balance": r.get("grenade_balance"),
                "standard_store_portal_status": r.get("store_portal_status", ["unknown", "unknown", "unknown"]),
                "promotions_observed": r.get("promotions_observed", []),
                "promotion_scan_completed": bool(r.get("promotion_scan_completed", False)),
                "event_rewards": r.get("event_rewards", []),
                "event_reward_claims_this_run": event_claim_count(r.get("event_rewards")),
                "progression_claimed_this_run": int(r.get("progression", 0) or 0),
                "loyalty_claimed_this_run": int(r.get("loyalty", 0) or 0),
                "duration_seconds": int(r.get("duration_s", 0) or 0),
            } for r in results
        ],
    }
    summary["reconciliation"] = build_run_reconciliation(
        results,
        run_label=run_label,
        configured_player_count=summary["configured_player_count"],
        totals=summary["totals"],
        counters=counters,
    )
    if summary["reconciliation"]["status"] != "matched":
        log("⚠️ RUN_RECONCILIATION=ATTENTION_REQUIRED: "
            + ", ".join(summary["reconciliation"]["warnings"]))
    else:
        log("✅ RUN_RECONCILIATION=MATCHED")

    path = os.path.join(RUN_ARTIFACTS_DIR, "run_summary.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, ensure_ascii=False)

    # The email stays script-free for client compatibility. This separate browser
    # artifact provides the rich/plain clipboard action for sharing the same run.
    copyable_path = os.path.join(RUN_ARTIFACTS_DIR, "report.html")
    try:
        with open(copyable_path, "w", encoding="utf-8") as handle:
            handle.write(build_copyable_report(summary))
        log(f"📋 Copyable report saved: {copyable_path}")
    except Exception as exc:
        # Report export is supplementary; never invalidate claim results or email.
        log(f"⚠️ Could not create copyable report: {type(exc).__name__}: {str(exc)[:160]}")

    log(f"📦 Run summary saved: {path}")
    return path


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 12 — MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    # These paths are read-only with respect to rewards and are deliberately exempt.
    if "--diagnose-login" in sys.argv[1:]:
        return run_login_diagnostic()
    if "--test-login-only" in sys.argv[1:]:
        return run_login_only_test()

    # Central fail-closed guard protects every claim-capable invocation, including
    # workflow_dispatch full runs, one-player real-claim tests, scheduled jobs, and recovery.
    if not enforce_claim_reset_guard():
        return 2

    if "--test-claims-one-player" in sys.argv[1:]:
        return run_claim_test_one_player()
    job_start = get_ist_time()
    log("=" * 60)
    log(f"CS HUB AUTO-CLAIMER {VERSION}")
    log("=" * 60)

    run_label, run_index = determine_run_context()
    if os.getenv("GITHUB_EVENT_NAME") == "schedule" and run_label == "Unknown Scheduled Run":
        log(f"⛔ UNKNOWN_SCHEDULE_BLOCKED: unrecognized SCHEDULE_CRON={os.getenv('SCHEDULE_CRON', '')!r}; refusing claim execution.")
        return 2
    ist_now = get_ist_time()
    log(f"📋 Run Context: {run_label}  |  {ist_now.strftime('%d-%b %H:%M IST')}")

    meta = load_bot_meta()
    if not RECOVERY_ONLY_FAILED and should_skip_duplicate_scheduled_retry(meta):
        log("⏭️ SCHEDULE_RETRY_SKIPPED=recent_run_completed")
        log("An earlier scheduled claim cycle completed recently; suppressing a duplicate fallback.")
        return 0

    players = []
    try:
        with open(PLAYER_ID_FILE, 'r') as f:
            for row in csv.DictReader(f):
                pid = row.get("player_id", "").strip()
                if pid:
                    hl = row.get("has_loyalty", "").strip().lower() in ("true", "yes", "1")
                    players.append((pid, hl))
    except Exception as e:
        log(f"❌ Failed to read {PLAYER_ID_FILE}: {type(e).__name__}: {str(e)[:180]}")
        return 1

    if not players:
        log(f"❌ {PLAYER_ID_FILE} contains no non-empty player_id entries; refusing a misleading empty run.")
        return 1
    player_ids = [pid for pid, _ in players]
    configured_player_count = len(player_ids)
    if len(player_ids) != len(set(player_ids)):
        log(f"❌ {PLAYER_ID_FILE} contains duplicate player IDs; refusing to process duplicate accounts.")
        return 1

    manual_daily_targets = {}
    if MANUAL_DAILY_RECOVERY:
        if not DAILY_ONLY_RECOVERY or RECOVERY_ONLY_FAILED:
            log("❌ Manual Daily recovery requires the dedicated Daily-only mode and cannot be combined with general recovery.")
            return 2
        if not MANUAL_DAILY_RECOVERY_PLAYER_ID:
            log("❌ Manual Daily recovery requires one exact Player ID.")
            return 2
        manual_daily_targets = manual_daily_recovery_targets(
            meta,
            player_ids,
            reset_date=_current_reset_date(),
            player_id=MANUAL_DAILY_RECOVERY_PLAYER_ID,
        )
        if len(manual_daily_targets) != 1 or MANUAL_DAILY_RECOVERY_PLAYER_ID not in manual_daily_targets:
            log("❌ Manual Daily recovery rejected: the requested Player ID is not a current-reset, pending, Daily-only recovery candidate.")
            return 2
        selected_pid = MANUAL_DAILY_RECOVERY_PLAYER_ID
        players = [(pid, loyalty) for pid, loyalty in players if pid == selected_pid]
        if len(players) != 1 or players[0][0] != selected_pid:
            log("❌ Manual Daily recovery failed exact one-account roster selection; no browser started.")
            return 2
        log(f"🎯 Manual Daily recovery validated for exactly one account: {selected_pid}.")
    elif MANUAL_DAILY_RECOVERY_PLAYER_ID:
        log("❌ A manual Daily Player ID was supplied without enabling manual Daily recovery; refusing execution.")
        return 2

    recovery_targets_by_player = {}
    if RECOVERY_ONLY_FAILED:
        prior = meta.get("last_run") or {}
        allowed_source_labels = {
            "Primary Run", "Manual Run",
            "Promotional Check #1", "Promotional Check #2", "Promotional Check #3", "Promotional Check #4",
            "Backup Run #1", "Backup Run #2", "Backup Run #3", "Backup Run #4",
        }
        if prior.get("run_label") not in allowed_source_labels:
            log("⏭️ Recovery skipped: latest saved run is not an eligible full-roster run.")
            return 0
        try:
            prior_end = datetime.fromisoformat(str(prior.get("timestamp")))
            if prior_end.tzinfo is not None:
                from datetime import timezone
                prior_end = prior_end.astimezone(timezone(timedelta(hours=5, minutes=30))).replace(tzinfo=None)
            prior_duration = max(0, int(prior.get("duration_seconds", 0) or 0))
            prior_start = prior_end - timedelta(seconds=prior_duration)
            now_ist = get_ist_time()
            reset = now_ist.replace(hour=5, minute=30, second=0, microsecond=0)
            if now_ist < reset:
                reset -= timedelta(days=1)
            age = (now_ist - prior_end).total_seconds()
            if prior_start < reset or age < 0 or age > 4 * 3600:
                log("⏭️ Recovery skipped: source cycle is stale or started before today's 05:30 IST reset.")
                return 0
        except Exception as exc:
            log(f"⏭️ Recovery skipped: could not validate the source run ({type(exc).__name__}).")
            return 0

        retry_ids = full_claim_retry_ids(prior)
        if not retry_ids:
            log("⏭️ Recovery skipped: full-claim scope, unique candidate count, or run-mode metadata failed validation.")
            return 0
        roster_ids = set(player_ids)
        unknown = [pid for pid in retry_ids if pid not in roster_ids]
        if unknown:
            log(f"❌ Recovery aborted: {len(unknown)} retry IDs are not present in players.csv.")
            return 1
        retry_set = set(retry_ids)
        players = [(pid, loyalty) for pid, loyalty in players if pid in retry_set]
        raw_targets = prior.get("retry_targets_by_player")
        recovery_targets_by_player = raw_targets if isinstance(raw_targets, dict) else {}
        log(f"🔁 Recovery mode: checking {len(players)} unresolved ID(s) using saved reward targets only.")

    log(f"👥 Loaded {len(players)} players "
        f"({sum(1 for _, h in players if h)} with loyalty)")

    results = []
    is_promotional_backup = run_label in {
        "Promotional Check #1", "Promotional Check #2", "Promotional Check #3", "Promotional Check #4"
    } and not RECOVERY_ONLY_FAILED
    daily_pending_targets = {}
    daily_recovery_eligible_ids = set()
    if DAILY_ONLY_RECOVERY and (is_promotional_backup or MANUAL_DAILY_RECOVERY):
        if MANUAL_DAILY_RECOVERY:
            # Re-read persisted state after the workflow concurrency lock was acquired.
            # Only this exact Player ID can enter the browser loop.
            daily_pending_targets = manual_daily_recovery_targets(
                meta,
                player_ids,
                reset_date=_current_reset_date(),
                player_id=MANUAL_DAILY_RECOVERY_PLAYER_ID,
            )
            if len(daily_pending_targets) != 1 or MANUAL_DAILY_RECOVERY_PLAYER_ID not in daily_pending_targets:
                log("⛔ Manual Daily recovery target changed or is no longer pending; no browser started.")
                return 2
            daily_recovery_eligible_ids = set(daily_pending_targets)
            log(f"🎯 Manual Daily-only recovery: exactly one validated target ({MANUAL_DAILY_RECOVERY_PLAYER_ID}); scheduled-slot counters will not be changed.")
        else:
            daily_pending_targets = daily_only_pending_targets(
                meta, player_ids, reset_date=_current_reset_date()
            )
            daily_recovery_eligible_ids = set(eligible_daily_recovery_ids(
                meta, player_ids, reset_date=_current_reset_date(), slot_label=run_label
            ))
            log(
                f"🎯 Daily-only backup recovery: {len(daily_pending_targets)} pending Daily account(s); "
                f"{len(daily_recovery_eligible_ids)} eligible for this slot ({run_label})."
            )

    for pid, has_loyalty in players:
        new_id = is_new_id(pid, meta)
        if RECOVERY_ONLY_FAILED:
            # Post-run recovery must replay the exact failed targets saved by the
            # source run. Missing metadata is deferred; it never falls back to
            # process_player() or a broad full-account reward sweep.
            targets = recovery_targets_by_player.get(pid)
            r, coverage_rechecked = recover_player_from_saved_targets(
                pid, has_loyalty, new_id, run_label, targets
            )
            if coverage_rechecked:
                _record_full_claim_coverage(meta, pid, r, run_label)
        elif DAILY_ONLY_RECOVERY and pid in daily_pending_targets:
            # Do not send this account through process_player(): the scheduled
            # safety-net is restricted to the exact saved Daily target only.
            targets = daily_pending_targets[pid]
            slot_available = (
                MANUAL_DAILY_RECOVERY
                or (
                    pid in daily_recovery_eligible_ids
                    and _mark_daily_recovery_slot_attempt(meta, pid, run_label)
                )
            )
            if pid in daily_recovery_eligible_ids and slot_available:
                daily_target_only = {
                    "login_retry": False,
                    "processing_error": False,
                    "daily_unconfirmed": True,
                    "store_unconfirmed_indices": [],
                    "progression_unconfirmed": False,
                    "progression_card_signature": None,
                    "event_retry_required": False,
                    "event_thresholds_by_program": {},
                    "event_rewards": [],
                }
                recovery_origin = "manual request" if MANUAL_DAILY_RECOVERY else run_label
                log(f"🎯 {pid}: executing bounded Daily-only recovery for {recovery_origin}; other reward categories are excluded.")
                r, coverage_rechecked = recover_player_from_saved_targets(
                    pid, has_loyalty, new_id, run_label, daily_target_only
                )
                # The saved-target result constructor represents a general
                # standard-reward audit. This branch touched Daily only, so it
                # must never mark Gold/Cash/Luckyloon as audited for this reset.
                r["standard_rewards_checked"] = False
                r["daily_only_recovery"] = True
                r["daily_recovery_attempted"] = True
                if coverage_rechecked:
                    _record_full_claim_coverage(meta, pid, r, run_label)
            else:
                defer_reason = (
                    "Manual Daily recovery target was no longer eligible at execution; no reward click attempted."
                    if MANUAL_DAILY_RECOVERY
                    else "Daily-only recovery was not attempted: this backup slot was already used or the four-slot per-reset limit was reached."
                )
                r = _deferred_daily_recovery_result(
                    pid,
                    targets,
                    defer_reason,
                )
                r["daily_recovery_attempted"] = False
                log(f"⏭️ {pid}: Daily-only recovery deferred; slot already used or per-reset retry limit reached.")
        elif is_promotional_backup and has_complete_full_claim_coverage(pid, meta):
            if needs_standard_backup_audit(pid, meta):
                r = process_player_backup_reconciliation(pid, new_id, run_label)
                if _full_claim_recovery_candidate(r):
                    r = immediate_recover_player_claims(pid, has_loyalty, new_id, run_label, r)
                if _full_claim_recovery_candidate(r) or r.get("standard_rewards_checked"):
                    _record_full_claim_coverage(meta, pid, r, run_label)
            else:
                # Promotional-only rechecks never overwrite full-claim coverage.
                r = process_player_promotions_only(pid, new_id, run_label)
        else:
            if is_promotional_backup:
                if new_id:
                    log(f"🆕 {pid}: new ID has no complete full-claim coverage; using full claim path at this backup.")
                else:
                    log(f"🔁 {pid}: no complete full-claim coverage for today's reset; retrying full claim path.")
            r = process_player(pid, has_loyalty, new_id, run_label)
            if _full_claim_recovery_candidate(r):
                r = immediate_recover_player_claims(pid, has_loyalty, new_id, run_label, r)
            _record_full_claim_coverage(meta, pid, r, run_label)
        mark_id_seen(pid, meta)
        results.append(r)
        time.sleep(0.5)

    # Metrics
    job_end = get_ist_time()
    legacy_followup_seed = seed_legacy_popup_followups(meta, results, now=job_end)
    if legacy_followup_seed.get("unmatched_suffixes"):
        log("⚠️ Legacy promotion follow-up bootstrap deferred; suffixes not matched uniquely: " + ", ".join(legacy_followup_seed["unmatched_suffixes"]))
    followup_results = filter_followup_results(results, meta, _promotion_detail_worthy)
    followup_summary = update_promotion_followups(meta, followup_results, run_label, now=job_end)
    followup_summary["legacy_seeded_count"] = legacy_followup_seed["seeded_count"]
    for player_result in results:
        player_id = str(player_result.get("pid") or "").strip()
        player_name = str(player_result.get("display_name") or "").strip()
        if player_id and player_name:
            record_player_display_name(
                meta,
                player_id,
                player_name,
                observed_at=player_result.get("display_name_captured_at_ist") or job_end,
            )
    dur_s   = int((job_end - job_start).total_seconds())
    td       = sum(r["daily"] for r in results)
    ts_daily = sum(r["store"] for r in results)
    ts_temp  = sum(r.get("store_temp", 0) for r in results)
    ts       = ts_daily + ts_temp
    tp       = sum(r["progression"] for r in results)
    tl       = sum(r.get("loyalty", 0) for r in results)
    event_reward_claims = sum(event_claim_count(r.get("event_rewards")) for r in results)
    tall     = td + ts + tp + tl + event_reward_claims
    full_claim_results = [r for r in results if r.get("processing_mode", "full_claim") == "full_claim"]
    tp_all = sum(r.get("possible", 0) for r in full_claim_results)
    has_unresolved_result = any(_result_needs_recovery(r) for r in full_claim_results)
    full_claim_confirmed = sum(
        int(r.get("daily", 0) or 0) + int(r.get("store", 0) or 0)
        + int(r.get("store_temp", 0) or 0) + int(r.get("loyalty", 0) or 0)
        for r in full_claim_results
    )
    eff = None if not full_claim_results else (
        0.0 if tp_all == 0 and has_unresolved_result
        else (100.0 if tp_all == 0 else round(full_claim_confirmed / tp_all * 100, 1))
    )
    eff_log = f"{eff:.1f}%" if eff is not None else "N/A (promotional-only)"

    timed   = [(r["pid"], r.get("duration_s", 0)) for r in results if not r.get("skipped_all")]
    avg_t   = round(sum(t for _, t in timed) / len(timed), 1) if timed else 0
    slowest = max(timed, key=lambda x: x[1]) if timed else None

    log(f"\n{'='*60}")
    log(f"Run complete: {tall} claimed | {eff_log} efficiency | {dur_s}s total")
    run_counts = _summary_counts(results)
    log(f"  Daily:{td}  Store Daily:{ts_daily}  Promotions:{ts_temp}  Prog:{tp}  Loyalty:{tl}  Event Rewards:{event_reward_claims}")
    log(f"  Accounts:{len(results)} ({run_counts['full_claim_accounts']} full / {run_counts.get('backup_reconciliation_accounts', 0)} backup-reconciliation / {run_counts['promotional_only_accounts']} promo-only) | Standard cooldowns:{run_counts['standard_rewards_on_cooldown']} | Distinct promo types:{run_counts['distinct_promotional_offer_types']} | Promo claims:{run_counts['promotional_claims_made']} | Partial:{run_counts['partial_results']} | Failures:{run_counts['actual_failures']} | Unconfirmed claims:{run_counts['unconfirmed_claims']} | Promo scan failures:{run_counts['promotion_detection_failures']}")
    log(f"{'='*60}")

    # Subset recovery attempts must not recalculate the roster-wide daily streak.
    if not RECOVERY_ONLY_FAILED and not DAILY_ONLY_RECOVERY:
        all_ok = compute_all_ok_today(players)
        if all_ok:
            log("✅ All daily+store claimed today — streak eligible")
        update_streak_day_level(meta, all_ok)
    else:
        log("ℹ️ Recovery subset: daily streak left unchanged.")

    prev_run = meta.get("last_run")
    hard_failed_ids = [r.get("pid") for r in results
                       if r.get("status") in ("Login Failed", "Error", "Failed") and r.get("pid")]
    retryable_ids = [
        result["pid"] for result in results
        if result.get("pid") and _full_claim_recovery_candidate(result)
    ]
    retry_targets_by_player = {
        result["pid"]: _recovery_targets_from_result(result)
        for result in results
        if result.get("pid") and _full_claim_recovery_candidate(result)
    }
    counts = _summary_counts(results)

    meta["last_run"] = {
        "timestamp":           job_end.isoformat(),
        "run_label":           run_label,
        "daily_only_recovery": DAILY_ONLY_RECOVERY,
        "daily_recovery_mode": (
            "manual" if MANUAL_DAILY_RECOVERY
            else ("scheduled_backup" if DAILY_ONLY_RECOVERY else None)
        ),
        "daily_recovery_slot": run_label if DAILY_ONLY_RECOVERY and not MANUAL_DAILY_RECOVERY else None,
        "manual_daily_recovery_player_id": MANUAL_DAILY_RECOVERY_PLAYER_ID if MANUAL_DAILY_RECOVERY else None,
        "failed_player_ids":   hard_failed_ids,
        "failed_player_count": len(hard_failed_ids),
        "retry_candidate_scope": "full_claim_only",
        "retry_player_ids":    list(dict.fromkeys(retryable_ids)),
        "retry_player_count":  len(set(retryable_ids)),
        "retry_targets_by_player": retry_targets_by_player,
        "total_claimed":       tall,
        "efficiency":          eff,
        "efficiency_scope":     "full_claim_accounts_only" if eff is not None else "not_applicable_promotional_only",
        "duration_seconds":    dur_s,
        "per_type":            {"daily": td, "store": ts, "store_daily": ts_daily,
                                 "store_temp": ts_temp, "promotions": ts_temp,
                                 "progression": tp, "loyalty": tl,
                                 "event_rewards": event_reward_claims},
        "slowest_player":      slowest[0] if slowest else None,
        "avg_time_per_player": avg_t,
        "accounts_processed": counts["accounts_processed"],
        "claims_made": counts["claims_made"],
        "unavailable_rewards": counts["unavailable_rewards"],
        "standard_rewards_on_cooldown": counts["standard_rewards_on_cooldown"],
        "promotional_unavailable_observations": counts["promotional_unavailable_observations"],
        "distinct_promotional_offer_types": counts["distinct_promotional_offer_types"],
        "promotional_claims_made": counts["promotional_claims_made"],
        "actionable_promotion_results": counts["actionable_promotion_results"],
        "partial_results": counts["partial_results"],
        "actual_failures": counts["actual_failures"],
        "unconfirmed_claims": counts["unconfirmed_claims"],
        "immediate_recovery_accounts_attempted": sum(1 for result in results if (result.get("immediate_recovery") or {}).get("attempted")),
        "immediate_recovery_accounts_resolved": sum(1 for result in results if (result.get("immediate_recovery") or {}).get("attempted") and (result.get("immediate_recovery") or {}).get("status") == "resolved"),
        "immediate_recovery_accounts_unresolved": sum(1 for result in results if (result.get("immediate_recovery") or {}).get("attempted") and (result.get("immediate_recovery") or {}).get("status") != "resolved"),
        "promotion_detection_failures": counts["promotion_detection_failures"],
        "promotion_followups_open": followup_summary["open_count"],
        "promotion_followups_still_blocked": followup_summary["still_blocked_count"],
        "promotion_followups_pending_recheck": followup_summary["pending_recheck_count"],
        "promotion_followups_legacy_seeded": legacy_followup_seed["seeded_count"],
        "promotional_only_accounts": counts["promotional_only_accounts"],
        "backup_reconciliation_accounts": counts.get("backup_reconciliation_accounts", 0),
        "full_claim_accounts": counts["full_claim_accounts"],
        "configured_player_count": configured_player_count,
    }
    cycle_claim_totals = {
        "daily": td,
        "store": ts_daily,
        "promotions": counts.get("promotional_claims_made", 0),
        "progression": tp,
        "loyalty": tl,
        "events": event_reward_claims,
    }
    cycle_summary = update_daily_cycle_summary(
        meta,
        reset_date=_current_reset_date(),
        run_label=run_label,
        configured_player_ids=player_ids,
        claim_totals=cycle_claim_totals,
        processed_player_ids=[str(result.get("pid") or "") for result in results],
        run_key=f"{os.getenv('GITHUB_RUN_ID', 'local')}:{os.getenv('GITHUB_RUN_ATTEMPT', '1')}",
        run_timestamp=job_end.isoformat(),
        is_full_roster_run=(
            not RECOVERY_ONLY_FAILED
            and len(results) == configured_player_count
            and counts.get("full_claim_accounts", 0) == configured_player_count
        ),
    )
    meta_for_email = dict(meta)
    meta_for_email["last_run"] = prev_run   # email delta uses previous run
    if cycle_summary is not None:
        meta_for_email["daily_cycle_email_summary"] = cycle_summary

    save_bot_meta(meta)

    html_body = build_email(
        results, run_label, run_index, job_start, meta_for_email,
        followup_summary=followup_summary,
        configured_player_count=configured_player_count,
    )

    n_players = len(results)
    run_subject_labels = {
        "Primary Run": "Primary",
        "Manual Run": "Manual",
        "Recovery Run": "Recovery",
        "Promotional Check #1": "Store Promo Check #1",
        "Promotional Check #2": "Store Promo Check #2",
        "Promotional Check #3": "Store Promo Check #3",
        "Promotional Check #4": "Store Promo Check #4",
    }
    subject_date = job_start.strftime("%d %b")
    if DAILY_ONLY_RECOVERY:
        run_subject_labels[run_label] = f"Daily Recovery + {run_subject_labels.get(run_label, run_label)}"
    if run_label == "Recovery Run" and cycle_summary is not None:
        if cycle_summary.get("cycle_complete"):
            subject = (
                f"✅ CS Hub | {subject_date} | Recovery Complete | "
                f"{cycle_summary['accounts_complete']}/{configured_player_count} Accounts"
            )
        else:
            subject = (
                f"⚠️ CS Hub | {subject_date} | Recovery Update | "
                f"{cycle_summary['accounts_complete']}/{configured_player_count} Accounts Complete"
            )
    else:
        subject = (
            f"🎮 CS Hub | {subject_date} | {run_subject_labels.get(run_label, run_label)} | "
            f"{len(results)}/{configured_player_count} Accounts"
        )

    log(f"📧 Sending email: {subject}")
    email_sent = send_email(html_body, subject)
    meta["last_run"]["email_sent"] = bool(email_sent)
    meta["last_run"]["player_count"] = n_players
    # Keep the latest full-roster coverage separately so a subset recovery run
    # cannot erase the record that today's post-reset full cycle already ran.
    if (not RECOVERY_ONLY_FAILED
            and len(results) == configured_player_count
            and all(result.get("processing_mode", "full_claim") == "full_claim" for result in results)
            and run_label in {"Primary Run", "Manual Run", "Promotional Check #1", "Promotional Check #2", "Promotional Check #3", "Promotional Check #4"}):
        # Only genuine full-roster full-claim cycles may replace this checkpoint.
        # Mixed promotional backups and subset recovery never erase it.
        meta["last_full_run"] = dict(meta["last_run"])
    save_bot_meta(meta)
    write_run_summary(results, run_label, job_start, job_end, email_sent, configured_player_count=configured_player_count, promotional_followups=followup_summary)
    log("EMAIL_DELIVERY_RESULT=SUCCESS" if email_sent else "EMAIL_DELIVERY_RESULT=FAILED")

    if not email_sent:
        log("❌ Aggregate report could not be delivered. debug_email.html and run_summary.json are retained as workflow artifacts.")
        return 1
    return 0

    # ── Legacy email helpers follow at module scope ─────────────────────────────
# These two functions must live at MODULE level (no indent), not inside main().
# The original IndentationError was caused by _resolve_email_config being
# nested inside main() with its docstring at the same indent as the def line.


def _resolve_email_config():
    """
    Resolve email credentials from either the new SMTP_* env vars
    or the legacy EMAIL_SENDER / EMAIL_PASSWORD / EMAIL_RECEIVER names.
    Returns (server, port, sender, password, receiver).
    """
    server = os.getenv("SMTP_SERVER", "smtp.gmail.com")

    try:
        port = int(os.getenv("SMTP_PORT", "465"))
    except ValueError:
        port = 465

    sender = (
        os.getenv("EMAIL_SENDER")
        or os.getenv("SENDER_EMAIL")
        or os.getenv("SMTP_USERNAME")
        or os.getenv("SMTP_FROM")
        or ""
    ).strip()

    password = (
        os.getenv("EMAIL_PASSWORD")
        or os.getenv("GMAIL_APP_PASSWORD")
        or os.getenv("SMTP_PASSWORD")
        or ""
    ).strip()

    receiver = (
        os.getenv("EMAIL_RECEIVER")
        or os.getenv("RECIPIENT_EMAIL")
        or os.getenv("SMTP_TO")
        or ""
    ).strip()

    return server, port, sender, password, receiver


def send_email(html_body, subject):
    """Save the exact report HTML and retry SMTP delivery up to three times."""
    try:
        with open("debug_email.html", "w", encoding="utf-8") as handle:
            handle.write(html_body)
    except Exception as exc:
        log(f"⚠️ Could not save email HTML artifact: {type(exc).__name__}")

    if not (SMTP_SERVER and SMTP_USERNAME and SMTP_PASSWORD and SMTP_FROM and SMTP_TO):
        log("❌ Email configuration is incomplete; check SENDER_EMAIL, GMAIL_APP_PASSWORD, RECIPIENT_EMAIL secrets.")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = SMTP_TO
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    for attempt in range(1, 4):
        try:
            with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, timeout=30) as server:
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
                server.sendmail(SMTP_FROM, [SMTP_TO], msg.as_string())
            log(f"📧 Email sent successfully (attempt {attempt}/3)")
            return True
        except smtplib.SMTPAuthenticationError as exc:
            log(f"❌ Email authentication failed (attempt {attempt}/3): SMTP {exc.smtp_code}")
        except Exception as exc:
            log(f"⚠️ Email delivery failed (attempt {attempt}/3): {type(exc).__name__}: {str(exc)[:160]}")
        if attempt < 3:
            time.sleep(4 * attempt)

    return False


if __name__ == "__main__":
    raise SystemExit(main() or 0)
