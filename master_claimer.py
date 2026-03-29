# master_claimer.py  –– CS Rewards Bot v3.0.0
# Staged delivery note: file is written top-to-bottom in one block.
# If generation stops mid-file, continue from the exact line indicated.

import csv
import time
import os
import json
import smtplib
import re
import subprocess
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, StaleElementReferenceException

# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — CONSTANTS & CONFIG
# ═══════════════════════════════════════════════════════════════════════════════

VERSION        = "v3.0.0"
PLAYER_ID_FILE = "players.csv"
HISTORY_FILE   = "claim_history.json"
BOT_META_FILE  = "bot_meta.json"
HEADLESS       = True

DAILY_RESET_HOUR_IST   = 5
DAILY_RESET_MINUTE_IST = 30
LOYALTY_COOLDOWN_HOURS = 24   # Loyalty is rolling 24h (not anchored to daily reset)

# Email — reads original secret names (GMAIL_APP_PASSWORD / SENDER_EMAIL / RECIPIENT_EMAIL)
# with SMTP_* as fallback so both old and new secret naming works.
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
    """Returns the most recent 5:30 AM IST reset that has already passed."""
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


# All 8 IST trigger times for the 3-hourly schedule (cron: '5 0/3 * * *')
# Index 0 = Primary, 1-7 = Backup #1 through Backup #7
_RUN_SLOTS = [(5,35), (8,35), (11,35), (14,35), (17,35), (20,35), (23,35), (2,35)]

def determine_run_context():
    """
    Returns (label, index):
      label = 'Primary Run' | 'Backup Run #N' | 'Manual Run'
      index = 0 (primary) | 1-7 (backups) | -1 (manual)
    """
    event = os.getenv("GITHUB_EVENT_NAME", "schedule")
    if event == "workflow_dispatch":
        return "Manual Run", -1
    ist = get_ist_time()
    h, mi = ist.hour, ist.minute
    for i, (slot_h, slot_m) in enumerate(_RUN_SLOTS):
        # Allow ±10 min window around each slot
        slot_dt = ist.replace(hour=slot_h, minute=slot_m, second=0, microsecond=0)
        if abs((ist - slot_dt).total_seconds()) <= 600:
            label = "Primary Run" if i == 0 else f"Backup Run #{i}"
            return label, i
    # Fallback — treat unrecognised times as backup
    return "Backup Run", 1


def next_scheduled_runs_ist():
    """Return list of (label, 'HH:MM IST') for all 8 slots."""
    return [
        ("Primary Run" if i == 0 else f"Backup #{i}", f"{h:02d}:{m:02d} IST")
        for i, (h, m) in enumerate(_RUN_SLOTS)
    ]


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — BOT META  (streak, last-run delta, new-ID tracking)
# ═══════════════════════════════════════════════════════════════════════════════

_META_DEFAULT = {
    "streak": {
        "current": 0,
        "best": 0,
        "last_success_date": None,   # YYYY-MM-DD (IST)
        "last_checked_date": None
    },
    "last_run": None,          # filled after each run
    "new_ids_seen": []         # IDs we've processed at least once
}


