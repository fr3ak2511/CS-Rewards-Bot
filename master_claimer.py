import csv
import time
import os
import json
import smtplib
import re
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
DAILY_RESET_MINUTE_IST = 30
EXPECTED_STORE_PER_PLAYER = 3
STORE_COOLDOWN_HOURS = 24
PROGRESSION_DEPENDS_ON_STORE = True

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

# IST timezone helper functions
def get_ist_time():
    """Get current time in IST (UTC+5:30)"""
    utc_now = datetime.utcnow()
    ist_offset = timedelta(hours=5, minutes=30)
    return utc_now + ist_offset

def get_next_daily_reset():
    """Get next daily reset time (5:30 AM IST)"""
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
    """Parse 'Next in 23h 45m' into timedelta"""
    try:
        # Extract hours and minutes from text like "Next in 23h 45m" or "23h 45m"
        hours = 0
        minutes = 0
        
        hour_match = re.search(r'(\d+)\s*h', timer_text, re.IGNORECASE)
        if hour_match:
            hours = int(hour_match.group(1))
        
        min_match = re.search(r'(\d+)\s*m', timer_text, re.IGNORECASE)
        if min_match:
            minutes = int(min_match.group(1))
        
        if hours > 0 or minutes > 0:
            return timedelta(hours=hours, minutes=minutes)
        
        return None
    except:
        return None

# Claim History Management
def load_claim_history():
    """Load claim history from JSON file"""
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
    """Save claim history to JSON file"""
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
            "progression": {"last_claim": None, "last_count": 0}
        }
        save_claim_history(history)
    
    return history

def update_claim_history(player_id, reward_type, claimed_count=0, reward_index=None, detected_cooldown=None):
    """
    Update claim history for a player
    
    detected_cooldown: timedelta object if cooldown was detected on page (even if no claim made)
    """
    history = init_player_history(player_id)
    ist_now = get_ist_time()
    
    if reward_type == "daily":
        if claimed_count > 0:
            # Successfully claimed
            history[player_id]["daily"]["last_claim"] = ist_now.isoformat()
            next_reset = get_next_daily_reset()
            history[player_id]["daily"]["next_available"] = next_reset.isoformat()
            history[player_id]["daily"]["status"] = "claimed"
            log(f"📝 Updated history: Daily claimed, next at {next_reset.strftime('%I:%M %p')}")
        elif detected_cooldown is not None:
            # Detected cooldown on page
            next_available = ist_now + detected_cooldown
            history[player_id]["daily"]["next_available"] = next_available.isoformat()
            history[player_id]["daily"]["status"] = "cooldown_detected"
            log(f"📝 Updated history: Daily cooldown detected, next in {format_time_until_reset(next_available)}")
    
    elif reward_type == "store" and reward_index is not None:
        reward_key = f"reward_{reward_index}"
        if claimed_count > 0:
            # Successfully claimed
            history[player_id]["store"][reward_key]["last_claim"] = ist_now.isoformat()
            next_available = ist_now + timedelta(hours=STORE_COOLDOWN_HOURS)
            history[player_id]["store"][reward_key]["next_available"] = next_available.isoformat()
            history[player_id]["store"][reward_key]["status"] = "claimed"
            log(f"📝 Updated history: Store Reward {reward_index} claimed, next in 24h")
        elif detected_cooldown is not None:
            # Detected cooldown on page
            next_available = ist_now + detected_cooldown
            history[player_id]["store"][reward_key]["next_available"] = next_available.isoformat()
            history[player_id]["store"][reward_key]["status"] = "cooldown_detected"
            log(f"📝 Updated history: Store Reward {reward_index} cooldown detected")
    
    elif reward_type == "progression" and claimed_count > 0:
        history[player_id]["progression"]["last_claim"] = ist_now.isoformat()
        history[player_id]["progression"]["last_count"] = claimed_count
        log(f"📝 Updated history: Progression claimed {claimed_count}")
    
    save_claim_history(history)
    return history

