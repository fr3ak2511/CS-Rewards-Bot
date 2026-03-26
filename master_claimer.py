import csv
import time
import os
import json
import smtplib
import re
import threading
import concurrent.futures
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import undetected_chromedriver as uc
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, StaleElementReferenceException

PLAYER_ID_FILE = "players.csv"
HISTORY_FILE = "claim_history.json"
HEADLESS = True

# Daily tracking constants
DAILY_RESET_HOUR_IST = 5
DAILY_RESET_MINUTE_IST = 35  # Shifted to 5:35 AM IST to bypass server boot
EXPECTED_STORE_PER_PLAYER = 3
STORE_COOLDOWN_HOURS = 24
PROGRESSION_DEPENDS_ON_STORE = True
MAX_CONCURRENT_BROWSERS = 3  # Safe limit for GitHub Actions

# Thread lock for safe JSON file writes during concurrent processing
history_lock = threading.Lock()

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

# IST timezone helper functions
def get_ist_time():
    """Get current time in IST (UTC+5:30)"""
    utc_now = datetime.utcnow()
    ist_offset = timedelta(hours=5, minutes=30)
    return utc_now + ist_offset

def get_next_daily_reset():
    """Get next daily reset time"""
    ist_now = get_ist_time()
    if ist_now.hour < DAILY_RESET_HOUR_IST or (ist_now.hour == DAILY_RESET_HOUR_IST and ist_now.minute < DAILY_RESET_MINUTE_IST):
        next_reset = ist_now.replace(hour=DAILY_RESET_HOUR_IST, minute=DAILY_RESET_MINUTE_IST, second=0, microsecond=0)
    else:
        next_reset = ist_now.replace(hour=DAILY_RESET_HOUR_IST, minute=DAILY_RESET_MINUTE_IST, second=0, microsecond=0) + timedelta(days=1)
    return next_reset

def format_time_until_reset(next_reset):
    """Format time remaining until next reset"""
    ist_now = get_ist_time()
    delta = next_reset - ist_now
    if delta.total_seconds() < 0:
        return "Available now"
    hours, remainder = divmod(int(delta.total_seconds()), 3600)
    minutes, _ = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}h {minutes}m"
    else:
        return f"{minutes}m"

def parse_timer_text(timer_text):
    """Parse timer text into timedelta - handles multiple formats"""
    try:
        hours = 0
        minutes = 0
        seconds = 0
        
        hour_match = re.search(r'(\d+)\s*h', timer_text, re.IGNORECASE)
        if hour_match:
            hours = int(hour_match.group(1))
        
        min_match = re.search(r'(\d+)\s*m', timer_text, re.IGNORECASE)
        if min_match:
            minutes = int(min_match.group(1))
        
        countdown_match = re.search(r'(\d{1,2}):(\d{2}):(\d{2})', timer_text)
        if countdown_match:
            hours = int(countdown_match.group(1))
            minutes = int(countdown_match.group(2))
            seconds = int(countdown_match.group(3))
        
        if hours > 0 or minutes > 0 or seconds > 0:
            return timedelta(hours=hours, minutes=minutes, seconds=seconds)
        
        return None
    except:
        return None

# Claim History Management (Thread Safe)
def load_claim_history():
    """Load claim history safely"""
    with history_lock:
        try:
            if os.path.exists(HISTORY_FILE):
                with open(HISTORY_FILE, 'r') as f:
                    return json.load(f)
            else:
                return {}
        except Exception as e:
            log(f"⚠️ Error loading history: {e}")
            return {}

def save_claim_history(history):
    """Save claim history safely"""
    with history_lock:
        try:
            with open(HISTORY_FILE, 'w') as f:
                json.dump(history, f, indent=2)
        except Exception as e:
            log(f"⚠️ Error saving history: {e}")

def init_player_history(player_id):
    """Initialize history structure for a player if not exists"""
    history = load_claim_history()
    
    if player_id not in history:
        history[player_id] = {
            "daily": {"last_claim": None, "next_available": None, "status": "unknown"},
            "store": {
                "reward_1": {"last_claim": None, "next_available": None, "status": "unknown"},
                "reward_2": {"last_claim": None, "next_available": None, "status": "unknown"},
                "reward_3": {"last_claim": None, "next_available": None, "status": "unknown"}
            },
            "progression": {"last_claim": None, "last_count": 0},
            "loyalty": {"last_claim": None, "last_count": 0}
        }
        save_claim_history(history)
    
    return history