def load_bot_meta():
    if os.path.exists(BOT_META_FILE):
        try:
            with open(BOT_META_FILE, 'r') as f:
                data = json.load(f)
            # Migrate old schema if needed
            if "streak" not in data:
                data["streak"] = _META_DEFAULT["streak"].copy()
                data["streak"]["current"] = data.pop("streak_days", 0)
            if "new_ids_seen" not in data:
                data["new_ids_seen"] = data.pop("known_ids", [])
            if "last_run" not in data:
                old = data.pop("last_run_stats", {})
                data["last_run"] = {
                    "timestamp": None, "run_label": None,
                    "total_claimed": old.get("claimed", 0),
                    "efficiency": old.get("efficiency", 0.0),
                    "duration_seconds": old.get("duration", 0),
                    "per_type": {"daily": 0, "store": 0, "progression": 0, "loyalty": 0},
                    "slowest_player": None, "avg_time_per_player": 0
                } if old else None
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
    Option B (day-level): streak increments if any run today results in
    all enrolled IDs having all rewards claimed.
    """
    streak   = meta.setdefault("streak", _META_DEFAULT["streak"].copy())
    ist_date = get_ist_time().strftime("%Y-%m-%d")

    if all_ok_today:
        if streak.get("last_success_date") != ist_date:
            streak["current"] = streak.get("current", 0) + 1
            streak["best"]    = max(streak.get("best", 0), streak["current"])
            streak["last_success_date"] = ist_date
            log(f"🔥 Streak: Day {streak['current']} (Best: {streak['best']})")
    else:
        # Break streak only if yesterday wasn't a success and we haven't already
        # marked today as success
        last_ok = streak.get("last_success_date")
        if last_ok and last_ok != ist_date:
            yesterday = (datetime.strptime(ist_date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
            if last_ok < yesterday:
                if streak.get("current", 0) > 0:
                    log(f"💔 Streak broken at Day {streak['current']}")
                streak["current"] = 0

    streak["last_checked_date"] = ist_date
    meta["streak"] = streak


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
            "progression": {"last_claim": None, "last_count": 0},
            "loyalty":     {"last_claim": None, "next_available": None, "status": "unknown"}
        }
        save_claim_history(h)
    else:
        changed = False
        if "loyalty" not in h[pid]:
            h[pid]["loyalty"] = {"last_claim": None, "next_available": None, "status": "unknown"}
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
    """
    KEY CHANGE v3.0:
      - DAILY  → next_available anchored to get_next_daily_reset()
      - STORE  → next_available anchored to get_next_daily_reset()  (changed from 24h rolling)
      - LOYALTY → next_available = ist_now + LOYALTY_COOLDOWN_HOURS (still rolling 24h)
    """
    h       = init_player_history(pid)
    ist_now = get_ist_time()
    nr      = get_next_daily_reset()  # used by both daily and store

    # ── DAILY ─────────────────────────────────────────────────────────────────
    if reward_type == "daily":
        if claimed_count > 0:
            h[pid]["daily"]["last_claim"]     = ist_now.isoformat()
            h[pid]["daily"]["next_available"] = nr.isoformat()
            h[pid]["daily"]["status"]         = "claimed"
            log(f"📝 Daily claimed → next reset {nr.strftime('%I:%M %p IST')}")
        elif detected_cooldown is not None:
            h[pid]["daily"]["next_available"] = nr.isoformat()   # anchor to reset
            h[pid]["daily"]["status"]         = "cooldown_detected"
            log(f"📝 Daily cooldown anchored → {nr.strftime('%I:%M %p IST')}")
        elif attempted:
            lc = h[pid]["daily"].get("last_claim")
            if lc and datetime.fromisoformat(lc) >= get_last_daily_reset():
                log(f"📝 Daily — preserving (claimed since last reset)")
            else:
                h[pid]["daily"]["status"] = "unavailable"
                log(f"📝 Daily unavailable")

    # ── STORE (anchored to daily reset, same as daily) ─────────────────────────
    elif reward_type == "store" and reward_index is not None:
        rk = f"reward_{reward_index}"
        if claimed_count > 0:
            h[pid]["store"][rk]["last_claim"]     = ist_now.isoformat()
            h[pid]["store"][rk]["next_available"] = nr.isoformat()   # anchor
            h[pid]["store"][rk]["status"]         = "claimed"
            log(f"📝 Store {reward_index} claimed → next reset {nr.strftime('%I:%M %p IST')}")
        elif detected_cooldown is not None:
            h[pid]["store"][rk]["next_available"] = nr.isoformat()   # anchor
            h[pid]["store"][rk]["status"]         = "cooldown_detected"
            log(f"📝 Store {reward_index} cooldown anchored → daily reset")
        elif attempted:
            lc = h[pid]["store"][rk].get("last_claim")
            if lc and datetime.fromisoformat(lc) >= get_last_daily_reset():
                log(f"📝 Store {reward_index} — preserving (claimed since last reset)")
            else:
                h[pid]["store"][rk]["status"] = "unavailable"

    # ── PROGRESSION ────────────────────────────────────────────────────────────
    elif reward_type == "progression" and claimed_count > 0:
        h[pid]["progression"]["last_claim"] = ist_now.isoformat()
        h[pid]["progression"]["last_count"] = claimed_count
        log(f"📝 Progression claimed {claimed_count}")

    # ── LOYALTY (rolling 24h) ──────────────────────────────────────────────────
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
    """
    Returns dict with availability flags and next-available strings.
    Uses last_reset anchor for daily+store (not 24h rolling).
    """
    h       = load_claim_history()
    ist_now = get_ist_time()
    lr      = get_last_daily_reset()
    nr      = get_next_daily_reset()

    if pid not in h:
        return {
            "daily_available":   True,
            "daily_next":        None,
            "daily_status":      "unknown",
            "store_available":   [True, True, True],
            "store_next":        [None, None, None],
            "store_status":      ["unknown", "unknown", "unknown"],
            "loyalty_available": True,
            "loyalty_next":      None,
            "loyalty_status":    "unknown",
        }

    ph = h[pid]

    # ── DAILY ─────────────────────────────────────────────────────────────────
    d_avail  = True
    d_next   = None
    d_status = ph["daily"].get("status", "unknown")

    lc_d = ph["daily"].get("last_claim")
    na_d = ph["daily"].get("next_available")

    if lc_d and datetime.fromisoformat(lc_d) >= lr:
        # Claimed since last reset
        d_avail  = False
        d_next   = format_time_until(nr)
        d_status = "claimed"
    elif na_d:
        nt = datetime.fromisoformat(na_d)
        if ist_now < nt:
            d_avail = False
            d_next  = format_time_until(nt)

    # ── STORE (same anchor logic as daily) ────────────────────────────────────
    s_avail  = [True,      True,      True]
    s_next   = [None,      None,      None]
    s_status = ["unknown", "unknown", "unknown"]

    for i in range(3):
        rk = f"reward_{i+1}"
        rd = ph["store"][rk]
        s_status[i] = rd.get("status", "unknown")
        lc_s = rd.get("last_claim")
        na_s = rd.get("next_available")

        if lc_s and datetime.fromisoformat(lc_s) >= lr:
            s_avail[i]  = False
            s_next[i]   = format_time_until(nr)
            s_status[i] = "claimed"
        elif na_s:
            nt = datetime.fromisoformat(na_s)
            if ist_now < nt:
                s_avail[i] = False
                s_next[i]  = format_time_until(nt)

    # ── LOYALTY (rolling 24h) ──────────────────────────────────────────────────
    l_avail  = True
    l_next   = None
    ld       = ph.get("loyalty", {})
    l_status = ld.get("status", "unknown")

    lc_l = ld.get("last_claim")
    na_l = ld.get("next_available")

    if lc_l:
        cd_end = datetime.fromisoformat(lc_l) + timedelta(hours=LOYALTY_COOLDOWN_HOURS)
        if ist_now < cd_end:
            l_avail  = False
            l_next   = format_time_until(cd_end)
            l_status = "claimed"
    if l_avail and na_l:
        nt = datetime.fromisoformat(na_l)
        if ist_now < nt:
            l_avail = False
            l_next  = format_time_until(nt)

    return {
        "daily_available":   d_avail,
        "daily_next":        d_next,
        "daily_status":      d_status,
        "store_available":   s_avail,
        "store_next":        s_next,
        "store_status":      s_status,
        "loyalty_available": l_avail,
        "loyalty_next":      l_next,
        "loyalty_status":    l_status,
    }


def all_claimable_on_cooldown(pid, has_loyalty):
    """True when every reward this player can claim is already on cooldown."""
    s = get_reward_status(pid)
    return (not s["daily_available"]
            and not any(s["store_available"])
            and (not has_loyalty or not s["loyalty_available"]))


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
                "profile.default_content_setting_values": {"images": 2, "notifications": 2, "popups": 2}
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
        time.sleep(2)
        if "just a moment" in driver.title.lower() or "verifying" in driver.page_source.lower():
            log("🛡️ Cloudflare detected — waiting...")
            time.sleep(5)
            try:
                driver.find_elements(By.XPATH, "//input[@type='checkbox']")[0].click()
                time.sleep(3)
            except:
                pass
            for _ in range(10):
                if "hub.vertigogames.co" in driver.current_url and "verifying" not in driver.page_source.lower():
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


def login_to_hub(driver, pid):
    log(f"🔐 Logging in: {pid}")
    try:
        driver.get("https://hub.vertigogames.co/daily-rewards")
        bypass_cloudflare(driver)
        time.sleep(1)
        accept_cookies(driver)

        login_clicked = False
        for sel in [
            "//button[contains(text(),'Login') or contains(text(),'Log in') or contains(text(),'Sign in')]",
            "//a[contains(text(),'Login') or contains(text(),'Log in')]",
            "//button[contains(@class,'btn') or contains(@class,'button')]",
        ]:
            try:
                for el in driver.find_elements(By.XPATH, sel):
                    if el.is_displayed() and el.is_enabled():
                        el.click()
                        login_clicked = True
                        break
                if login_clicked:
                    break
            except:
                continue

        if not login_clicked:
            log("❌ Login button not found")
            return False

        time.sleep(2)
        orig = driver.current_window_handle
        if len(driver.window_handles) > 1:
            for w in driver.window_handles:
                if w != orig:
                    driver.switch_to.window(w)
                    break
            time.sleep(1)

        id_field = None
        for sel in ["//input[@placeholder='Player ID' or @name='playerId']",
                    "//input[@type='text']",
                    "//input[contains(@placeholder,'ID')]"]:
            try:
                f = driver.find_element(By.XPATH, sel)
                if f.is_displayed():
                    f.clear()
                    f.send_keys(pid)
                    id_field = f
                    log(f"✅ ID entered: {pid}")
                    break
            except:
                continue

        if not id_field:
            log("❌ ID input not found")
            return False

        time.sleep(1)
        submitted = False
        for sel in ["//button[contains(text(),'Login') or contains(text(),'Submit') or contains(text(),'Continue')]",
                    "//button[@type='submit']"]:
            try:
                btn = driver.find_element(By.XPATH, sel)
                if btn.is_displayed() and btn.is_enabled():
                    btn.click()
                    submitted = True
                    break
            except:
                continue
        if not submitted:
            try:
                id_field.send_keys(Keys.RETURN)
            except:
                log("❌ Could not submit login")
                return False

        time.sleep(3)
        if len(driver.window_handles) > 1:
            driver.close()
            driver.switch_to.window(orig)
            time.sleep(1)

        time.sleep(2)
        src = driver.page_source.lower()
        if "daily-rewards" in driver.current_url or "claim" in src or pid.lower() in src:
            log("✅ Login successful")
            return True
        log("⚠️ Login uncertain — proceeding")
        return True
    except Exception as e:
        log(f"❌ Login error: {e}")
        return False


def close_popup(driver):
    try:
        for sel in [
            "//button[contains(text(),'Close') or contains(text(),'×') or contains(@class,'close')]",
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
        driver.execute_script("arguments[0].scrollIntoView({behavior:'smooth',block:'center'});", el)
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
                    for(let d=0;d<6;d++){
                        if(!c)break;
                        let ls=Array.from(c.querySelectorAll('*')).filter(e=>e.children.length===0);
                        let h=null,m=null,s=0;
                        for(let l of ls){
                            let t2=(l.innerText||'').trim().toLowerCase();
                            if(t2==='hours'||t2==='hour'){let sb=l.previousElementSibling;if(sb)h=getNum(sb);}
                            if(t2==='minutes'||t2==='minute'){let sb=l.previousElementSibling;if(sb)m=getNum(sb);}
                            if(t2==='seconds'||t2==='second'){let sb=l.previousElementSibling;if(sb)s=getNum(sb);}
                        }
                        if(h!==null&&m!==null)return{hours:h,minutes:m,seconds:s};
                        c=c.parentElement;
                    }
                }
            }
            return null;
        """)
        if res:
            td = timedelta(hours=res.get('hours', 0), minutes=res.get('minutes', 0), seconds=res.get('seconds', 0))
            log(f"🔍 Daily timer: {res['hours']}h {res['minutes']}m {res['seconds']}s")
            return td
    except Exception as e:
        log(f"⚠️ Daily timer JS error: {e}")
    return None


