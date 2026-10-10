# master_claimer.py — CS Rewards Bot v3.0.1
import csv
import time
import os
import json
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

# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — CONSTANTS & CONFIG
# ═══════════════════════════════════════════════════════════════════════════════

VERSION        = "v3.0.1"
PLAYER_ID_FILE = "players.csv"
HISTORY_FILE   = "claim_history.json"
BOT_META_FILE  = "bot_meta.json"
HEADLESS       = True
LOGIN_DIAGNOSTICS_DIR = "login_diagnostics"
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


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — TIME HELPERS & RUN CLASSIFICATION
# ═══════════════════════════════════════════════════════════════════════════════

def get_ist_time():
    return datetime.utcnow() + timedelta(hours=5, minutes=30)


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


# 8 run slots — Primary at 05:35 IST (00:05 UTC), then every 3h
_RUN_SLOTS = [(5,35),(8,35),(11,35),(14,35),(17,35),(20,35),(23,35),(2,35)]


def determine_run_context():
    event = os.getenv("GITHUB_EVENT_NAME", "schedule")
    if event == "workflow_dispatch":
        return "Manual Run", -1
    ist = get_ist_time()
    for i, (slot_h, slot_m) in enumerate(_RUN_SLOTS):
        slot_dt = ist.replace(hour=slot_h, minute=slot_m, second=0, microsecond=0)
        if abs((ist - slot_dt).total_seconds()) <= 600:
            label = "Primary Run" if i == 0 else f"Backup Run #{i}"
            return label, i
    return "Backup Run", 1


def next_scheduled_runs_ist():
    return [
        ("Primary Run" if i == 0 else f"Backup #{i}", f"{h:02d}:{m:02d} IST")
        for i, (h, m) in enumerate(_RUN_SLOTS)
    ]


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
        meta["new_ids_seen"].append(pid)


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
        lc = ph.get("daily", {}).get("last_claim")
        if not lc or datetime.fromisoformat(lc) < lr:
            return False

        # Store — all 3 cards claimed since last reset
        for i in range(1, 4):
            lc = ph.get("store", {}).get(f"reward_{i}", {}).get("last_claim")
            if not lc or datetime.fromisoformat(lc) < lr:
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


def init_player_history(pid):
    h = load_claim_history()
    if pid not in h:
        h[pid] = {
            "daily":       {"last_claim": None, "next_available": None, "status": "unknown"},
            "store": {
                f"reward_{i}": {"last_claim": None, "next_available": None, "status": "unknown"}
                for i in range(1, 4)
            },
            "progression": {"last_claim": None, "last_count": 0, "last_visit": None},
            "loyalty":     {"last_claim": None, "next_available": None, "status": "unknown"}
        }
        save_claim_history(h)
    else:
        changed = False
        if "loyalty" not in h[pid]:
            h[pid]["loyalty"] = {"last_claim": None, "next_available": None, "status": "unknown"}
            changed = True
        if "last_visit" not in h[pid].get("progression", {}):
            h[pid].setdefault("progression", {})["last_visit"] = None
            changed = True
        for rk in ("reward_1", "reward_2", "reward_3"):
            if "status" not in h[pid]["store"].get(rk, {}):
                h[pid]["store"].setdefault(rk, {})["status"] = "unknown"
                changed = True
        if changed:
            save_claim_history(h)
    return h


def update_claim_history(pid, reward_type, claimed_count=0,
                         reward_index=None, detected_cooldown=None, attempted=False):
    h       = init_player_history(pid)
    ist_now = get_ist_time()
    nr      = get_next_daily_reset()

    if reward_type == "daily":
        if claimed_count > 0:
            h[pid]["daily"]["last_claim"]     = ist_now.isoformat()
            h[pid]["daily"]["next_available"] = nr.isoformat()
            h[pid]["daily"]["status"]         = "claimed"
            log(f"📝 Daily claimed → next reset {nr.strftime('%I:%M %p IST')}")
        elif detected_cooldown is not None:
            h[pid]["daily"]["next_available"] = nr.isoformat()
            h[pid]["daily"]["status"]         = "cooldown_detected"
            log(f"📝 Daily cooldown anchored → {nr.strftime('%I:%M %p IST')}")
        elif attempted:
            lc = h[pid]["daily"].get("last_claim")
            if lc and datetime.fromisoformat(lc) >= get_last_daily_reset():
                log(f"📝 Daily — preserving (claimed since last reset)")
            else:
                h[pid]["daily"]["status"] = "unavailable"
                log(f"📝 Daily unavailable")

    elif reward_type == "store" and reward_index is not None:
        rk = f"reward_{reward_index}"
        if claimed_count > 0:
            h[pid]["store"][rk]["last_claim"]     = ist_now.isoformat()
            h[pid]["store"][rk]["next_available"] = nr.isoformat()
            h[pid]["store"][rk]["status"]         = "claimed"
            log(f"📝 Store {reward_index} claimed → next reset {nr.strftime('%I:%M %p IST')}")
        elif detected_cooldown is not None:
            h[pid]["store"][rk]["next_available"] = nr.isoformat()
            h[pid]["store"][rk]["status"]         = "cooldown_detected"
            log(f"📝 Store {reward_index} cooldown anchored → daily reset")
        elif attempted:
            lc = h[pid]["store"][rk].get("last_claim")
            if lc and datetime.fromisoformat(lc) >= get_last_daily_reset():
                log(f"📝 Store {reward_index} — preserving (claimed since last reset)")
            else:
                h[pid]["store"][rk]["status"] = "unavailable"

    elif reward_type == "progression":
        # Always update last_visit — records that the page was visited
        h[pid]["progression"]["last_visit"] = ist_now.isoformat()
        if claimed_count > 0:
            h[pid]["progression"]["last_claim"] = ist_now.isoformat()
            h[pid]["progression"]["last_count"] = claimed_count
            log(f"📝 Progression claimed {claimed_count}, last_visit updated")

    elif reward_type == "loyalty":
        if claimed_count > 0:
            na = ist_now + timedelta(hours=LOYALTY_COOLDOWN_HOURS)
            h[pid]["loyalty"]["last_claim"]     = ist_now.isoformat()
            h[pid]["loyalty"]["next_available"] = na.isoformat()
            h[pid]["loyalty"]["status"]         = "claimed"
            log(f"📝 Loyalty claimed {claimed_count}, next in {LOYALTY_COOLDOWN_HOURS}h")
        elif detected_cooldown is not None:
            na = ist_now + detected_cooldown
            h[pid]["loyalty"]["next_available"] = na.isoformat()
            h[pid]["loyalty"]["status"]         = "cooldown_detected"
            log(f"📝 Loyalty cooldown → {format_time_until(na)}")
        elif attempted:
            lc = h[pid]["loyalty"].get("last_claim")
            if lc and ist_now < datetime.fromisoformat(lc) + timedelta(hours=LOYALTY_COOLDOWN_HOURS):
                log(f"📝 Loyalty — preserving (within 24h cooldown)")
            else:
                h[pid]["loyalty"]["status"] = "unavailable"
                log(f"📝 Loyalty unavailable")

    save_claim_history(h)
    return h