def update_claim_history(player_id, reward_type, claimed_count=0, reward_index=None, detected_cooldown=None, attempted=False):
    """Update claim history for a player safely."""
    history = init_player_history(player_id)
    ist_now = get_ist_time()
    
    if reward_type == "daily":
        if claimed_count > 0:
            history[player_id]["daily"]["last_claim"] = ist_now.isoformat()
            next_reset = get_next_daily_reset()
            history[player_id]["daily"]["next_available"] = next_reset.isoformat()
            history[player_id]["daily"]["status"] = "claimed"
            log(f"📝 Updated history: Daily claimed, next at {next_reset.strftime('%I:%M %p')}")
        elif detected_cooldown is not None:
            next_available = ist_now + detected_cooldown
            history[player_id]["daily"]["next_available"] = next_available.isoformat()
            history[player_id]["daily"]["status"] = "cooldown_detected"
        elif attempted and claimed_count == 0:
            last_claim = history[player_id]["daily"].get("last_claim")
            if last_claim:
                last_time = datetime.fromisoformat(last_claim)
                cooldown_end = last_time + timedelta(hours=24)
                if ist_now < cooldown_end:
                    save_claim_history(history)
                    return history
            history[player_id]["daily"]["status"] = "unavailable"
    
    elif reward_type == "store" and reward_index is not None:
        reward_key = f"reward_{reward_index}"
        if claimed_count > 0:
            history[player_id]["store"][reward_key]["last_claim"] = ist_now.isoformat()
            next_available = ist_now + timedelta(hours=STORE_COOLDOWN_HOURS)
            history[player_id]["store"][reward_key]["next_available"] = next_available.isoformat()
            history[player_id]["store"][reward_key]["status"] = "claimed"
        elif detected_cooldown is not None:
            next_available = ist_now + detected_cooldown
            history[player_id]["store"][reward_key]["next_available"] = next_available.isoformat()
            history[player_id]["store"][reward_key]["status"] = "cooldown_detected"
        elif attempted and claimed_count == 0:
            last_claim = history[player_id]["store"][reward_key].get("last_claim")
            if last_claim:
                last_time = datetime.fromisoformat(last_claim)
                cooldown_end = last_time + timedelta(hours=STORE_COOLDOWN_HOURS)
                if ist_now < cooldown_end:
                    save_claim_history(history)
                    return history
            history[player_id]["store"][reward_key]["status"] = "unavailable"
    
    elif reward_type == "progression" and claimed_count > 0:
        history[player_id]["progression"]["last_claim"] = ist_now.isoformat()
        history[player_id]["progression"]["last_count"] = claimed_count
        log(f"📝 Updated history: Progression claimed {claimed_count}")

    elif reward_type == "loyalty" and claimed_count > 0:
        if "loyalty" not in history[player_id]: history[player_id]["loyalty"] = {}
        history[player_id]["loyalty"]["last_claim"] = ist_now.isoformat()
        history[player_id]["loyalty"]["last_count"] = claimed_count
        log(f"📝 Updated history: Loyalty claimed {claimed_count}")
    
    save_claim_history(history)
    return history

def detect_daily_timer_js(driver):
    try:
        result = driver.execute_script("""
            function getNum(el) {
                return parseInt((el.innerText || el.textContent || '').trim()) || 0;
            }
            let allLeafEls = Array.from(document.querySelectorAll('*')).filter(e => e.children.length === 0);
            for (let el of allLeafEls) {
                let txt = (el.innerText || '').trim().toLowerCase();
                if (txt === 'next reward in' || txt === 'next in' || txt === 'next reward') {
                    let container = el.parentElement;
                    for (let depth = 0; depth < 6; depth++) {
                        if (!container) break;
                        let leaves = Array.from(container.querySelectorAll('*')).filter(e => e.children.length === 0);
                        let h = null, m = null, s = 0;
                        for (let leaf of leaves) {
                            let t = (leaf.innerText || '').trim().toLowerCase();
                            if (t === 'hours' || t === 'hour') {
                                let sib = leaf.previousElementSibling;
                                if (sib) h = getNum(sib);
                            }
                            if (t === 'minutes' || t === 'minute') {
                                let sib = leaf.previousElementSibling;
                                if (sib) m = getNum(sib);
                            }
                            if (t === 'seconds' || t === 'second') {
                                let sib = leaf.previousElementSibling;
                                if (sib) s = getNum(sib);
                            }
                        }
                        if (h !== null && m !== null) return { hours: h, minutes: m, seconds: s, method: 'label-search' };
                        container = container.parentElement;
                    }
                }
            }
            let hourLabels = allLeafEls.filter(e => (e.innerText || '').trim().toLowerCase() === 'hours');
            for (let hl of hourLabels) {
                let parent = hl.parentElement;
                let grandparent = parent ? parent.parentElement : null;
                if (!grandparent) continue;
                let cards = Array.from(grandparent.children);
                let h = null, m = null, s = 0;
                for (let card of cards) {
                    let labels = Array.from(card.querySelectorAll('*')).filter(e => e.children.length === 0);
                    let hasHour = labels.some(e => (e.innerText||'').trim().toLowerCase() === 'hours');
                    let hasMin  = labels.some(e => (e.innerText||'').trim().toLowerCase() === 'minutes');
                    let hasSec  = labels.some(e => (e.innerText||'').trim().toLowerCase() === 'seconds');
                    let numEl   = labels.find(e => !isNaN(parseInt(e.innerText)) && e.innerText.trim().length <= 3);
                    if (hasHour && numEl) h = getNum(numEl);
                    if (hasMin  && numEl) m = getNum(numEl);
                    if (hasSec  && numEl) s = getNum(numEl);
                }
                if (h !== null && m !== null) return { hours: h, minutes: m, seconds: s, method: 'sibling-card' };
            }
            return null;
        """)
        if result:
            h, m, s = result.get('hours', 0), result.get('minutes', 0), result.get('seconds', 0)
            return timedelta(hours=h, minutes=m, seconds=s)
    except Exception as e:
        pass
    return None