def detect_store_timers_js(driver):
    result = {1: None, 2: None, 3: None}
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
                    var own=Array.from(els[i].childNodes).filter(n=>n.nodeType===3).map(n=>n.textContent).join('').trim().toLowerCase();
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
                            var own2=Array.from(ch[j].childNodes).filter(n=>n.nodeType===3).map(n=>n.textContent).join('').trim();
                            if(own2.toLowerCase().includes('next in')&&own2.length<50){
                                return 'timer:'+(ch[j].innerText||ch[j].textContent||'').trim();
                            }
                        }
                        return 'timer:unknown';
                    }
                    if(d>=4){
                        var btns=node.querySelectorAll('button');
                        for(var b=0;b<btns.length;b++)if((btns[b].innerText||'').trim().toLowerCase()==='free')return 'free';
                    }
                }
                return 'free';
            }
            var r={};
            for(var k in anchors)r[k]=findCard(anchors[k]);
            return r;
        """)
        if res:
            NAMES = {1: "Gold", 2: "Cash", 3: "Luckyloon"}
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
                    log(f"🔍 Store {NAMES.get(n, n)}: Free")
    except Exception as e:
        log(f"⚠️ Store timer JS error: {e}")
    return result


def detect_loyalty_timer_js(driver):
    try:
        res = driver.execute_script("""
            var els=Array.from(document.querySelectorAll('*'));
            for(var i=0;i<els.length;i++){
                var own=Array.from(els[i].childNodes).filter(n=>n.nodeType===3).map(n=>n.textContent).join('').trim();
                if(own.toLowerCase().includes('next in')&&own.length<60)return own;
            }
            return null;
        """)
        if res:
            d = parse_timer_text(res)
            if d and d.total_seconds() > 60:
                log(f"🔍 Loyalty timer: {res}")
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
            if s2["daily_available"] and s2["daily_status"] not in ("cooldown_detected", "claimed"):
                update_claim_history(pid, "daily", attempted=True)

        driver.save_screenshot(f"daily_{pid}.png")
    except Exception as e:
        log(f"❌ Daily error: {e}")
    return claimed, False


def claim_store_rewards(driver, pid):
    """Returns (count_claimed, skip_flags[3]) where skip_flags[i]=True means card i already claimed."""
    s = get_reward_status(pid)
    skip_flags = [not a for a in s["store_available"]]

    if not any(s["store_available"]):
        log(f"⏩ All store rewards on cooldown")
        return 0, skip_flags

    log("🏪 Claiming Store Rewards...")
    claimed = 0

    try:
        driver.get("https://hub.vertigogames.co/store")
        bypass_cloudflare(driver)
        time.sleep(2)
        close_popup(driver)
        detect_page_cooldowns(driver, pid, "store")

        # Re-read after detection
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
                        if btn.text.strip().lower() == "free" and btn.is_displayed() and btn.is_enabled():
                            par = btn.find_element(By.XPATH, "./..")
                            if "next in" in par.text.lower():
                                continue
                            return btn
                    except:
                        continue
            except:
                pass
            return None

        # Phase 1: physical click (claims 1-2)
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
                # JS fallback
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

        # Mark unclaimed as attempted
        s3 = get_reward_status(pid)
        for i in range(claimed + 1, 4):
            if s3["store_available"][i-1] and s3["store_status"][i-1] not in ("cooldown_detected", "claimed"):
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
                for(let btn of document.querySelectorAll('button')){
                    let t=(btn.innerText||btn.textContent).trim().toLowerCase();
                    if(t==='claim'&&btn.offsetParent!==null&&!btn.disabled){
                        let pt=(btn.parentElement.innerText||btn.parentElement.textContent)||'';
                        if(!pt.includes('Delivered')){
                            btn.scrollIntoView({behavior:'smooth',block:'center',inline:'center'});
                            setTimeout(()=>btn.click(),300); return true;
                        }
                    }
                }
                return false;
            """)
            if ok:
                claimed += 1
                time.sleep(2.0)
                close_popup(driver)
            else:
                driver.execute_script(
                    "for(let i of document.querySelectorAll('div'))"
                    "{if(i.scrollWidth>i.clientWidth)i.scrollLeft+=400;}"
                )
                time.sleep(1)
        if claimed > 0:
            update_claim_history(pid, "progression", claimed_count=claimed)
    except:
        pass
    return claimed