def get_reward_status(pid):
    h       = load_claim_history()
    ist_now = get_ist_time()
    lr      = get_last_daily_reset()
    nr      = get_next_daily_reset()

    if pid not in h:
        return {
            "daily_available":   True,  "daily_next": None,    "daily_status": "unknown",
            "store_available":   [True, True, True],
            "store_next":        [None, None, None],
            "store_status":      ["unknown", "unknown", "unknown"],
            "loyalty_available": True,  "loyalty_next": None,  "loyalty_status": "unknown",
        }

    ph = h[pid]

    # Daily
    d_avail, d_next = True, None
    d_status = ph["daily"].get("status", "unknown")
    lc_d = ph["daily"].get("last_claim")
    na_d = ph["daily"].get("next_available")
    if lc_d and datetime.fromisoformat(lc_d) >= lr:
        d_avail, d_next, d_status = False, format_time_until(nr), "claimed"
    elif na_d:
        nt = datetime.fromisoformat(na_d)
        if ist_now < nt:
            d_avail, d_next = False, format_time_until(nt)

    # Store
    s_avail  = [True, True, True]
    s_next   = [None, None, None]
    s_status = ["unknown", "unknown", "unknown"]
    for i in range(3):
        rk = f"reward_{i+1}"
        rd = ph["store"][rk]
        s_status[i] = rd.get("status", "unknown")
        lc_s = rd.get("last_claim")
        na_s = rd.get("next_available")
        if lc_s and datetime.fromisoformat(lc_s) >= lr:
            s_avail[i], s_next[i], s_status[i] = False, format_time_until(nr), "claimed"
        elif na_s:
            nt = datetime.fromisoformat(na_s)
            if ist_now < nt:
                s_avail[i], s_next[i] = False, format_time_until(nt)

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
        "daily_available":   d_avail, "daily_next":    d_next,    "daily_status":   d_status,
        "store_available":   s_avail, "store_next":    s_next,    "store_status":   s_status,
        "loyalty_available": l_avail, "loyalty_next":  l_next,    "loyalty_status": l_status,
    }


def all_claimable_on_cooldown(pid, has_loyalty):
    """
    Returns True only when every claimable reward is on cooldown AND
    progression was visited within the last 4 hours.
    Loyalty uses only last_claim — never next_available alone (can be poisoned).
    """
    PROGRESSION_CHECK_WINDOW_HOURS = 4

    s = get_reward_status(pid)
    if not s["daily_available"] and not any(s["store_available"]):
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


def capture_display_name(driver):
    """
    Extracts in-game display name from hub top-right corner after login.
    Falls back to None — caller uses masked ID fallback.
    """
    try:
        result = driver.execute_script("""
            var selectors = [
                '[class*="username"]', '[class*="display-name"]',
                '[class*="player-name"]', '[class*="user-name"]',
                '[class*="nickname"]', '[data-testid="username"]',
                '[data-testid="display-name"]'
            ];
            for (var i = 0; i < selectors.length; i++) {
                var el = document.querySelector(selectors[i]);
                if (el) {
                    var t = (el.innerText || el.textContent || '').trim();
                    if (t && t.length > 0 && t.length < 40) return t;
                }
            }
            // Heuristic: text near avatar image
            var avatars = document.querySelectorAll(
                'img[src*="avatar"],img[src*="profile"],img[alt*="avatar"],img[alt*="profile"]'
            );
            for (var j = 0; j < avatars.length; j++) {
                var parent = avatars[j].parentElement;
                for (var d = 0; d < 4 && parent; d++) {
                    var texts = Array.from(parent.querySelectorAll('*')).filter(function(e) {
                        var t = (e.innerText || '').trim();
                        return t.length > 1 && t.length < 40 && e.children.length === 0
                            && !/^[0-9]+$/.test(t) && !t.includes('/') && !t.includes(':');
                    });
                    if (texts.length > 0) {
                        var name = (texts[0].innerText || texts[0].textContent || '').trim();
                        if (name) return name;
                    }
                    parent = parent.parentElement;
                }
            }
            return null;
        """)
        if result and len(result.strip()) > 0:
            log(f"👤 Display name: {result.strip()}")
            return result.strip()
    except Exception as e:
        log(f"⚠️ Display name capture failed (non-critical): {e}")
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