def detect_store_timers_js(driver):
    result = {1: None, 2: None, 3: None}
    try:
        card_results = driver.execute_script("""
            var rewardAnchors = {
                1: ['gold (daily)', 'gold(daily)', '5 gold', 'gold daily'],
                2: ['cash (daily)', 'cash(daily)', '500 cash', 'cash daily'],
                3: ['luckyloon (daily)', 'luckyloon(daily)', '10 luckyloon', 'luckyloon daily']
            };
            function findCardStatus(anchorKeywords) {
                var allEls = Array.from(document.querySelectorAll('*'));
                var labelEl = null;
                for (var i = 0; i < allEls.length; i++) {
                    var el = allEls[i];
                    var ownText = Array.from(el.childNodes)
                        .filter(function(n) { return n.nodeType === 3; })
                        .map(function(n) { return n.textContent; })
                        .join('').trim().toLowerCase();
                    var matched = anchorKeywords.some(function(kw) { return ownText.includes(kw); });
                    if (matched && ownText.length < 35) { labelEl = el; break; }
                }
                if (!labelEl) return 'not_found';
                var node = labelEl;
                for (var depth = 0; depth < 15; depth++) {
                    node = node.parentElement;
                    if (!node || node === document.body || node === document.documentElement) break;
                    var nodeText = (node.innerText || node.textContent || '').toLowerCase();
                    if (nodeText.includes('next in')) {
                        var children = Array.from(node.querySelectorAll('*'));
                        for (var j = 0; j < children.length; j++) {
                            var child = children[j];
                            var childOwn = Array.from(child.childNodes)
                                .filter(function(n) { return n.nodeType === 3; })
                                .map(function(n) { return n.textContent; })
                                .join('').trim();
                            if (childOwn.toLowerCase().includes('next in') && childOwn.length < 50) {
                                var fullText = (child.innerText || child.textContent || '').trim();
                                if (fullText.length > 5 && fullText.length < 50) return 'timer:' + fullText;
                            }
                        }
                        return 'timer:unknown';
                    }
                    if (depth >= 4) {
                        var btns = node.querySelectorAll('button');
                        for (var b = 0; b < btns.length; b++) {
                            if ((btns[b].innerText || '').trim().toLowerCase() === 'free') return 'free';
                        }
                    }
                }
                return 'free';
            }
            var results = {};
            for (var cardNum in rewardAnchors) { results[cardNum] = findCardStatus(rewardAnchors[cardNum]); }
            return results;
        """)

        if not card_results: return result
        for card_num_str, status in card_results.items():
            card_num = int(card_num_str)
            if status.startswith('timer:'):
                timer_text = status[6:]
                if timer_text != 'unknown':
                    delta = parse_timer_text(timer_text)
                    if delta and delta.total_seconds() > 60:
                        result[card_num] = delta
    except Exception as e:
        pass
    return result

def detect_page_cooldowns(driver, player_id, page_type):
    detected = {}
    try:
        if page_type == "daily":
            cooldown_delta = detect_daily_timer_js(driver)
            if cooldown_delta and cooldown_delta.total_seconds() > 60:
                update_claim_history(player_id, "daily", claimed_count=0, detected_cooldown=cooldown_delta)
                detected['daily'] = cooldown_delta
        elif page_type == "store":
            timer_map = detect_store_timers_js(driver)
            for card_num, cooldown_delta in timer_map.items():
                if cooldown_delta is not None:
                    update_claim_history(player_id, "store", claimed_count=0, reward_index=card_num, detected_cooldown=cooldown_delta)
                    detected[f'store_{card_num}'] = cooldown_delta
    except Exception as e:
        pass
    return detected

def get_reward_status(player_id):
    history = load_claim_history()
    ist_now = get_ist_time()
    
    if player_id not in history:
        return {
            "daily_available": True, "store_available": [True, True, True],
            "daily_next": None, "daily_status": "unknown",
            "store_next": [None, None, None], "store_status": ["unknown", "unknown", "unknown"]
        }
    
    player_history = history[player_id]
    
    daily_available = True
    daily_next = None
    daily_status = player_history["daily"].get("status", "unknown")
    
    last_daily_claim = player_history["daily"].get("last_claim")
    if last_daily_claim:
        last_claim_time = datetime.fromisoformat(last_daily_claim)
        next_reset = None
        if player_history["daily"]["next_available"]:
            next_reset = datetime.fromisoformat(player_history["daily"]["next_available"])
        if next_reset is None:
            next_reset = last_claim_time + timedelta(hours=24)
        if ist_now < next_reset:
            daily_available = False
            daily_next = format_time_until_reset(next_reset)
            daily_status = "claimed"
    
    if daily_available and player_history["daily"]["next_available"]:
        next_time = datetime.fromisoformat(player_history["daily"]["next_available"])
        if ist_now < next_time:
            daily_available = False
            daily_next = format_time_until_reset(next_time)
            
    store_available = [True, True, True]
    store_next = [None, None, None]
    store_status = ["unknown", "unknown", "unknown"]
    
    for i in range(3):
        reward_key = f"reward_{i+1}"
        reward_data = player_history["store"][reward_key]
        store_status[i] = reward_data.get("status", "unknown")
        
        last_store_claim = reward_data.get("last_claim")
        if last_store_claim:
            last_claim_time = datetime.fromisoformat(last_store_claim)
            cooldown_end = last_claim_time + timedelta(hours=STORE_COOLDOWN_HOURS)
            if ist_now < cooldown_end:
                store_available[i] = False
                store_next[i] = format_time_until_reset(cooldown_end)
                store_status[i] = "claimed"
                continue
                
        if reward_data["next_available"]:
            next_time = datetime.fromisoformat(reward_data["next_available"])
            if ist_now < next_time:
                store_available[i] = False
                store_next[i] = format_time_until_reset(next_time)
                
    return {
        "daily_available": daily_available, "store_available": store_available,
        "daily_next": daily_next, "daily_status": daily_status,
        "store_next": store_next, "store_status": store_status
    }