def claim_loyalty_program(driver, pid):
    """Returns (count_claimed, was_skipped)."""
    s = get_reward_status(pid)
    if not s["loyalty_available"]:
        log(f"⏩ Loyalty on cooldown. Next: {s['loyalty_next']}")
        return 0, True

    log("🏆 Claiming Loyalty Program...")
    claimed = 0
    try:
        driver.get("https://hub.vertigogames.co/loyalty-program")
        bypass_cloudflare(driver)
        time.sleep(2)
        close_popup(driver)
        detect_page_cooldowns(driver, pid, "loyalty")

        s2 = get_reward_status(pid)
        if not s2["loyalty_available"]:
            log(f"⏩ Loyalty cooldown confirmed by page. Next: {s2['loyalty_next']}")
            return 0, True

        for attempt in range(5):
            ok = driver.execute_script("""
                for(let btn of document.querySelectorAll('button')){
                    let t=(btn.innerText||btn.textContent||'').trim().toLowerCase();
                    if(t!=='claim'&&t!=='free') continue;
                    if(!btn.offsetParent||btn.disabled) continue;
                    let node=btn.parentElement, cd=false;
                    for(let d=0;d<8&&node;d++){
                        if((node.innerText||'').toLowerCase().includes('next in')){cd=true;break;}
                        if(node.offsetHeight>600)break;
                        node=node.parentElement;
                    }
                    if(!cd){
                        btn.scrollIntoView({behavior:'smooth',block:'center'});
                        setTimeout(()=>btn.click(),300); return true;
                    }
                }
                return false;
            """)
            if ok:
                log(f"✅ Loyalty Claimed")
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
        "pid":            pid,
        "is_new":         is_new,
        "has_loyalty":    has_loyalty,
        "daily":          0,
        "store":          0,
        "progression":    0,
        "loyalty":        0,
        "daily_skipped":  False,
        "store_skipped":  [False, False, False],
        "loyalty_skipped":False,
        "skipped_all":    False,
        "status":         "Failed",
        "fail_reason":    None,
        "duration_s":     0,
        "possible":       0,
    }

    init_player_history(pid)

    # ── Smart skip: no browser needed if everything on cooldown ───────────────
    if all_claimable_on_cooldown(pid, has_loyalty):
        log(f"\n⏩ {pid} — all on cooldown, smart-skipping")
        snap = get_reward_status(pid)
        stats.update({
            "skipped_all":    True,
            "daily_skipped":  True,
            "store_skipped":  [True, True, True],
            "loyalty_skipped":has_loyalty,
            "status":         "All Skipped (Cooldown)",
        })
        stats["duration_s"] = int((get_ist_time() - start).total_seconds())
        return stats

    driver = None
    try:
        log(f"\n🚀 {pid}" + (" 🆕 NEW ID" if is_new else "") + f"  [{run_label}]")
        driver = create_driver()

        if not login_to_hub(driver, pid):
            stats["status"]     = "Login Failed"
            stats["fail_reason"] = "Could not authenticate"
            return stats

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
                log(f"⚠️ No progression, retrying...")
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

        # Status
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
            f"(D:{stats['daily']} S:{stats['store']} P:{stats['progression']} L:{stats['loyalty']})")

    except Exception as e:
        log(f"❌ Error: {e}")
        stats["status"]     = "Error"
        stats["fail_reason"] = str(e)[:120]
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass

    stats["duration_s"] = int((get_ist_time() - start).total_seconds())

    # Snapshot next-available for email display
    snap = get_reward_status(pid)
    stats["store_next"]   = snap["store_next"]
    stats["daily_next"]   = snap["daily_next"]
    stats["loyalty_next"] = snap.get("loyalty_next")

    return stats


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 10 — EMAIL (Premium Dark Dashboard)
# ═══════════════════════════════════════════════════════════════════════════════