def physical_click(driver, el):
    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({behavior:'smooth',block:'center'});", el)
        time.sleep(0.5)
        el.click()
        return True
    except:
        try:
            ActionChains(driver).move_to_element(el).click().perform()
            return True
        except:
            try:
                driver.execute_script("arguments[0].click();", el)
                return True
            except:
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

def claim_daily_rewards(driver, pid):
    """Returns (count_claimed, was_skipped)."""
    s = get_reward_status(pid)
    if not s["daily_available"]:
        log(f"⏩ Daily already claimed. Next: {s['daily_next']}")
        return 0, True

    log("🎁 Claiming Daily Rewards...")
    claimed = 0
    try:
        driver.get("https://hub.vertigogames.co/daily-rewards")
        bypass_cloudflare(driver)
        time.sleep(2)
        close_popup(driver)
        detect_page_cooldowns(driver, pid, "daily")

        for attempt in range(3):
            ok = driver.execute_script("""
                for(let btn of document.querySelectorAll('button')){
                    let t=(btn.innerText||btn.textContent).trim().toLowerCase();
                    if(t==='claim'&&btn.offsetParent!==null&&!btn.disabled){
                        btn.scrollIntoView({behavior:'smooth',block:'center'});
                        setTimeout(()=>btn.click(),300);
                        return true;
                    }
                }
                return false;
            """)
            if ok:
                log("✅ Daily Claimed")
                claimed = 1
                time.sleep(2)
                close_popup(driver)
                update_claim_history(pid, "daily", claimed_count=1)
                break
            else:
                log(f"ℹ️  No claimable daily (attempt {attempt+1})")
                time.sleep(1)

        if claimed == 0:
            s2 = get_reward_status(pid)
            if s2["daily_available"] and s2["daily_status"] not in ("cooldown_detected","claimed"):
                update_claim_history(pid, "daily", attempted=True)

        driver.save_screenshot(f"daily_{pid}.png")
    except Exception as e:
        log(f"❌ Daily error: {e}")
    return claimed, False


def claim_store_rewards(driver, pid):
    """Returns (count_claimed, skip_flags[3])."""
    s = get_reward_status(pid)
    skip_flags = [not a for a in s["store_available"]]

    if not any(s["store_available"]):
        log("⏩ All store rewards on cooldown")
        return 0, skip_flags

    log("🏪 Claiming Store Rewards...")
    claimed = 0
    try:
        driver.get("https://hub.vertigogames.co/store")
        bypass_cloudflare(driver)
        time.sleep(2)
        close_popup(driver)
        detect_page_cooldowns(driver, pid, "store")

        s2 = get_reward_status(pid)
        skip_flags = [not a for a in s2["store_available"]]
        if not any(s2["store_available"]):
            log("⏩ All store rewards on cooldown (confirmed by page)")
            return 0, skip_flags

        log(f"🎯 {sum(s2['store_available'])}/3 store rewards available")

        def _find_free_btn():
            try:
                for btn in driver.find_elements(By.TAG_NAME, "button"):
                    try:
                        if (btn.text.strip().lower() == "free"
                                and btn.is_displayed() and btn.is_enabled()):
                            par = btn.find_element(By.XPATH, "./..")
                            if "next in" in par.text.lower():
                                continue
                            return btn
                    except:
                        continue
            except:
                pass
            return None

        # Phase 1: physical clicks (claims 1-2)
        for attempt in range(3):
            if claimed >= 2:
                break
            if "store" not in driver.current_url:
                driver.get("https://hub.vertigogames.co/store")
                bypass_cloudflare(driver)
                time.sleep(2)
            time.sleep(1)
            btn = _find_free_btn()
            if btn:
                if physical_click(driver, btn):
                    time.sleep(4)
                    close_popup(driver)
                    claimed += 1
                    log(f"✅ Store Claim #{claimed}")
                    update_claim_history(pid, "store", claimed_count=1, reward_index=claimed)
                    time.sleep(1)
            elif attempt >= 1:
                break
            else:
                time.sleep(1)

        # Phase 2: 3rd claim — physical + JS fallback
        if claimed < 3:
            for attempt in range(4):
                if claimed >= 3:
                    break
                if "store" not in driver.current_url:
                    driver.get("https://hub.vertigogames.co/store")
                    bypass_cloudflare(driver)
                    time.sleep(2)
                time.sleep(1.5)
                btn = _find_free_btn()
                if btn:
                    if physical_click(driver, btn):
                        time.sleep(4)
                        close_popup(driver)
                        claimed += 1
                        log(f"✅ Store Claim #{claimed}")
                        update_claim_history(pid, "store", claimed_count=1, reward_index=claimed)
                        break
                ok = driver.execute_script("""
                    let cards=document.querySelectorAll('[class*="StoreBonus"]');
                    if(!cards.length)cards=document.querySelectorAll('div');
                    for(let card of cards){
                        let ct=card.innerText||'';
                        if(ct.includes('Next in')||ct.match(/\\d+h\\s+\\d+m/))continue;
                        for(let btn of card.querySelectorAll('button')){
                            let t=btn.innerText.trim().toLowerCase();
                            if((t==='free'||t==='claim')&&btn.offsetParent!==null&&!btn.disabled){
                                btn.scrollIntoView({behavior:'smooth',block:'center'});
                                btn.click(); return true;
                            }
                        }
                    }
                    return false;
                """)
                if ok:
                    claimed += 1
                    log(f"✅ Store Claim #{claimed} (JS)")
                    time.sleep(4)
                    close_popup(driver)
                    update_claim_history(pid, "store", claimed_count=1, reward_index=claimed)
                    break
                elif attempt < 3:
                    log(f"ℹ️  Both methods failed, retry {attempt+1}/4")
                    time.sleep(2)

        s3 = get_reward_status(pid)
        for i in range(claimed + 1, 4):
            if (s3["store_available"][i-1]
                    and s3["store_status"][i-1] not in ("cooldown_detected","claimed")):
                update_claim_history(pid, "store", reward_index=i, attempted=True)

        log(f"📊 Store: {claimed}/3")
        driver.save_screenshot(f"store_{pid}.png")
    except Exception as e:
        log(f"❌ Store error: {e}")

    return claimed, skip_flags