def detect_page_cooldowns(driver, player_id, page_type):
    """
    Detect cooldowns by analyzing the actual page
    Updates history with detected cooldowns
    
    page_type: 'daily', 'store', or 'progression'
    Returns: dict with cooldown info
    """
    detected = {}
    
    try:
        if page_type == "daily":
            # Look for timer text on daily page
            timer_elements = driver.find_elements(By.XPATH, "//*[contains(text(), 'Next in') or contains(text(), 'next in')]")
            if timer_elements:
                timer_text = timer_elements[0].text
                cooldown_delta = parse_timer_text(timer_text)
                if cooldown_delta:
                    log(f"🔍 Daily page: Detected cooldown - {timer_text}")
                    update_claim_history(player_id, "daily", claimed_count=0, detected_cooldown=cooldown_delta)
                    detected['daily'] = cooldown_delta
        
        elif page_type == "store":
            # Look for store reward cards with timers
            try:
                # Find all cards/sections that might contain store rewards
                page_text = driver.page_source
                
                # Find all "Next in" patterns with context
                timer_pattern = r'Next in\s+(\d+h?\s*\d*m?)'
                matches = re.finditer(timer_pattern, page_text, re.IGNORECASE)
                
                reward_cooldowns = []
                for match in matches:
                    timer_text = match.group(0)
                    cooldown_delta = parse_timer_text(timer_text)
                    if cooldown_delta:
                        reward_cooldowns.append(cooldown_delta)
                
                # Update history for detected cooldowns
                for i, cooldown_delta in enumerate(reward_cooldowns[:3]):  # Max 3 store rewards
                    log(f"🔍 Store page: Reward {i+1} cooldown detected")
                    update_claim_history(player_id, "store", claimed_count=0, reward_index=i+1, detected_cooldown=cooldown_delta)
                    detected[f'store_{i+1}'] = cooldown_delta
                    
            except Exception as e:
                log(f"⚠️ Store cooldown detection error: {e}")
        
    except Exception as e:
        log(f"⚠️ Page cooldown detection error: {e}")
    
    return detected

def get_reward_status(player_id):
    """Get current status of all rewards for a player"""
    history = load_claim_history()
    ist_now = get_ist_time()
    
    if player_id not in history:
        return {
            "daily_available": True,
            "store_available": [True, True, True],
            "daily_next": None,
            "daily_status": "unknown",
            "store_next": [None, None, None],
            "store_status": ["unknown", "unknown", "unknown"]
        }
    
    player_history = history[player_id]
    
    # Check daily
    daily_available = True
    daily_next = None
    daily_status = player_history["daily"].get("status", "unknown")
    
    if player_history["daily"]["next_available"]:
        next_time = datetime.fromisoformat(player_history["daily"]["next_available"])
        if ist_now < next_time:
            daily_available = False
            daily_next = format_time_until_reset(next_time)
    
    # Check store (each reward individually)
    store_available = [True, True, True]
    store_next = [None, None, None]
    store_status = ["unknown", "unknown", "unknown"]
    
    for i in range(3):
        reward_key = f"reward_{i+1}"
        store_status[i] = player_history["store"][reward_key].get("status", "unknown")
        
        if player_history["store"][reward_key]["next_available"]:
            next_time = datetime.fromisoformat(player_history["store"][reward_key]["next_available"])
            if ist_now < next_time:
                store_available[i] = False
                store_next[i] = format_time_until_reset(next_time)
    
    return {
        "daily_available": daily_available,
        "store_available": store_available,
        "daily_next": daily_next,
        "daily_status": daily_status,
        "store_next": store_next,
        "store_status": store_status
    }

def create_driver():
    """GitHub Actions-compatible driver - FORCED CHROME 144"""
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

            prefs = {
                "profile.default_content_setting_values": {
                    "images": 2,
                    "notifications": 2,
                    "popups": 2,
                }
            }
            options.add_experimental_option("prefs", prefs)

            driver = uc.Chrome(options=options, version_main=144, use_subprocess=True)
            driver.set_page_load_timeout(30)
            driver.set_script_timeout(30)
            log("✅ Driver initialized (v144)")
            return driver

        except Exception as e:
            log(f"⚠️ Driver init attempt {attempt+1} failed: {str(e)[:100]}")
            time.sleep(2)
            if attempt == 2:
                log(f"❌ All driver init attempts failed")
                raise