_CSS = """
*{box-sizing:border-box;margin:0;padding:0;}
/* ── Base — dark but readable ── */
body{background:#111827;font-family:'Segoe UI',Arial,sans-serif;padding:20px;color:#e2e8f0;}
.wrap{max-width:980px;margin:0 auto;}
/* ── Hero ── */
.hero{background:linear-gradient(135deg,#1e2a3a 0%,#111827 100%);border:1px solid #374151;border-radius:14px;padding:26px 30px;margin-bottom:14px;}
.badge{display:inline-block;padding:4px 14px;border-radius:20px;font-size:11px;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;margin-bottom:14px;}
.bp{background:#14532d;color:#86efac;border:1px solid #16a34a;}
.bb{background:#1e3a5f;color:#93c5fd;border:1px solid #2563eb;}
.bm{background:#431407;color:#fdba74;border:1px solid #c2410c;}
.hero-grid{display:flex;justify-content:space-between;align-items:center;gap:20px;flex-wrap:wrap;}
.hero-left h1{font-size:20px;font-weight:700;color:#f8fafc;margin-bottom:6px;}
.hero-left p{font-size:12px;color:#94a3b8;}
.hero-nums{display:flex;gap:28px;}
.hnum{text-align:center;}
.hv{display:block;font-size:34px;font-weight:800;line-height:1;}
.g{color:#4ade80;}.b{color:#60a5fa;}.a{color:#fbbf24;}
.hl{font-size:10px;color:#94a3b8;text-transform:uppercase;letter-spacing:.8px;margin-top:4px;}
/* ── KPI Row ── */
.kpi-row{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:14px;}
.kpi{background:#1f2937;border:1px solid #374151;border-radius:10px;padding:16px 18px;}
.kl{font-size:10px;color:#94a3b8;text-transform:uppercase;letter-spacing:.8px;margin-bottom:8px;}
.kv{font-size:26px;font-weight:700;color:#f8fafc;}
.kv span{font-size:13px;color:#6b7280;}
.ks{font-size:11px;color:#9ca3af;margin-top:4px;}
.du{color:#4ade80;font-weight:600;}.dd{color:#f87171;font-weight:600;}.de{color:#9ca3af;}
.pb{background:#374151;border-radius:3px;height:4px;margin-top:10px;}
.pf{height:4px;border-radius:3px;}
.pg{background:#22c55e;}.pb2{background:#3b82f6;}.pp{background:#a855f7;}.pa{background:#f59e0b;}
/* ── Strip ── */
.strip{background:#1f2937;border:1px solid #374151;border-radius:8px;padding:12px 18px;margin-bottom:14px;display:flex;gap:20px;flex-wrap:wrap;align-items:center;}
.si{font-size:12px;color:#9ca3af;}
.si strong{color:#e2e8f0;}
/* ── Table — FIX: outer wrapper does NOT clip so inner scroll works ── */
.tbx{border:1px solid #374151;border-radius:10px;margin-bottom:14px;overflow:visible;}
.tbh{background:#1f2937;padding:13px 18px;border-bottom:1px solid #374151;font-size:13px;font-weight:600;color:#e2e8f0;display:flex;justify-content:space-between;align-items:center;border-radius:10px 10px 0 0;}
.tbh small{font-size:11px;color:#9ca3af;}
/* ── Scrollable table container ── */
.tsc{overflow-x:auto;-webkit-overflow-scrolling:touch;border-radius:0 0 10px 10px;}
table{width:100%;border-collapse:collapse;font-size:12px;min-width:780px;background:#1f2937;}
th{background:#111827;color:#9ca3af;font-size:10px;text-transform:uppercase;letter-spacing:.8px;padding:10px 10px;text-align:center;border-bottom:1px solid #374151;white-space:nowrap;}
th.idh{text-align:left;padding-left:16px;min-width:185px;}
td{padding:10px 10px;text-align:center;border-bottom:1px solid #1f2937;vertical-align:middle;color:#e2e8f0;}
td.idc{text-align:left;padding-left:16px;font-family:'Courier New',monospace;font-size:11px;color:#60a5fa;white-space:nowrap;font-weight:600;}
tr:last-child td{border-bottom:none;}
tr.rs{background:#14271c;}
tr.rp{background:#292113;}
tr.rf{background:#2d1515;}
tr.rk{background:#171e2b;}
.ic-ok{color:#4ade80;font-size:15px;}.ic-cd{color:#fbbf24;font-size:15px;}.ic-fl{color:#f87171;font-size:15px;}
.ic-lk{color:#6b7280;font-size:15px;}.ic-pd{color:#60a5fa;font-size:15px;}.ic-na{color:#4b5563;font-size:15px;}
.sb{display:inline-block;padding:3px 9px;border-radius:10px;font-size:10px;font-weight:700;white-space:nowrap;}
.ss{background:#14532d;color:#86efac;}.sp{background:#422006;color:#fdba74;}
.sf{background:#450a0a;color:#fca5a5;}.sk{background:#1f2937;color:#6b7280;}
.sn{background:#1f2937;color:#9ca3af;}
/* ── Detail cards ── */
.dcs{margin-bottom:14px;}
.dct{font-size:11px;font-weight:600;color:#94a3b8;text-transform:uppercase;letter-spacing:.8px;margin-bottom:10px;padding:0 4px;}
.dc{background:#1f2937;border:1px solid #374151;border-radius:8px;padding:14px 18px;margin-bottom:10px;}
.dcf{border-left:3px solid #f87171;}.dcp{border-left:3px solid #fbbf24;}
.dcid{font-family:'Courier New',monospace;font-size:13px;color:#60a5fa;font-weight:700;margin-bottom:10px;}
.dcr{display:flex;justify-content:space-between;font-size:12px;padding:6px 0;border-bottom:1px solid #374151;}
.dcr:last-child{border-bottom:none;}
.dcl{color:#94a3b8;font-weight:500;}.dcv{color:#e2e8f0;}
.dce{color:#f87171;font-size:11px;font-style:italic;margin-bottom:8px;}
/* ── Footer ── */
.foot{background:#111827;border:1px solid #374151;border-radius:10px;padding:20px 24px;}
.ft{font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:.8px;margin-bottom:12px;font-weight:600;}
.nr-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:14px;}
.nr{background:#1f2937;border:1px solid #374151;border-radius:6px;padding:8px 12px;text-align:center;}
.nrl{font-size:10px;color:#94a3b8;margin-bottom:4px;font-weight:500;}
.nrt{font-size:12px;font-family:'Courier New',monospace;color:#e2e8f0;}
.nr.nrp .nrt{color:#4ade80;font-weight:700;}
.leg{display:flex;flex-wrap:wrap;gap:12px;padding-top:12px;border-top:1px solid #374151;margin-top:12px;}
.leg span{font-size:11px;color:#9ca3af;}
.ver{text-align:center;font-size:10px;color:#6b7280;margin-top:12px;padding-top:10px;border-top:1px solid #374151;}
"""