def claim_progression_program_rewards(driver, pid):
    log("🎯 Claiming Progression Program...")
    claimed = 0
    try:
        driver.get("https://hub.vertigogames.co/progression-program")
        bypass_cloudflare(driver)
        time.sleep(2)
        close_popup(driver)
        for _ in range(6):
            ok = driver.execute_script("""
                // Detects both 'Claim' (single reward) and 'Claim all' (multi-reward card)
                for(let btn of document.querySelectorAll('button')){
                    let t=(btn.innerText||btn.textContent).trim().toLowerCase();
                    let isClaimBtn = (t==='claim' || t==='claim all');
                    if(!isClaimBtn) continue;
                    if(btn.disabled) continue;
                    // offsetParent check skipped for 'claim all' (carousel may not be visible)
                    if(t==='claim' && btn.offsetParent===null) continue;
                    let pt=(btn.parentElement.innerText||btn.parentElement.textContent)||'';
                    if(pt.includes('Delivered')) continue;
                    btn.scrollIntoView({behavior:'smooth',block:'center',inline:'center'});
                    setTimeout(()=>btn.click(),300);
                    return t;
                }
                return false;
            """)
            if ok:
                log(f"✅ Progression: '{ok}' clicked")
                claimed += 1
                time.sleep(2.0)
                close_popup(driver)
            else:
                driver.execute_script(
                    "for(let i of document.querySelectorAll('div'))"
                    "{if(i.scrollWidth>i.clientWidth)i.scrollLeft+=400;}"
                )
                time.sleep(1)

        # Always record last_visit so smart-skip knows the page was checked
        update_claim_history(pid, "progression", claimed_count=claimed)
    except:
        # Still try to record visit even on exception
        try:
            update_claim_history(pid, "progression", claimed_count=0)
        except:
            pass
    return claimed


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
        "is_new":          is_new,
        "has_loyalty":     has_loyalty,
        "daily":           0,
        "store":           0,
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
    }

    init_player_history(pid)

    # Smart skip — no browser needed if all rewards on cooldown + progression checked
    if all_claimable_on_cooldown(pid, has_loyalty):
        log(f"\n⏩ {pid} — all on cooldown, smart-skipping")
        stats.update({
            "skipped_all":     True,
            "daily_skipped":   True,
            "store_skipped":   [True, True, True],
            "loyalty_skipped": has_loyalty,
            "status":          "All Skipped (Cooldown)",
        })
        # Snapshot next-available for email display
        snap = get_reward_status(pid)
        stats["store_next"]   = snap["store_next"]
        stats["daily_next"]   = snap["daily_next"]
        stats["loyalty_next"] = snap.get("loyalty_next")
        stats["duration_s"]   = int((get_ist_time() - start).total_seconds())
        return stats

    driver = None
    try:
        log(f"\n🚀 {pid}" + (" 🆕 NEW ID" if is_new else "") + f"  [{run_label}]")
        driver = create_driver()

        if not login_to_hub(driver, pid):
            stats["status"]      = "Login Failed"
            stats["fail_reason"] = "Could not authenticate"
            return stats

        # Capture display name right after login — used in email instead of raw player ID
        stats["display_name"] = capture_display_name(driver)

        # Daily
        d, d_skip = claim_daily_rewards(driver, pid)
        stats["daily"]         = d
        stats["daily_skipped"] = d_skip
        if not d_skip:
            stats["possible"] += 1

        # Store
        for retry in range(2):
            s, s_skips = claim_store_rewards(driver, pid)
            stats["store"]         = s
            stats["store_skipped"] = s_skips
            if s >= 3:
                break
            elif s > 0 and retry < 1:
                log(f"⚠️ Got {s}/3 store. Retrying...")
                time.sleep(2)
            elif s == 0:
                break

        stats["possible"] += sum(1 for sk in stats["store_skipped"] if not sk)

        if stats["store"] > 0:
            log("⏳ Waiting for server to process store claims...")
            time.sleep(3)

        # Progression
        for retry in range(2):
            p = claim_progression_program_rewards(driver, pid)
            stats["progression"] += p
            if p == 0 and retry < 1:
                log("⚠️ No progression, retrying...")
                time.sleep(2)
            elif p == 0:
                log("ℹ️  No progression available")
                break
            elif retry < 1:
                log(f"✅ Got {p} progression, checking for more...")
                time.sleep(1)

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

        # Determine status
        claimed_now = stats["daily"] + stats["store"] + stats.get("loyalty", 0)
        possible    = stats["possible"]
        if possible == 0:
            stats["status"] = "All Skipped (Cooldown)"
        elif claimed_now >= possible:
            stats["status"] = "Success"
        elif claimed_now > 0:
            stats["status"] = "Partial"
        else:
            stats["status"] = "No Rewards"

        total_inc_prog = claimed_now + stats["progression"]
        log(f"🎉 {pid}: {total_inc_prog} claimed "
            f"(D:{stats['daily']} S:{stats['store']} "
            f"P:{stats['progression']} L:{stats['loyalty']})")

    except Exception as e:
        log(f"❌ Error: {e}")
        stats["status"]      = "Error"
        stats["fail_reason"] = str(e)[:120]
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass

    stats["duration_s"] = int((get_ist_time() - start).total_seconds())

    snap = get_reward_status(pid)
    stats["store_next"]   = snap["store_next"]
    stats["daily_next"]   = snap["daily_next"]
    stats["loyalty_next"] = snap.get("loyalty_next")

    return stats