def bypass_cloudflare(driver):
    """Specifically handle the 'Verifying you are human' screen"""
    try:
        time.sleep(2)
        title = driver.title.lower()
        source = driver.page_source.lower()
        
        if "just a moment" in title or "verifying" in source or "hub.vertigogames.co" in title:
            log("🛡️ Cloudflare Challenge detected. Attempting bypass...")
            time.sleep(5)
            
            if "daily rewards" in driver.title.lower() or "login" in driver.page_source.lower():
                log("✅ Passed Cloudflare (Automatic)")
                return True

            try:
                checkbox = driver.find_elements(By.XPATH, "//input[@type='checkbox']")
                if checkbox:
                    checkbox[0].click()
                    log("✅ Clicked Verification Checkbox")
                    time.sleep(3)
            except:
                pass
            
            for _ in range(15):
                if "daily-rewards" in driver.current_url or "hub.vertigogames.co" in driver.current_url:
                    if "verifying" not in driver.page_source.lower():
                        log("✅ Cloudflare cleared")
                        return True
                time.sleep(1)
                
            log("⚠️ Warning: Might still be on Cloudflare page")
            
    except Exception as e:
        log(f"ℹ️ Cloudflare check error (ignorable): {e}")

def accept_cookies(driver):
    """Accept cookie banner"""
    try:
        btn = WebDriverWait(driver, 3).until(
            EC.element_to_be_clickable((
                By.XPATH,
                "//button[normalize-space()='Accept All' or contains(text(), 'Accept') or "
                "contains(text(), 'Allow') or contains(text(), 'Consent')]"
            ))
        )
        btn.click()
        time.sleep(0.3)
        log("✅ Cookies accepted")
    except TimeoutException:
        log("ℹ️  No cookie banner")