def _badge_cls(run_label):
    if run_label == "Primary Run": return "bp"
    if run_label == "Manual Run":  return "bm"
    return "bb"

def _row_cls(status):
    return {"Success":"rs","Partial":"rp","Login Failed":"rf",
            "Error":"rf","Failed":"rf"}.get(status, "rk")

def _sb_html(status):
    m = {
        "Success":              ("ss","✅ Success"),
        "Partial":              ("sp","⚠️ Partial"),
        "All Skipped (Cooldown)":("sk","⏩ Skipped"),
        "No Rewards":           ("sn","⏳ No Rewards"),
        "Login Failed":         ("sf","🔐 Login Failed"),
        "Error":                ("sf","❌ Error"),
        "Failed":               ("sf","❌ Failed"),
    }
    cls, lbl = m.get(status, ("sk", status))
    return f'<span class="sb {cls}">{lbl}</span>'

def _delta_html(cur, prev, unit=""):
    if prev is None or prev == 0 and cur == 0:
        return '<span class="de">first run</span>'
    d = cur - prev
    if d > 0:  return f'<span class="du">▲{d}{unit}</span>'
    if d < 0:  return f'<span class="dd">▼{abs(d)}{unit}</span>'
    return '<span class="de">✓ same</span>'

def _pbar(pct, cls):
    p = min(int(pct), 100)
    return f'<div class="pb"><div class="pf {cls}" style="width:{p}%;"></div></div>'

def _drow(lbl, val):
    return f'<div class="dcr"><span class="dcl">{lbl}</span><span class="dcv">{val}</span></div>'