def create_driver():
    for attempt in range(3):
        try:
            options = uc.ChromeOptions()
            if HEADLESS:
                options.add_argument("--headless=new")
            options.add_argument("--window-size=1920,1080")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_argument("--disable-gpu")
            options.add_argument("--disable-logging")
            options.add_argument("--disable-notifications")
            options.add_argument("--disable-popup-blocking")
            options.add_argument("--remote-debugging-port=0") 
            prefs = {"profile.default_content_setting_values": {"images": 2, "notifications": 2, "popups": 2}}
            options.add_experimental_option("prefs", prefs)

            driver = uc.Chrome(options=options, version_main=144, use_subprocess=True)
            driver.set_page_load_timeout(30)
            driver.set_script_timeout(30)
            return driver
        except Exception as e:
            time.sleep(2)
            if attempt == 2: raise

def bypass_cloudflare(driver):
    try:
        time.sleep(2)
        title, source = driver.title.lower(), driver.page_source.lower()
        if "just a moment" in title or "verifying" in source or "hub.vertigogames.co" in title:
            time.sleep(5)
            if "daily rewards" in driver.title.lower() or "login" in driver.page_source.lower(): return True
            try:
                checkbox = driver.find_elements(By.XPATH, "//input[@type='checkbox']")
                if checkbox: checkbox[0].click(); time.sleep(3)
            except: pass
            for _ in range(15):
                if "daily-rewards" in driver.current_url or "hub.vertigogames.co" in driver.current_url:
                    if "verifying" not in driver.page_source.lower(): return True
                time.sleep(1)
    except Exception: pass

def accept_cookies(driver):
    try:
        btn = WebDriverWait(driver, 3).until(EC.element_to_be_clickable((By.XPATH, "//button[normalize-space()='Accept All' or contains(text(), 'Accept') or contains(text(), 'Allow') or contains(text(), 'Consent')]")))
        btn.click()
        time.sleep(0.3)
    except TimeoutException: pass

def login_to_hub(driver, player_id):
    log(f"🔐 Logging in: {player_id}")
    try:
        driver.get("https://hub.vertigogames.co/daily-rewards")
        bypass_cloudflare(driver)
        time.sleep(1)
        accept_cookies(driver)
        
        login_selectors = ["//button[contains(text(),'Login') or contains(text(),'Log in') or contains(text(), 'Sign in')]", "//a[contains(text(),'Login') or contains(text(),'Log in') or contains(text(), 'Sign in')]", "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'login')]", "//button[contains(text(), 'claim')]", "//div[contains(text(), 'Daily Rewards') or contains(text(), 'daily')]//button", "//button[contains(@class, 'btn') or contains(@class, 'button')]", "//*[contains(text(), 'Login') or contains(text(), 'login')][@onclick or @href or self::button or self::a]"]
        
        login_clicked = False
        for selector in login_selectors:
            try:
                elements = driver.find_elements(By.XPATH, selector)
                if elements:
                    for element in elements:
                        if element.is_displayed() and element.is_enabled():
                            element.click()
                            login_clicked = True
                            break
                if login_clicked: break
            except: continue
        
        if not login_clicked: return False
        time.sleep(2)
        
        original_window = driver.current_window_handle
        if len(driver.window_handles) > 1:
            for window in driver.window_handles:
                if window != original_window: driver.switch_to.window(window); break
            time.sleep(1)
        
        id_input_selectors = ["//input[@placeholder='Player ID' or @name='playerId' or @id='playerId']", "//input[@type='text']", "//input[contains(@placeholder, 'ID')]"]
        id_entered = False
        for selector in id_input_selectors:
            try:
                id_field = driver.find_element(By.XPATH, selector)
                if id_field.is_displayed():
                    id_field.clear()
                    id_field.send_keys(player_id)
                    id_entered = True
                    break
            except: continue
        
        if not id_entered: return False
        time.sleep(1)
        
        submit_selectors = ["//button[contains(text(), 'Login') or contains(text(), 'Submit') or contains(text(), 'Continue')]", "//button[@type='submit']", "//input[@type='submit']"]
        submit_clicked = False
        for selector in submit_selectors:
            try:
                submit_btn = driver.find_element(By.XPATH, selector)
                if submit_btn.is_displayed() and submit_btn.is_enabled():
                    submit_btn.click()
                    submit_clicked = True
                    break
            except: continue
        
        if not submit_clicked:
            try: id_field.send_keys(Keys.RETURN)
            except: return False
        
        time.sleep(3)
        if len(driver.window_handles) > 1:
            driver.close()
            driver.switch_to.window(original_window)
            time.sleep(1)
        
        time.sleep(2)
        if "daily-rewards" in driver.current_url or "claim" in driver.page_source.lower() or player_id.lower() in driver.page_source.lower():
            return True
        return True
    except Exception as e: return False

def close_popup(driver):
    try:
        close_selectors = ["//button[contains(text(), 'Close') or contains(text(), '×') or contains(@class, 'close')]", "//div[contains(@class, 'modal')]//button", "//*[@aria-label='Close' or @title='Close']"]
        for selector in close_selectors:
            try:
                btns = driver.find_elements(By.XPATH, selector)
                for btn in btns:
                    if btn.is_displayed(): btn.click(); time.sleep(0.3); return
            except: continue
        try:
            driver.execute_script("""
                let modals = document.querySelectorAll('[class*="modal"], [class*="overlay"]');
                for (let m of modals) { if (m.offsetParent !== null) { m.click(); } }
            """)
        except: pass
    except: pass