def login_to_hub(driver, player_id):
    """Login using multi-selector strategy"""
    log(f"🔐 Logging in: {player_id}")
    
    try:
        driver.get("https://hub.vertigogames.co/daily-rewards")
        bypass_cloudflare(driver)
        time.sleep(1)
        driver.save_screenshot(f"01_page_loaded_{player_id}.png")
        
        accept_cookies(driver)
        
        login_selectors = [
            "//button[contains(text(),'Login') or contains(text(),'Log in') or contains(text(), 'Sign in')]",
            "//a[contains(text(),'Login') or contains(text(),'Log in') or contains(text(), 'Sign in')]",
            "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'login')]",
            "//button[contains(text(), 'claim')]",
            "//div[contains(text(), 'Daily Rewards') or contains(text(), 'daily')]//button",
            "//button[contains(@class, 'btn') or contains(@class, 'button')]",
            "//*[contains(text(), 'Login') or contains(text(), 'login')][@onclick or @href or self::button or self::a]",
        ]
        
        login_clicked = False
        for i, selector in enumerate(login_selectors):
            try:
                elements = driver.find_elements(By.XPATH, selector)
                if elements:
                    for element in elements:
                        try:
                            if element.is_displayed() and element.is_enabled():
                                element.click()
                                login_clicked = True
                                log(f"✅ Login button clicked (selector {i+1})")
                                break
                        except:
                            continue
                if login_clicked:
                    break
            except:
                continue
        
        if not login_clicked:
            log("❌ No login button found")
            driver.save_screenshot(f"02_login_not_found_{player_id}.png")
            return False
        
        time.sleep(2)
        driver.save_screenshot(f"03_login_clicked_{player_id}.png")
        
        # Handle new tab login
        original_window = driver.current_window_handle
        all_windows = driver.window_handles
        if len(all_windows) > 1:
            log("🔄 New tab detected. Switching...")
            for window in all_windows:
                if window != original_window:
                    driver.switch_to.window(window)
                    break
            time.sleep(1)
        
        # Enter Player ID
        id_input_selectors = [
            "//input[@placeholder='Player ID' or @name='playerId' or @id='playerId']",
            "//input[@type='text']",
            "//input[contains(@placeholder, 'ID')]",
        ]
        
        id_entered = False
        for selector in id_input_selectors:
            try:
                id_field = driver.find_element(By.XPATH, selector)
                if id_field.is_displayed():
                    id_field.clear()
                    id_field.send_keys(player_id)
                    log(f"✅ Entered ID: {player_id}")
                    id_entered = True
                    break
            except:
                continue
        
        if not id_entered:
            log("❌ ID field not found")
            driver.save_screenshot(f"04_id_field_not_found_{player_id}.png")
            return False
        
        time.sleep(1)
        driver.save_screenshot(f"05_id_entered_{player_id}.png")
        
        # Click Login/Submit
        submit_selectors = [
            "//button[contains(text(), 'Login') or contains(text(), 'Submit') or contains(text(), 'Continue')]",
            "//button[@type='submit']",
            "//input[@type='submit']",
        ]
        
        submit_clicked = False
        for selector in submit_selectors:
            try:
                submit_btn = driver.find_element(By.XPATH, selector)
                if submit_btn.is_displayed() and submit_btn.is_enabled():
                    submit_btn.click()
                    log("✅ Submit clicked")
                    submit_clicked = True
                    break
            except:
                continue
        
        if not submit_clicked:
            log("⚠️ Submit button not found, trying Enter key")
            try:
                id_field.send_keys(Keys.RETURN)
                log("✅ Pressed Enter")
            except:
                log("❌ Could not submit")
                return False
        
        time.sleep(3)
        driver.save_screenshot(f"06_after_submit_{player_id}.png")
        
        # Switch back to original window if needed
        if len(driver.window_handles) > 1:
            driver.close()
            driver.switch_to.window(original_window)
            time.sleep(1)
        
        # Verify login success
        time.sleep(2)
        current_url = driver.current_url
        page_source = driver.page_source.lower()
        
        if "daily-rewards" in current_url or "claim" in page_source or player_id.lower() in page_source:
            log("✅ Login successful")
            driver.save_screenshot(f"07_login_success_{player_id}.png")
            return True
        else:
            log("⚠️ Login verification uncertain")
            driver.save_screenshot(f"08_login_uncertain_{player_id}.png")
            return True
            
    except Exception as e:
        log(f"❌ Login error: {e}")
        driver.save_screenshot(f"09_login_error_{player_id}.png")
        return False

def close_popup(driver):
    """Close any reward popups"""
    try:
        close_selectors = [
            "//button[contains(text(), 'Close') or contains(text(), '×') or contains(@class, 'close')]",
            "//div[contains(@class, 'modal')]//button",
            "//*[@aria-label='Close' or @title='Close']",
        ]
        
        for selector in close_selectors:
            try:
                btns = driver.find_elements(By.XPATH, selector)
                for btn in btns:
                    if btn.is_displayed():
                        btn.click()
                        time.sleep(0.3)
                        log("✅ Popup closed")
                        return
            except:
                continue
                
        # Click outside modal
        try:
            driver.execute_script("""
                let modals = document.querySelectorAll('[class*="modal"], [class*="overlay"]');
                for (let m of modals) {
                    if (m.offsetParent !== null) {
                        m.click();
                    }
                }
            """)
        except:
            pass
            
    except Exception as e:
        pass