def build_email(results, run_label, run_index, job_start, meta):
    ist_now  = get_ist_time()
    dur_s    = int((ist_now - job_start).total_seconds())
    dur_str  = f"{dur_s // 60}m {dur_s % 60}s"
    n        = len(results)

    td   = sum(r["daily"]       for r in results)
    ts   = sum(r["store"]       for r in results)
    tp   = sum(r["progression"] for r in results)
    tl   = sum(r.get("loyalty", 0) for r in results)
    tall = td + ts + tp + tl

    tp_all  = sum(r.get("possible", 0) for r in results)
    eff     = 100.0 if tp_all == 0 else round((td + ts + tl) / tp_all * 100, 1)
    l_enrl  = sum(1 for r in results if r.get("has_loyalty"))
    skip_ct = sum(1 for r in results if r.get("skipped_all"))
    act_ct  = n - skip_ct

    streak  = meta.get("streak", {})
    s_cur   = streak.get("current", 0)
    s_best  = streak.get("best", 0)
    lr      = meta.get("last_run") or {}
    lr_d    = lr.get("per_type", {}).get("daily")
    lr_s    = lr.get("per_type", {}).get("store")
    lr_l    = lr.get("per_type", {}).get("loyalty")
    lr_tot  = lr.get("total_claimed")
    lr_eff  = lr.get("efficiency")

    timed   = [(r["pid"], r.get("duration_s", 0)) for r in results if not r.get("skipped_all")]
    avg_t   = round(sum(t for _, t in timed) / len(timed), 1) if timed else 0
    slowest = max(timed, key=lambda x: x[1]) if timed else None

    bc   = _badge_cls(run_label)
    bi   = {"Primary Run":"🟢","Manual Run":"🔧"}.get(run_label, "🔵")

    d_pct  = min(round(td / n * 100) if n else 0, 100)
    s_pct  = min(round(ts / (n*3) * 100) if n else 0, 100)
    l_pct  = min(round(tl / l_enrl * 100) if l_enrl else 0, 100)
    e_pct  = min(int(eff), 100)

    # Pre-build delta strings
    dlt_d   = _delta_html(td,   lr_d)
    dlt_s   = _delta_html(ts,   lr_s)
    dlt_l   = _delta_html(tl,   lr_l)
    dlt_tot = _delta_html(tall, lr_tot)
    dlt_eff = _delta_html(round(eff, 1), round(lr_eff, 1) if lr_eff is not None else None, "%")

    # ── Build table rows ──────────────────────────────────────────────────────
    table_rows   = ""
    detail_cards = ""
    has_details  = False

    for r in results:
        pid    = r["pid"]
        status = r["status"]
        rc     = _row_cls(status)
        new_mark = " 🆕" if r.get("is_new") else ""

        # Daily cell
        if r["daily_skipped"]:
            dn = r.get("daily_next") or "next reset"
            dc = f'<span class="ic-cd" title="Next: {dn}">⏰</span>'
        elif r["daily"] > 0:
            dc = '<span class="ic-ok">✅</span>'
        elif status in ("Login Failed","Error","Failed"):
            dc = '<span class="ic-fl">❌</span>'
        else:
            dc = '<span class="ic-pd">⏳</span>'

        # Map claimed count to which cards were actually free
        sk = r.get("store_skipped", [False, False, False])
        if not isinstance(sk, list) or len(sk) < 3:
            sk = [False, False, False]
        free_slots = [i for i in range(3) if not sk[i]]
        claimed_cards = [False, False, False]
        for j, idx in enumerate(free_slots):
            if j < r["store"]:
                claimed_cards[idx] = True

        sn_list = r.get("store_next") or [None, None, None]
        sc_html = ""
        for i in range(3):
            sep = ' style="border-left:2px solid #1a2d4a;"' if i == 0 else ''
            if sk[i]:
                nxt = (sn_list[i] if sn_list and len(sn_list) > i else None) or "next reset"
                cell = f'<span class="ic-cd" title="Next: {nxt}">⏰</span>'
            elif claimed_cards[i]:
                cell = '<span class="ic-ok">✅</span>'
            elif status in ("Login Failed","Error","Failed"):
                cell = '<span class="ic-fl">❌</span>'
            else:
                cell = '<span class="ic-pd">⏳</span>'
            sc_html += f'<td{sep}>{cell}</td>'

        # Progression
        pc = ('<span class="ic-ok">✅</span>' if r["progression"] > 0
              else '<span class="ic-fl">❌</span>' if status in ("Login Failed","Error","Failed")
              else '<span class="ic-lk" title="Awaiting grenades/bullets">⏳</span>')

        # Loyalty
        if not r.get("has_loyalty"):
            lc = '<span class="ic-na">—</span>'
        elif r.get("loyalty_skipped"):
            ln = r.get("loyalty_next") or "24h"
            lc = f'<span class="ic-cd" title="Next: {ln}">⏰</span>'
        elif r.get("loyalty", 0) > 0:
            lc = '<span class="ic-ok">✅</span>'
        elif status in ("Login Failed","Error","Failed"):
            lc = '<span class="ic-fl">❌</span>'
        else:
            lc = '<span class="ic-lk" title="Awaiting LP from purchases">🔒</span>'

        # Time
        d_s = r.get("duration_s", 0)
        tm  = (f'<span style="color:#6e7681;">{d_s//60}m{d_s%60}s</span>'
               if d_s and not r.get("skipped_all")
               else '<span class="ic-na">—</span>')

        table_rows += (
            f'<tr class="{rc}">'
            f'<td class="idc">{pid}{new_mark}</td>'
            f'<td style="border-left:2px solid #1a3a24;">{dc}</td>'
            f'{sc_html}'
            f'<td style="border-left:2px solid #2d1f0d;">{pc}</td>'
            f'<td style="border-left:2px solid #271a45;">{lc}</td>'
            f'<td style="border-left:2px solid #1c2128;">{tm}</td>'
            f'<td>{_sb_html(status)}</td>'
            f'</tr>'
        )

        # Detail cards for failed / partial only
        if status in ("Failed","Partial","Login Failed","Error","No Rewards"):
            has_details = True
            dc_cls = "dcf" if status in ("Error","Failed","Login Failed") else "dcp"
            err_html = (f'<div class="dce">⚠️ {r["fail_reason"]}</div>'
                        if r.get("fail_reason") else "")

            d_val = ("✅ Claimed" if r["daily"] > 0
                     else f'⏰ {r.get("daily_next") or "On cooldown"}' if r["daily_skipped"]
                     else "⏳ Not claimed")

            SNAMES = ["🥇 Gold","💵 Cash","🍀 Luckyloon"]
            s_rows = ""
            for i in range(3):
                if claimed_cards[i]:
                    sv = "✅ Claimed"
                elif sk[i]:
                    nxt = (sn_list[i] if sn_list and len(sn_list) > i else None) or "On cooldown"
                    sv = f"⏰ {nxt}"
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
                f'<div class="dcid">{pid}{new_mark}</div>'
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

    # ── Run schedule footer ───────────────────────────────────────────────────
    sched = next_scheduled_runs_ist()
    nr_html = ""
    for i, (lbl, t) in enumerate(sched):
        cls = ' class="nr nrp"' if i == 0 else ' class="nr"'
        nr_html += f'<div{cls}><div class="nrl">{lbl}</div><div class="nrt">{t}</div></div>'

    slowest_str = f"{slowest[0][:8]}… ({slowest[1]}s)" if slowest else "—"

    # ── Assemble HTML ─────────────────────────────────────────────────────────
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
        f"<div class='hnum'><span class='hv g'>{tall}</span><span class='hl'>Total Claimed</span></div>"
        f"<div class='hnum'><span class='hv b'>{eff:.1f}%</span><span class='hl'>Efficiency</span></div>"
        f"<div class='hnum'><span class='hv a'>🔥 {s_cur}</span><span class='hl'>Day Streak</span></div>"
        f"</div></div></div>"

        # KPI Row
        f"<div class='kpi-row'>"
        f"<div class='kpi'><div class='kl'>🎁 Daily</div>"
        f"<div class='kv'>{td}<span>/{n}</span></div>"
        f"<div class='ks'>{dlt_d}</div>"
        f"{_pbar(d_pct, 'pg')}</div>"

        f"<div class='kpi'><div class='kl'>🏪 Store</div>"
        f"<div class='kv'>{ts}<span>/{n*3}</span></div>"
        f"<div class='ks'>{dlt_s}</div>"
        f"{_pbar(s_pct, 'pb2')}</div>"

        f"<div class='kpi'><div class='kl'>🎯 Progression</div>"
        f"<div class='kv'>{tp}<span> items</span></div>"
        f"<div class='ks'>Variable — grenade dependent</div>"
        f"{_pbar(min(tp * 10, 100), 'pp')}</div>"

        f"<div class='kpi'><div class='kl'>🏆 Loyalty</div>"
        f"<div class='kv'>{tl}<span>/{l_enrl}</span></div>"
        f"<div class='ks'>{dlt_l}</div>"
        f"{_pbar(l_pct, 'pa')}</div>"
        f"</div>"

        # Strip
        f"<div class='strip'>"
        f"<span class='si'>⏱️ <strong>{dur_str}</strong> total</span>"
        f"<span class='si'>👤 Avg <strong>{avg_t}s</strong>/ID</span>"
        f"<span class='si'>🐢 Slowest: <strong>{slowest_str}</strong></span>"
        f"<span class='si'>🔥 Best streak: <strong>{s_best} days</strong></span>"
        f"<span class='si'>📊 Efficiency: <strong>{eff:.1f}%</strong> {dlt_eff}</span>"
        f"<span class='si'>📦 Total all-time: <strong>{tall}</strong> claimed this run {dlt_tot}</span>"
        f"</div>"

        # Table
        f"<div class='tbx'>"
        f"<div class='tbh'><span>👥 All {n} Player IDs</span>"
        f"<small>⏩ = already claimed &nbsp;|&nbsp; 🆕 = new ID this run</small></div>"
        f"<div class='tsc'><table>"
        f"<tr>"
        f"<th class='idh'>Player ID</th>"
        f"<th style='border-left:2px solid #1a3a24;'>🎁 Daily</th>"
        f"<th style='border-left:2px solid #1a2d4a;'>🥇 Gold</th>"
        f"<th>💵 Cash</th>"
        f"<th>🍀 Lucky</th>"
        f"<th style='border-left:2px solid #2d1f0d;'>🎯 Prog</th>"
        f"<th style='border-left:2px solid #271a45;'>🏆 Loyal</th>"
        f"<th style='border-left:2px solid #1c2128;'>⏱️ Time</th>"
        f"<th>Status</th>"
        f"</tr>"
        f"{table_rows}"
        f"</table></div></div>"

        # Detail cards (only when there are issues)
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
        f"<span>📌 Daily &amp; Store reset at 5:30 AM IST daily</span>"
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