def claim_daily_rewards(driver, player_id):
    status = get_reward_status(player_id)
    if not status["daily_available"] and status["daily_status"] == "claimed":
        return 0
    
    claimed = 0
    try:
        driver.get("https://hub.vertigogames.co/daily-rewards")
        bypass_cloudflare(driver)
        time.sleep(2)
        close_popup(driver)
        detect_page_cooldowns(driver, player_id, "daily")
        
        for attempt in range(3):
            result = driver.execute_script("""
                let buttons = document.querySelectorAll('button');
                for (let btn of buttons) {
                    let text = (btn.innerText || btn.textContent).trim().toLowerCase();
                    if (text === 'claim' && btn.offsetParent !== null && !btn.disabled) {
                        btn.scrollIntoView({behavior: 'smooth', block: 'center'});
                        setTimeout(function() { btn.click(); }, 300);
                        return true;
                    }
                }
                return false;
            """)
            if result:
                claimed = 1
                time.sleep(2)
                close_popup(driver)
                update_claim_history(player_id, "daily", claimed_count=1)
                break
            time.sleep(1)
        
        if claimed == 0:
            status = get_reward_status(player_id)
            already_tracked = (status["daily_status"] in ["cooldown_detected", "claimed"] or status["daily_next"] is not None)
            if not already_tracked:
                update_claim_history(player_id, "daily", claimed_count=0, attempted=True)
    except Exception as e: pass
    return claimed

def physical_click(driver, element):
    try:
        driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", element)
        time.sleep(0.5)
        try: element.click(); return True
        except:
            try: ActionChains(driver).move_to_element(element).click().perform(); return True
            except:
                try: driver.execute_script("arguments[0].click();", element); return True
                except: return False
    except: return False

def ensure_store_page(driver):
    try:
        if "store" not in driver.current_url:
            driver.get("https://hub.vertigogames.co/store")
            bypass_cloudflare(driver)
            time.sleep(2)
        return True
    except: return False

def claim_store_rewards(driver, player_id):
    claimed = 0
    max_claims = 3
    try:
        driver.get("https://hub.vertigogames.co/store")
        bypass_cloudflare(driver)
        time.sleep(2)
        close_popup(driver)
        
        detected = detect_page_cooldowns(driver, player_id, "store")
        status = get_reward_status(player_id)
        if sum(status["store_available"]) == 0: return 0
        
        for attempt in range(3):
            if claimed >= 2: break
            ensure_store_page(driver)
            time.sleep(1)
            found_btn = None
            try:
                buttons = driver.find_elements(By.TAG_NAME, "button")
                for btn in buttons:
                    try:
                        btn_text = btn.text.strip().lower()
                        if btn_text == "free" and btn.is_displayed() and btn.is_enabled():
                            try:
                                parent = btn.find_element(By.XPATH, "./..")
                                if "next in" in parent.text.lower(): continue
                            except: pass
                            found_btn = btn
                            break
                    except: continue
            except: pass
            
            if found_btn:
                if physical_click(driver, found_btn):
                    time.sleep(4)
                    close_popup(driver)
                    claimed += 1
                    update_claim_history(player_id, "store", claimed_count=1, reward_index=claimed)
                    ensure_store_page(driver)
                    time.sleep(1)
            else:
                if attempt >= 1: break
                time.sleep(1)
        
        if claimed < max_claims:
            for attempt in range(4):
                if claimed >= max_claims: break
                ensure_store_page(driver)
                time.sleep(1.5)
                found_btn = None
                try:
                    buttons = driver.find_elements(By.TAG_NAME, "button")
                    for btn in buttons:
                        try:
                            btn_text = btn.text.strip().lower()
                            if btn_text == "free" and btn.is_displayed() and btn.is_enabled():
                                try:
                                    parent = btn.find_element(By.XPATH, "./..")
                                    if "next in" in parent.text.lower(): continue
                                except: pass
                                found_btn = btn
                                break
                        except: continue
                except: pass
                
                if found_btn:
                    if physical_click(driver, found_btn):
                        time.sleep(4)
                        close_popup(driver)
                        claimed += 1
                        update_claim_history(player_id, "store", claimed_count=1, reward_index=claimed)
                        ensure_store_page(driver)
                        break
                
                if claimed < max_claims:
                    result = driver.execute_script("""
                        let storeBonusCards = document.querySelectorAll('[class*="StoreBonus"]');
                        if (storeBonusCards.length === 0) storeBonusCards = document.querySelectorAll('div');
                        for (let card of storeBonusCards) {
                            let cardText = card.innerText || '';
                            if (cardText.includes('Next in') || cardText.match(/\\d+h\\s+\\d+m/)) continue;
                            let buttons = card.querySelectorAll('button');
                            for (let btn of buttons) {
                                let btnText = btn.innerText.trim().toLowerCase();
                                if ((btnText === 'free' || btnText === 'claim') && btn.offsetParent !== null && !btn.disabled) {
                                    btn.scrollIntoView({behavior: 'smooth', block: 'center'});
                                    btn.click(); return true;
                                }
                            }
                        }
                        return false;
                    """)
                    if result:
                        claimed += 1
                        time.sleep(4)
                        close_popup(driver)
                        update_claim_history(player_id, "store", claimed_count=1, reward_index=claimed)
                        time.sleep(1)
                        break
                    else:
                        time.sleep(2)
        
        status = get_reward_status(player_id)
        for i in range(claimed + 1, 4):
            already_tracked = (status["store_status"][i-1] in ["cooldown_detected", "claimed"] or status["store_next"][i-1] is not None)
            if not already_tracked:
                update_claim_history(player_id, "store", claimed_count=0, reward_index=i, attempted=True)
    except Exception as e: pass
    return claimed