def claim_daily_rewards(driver, player_id):
    """Claim Daily Rewards with cooldown detection"""
    log("🎁 Claiming Daily Rewards...")
    
    # Check if on cooldown from history
    status = get_reward_status(player_id)
    if not status["daily_available"] and status["daily_status"] == "claimed":
        log(f"⏰ Daily already claimed. Next: {status['daily_next']}")
        return 0
    
    claimed = 0
    try:
        driver.get("https://hub.vertigogames.co/daily-rewards")
        bypass_cloudflare(driver)
        time.sleep(2)
        close_popup(driver)
        
        # Detect cooldowns from page
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
                log(f"✅ Daily Reward Claimed")
                claimed = 1
                time.sleep(2)
                close_popup(driver)
                update_claim_history(player_id, "daily", claimed_count=1)
                break
            else:
                log(f"ℹ️  No claimable daily rewards (attempt {attempt + 1})")
                time.sleep(1)
        
        driver.save_screenshot(f"daily_final_{player_id}.png")
        
    except Exception as e:
        log(f"❌ Daily error: {e}")
    
    return claimed

def physical_click(driver, element):
    """Physical click helper"""
    try:
        driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", element)
        time.sleep(0.5)
        
        try:
            element.click()
            return True
        except:
            try:
                ActionChains(driver).move_to_element(element).click().perform()
                return True
            except:
                try:
                    driver.execute_script("arguments[0].click();", element)
                    return True
                except:
                    return False
    except:
        return False

def ensure_store_page(driver):
    """Ensure we're on store page"""
    try:
        if "store" not in driver.current_url:
            driver.get("https://hub.vertigogames.co/store")
            bypass_cloudflare(driver)
            time.sleep(2)
        return True
    except:
        return False

def claim_store_rewards(driver, player_id):
    """OPTIMIZED Store Claims with Page-Based Cooldown Detection"""
    log("🏪 Claiming Store Rewards (OPTIMIZED + Detection)...")
    claimed = 0
    max_claims = 3
    
    try:
        driver.get("https://hub.vertigogames.co/store")
        bypass_cloudflare(driver)
        time.sleep(2)
        close_popup(driver)
        
        # Detect cooldowns from actual page
        detected = detect_page_cooldowns(driver, player_id, "store")
        
        # Check status after detection
        status = get_reward_status(player_id)
        available_count = sum(status["store_available"])
        
        if available_count == 0:
            log(f"⏰ All store rewards on cooldown (detected from page)")
            return 0
        else:
            log(f"🎯 {available_count}/3 store rewards potentially available")
        
        # FIRST 2 CLAIMS: Physical Click (Optimized to 3 attempts)
        log("🔹 Phase 1: Physical Click Method (Claims 1-2)")
        for attempt in range(3):
            if claimed >= 2:
                break
                
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
                log(f"🖱️ Found Free Button. Clicking...")
                if physical_click(driver, found_btn):
                    time.sleep(4)
                    close_popup(driver)
                    claimed += 1
                    log(f"✅ Store Claim #{claimed} (Physical Click)")
                    update_claim_history(player_id, "store", claimed_count=1, reward_index=claimed)
                    ensure_store_page(driver)
                    time.sleep(1)
            else:
                log(f"ℹ️  No 'Free' buttons in Phase 1 (attempt {attempt+1})")
                if attempt >= 1:
                    break
                time.sleep(1)
        
        # THIRD CLAIM: JavaScript with Fallback (Optimized to 2 attempts)
        if claimed < max_claims:
            log("🔹 Phase 2: JavaScript Method (Claim 3) with Fallback")
            for attempt in range(2):
                if claimed >= max_claims:
                    break
                
                ensure_store_page(driver)
                time.sleep(1)
                
                result = driver.execute_script("""
                    let storeBonusCards = document.querySelectorAll('[class*="StoreBonus"]');
                    if (storeBonusCards.length === 0) {
                        storeBonusCards = document.querySelectorAll('div');
                    }
                    
                    for (let card of storeBonusCards) {
                        let cardText = card.innerText || '';
                        
                        if (cardText.includes('Next in') || cardText.match(/\\d+h\\s+\\d+m/)) {
                            continue;
                        }
                        
                        let buttons = card.querySelectorAll('button');
                        for (let btn of buttons) {
                            let btnText = btn.innerText.trim().toLowerCase();
                            if ((btnText === 'free' || btnText === 'claim') && btn.offsetParent !== null && !btn.disabled) {
                                btn.scrollIntoView({behavior: 'smooth', block: 'center'});
                                btn.click();
                                return true;
                            }
                        }
                    }
                    return false;
                """)
                
                if result:
                    claimed += 1
                    log(f"✅ Store Claim #{claimed} (JavaScript)")
                    time.sleep(4)
                    close_popup(driver)
                    update_claim_history(player_id, "store", claimed_count=1, reward_index=claimed)
                    time.sleep(1)
                    break
                else:
                    log(f"ℹ️  JavaScript found no button. Trying Physical Click...")
                    
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
                        log(f"🖱️ Found Free Button with Physical method")
                        if physical_click(driver, found_btn):
                            time.sleep(4)
                            close_popup(driver)
                            claimed += 1
                            log(f"✅ Store Claim #{claimed} (Physical Fallback)")
                            update_claim_history(player_id, "store", claimed_count=1, reward_index=claimed)
                            ensure_store_page(driver)
                            break
                    else:
                        log(f"ℹ️  No more claims available (attempt {attempt + 1})")
                        break
        
        log(f"📊 Store Claims Complete: {claimed}/{max_claims}")
        driver.save_screenshot(f"store_final_{player_id}.png")
        
    except Exception as e:
        log(f"❌ Store error: {e}")
    
    return claimed