# ═══════════════════════════════════════════════════════════════════════════════
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


def _row_cls(status):
    return {"Success":"rs","Partial":"rp","Login Failed":"rf",
            "Error":"rf","Failed":"rf"}.get(status, "rk")


def _sb_html(status):
    m = {
        "Success":               ("ss", "✅ Success"),
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
        d_s2        = r.get("duration_s", 0)
        tm2         = f"{d_s2//60}m{d_s2%60}s" if d_s2 else "—"

        sk2 = r.get("store_skipped", [False, False, False])
        if not isinstance(sk2, list) or len(sk2) < 3:
            sk2 = [False, False, False]
        free_slots2 = [i for i in range(3) if not sk2[i]]
        cc2         = [False, False, False]
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
        p_ic  = "✅" if r["progression"] > 0 else ("❌" if is_fail2 else "⏳")

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

        mob_html += (
            f'<div class="mpc {rc}">'
            f'<div class="mpc-id">{display_lbl}{new_mark}'
            f'<span class="mpc-st">{_sb_html(status)}</span></div>'
            f'<div class="mpc-row">'
            f'<span class="mpc-lbl">🎁 Daily &nbsp;🥇 Gold &nbsp;💵 Cash &nbsp;🍀 Lucky</span>'
            f'<span class="mpc-val">{d_ic} &nbsp;{g_ic} &nbsp;{c_ic} &nbsp;{l_ic}</span></div>'
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
        f"<span>👥 All {n} Players</span>"
        f"<small>🆕 = new ID this run</small></div>"
        f"{mob_html}"
        f"</div>"
    )


def build_email(results, run_label, run_index, job_start, meta):
    ist_now = get_ist_time()
    dur_s   = int((ist_now - job_start).total_seconds())
    dur_str = f"{dur_s // 60}m {dur_s % 60}s"
    n       = len(results)

    td   = sum(r["daily"]          for r in results)
    ts   = sum(r["store"]          for r in results)
    tp   = sum(r["progression"]    for r in results)
    tl   = sum(r.get("loyalty", 0) for r in results)
    tall = td + ts + tp + tl

    tp_all  = sum(r.get("possible", 0) for r in results)
    eff     = 100.0 if tp_all == 0 else round((td + ts + tl) / tp_all * 100, 1)
    l_enrl  = sum(1 for r in results if r.get("has_loyalty"))
    skip_ct = sum(1 for r in results if r.get("skipped_all"))
    act_ct  = n - skip_ct

    streak = meta.get("streak", {})
    s_cur  = streak.get("current", 0)
    s_best = streak.get("best", 0)
    lr     = meta.get("last_run") or {}
    lr_d   = lr.get("per_type", {}).get("daily")
    lr_s   = lr.get("per_type", {}).get("store")
    lr_l   = lr.get("per_type", {}).get("loyalty")
    lr_tot = lr.get("total_claimed")
    lr_eff = lr.get("efficiency")

    timed   = [(r["pid"], r.get("duration_s", 0)) for r in results if not r.get("skipped_all")]
    avg_t   = round(sum(t for _, t in timed) / len(timed), 1) if timed else 0
    slowest = max(timed, key=lambda x: x[1]) if timed else None

    bc  = _badge_cls(run_label)
    bi  = {"Primary Run": "🟢", "Manual Run": "🔧"}.get(run_label, "🔵")

    d_pct = min(round(td / n * 100) if n else 0, 100)
    s_pct = min(round(ts / (n * 3) * 100) if n else 0, 100)
    l_pct = min(round(tl / l_enrl * 100) if l_enrl else 0, 100)

    dlt_d   = _delta_html(td,   lr_d)
    dlt_s   = _delta_html(ts,   lr_s)
    dlt_l   = _delta_html(tl,   lr_l)
    dlt_tot = _delta_html(tall, lr_tot)
    dlt_eff = _delta_html(
        round(eff, 1),
        round(lr_eff, 1) if lr_eff is not None else None, "%"
    )

    # ── Build table rows ──────────────────────────────────────────────────────
    table_rows   = ""
    detail_cards = ""
    has_details  = False

    for r in results:
        pid         = r["pid"]
        display_lbl = _display_label(r)   # privacy-safe — never raw pid
        status      = r["status"]
        rc          = _row_cls(status)
        new_mark    = " 🆕" if r.get("is_new") else ""

        # Daily cell
        if r["daily_skipped"]:
            dn = r.get("daily_next") or "next reset"
            dc = f'<span class="ic-cd" title="Next: {dn}">⏰</span>'
        elif r["daily"] > 0:
            dc = '<span class="ic-ok">✅</span>'
        elif status in ("Login Failed", "Error", "Failed"):
            dc = '<span class="ic-fl">❌</span>'
        else:
            dc = '<span class="ic-pd">⏳</span>'

        # Store cells
        sk = r.get("store_skipped", [False, False, False])
        if not isinstance(sk, list) or len(sk) < 3:
            sk = [False, False, False]
        free_slots    = [i for i in range(3) if not sk[i]]
        claimed_cards = [False, False, False]
        for j, idx in enumerate(free_slots):
            if j < r["store"]:
                claimed_cards[idx] = True

        sn_list = r.get("store_next") or [None, None, None]
        sc_html = ""
        for i in range(3):
            sep = ' style="border-left:1px solid #e5e7eb;"' if i == 0 else ''
            if sk[i]:
                nxt  = (sn_list[i] if sn_list and len(sn_list) > i else None) or "next reset"
                cell = f'<span class="ic-cd" title="Next: {nxt}">⏰</span>'
            elif claimed_cards[i]:
                cell = '<span class="ic-ok">✅</span>'
            elif status in ("Login Failed", "Error", "Failed"):
                cell = '<span class="ic-fl">❌</span>'
            else:
                cell = '<span class="ic-pd">⏳</span>'
            sc_html += f'<td{sep}>{cell}</td>'

        # Progression cell
        pc = ('<span class="ic-ok">✅</span>' if r["progression"] > 0
              else '<span class="ic-fl">❌</span>'
              if status in ("Login Failed", "Error", "Failed")
              else '<span class="ic-lk" title="Awaiting grenades/bullets">⏳</span>')

        # Loyalty cell
        if not r.get("has_loyalty"):
            lc = '<span class="ic-na">—</span>'
        elif r.get("loyalty_skipped"):
            ln = r.get("loyalty_next") or "24h"
            lc = f'<span class="ic-cd" title="Next: {ln}">⏰</span>'
        elif r.get("loyalty", 0) > 0:
            lc = '<span class="ic-ok">✅</span>'
        elif status in ("Login Failed", "Error", "Failed"):
            lc = '<span class="ic-fl">❌</span>'
        else:
            lc = '<span class="ic-lk" title="Awaiting LP from purchases">🔒</span>'

        # Time cell — always shows duration (even smart-skipped)
        d_s = r.get("duration_s", 0)
        tm  = (f'<span style="color:#6b7280;">{d_s//60}m{d_s%60}s</span>'
               if d_s else '<span class="ic-na">—</span>')

        table_rows += (
            f'<tr class="{rc}">'
            f'<td class="idc">{display_lbl}{new_mark}</td>'
            f'<td style="border-left:1px solid #e5e7eb;">{dc}</td>'
            f'{sc_html}'
            f'<td style="border-left:1px solid #e5e7eb;">{pc}</td>'
            f'<td style="border-left:1px solid #e5e7eb;">{lc}</td>'
            f'<td style="border-left:1px solid #e5e7eb;">{tm}</td>'
            f'<td>{_sb_html(status)}</td>'
            f'</tr>'
        )

        # Detail cards (failed/partial only)
        if status in ("Failed", "Partial", "Login Failed", "Error", "No Rewards"):
            has_details = True
            dc_cls   = "dcf" if status in ("Error", "Failed", "Login Failed") else "dcp"
            err_html = (f'<div class="dce">⚠️ {r["fail_reason"]}</div>'
                        if r.get("fail_reason") else "")

            d_val = ("✅ Claimed" if r["daily"] > 0
                     else f'⏰ {r.get("daily_next") or "On cooldown"}' if r["daily_skipped"]
                     else "⏳ Not claimed")

            SNAMES = ["🥇 Gold", "💵 Cash", "🍀 Luckyloon"]
            s_rows = ""
            for i in range(3):
                if claimed_cards[i]:
                    sv = "✅ Claimed"
                elif sk[i]:
                    nxt = (sn_list[i] if sn_list and len(sn_list) > i else None) or "On cooldown"
                    sv  = f"⏰ {nxt}"
                else:
                    sv = "⏳ Not claimed"
                s_rows += _drow(SNAMES[i], sv)

            pg = (f"✅ {r['progression']} claimed" if r["progression"] > 0
                  else "⏳ Awaiting grenade/bullet threshold")

            if r.get("has_loyalty"):
                if r.get("loyalty", 0) > 0:
                    lv = f"✅ {r['loyalty']} claimed"
                elif r.get("loyalty_skipped"):
                    lv = f"⏰ {r.get('loyalty_next') or 'On 24h cooldown'}"
                else:
                    lv = "🔒 Awaiting LP threshold from purchases"
            else:
                lv = "— Not enrolled"

            detail_cards += (
                f'<div class="dc {dc_cls}">'
                f'<div class="dcid">{display_lbl}{new_mark}</div>'
                f'{err_html}'
                f'{_drow("🎁 Daily", d_val)}'
                f'{s_rows}'
                f'{_drow("🎯 Progression", pg)}'
                f'{_drow("🏆 Loyalty", lv)}'
                f'{_drow("⏱️ Time", str(d_s) + "s") if d_s else ""}'
                f'</div>'
            )

    detail_sec = ""
    if has_details:
        detail_sec = (
            f'<div class="dcs">'
            f'<div class="dct">⚠️ Requires Attention — Failed &amp; Partial IDs</div>'
            f'{detail_cards}'
            f'</div>'
        )

    # Schedule footer
    sched   = next_scheduled_runs_ist()
    nr_html = ""
    for i, (lbl, t) in enumerate(sched):
        cls      = ' class="nr nrp"' if i == 0 else ' class="nr"'
        nr_html += f'<div{cls}><div class="nrl">{lbl}</div><div class="nrt">{t}</div></div>'

    slowest_str = f"{slowest[0][:8]}… ({slowest[1]}s)" if slowest else "—"

    # Mobile stacked cards
    mob_section = build_mobile_cards(results, n)

    html = (
        "<!DOCTYPE html><html lang='en'>"
        "<head><meta charset='UTF-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1.0'>"
        f"<style>{_CSS}</style></head>"
        "<body><div class='wrap'>"

        # Hero
        f"<div class='hero'>"
        f"<span class='badge {bc}'>{bi} {run_label}</span>"
        f"<div class='hero-grid'>"
        f"<div class='hero-left'>"
        f"<h1>CS Hub Rewards Dashboard</h1>"
        f"<p>📅 {ist_now.strftime('%d %b %Y, %I:%M %p IST')}"
        f" &nbsp;·&nbsp; ⏱️ {dur_str}"
        f" &nbsp;·&nbsp; 👥 {act_ct} active / {skip_ct} smart-skipped</p>"
        f"</div>"
        f"<div class='hero-nums'>"
        f"<div class='hnum'><span class='hv g'>{tall}</span>"
        f"<span class='hl'>Total Claimed</span></div>"
        f"<div class='hnum'><span class='hv b'>{eff:.1f}%</span>"
        f"<span class='hl'>Efficiency</span></div>"
        f"<div class='hnum'><span class='hv a'>🔥 {s_cur}</span>"
        f"<span class='hl'>Day Streak</span></div>"
        f"</div></div></div>"

        # KPI Row
        f"<div class='kpi-row'>"
        f"<div class='kpi'><div class='kl'>🎁 Daily</div>"
        f"<div class='kv'>{td}<span>/{n}</span></div>"
        f"<div class='ks'>{dlt_d}</div>{_pbar(d_pct,'pg')}</div>"

        f"<div class='kpi'><div class='kl'>🏪 Store</div>"
        f"<div class='kv'>{ts}<span>/{n*3}</span></div>"
        f"<div class='ks'>{dlt_s}</div>{_pbar(s_pct,'pb2')}</div>"

        f"<div class='kpi'><div class='kl'>🎯 Progression</div>"
        f"<div class='kv'>{tp}<span> items</span></div>"
        f"<div class='ks'>Variable — grenade dependent</div>"
        f"{_pbar(min(tp*10,100),'pp')}</div>"

        f"<div class='kpi'><div class='kl'>🏆 Loyalty</div>"
        f"<div class='kv'>{tl}<span>/{l_enrl}</span></div>"
        f"<div class='ks'>{dlt_l}</div>{_pbar(l_pct,'pa')}</div>"
        f"</div>"

        # Strip
        f"<div class='strip'>"
        f"<span class='si'>⏱️ <strong>{dur_str}</strong> total</span>"
        f"<span class='si'>👤 Avg <strong>{avg_t}s</strong>/ID</span>"
        f"<span class='si'>🐢 Slowest: <strong>{slowest_str}</strong></span>"
        f"<span class='si'>🔥 Best streak: <strong>{s_best} days</strong></span>"
        f"<span class='si'>📊 Efficiency: <strong>{eff:.1f}%</strong> {dlt_eff}</span>"
        f"<span class='si'>📦 This run: <strong>{tall}</strong> claimed {dlt_tot}</span>"
        f"</div>"

        # Desktop table
        f"<div class='tbx'>"
        f"<div class='tbh'><span>👥 All {n} Players</span>"
        f"<small>⏩ = already claimed &nbsp;|&nbsp; 🆕 = new ID this run</small></div>"
        f"<div class='tsc'><table>"
        f"<tr>"
        f"<th class='idh'>Player</th>"
        f"<th style='border-left:1px solid #e5e7eb;'>🎁 Daily</th>"
        f"<th style='border-left:1px solid #e5e7eb;'>🥇 Gold</th>"
        f"<th>💵 Cash</th><th>🍀 Lucky</th>"
        f"<th style='border-left:1px solid #e5e7eb;'>🎯 Prog</th>"
        f"<th style='border-left:1px solid #e5e7eb;'>🏆 Loyal</th>"
        f"<th style='border-left:1px solid #e5e7eb;'>⏱️ Time</th>"
        f"<th>Status</th>"
        f"</tr>"
        f"{table_rows}"
        f"</table></div></div>"

        # Mobile stacked cards (hidden on desktop)
        f"{mob_section}"

        # Detail cards
        f"{detail_sec}"

        # Footer
        f"<div class='foot'>"
        f"<div class='ft'>🗓️ All 8 Scheduled Runs Today (IST)</div>"
        f"<div class='nr-grid'>{nr_html}</div>"
        f"<div class='leg'>"
        f"<span>✅ Claimed this run</span>"
        f"<span>⏰ Already claimed / on cooldown</span>"
        f"<span>⏳ Pending (dependency)</span>"
        f"<span>🔒 LP threshold not met</span>"
        f"<span>❌ Failed</span>"
        f"<span>— Not enrolled</span>"
        f"<span>🆕 New ID (first run)</span>"
        f"</div>"
        f"<div class='leg' style='margin-top:8px;'>"
        f"<span>📌 Daily &amp; Store reset at 5:30 AM IST</span>"
        f"<span>📌 Progression: monthly reset, grenade-dependent</span>"
        f"<span>📌 Loyalty: 24h rolling, LP-dependent</span>"
        f"</div>"
        f"<div class='ver'>CS Rewards Bot {VERSION} &nbsp;·&nbsp; "
        f"Automated via GitHub Actions &nbsp;·&nbsp; "
        f"Run #{run_index+1 if run_index >= 0 else '(manual)'} of 8</div>"
        f"</div>"
        f"</div></body></html>"
    )
    return html




# ═══════════════════════════════════════════════════════════════════════════════
# LOGIN DIAGNOSTICS — read-only snapshots; does not type an ID or claim rewards
# ═══════════════════════════════════════════════════════════════════════════════

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


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 11 — MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    if "--diagnose-login" in sys.argv[1:]:
        return run_login_diagnostic()

    job_start = get_ist_time()
    log("=" * 60)
    log(f"CS HUB AUTO-CLAIMER {VERSION}")
    log("=" * 60)

    run_label, run_index = determine_run_context()
    ist_now = get_ist_time()
    log(f"📋 Run Context: {run_label}  |  {ist_now.strftime('%d-%b %H:%M IST')}")

    meta = load_bot_meta()

    players = []
    try:
        with open(PLAYER_ID_FILE, 'r') as f:
            for row in csv.DictReader(f):
                pid = row.get("player_id", "").strip()
                if pid:
                    hl = row.get("has_loyalty", "").strip().lower() in ("true", "yes", "1")
                    players.append((pid, hl))
    except Exception as e:
        log(f"❌ Failed to read {PLAYER_ID_FILE}: {e}")
        return

    log(f"👥 Loaded {len(players)} players "
        f"({sum(1 for _, h in players if h)} with loyalty)")

    results = []
    for pid, has_loyalty in players:
        new_id = is_new_id(pid, meta)
        mark_id_seen(pid, meta)
        r = process_player(pid, has_loyalty, new_id, run_label)
        results.append(r)
        time.sleep(0.5)

    # Metrics
    job_end = get_ist_time()
    dur_s   = int((job_end - job_start).total_seconds())
    td      = sum(r["daily"]          for r in results)
    ts      = sum(r["store"]          for r in results)
    tp      = sum(r["progression"]    for r in results)
    tl      = sum(r.get("loyalty", 0) for r in results)
    tall    = td + ts + tp + tl
    tp_all  = sum(r.get("possible", 0) for r in results)
    eff     = 100.0 if tp_all == 0 else round((td + ts + tl) / tp_all * 100, 1)

    timed   = [(r["pid"], r.get("duration_s", 0)) for r in results if not r.get("skipped_all")]
    avg_t   = round(sum(t for _, t in timed) / len(timed), 1) if timed else 0
    slowest = max(timed, key=lambda x: x[1]) if timed else None

    log(f"\n{'='*60}")
    log(f"Run complete: {tall} claimed | {eff:.1f}% efficiency | {dur_s}s total")
    log(f"  Daily:{td}  Store:{ts}  Prog:{tp}  Loyalty:{tl}")
    log(f"{'='*60}")

    # Streak: only requires daily + store, NOT loyalty (LP-locked players would break it)
    all_ok = compute_all_ok_today(players)
    if all_ok:
        log("✅ All daily+store claimed today — streak eligible")
    update_streak_day_level(meta, all_ok)

    prev_run = meta.get("last_run")
    meta["last_run"] = {
        "timestamp":           job_end.isoformat(),
        "run_label":           run_label,
        "total_claimed":       tall,
        "efficiency":          eff,
        "duration_seconds":    dur_s,
        "per_type":            {"daily": td, "store": ts, "progression": tp, "loyalty": tl},
        "slowest_player":      slowest[0] if slowest else None,
        "avg_time_per_player": avg_t,
    }
    meta_for_email = dict(meta)
    meta_for_email["last_run"] = prev_run   # email delta uses previous run

    save_bot_meta(meta)

    html_body = build_email(results, run_label, run_index, job_start, meta_for_email)

    n_players = len(players)
    ok_count  = sum(1 for r in results if r["status"] == "Success")
    ist_label = job_start.strftime('%d-%b %I:%M %p')
    streak_d  = meta["streak"].get("current", 0)
    subject = (
        f"🎮 CS Hub | {ist_label} IST | {ok_count}/{n_players} IDs ✅ "
        f"| {eff:.1f}% Efficiency | Day {streak_d} 🔥"
    )

    log(f"📧 Sending email: {subject}")
    send_email(html_body, subject)

    # ── Replace everything from line 2139 to end of file ──────────────────────────
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
    # 1. THE FAILSAFE: Save a local copy of the email. 
    # Your schedule.yml will automatically upload this to GitHub Artifacts!
    try:
        with open("debug_email.html", "w", encoding="utf-8") as f:
            f.write(html_body)
    except Exception as e:
        pass

    if not (SMTP_SERVER and SMTP_USERNAME and SMTP_PASSWORD and SMTP_FROM and SMTP_TO):
        log("⚠️ Email env vars missing — skipping email")
        return

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = SMTP_FROM
        msg["To"]      = SMTP_TO
        
        # 2. THE CRITICAL FIX: Explicitly forcing UTF-8 encoding for the emojis
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, timeout=30) as server:
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM, [SMTP_TO], msg.as_string())
            
        log("📧 Email sent successfully")
    except smtplib.SMTPAuthenticationError as e:
        log(f"⚠️ Email auth failed: {e.smtp_code} {e.smtp_error}")
    except Exception as e:
        log(f"⚠️ Email failed: {type(e).__name__}: {e}")

    return False


if __name__ == "__main__":
    raise SystemExit(main() or 0)