def claim_progression_program_rewards(driver, player_id):
    claimed = 0
    try:
        driver.get("https://hub.vertigogames.co/progression-program")
        bypass_cloudflare(driver)
        time.sleep(2)
        close_popup(driver)
        
        for _ in range(6):
            result = driver.execute_script("""
                let allButtons = document.querySelectorAll('button');
                for (let btn of allButtons) {
                    let btnText = (btn.innerText || btn.textContent).trim().toLowerCase();
                    if (btnText === 'claim' && btn.offsetParent !== null && !btn.disabled) {
                         let pText = (btn.parentElement.innerText || btn.parentElement.textContent) || '';
                         if (!pText.includes('Delivered')) {
                             btn.scrollIntoView({behavior: 'smooth', block: 'center', inline: 'center'});
                             setTimeout(function() { btn.click(); }, 300);
                             return true;
                         }
                    }
                }
                return false;
            """)
            if result:
                claimed += 1
                time.sleep(2.0)
                close_popup(driver)
            else:
                driver.execute_script("let c=document.querySelectorAll('div');for(let i of c){if(i.scrollWidth>i.clientWidth){i.scrollLeft+=400;}}")
                time.sleep(1)
        if claimed > 0: update_claim_history(player_id, "progression", claimed_count=claimed)
    except: pass
    return claimed

def claim_loyalty_rewards(driver, player_id):
    """Claim New Loyalty Program Rewards (Req 3)"""
    claimed = 0
    try:
        driver.get("https://hub.vertigogames.co/loyalty-program")
        bypass_cloudflare(driver)
        time.sleep(4)
        close_popup(driver)
        
        result = driver.execute_script("""
            let buttons = document.querySelectorAll('button.loyalty-claim-button.claim-btn:not(.disabled)');
            let count = 0;
            for (let btn of buttons) {
                if ((btn.innerText || btn.textContent).trim().toUpperCase().includes('CLAIM')) {
                    btn.scrollIntoView({behavior: 'smooth', block: 'center'});
                    btn.click();
                    count++;
                }
            }
            return count;
        """)
        
        if result and result > 0:
            claimed = result
            time.sleep(2)
            update_claim_history(player_id, "loyalty", claimed_count=claimed)
    except Exception as e:
        log(f"⚠️ Loyalty error: {e}")
    return claimed

def process_player(player_id):
    """Thread-safe Process single player logic"""
    driver = None
    stats = {"player_id": player_id, "daily": 0, "store": 0, "progression": 0, "loyalty": 0, "status": "Failed"}
    init_player_history(player_id)
    
    try:
        log(f"\n🚀 {player_id}")
        driver = create_driver()
        if not login_to_hub(driver, player_id):
            stats['status'] = "Login Failed"
            return stats
        
        stats['daily'] = claim_daily_rewards(driver, player_id)
        
        max_store_expected = 3
        store_retry_attempts = 2
        for retry in range(store_retry_attempts):
            stats['store'] = claim_store_rewards(driver, player_id)
            if stats['store'] >= max_store_expected: break
            elif stats['store'] > 0 and retry < store_retry_attempts - 1: time.sleep(2)
            elif stats['store'] == 0: break
        
        if stats['store'] > 0: time.sleep(3)
        
        progression_retry_attempts = 2
        for retry in range(progression_retry_attempts):
            claimed = claim_progression_program_rewards(driver, player_id)
            stats['progression'] += claimed
            if claimed == 0 and retry < progression_retry_attempts - 1: time.sleep(2)
            elif claimed == 0: break
            else:
                if retry < progression_retry_attempts - 1: time.sleep(1)

        # 4. New Loyalty Rewards
        stats['loyalty'] = claim_loyalty_rewards(driver, player_id)
        
        total = stats['daily'] + stats['store'] + stats['progression'] + stats['loyalty']
        if total > 0: stats['status'] = "Success"
        else: stats['status'] = "No Rewards"
        
        log(f"🎉 {player_id} Total: {total} (D: {stats['daily']}, S: {stats['store']}, P: {stats['progression']}, L: {stats['loyalty']})")
            
    except Exception as e:
        log(f"❌ Error: {e}")
        stats['status'] = "Error"
    finally:
        if driver:
            try: driver.quit()
            except: pass
    return stats