def claim_progression_program_rewards(driver, player_id):
    """Claim Progression - Optimized"""
    log("🎯 Claiming Progression Program...")
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
                log(f"✅ Progression Claim SUCCESS")
                claimed += 1
                time.sleep(2.0)
                close_popup(driver)
            else:
                driver.execute_script("let c=document.querySelectorAll('div');for(let i of c){if(i.scrollWidth>i.clientWidth){i.scrollLeft+=400;}}")
                time.sleep(1)
        
        if claimed > 0:
            update_claim_history(player_id, "progression", claimed_count=claimed)
        
    except: pass
    return claimed

def process_player(player_id):
    """Process single player with optimized retry logic"""
    driver = None
    stats = {"player_id": player_id, "daily": 0, "store": 0, "progression": 0, "status": "Failed"}
    
    # Initialize player history
    init_player_history(player_id)
    
    try:
        log(f"\n🚀 {player_id}")
        driver = create_driver()
        if not login_to_hub(driver, player_id):
            stats['status'] = "Login Failed"
            return stats
        
        # Claim Daily Rewards
        stats['daily'] = claim_daily_rewards(driver, player_id)
        
        # Claim Store Rewards with smart retry (max 2 retries)
        max_store_expected = 3
        store_retry_attempts = 2
        for retry in range(store_retry_attempts):
            stats['store'] = claim_store_rewards(driver, player_id)
            if stats['store'] >= max_store_expected:
                log(f"✅ All {max_store_expected} Store rewards claimed!")
                break
            elif stats['store'] > 0 and retry < store_retry_attempts - 1:
                log(f"⚠️ Got {stats['store']}/{max_store_expected}. Retry {retry + 1}/{store_retry_attempts - 1}...")
                time.sleep(2)
            elif stats['store'] == 0:
                log(f"ℹ️  No store claims (likely on cooldown)")
                break
        
        # Wait for server to process store claims
        if stats['store'] > 0:
            log("⏳ Waiting for server to process store claims...")
            time.sleep(3)
        
        # Claim Progression with optimized retry
        progression_retry_attempts = 2
        for retry in range(progression_retry_attempts):
            claimed = claim_progression_program_rewards(driver, player_id)
            stats['progression'] += claimed
            if claimed == 0 and retry < progression_retry_attempts - 1:
                log(f"⚠️ No progression claimed. Retry {retry + 1}/{progression_retry_attempts - 1}...")
                time.sleep(2)
            elif claimed == 0:
                log(f"ℹ️  No progression rewards available")
                break
            else:
                if retry < progression_retry_attempts - 1:
                    log(f"✅ Claimed {claimed} progression. Checking for more...")
                    time.sleep(1)
        
        total = stats['daily'] + stats['store'] + stats['progression']
        if total > 0:
            stats['status'] = "Success"
        else:
            stats['status'] = "No Rewards"
        
        log(f"🎉 Total: {total} (Daily: {stats['daily']}, Store: {stats['store']}, Progression: {stats['progression']})")
            
    except Exception as e:
        log(f"❌ Error: {e}")
        stats['status'] = "Error"
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass
    return stats