def send_email(html_body, subject):
    if not (SMTP_SERVER and SMTP_USERNAME and SMTP_PASSWORD and SMTP_FROM and SMTP_TO):
        log("⚠️ Email env vars missing — skipping email")
        return
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = SMTP_FROM
        msg["To"]      = SMTP_TO
        msg.attach(MIMEText(html_body, "html"))
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as server:
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM, [SMTP_TO], msg.as_string())
        log("📧 Email sent successfully")
    except Exception as e:
        log(f"⚠️ Email failed: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 11 — MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    job_start = get_ist_time()
    log("=" * 60)
    log(f"CS HUB AUTO-CLAIMER {VERSION}")
    log("=" * 60)

    run_label, run_index = determine_run_context()
    ist_now = get_ist_time()
    log(f"📋 Run Context: {run_label}  |  {ist_now.strftime('%d-%b %H:%M IST')}")

    # Load state files
    meta = load_bot_meta()

    # Read players
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

    # Process all players
    results = []
    for pid, has_loyalty in players:
        new_id = is_new_id(pid, meta)
        mark_id_seen(pid, meta)
        r = process_player(pid, has_loyalty, new_id, run_label)
        results.append(r)
        time.sleep(3)

    # ── Compute metrics ───────────────────────────────────────────────────────
    job_end  = get_ist_time()
    dur_s    = int((job_end - job_start).total_seconds())
    td       = sum(r["daily"]       for r in results)
    ts       = sum(r["store"]       for r in results)
    tp       = sum(r["progression"] for r in results)
    tl       = sum(r.get("loyalty", 0) for r in results)
    tall     = td + ts + tp + tl
    tp_all   = sum(r.get("possible", 0) for r in results)
    eff      = 100.0 if tp_all == 0 else round((td + ts + tl) / tp_all * 100, 1)

    timed    = [(r["pid"], r.get("duration_s", 0)) for r in results if not r.get("skipped_all")]
    avg_t    = round(sum(t for _, t in timed) / len(timed), 1) if timed else 0
    slowest  = max(timed, key=lambda x: x[1]) if timed else None

    log(f"\n{'='*60}")
    log(f"Run complete: {tall} claimed | {eff:.1f}% efficiency | {dur_s}s total")
    log(f"  Daily:{td}  Store:{ts}  Prog:{tp}  Loyalty:{tl}")
    log(f"{'='*60}")

    # ── Check if all-ok today (for streak) ────────────────────────────────────
    n_players = len(players)
    all_ok = (
        td == n_players and
        ts == n_players * 3 and
        tl == sum(1 for _, h in players if h)
    )
    update_streak_day_level(meta, all_ok)

    # ── Update last_run in meta ───────────────────────────────────────────────
    prev_run = meta.get("last_run")
    meta["last_run"] = {
        "timestamp":          job_end.isoformat(),
        "run_label":          run_label,
        "total_claimed":      tall,
        "efficiency":         eff,
        "duration_seconds":   dur_s,
        "per_type":           {"daily": td, "store": ts, "progression": tp, "loyalty": tl},
        "slowest_player":     slowest[0] if slowest else None,
        "avg_time_per_player":avg_t,
    }
    # Temporarily restore prev_run for the email delta calculation
    meta_for_email = dict(meta)
    meta_for_email["last_run"] = prev_run  # email uses prev run for deltas

    save_bot_meta(meta)

    # ── Build and send email ──────────────────────────────────────────────────
    html_body = build_email(results, run_label, run_index, job_start, meta_for_email)

    # Subject: Option 1 format
    ok_count  = sum(1 for r in results if r["status"] == "Success")
    ist_label = job_start.strftime('%d-%b %I:%M %p')
    streak_d  = meta["streak"].get("current", 0)
    subject = (
        f"🎮 CS Hub | {ist_label} IST | {ok_count}/{n_players} IDs ✅ "
        f"| {eff:.1f}% Efficiency | Day {streak_d} 🔥"
    )

    send_email(html_body, subject)

    log("✅ All done.")


if __name__ == "__main__":
    main()