def send_email_summary(results, num_players):
    try:
        sender = os.environ.get("SENDER_EMAIL")
        recipient = os.environ.get("RECIPIENT_EMAIL")
        password = os.environ.get("GMAIL_APP_PASSWORD")
        if not all([sender, recipient, password]): return
        
        total_d = sum(r['daily'] for r in results)
        total_s = sum(r['store'] for r in results)
        total_p = sum(r['progression'] for r in results)
        total_l = sum(r.get('loyalty', 0) for r in results)
        total_all = total_d + total_s + total_p + total_l
        
        ist_now = get_ist_time()
        history = load_claim_history()
        on_cooldown = 0
        
        for result in results:
            player_id = result['player_id']
            status = get_reward_status(player_id)
            if not status['daily_available']: on_cooldown += 1
            on_cooldown += sum(1 for x in status['store_available'] if not x)
        
        next_run_time, next_run_reason = None, None
        for player_id in [r['player_id'] for r in results]:
            if player_id in history:
                ph = history[player_id]
                if ph['daily']['next_available']:
                    next_time = datetime.fromisoformat(ph['daily']['next_available'])
                    if not next_run_time or next_time < next_run_time:
                        next_run_time, next_run_reason = next_time, "Daily rewards reset"
                for i in range(3):
                    reward_key = f"reward_{i+1}"
                    if ph['store'][reward_key]['next_available']:
                        next_time = datetime.fromisoformat(ph['store'][reward_key]['next_available'])
                        if not next_run_time or next_time < next_run_time:
                            next_run_time, next_run_reason = next_time, f"Store Reward {i+1} available"
        
        html = f"""
        <html>
        <head>
        <style>
            body {{ font-family: 'Segoe UI', Arial, sans-serif; background-color: #f5f5f5; padding: 20px; }}
            .container {{ max-width: 900px; margin: 0 auto; background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
            h2 {{ color: #2c3e50; border-bottom: 3px solid #3498db; padding-bottom: 10px; }}
            .stat-box {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 20px; border-radius: 8px; margin: 15px 0; }}
            .stat-row {{ display: flex; justify-content: space-between; margin: 8px 0; }}
            .section {{ background: #f8f9fa; padding: 15px; border-radius: 8px; margin: 20px 0; border-left: 4px solid #3498db; }}
            .player-card {{ background: white; border: 1px solid #e0e0e0; padding: 15px; margin: 10px 0; border-radius: 6px; }}
            .status-claimed {{ color: #27ae60; font-weight: bold; }}
            .status-cooldown {{ color: #e67e22; font-weight: bold; }}
            .status-unavailable {{ color: #95a5a6; font-weight: bold; }}
            .status-available {{ color: #3498db; font-weight: bold; }}
        </style>
        </head>
        <body>
        <div class="container">
        <h2>🎮 Hub Rewards Summary</h2>
        
        <div class="section" style="border-left-color: #f1c40f;">
            <h3 style="margin-top:0;">⭐ New Loyalty Program</h3>
            <p>Loyalty program for dedicated players. Get rewards for daily activity, completing quests and participating in battles.</p>
        </div>

        <div class="stat-box">
            <div class="stat-row"><strong>📅 Run Time:</strong> <span>{ist_now.strftime('%Y-%m-%d %I:%M %p IST')}</span></div>
            <div class="stat-row"><strong>✅ Claimed This Run:</strong> <span>{total_all}</span></div>
            <div class="stat-row"><strong>⏰ On Cooldown:</strong> <span>{on_cooldown}</span></div>
        </div>
        
        <div class="section">
            <h3 style="margin-top:0;">📊 Current Run Breakdown</h3>
            <div class="stat-row"><strong>🎁 Daily:</strong> {total_d}/{num_players}</div>
            <div class="stat-row"><strong>🏪 Store:</strong> {total_s}/{num_players * 3}</div>
            <div class="stat-row"><strong>🎯 Progression:</strong> {total_p}</div>
            <div class="stat-row"><strong>🎖️ Loyalty:</strong> {total_l}</div>
        </div>
        
        <div class="section"><h3 style="margin-top:0;">👥 Detailed Player Status</h3>
        """
        
        for result in results:
            player_id = result['player_id']
            status = get_reward_status(player_id)
            
            if result['daily'] > 0: daily_status = f'<span class="status-claimed">✅ Claimed This Run</span>'
            elif status['daily_next']: daily_status = f'<span class="status-cooldown">⏰ On Cooldown - Next in {status["daily_next"]}</span>'
            elif status['daily_status'] == 'unavailable': daily_status = f'<span class="status-unavailable">⏳ Not Available</span>'
            else: daily_status = f'<span class="status-available">🔄 Check Manually</span>'
            
            store_status_lines = []
            for i in range(3):
                reward_label = {0: "🥇 Gold", 1: "💵 Cash", 2: "🍀 Luckyloon"}.get(i, f"Reward {i+1}")
                if i < result['store']: store_status_lines.append(f'{reward_label}: <span class="status-claimed">✅ Claimed This Run</span>')
                elif status['store_next'][i]: store_status_lines.append(f'{reward_label}: <span class="status-cooldown">⏰ On Cooldown - Next in {status["store_next"][i]}</span>')
                elif status['store_status'][i] == 'unavailable': store_status_lines.append(f'{reward_label}: <span class="status-unavailable">⏳ Not Available</span>')
                else: store_status_lines.append(f'{reward_label}: <span class="status-available">🔄 Check Manually</span>')
            
            prog_status = f'<span class="status-claimed">✅ Claimed {result["progression"]}</span>' if result['progression'] > 0 else f'<span class="status-unavailable">⏳ Not Available</span>'
            loyalty_status = f'<span class="status-claimed">✅ Claimed {result.get("loyalty", 0)}</span>' if result.get('loyalty', 0) > 0 else f'<span class="status-unavailable">⏳ Not Available</span>'
            
            html += f"""
            <div class="player-card">
                <strong style="color: #2c3e50; font-size: 16px;">🆔 {player_id}</strong>
                <div style="margin-top: 10px;">
                    <div style="margin: 5px 0;">🎁 <strong>Daily:</strong> {daily_status}</div>
                    <div style="margin: 5px 0;">🏪 <strong>Store:</strong></div><div style="margin-left: 20px;">{"<br>".join(store_status_lines)}</div>
                    <div style="margin: 5px 0;">🎯 <strong>Progression:</strong> {prog_status}</div>
                    <div style="margin: 5px 0;">🎖️ <strong>Loyalty:</strong> {loyalty_status}</div>
                </div>
            </div>
            """
        html += "</div>"
        
        if next_run_time:
            time_until = format_time_until_reset(next_run_time)
            html += f"""
            <div class="section" style="border-left-color: #e74c3c;">
                <h3 style="margin-top:0; color: #e74c3c;">⏰ Next Recommended Run</h3>
                <div class="stat-row"><strong>⏳ In:</strong> {time_until}</div>
                <div class="stat-row"><strong>📅 Time:</strong> {next_run_time.strftime('%I:%M %p IST')}</div>
            </div>
            """
        
        html += "</div></body></html>"
        msg = MIMEMultipart('alternative')
        msg['Subject'] = f"🎮 Hub Rewards - {ist_now.strftime('%d-%b %I:%M %p')} IST ({total_all} claimed)"
        msg['From'] = sender
        msg['To'] = recipient
        msg.attach(MIMEText(html, 'html'))
        
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(sender, password)
            server.send_message(msg)
        log("✅ Email sent")
    except Exception as e: log(f"❌ Email error: {e}")