def send_email_summary(results, num_players):
    """Send enhanced email summary with page-based status detection"""
    try:
        sender = os.environ.get("SENDER_EMAIL")
        recipient = os.environ.get("RECIPIENT_EMAIL")
        password = os.environ.get("GMAIL_APP_PASSWORD")
        if not all([sender, recipient, password]): return
        
        total_d = sum(r['daily'] for r in results)
        total_s = sum(r['store'] for r in results)
        total_p = sum(r['progression'] for r in results)
        total_all = total_d + total_s + total_p
        
        ist_now = get_ist_time()
        history = load_claim_history()
        
        # Calculate stats
        on_cooldown = 0
        
        for result in results:
            player_id = result['player_id']
            status = get_reward_status(player_id)
            
            if not status['daily_available']:
                on_cooldown += 1
            
            cooldown_store = sum(1 for x in status['store_available'] if not x)
            on_cooldown += cooldown_store
        
        # Get next recommended run time
        next_run_time = None
        next_run_reason = None
        for player_id in [r['player_id'] for r in results]:
            if player_id in history:
                ph = history[player_id]
                
                if ph['daily']['next_available']:
                    next_time = datetime.fromisoformat(ph['daily']['next_available'])
                    if not next_run_time or next_time < next_run_time:
                        next_run_time = next_time
                        next_run_reason = f"Daily rewards reset"
                
                for i in range(3):
                    reward_key = f"reward_{i+1}"
                    if ph['store'][reward_key]['next_available']:
                        next_time = datetime.fromisoformat(ph['store'][reward_key]['next_available'])
                        if not next_run_time or next_time < next_run_time:
                            next_run_time = next_time
                            next_run_reason = f"Store Reward {i+1} available"
        
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
            .status-available {{ color: #3498db; font-weight: bold; }}
            .legend {{ background: #ecf0f1; padding: 15px; border-radius: 6px; margin-top: 20px; }}
        </style>
        </head>
        <body>
        <div class="container">
        <h2>🎮 Hub Rewards Summary</h2>
        
        <div class="stat-box">
            <div class="stat-row"><strong>📅 Run Time:</strong> <span>{ist_now.strftime('%Y-%m-%d %I:%M %p IST')}</span></div>
            <div class="stat-row"><strong>✅ Claimed This Run:</strong> <span style="font-size: 32px; font-weight: bold;">{total_all}</span></div>
            <div class="stat-row"><strong>⏰ On Cooldown:</strong> <span>{on_cooldown}</span></div>
        </div>
        
        <div class="section">
            <h3 style="margin-top:0;">📊 Current Run Breakdown</h3>
            <div class="stat-row"><strong>🎁 Daily:</strong> {total_d}/{num_players}</div>
            <div class="stat-row"><strong>🏪 Store:</strong> {total_s}/{num_players * 3}</div>
            <div class="stat-row"><strong>🎯 Progression:</strong> {total_p}</div>
        </div>
        """
        
        # Per-player detailed status
        html += """
        <div class="section">
            <h3 style="margin-top:0;">👥 Detailed Player Status</h3>
        """
        
        for result in results:
            player_id = result['player_id']
            status = get_reward_status(player_id)
            
            # Daily status with better indicators
            if result['daily'] > 0:
                daily_status = f'<span class="status-claimed">✅ Claimed This Run</span>'
            elif not status['daily_available']:
                if status['daily_status'] == 'claimed':
                    daily_status = f'<span class="status-cooldown">⏰ Already Claimed - Next in {status["daily_next"]}</span>'
                else:
                    daily_status = f'<span class="status-cooldown">⏰ On Cooldown - Next in {status["daily_next"]}</span>'
            else:
                daily_status = f'<span class="status-available">🔄 Available Now</span>'
            
            # Store status
            store_status_lines = []
            for i in range(3):
                if i < result['store']:
                    store_status_lines.append(f'Reward {i+1}: <span class="status-claimed">✅ Claimed This Run</span>')
                elif not status['store_available'][i]:
                    if status['store_status'][i] == 'claimed':
                        store_status_lines.append(f'Reward {i+1}: <span class="status-cooldown">⏰ Already Claimed - Next in {status["store_next"][i]}</span>')
                    else:
                        store_status_lines.append(f'Reward {i+1}: <span class="status-cooldown">⏰ On Cooldown - Next in {status["store_next"][i]}</span>')
                else:
                    store_status_lines.append(f'Reward {i+1}: <span class="status-available">🔄 Available Now</span>')
            
            # Progression status
            if result['progression'] > 0:
                prog_status = f'<span class="status-claimed">✅ Claimed {result["progression"]} This Run</span>'
            else:
                prog_status = f'<span class="status-cooldown">⏳ Check after Store claims</span>'
            
            html += f"""
            <div class="player-card">
                <strong style="color: #2c3e50; font-size: 16px;">🆔 {player_id}</strong>
                <div style="margin-top: 10px;">
                    <div style="margin: 5px 0;">🎁 <strong>Daily:</strong> {daily_status}</div>
                    <div style="margin: 5px 0;">🏪 <strong>Store:</strong></div>
                    <div style="margin-left: 20px;">
                        {"<br>".join(store_status_lines)}
                    </div>
                    <div style="margin: 5px 0;">🎯 <strong>Progression:</strong> {prog_status}</div>
                </div>
            </div>
            """
        
        html += "</div>"
        
        # Next recommended run
        if next_run_time:
            time_until = format_time_until_reset(next_run_time)
            html += f"""
            <div class="section" style="border-left-color: #e74c3c;">
                <h3 style="margin-top:0; color: #e74c3c;">⏰ Next Recommended Run</h3>
                <div class="stat-row"><strong>⏳ In:</strong> {time_until}</div>
                <div class="stat-row"><strong>📅 Time:</strong> {next_run_time.strftime('%I:%M %p IST')}</div>
                <div class="stat-row"><strong>📝 Reason:</strong> {next_run_reason}</div>
            </div>
            """
        
        # Legend
        html += """
        <div class="legend">
            <h4 style="margin-top:0;">💡 Status Legend</h4>
            <div><span class="status-claimed">✅ Claimed This Run</span> - Successfully claimed in this run</div>
            <div><span class="status-cooldown">⏰ Already Claimed</span> - Claimed in previous run, waiting for cooldown</div>
            <div><span class="status-cooldown">⏰ On Cooldown</span> - Detected cooldown on page</div>
            <div><span class="status-available">🔄 Available Now</span> - Should be claimable</div>
            <div style="margin-top:10px;"><strong>Notes:</strong></div>
            <ul style="margin: 5px 0;">
                <li><strong>Daily Rewards:</strong> Reset at 5:30 AM IST daily</li>
                <li><strong>Store Rewards:</strong> Each reward has individual 24h cooldown from claim time</li>
                <li><strong>Progression:</strong> Depends on Store rewards (bullets/grenades)</li>
            </ul>
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
    except Exception as e:
        log(f"❌ Email error: {e}")

def main():
    log("="*60)
    log("CS HUB AUTO-CLAIMER v2.1 (Optimized + Page Detection)")
    log("="*60)
    
    players = []
    try:
        with open(PLAYER_ID_FILE, 'r') as f:
            reader = csv.DictReader(f)
            players = [row['player_id'].strip() for row in reader if row['player_id'].strip()]
    except: 
        log("❌ Could not read players.csv")
        return
    
    results = []
    for player_id in players:
        stats = process_player(player_id)
        results.append(stats)
        time.sleep(3)
    
    send_email_summary(results, len(players))
    log("🏁 Done!")

if __name__ == "__main__":
    main()