def get_next_wake_time(players):
    history = load_claim_history()
    ist_now = get_ist_time()
    soonest = None

    for player_id in players:
        if player_id not in history: continue
        ph = history[player_id]

        daily_na = ph["daily"].get("next_available")
        if daily_na:
            t = datetime.fromisoformat(daily_na)
            if t > ist_now: soonest = t if soonest is None else min(soonest, t)

        for rkey in ["reward_1", "reward_2", "reward_3"]:
            store_na = ph["store"][rkey].get("next_available")
            if store_na:
                t = datetime.fromisoformat(store_na)
                if t > ist_now: soonest = t if soonest is None else min(soonest, t)

            last_claim = ph["store"][rkey].get("last_claim")
            if last_claim:
                cooldown_end = datetime.fromisoformat(last_claim) + timedelta(hours=STORE_COOLDOWN_HOURS)
                if cooldown_end > ist_now: soonest = cooldown_end if soonest is None else min(soonest, cooldown_end)

        last_daily = ph["daily"].get("last_claim")
        if last_daily:
            next_daily = datetime.fromisoformat(last_daily) + timedelta(hours=24)
            if next_daily > ist_now: soonest = next_daily if soonest is None else min(soonest, next_daily)

    return soonest

def main():
    log("=" * 60)
    log("CS HUB AUTO-CLAIMER v3.0 (Parallel Processing & Loyalty)")
    log("=" * 60)
    log("")

    try:
        with open(PLAYER_ID_FILE, 'r') as f:
            reader = csv.DictReader(f)
            players = [row['player_id'].strip() for row in reader if row['player_id'].strip()]
    except:
        log("❌ Could not read players.csv")
        return

    JOB_START = get_ist_time()
    JOB_MAX_SECONDS = 1.9 * 3600
    MIN_SLEEP_SECONDS = 60
    EARLY_WAKE_BUFFER = 90
    run_count = 0

    while True:
        run_count += 1
        run_start = get_ist_time()
        elapsed_total = (run_start - JOB_START).total_seconds()

        log(f"\n{'='*60}")
        log(f"🔄 Run #{run_count}  |  Job elapsed: {int(elapsed_total//3600)}h {int((elapsed_total%3600)//60)}m")
        log(f"{'='*60}\n")

        # Req 2: Parallel execution to fix sequential timing drift
        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_CONCURRENT_BROWSERS) as executor:
            future_to_player = {executor.submit(process_player, pid): pid for pid in players}
            for future in concurrent.futures.as_completed(future_to_player):
                try:
                    results.append(future.result())
                except Exception as exc:
                    log(f"❌ Thread generated an exception: {exc}")

        send_email_summary(results, len(players))

        next_wake = get_next_wake_time(players)
        ist_now = get_ist_time()
        elapsed_total = (ist_now - JOB_START).total_seconds()
        remaining_budget = JOB_MAX_SECONDS - elapsed_total

        if next_wake is None:
            log("⏹  No future rewards detected — exiting (next scheduled run will catch them)")
            break

        sleep_needed = (next_wake - ist_now).total_seconds() - EARLY_WAKE_BUFFER

        if sleep_needed < MIN_SLEEP_SECONDS:
            log(f"⚡ Next reward available in <{int(sleep_needed)+EARLY_WAKE_BUFFER}s — re-running immediately")
            time.sleep(max(0, sleep_needed))
            continue

        if sleep_needed > remaining_budget:
            wake_str = next_wake.strftime('%H:%M IST')
            log(f"⏹  Next reward at {wake_str} is beyond job time budget — exiting")
            break

        wake_str = next_wake.strftime('%d-%b %H:%M:%S IST')
        h, m, s = int(sleep_needed // 3600), int((sleep_needed % 3600) // 60), int(sleep_needed % 60)
        log(f"💤 Sleeping {h}h {m}m {s}s — waking at {wake_str} for next available reward")
        time.sleep(sleep_needed)

    log("\n🏁 Job complete!")

if __name__ == "__main__":
    main()
