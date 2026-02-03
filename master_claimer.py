import csv
import time
import os
import smtplib
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
from selenium.common.exceptions import TimeoutException, NoSuchElementException

PLAYER_ID_FILE = "players.csv"
HEADLESS = True

# Daily tracking constants
DAILY_RESET_HOUR_IST = 5
DAILY_RESET_MINUTE_IST = 30
EXPECTED_STORE_PER_PLAYER = 3

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

# IST timezone helper functions
def get_ist_time():
    """Get current time in IST (UTC+5:30)"""
    utc_now = datetime.utcnow()
    ist_offset = timedelta(hours=5, minutes=30)
    return utc_now + ist_offset

def get_current_daily_window_start():
    """Get the start of current daily window (5:30 AM IST)"""
    ist_now = get_ist_time()
    if ist_now.hour < DAILY_RESET_HOUR_IST or (ist_now.hour == DAILY_RESET_HOUR_IST and ist_now.minute < DAILY_RESET_MINUTE_IST):
        window_start = ist_now.replace(hour=DAILY_RESET_HOUR_IST, minute=DAILY_RESET_MINUTE_IST, second=0, microsecond=0) - timedelta(days=1)
    else:
        window_start = ist_now.replace(hour=DAILY_RESET_HOUR_IST, minute=DAILY_RESET_MINUTE_IST, second=0, microsecond=0)
    return window_start

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
    hours, remainder = divmod(delta.seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    return f"{hours}h {minutes}m"

def create_driver():
    """GitHub Actions-compatible driver - FORCED CHROME 144"""
    
    # Attempt to initialize driver up to 3 times
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
            # Helps with port conflicts
            options.add_argument("--remote-debugging-port=0") 

            # Stealth preferences
            prefs = {
                "profile.default_content_setting_values": {
                    "images": 2,
                    "notifications": 2,
                    "popups": 2,
                }
            }
            options.add_experimental_option("prefs", prefs)

            # FORCE Version 144 to match GitHub Runner
            driver = uc.Chrome(options=options, version_main=144, use_subprocess=True)
            
            driver.set_page_load_timeout(30)
            driver.set_script_timeout(30)
            log("✅ Driver initialized (v144)")
            return driver

        except Exception as e:
            log(f"⚠️ Driver init attempt {attempt+1} failed: {str(e)[:100]}")
            time.sleep(2) # Wait briefly before retry
            
            # If it's the last attempt, raise the error
            if attempt == 2:
                log(f"❌ All driver init attempts failed")
                raise

def bypass_cloudflare(driver):
    """Specifically handle the 'Verifying you are human' screen"""
    try:
        time.sleep(2)
        title = driver.title.lower()
        source = driver.page_source.lower()
        
        # Check if we are on a challenge page
        if "just a moment" in title or "verifying" in source or "hub.vertigogames.co" in title:
            log("🛡️ Cloudflare Challenge detected. Attempting bypass...")
            
            # Wait a bit for the automatic JS check
            time.sleep(5)
            
            # Check if we got through just by waiting
            if "daily rewards" in driver.title.lower() or "login" in driver.page_source.lower():
                log("✅ Passed Cloudflare (Automatic)")
                return True

            # Look for the Turnstile/Challenge checkbox
            try:
                # Try locating the checkbox directly (common in Turnstile)
                checkbox = driver.find_elements(By.XPATH, "//input[@type='checkbox']")
                if checkbox:
                    checkbox[0].click()
                    log("✅ Clicked Verification Checkbox")
                    time.sleep(3)
            except:
                pass
            
            # Wait up to 15 seconds for redirect
            for _ in range(15):
                if "daily-rewards" in driver.current_url or "hub.vertigogames.co" in driver.current_url:
                    # If the page source has changed significantly
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
        
        # --- NEW: Run Cloudflare Bypass immediately ---
        bypass_cloudflare(driver)
        # ----------------------------------------------

        time.sleep(1)
        driver.save_screenshot(f"01_page_loaded_{player_id}.png")
        
        accept_cookies(driver)
        
        # Login button detection
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
        
        time.sleep(0.5)
        driver.save_screenshot(f"02_login_clicked_{player_id}.png")
        
        # Input field detection
        input_selectors = [
            "#user-id-input",
            "//input[contains(@placeholder, 'ID') or contains(@placeholder, 'User') or contains(@name, 'user') or contains(@placeholder, 'id')]",
            "//input[@type='text']",
            "//input[contains(@class, 'input')]",
            "//div[contains(@class, 'modal') or contains(@class, 'dialog')]//input[@type='text']",
        ]
        
        input_found = False
        input_box = None
        for selector in input_selectors:
            try:
                if selector.startswith("#"):
                    input_box = WebDriverWait(driver, 3).until(
                        EC.visibility_of_element_located((By.ID, selector[1:]))
                    )
                else:
                    input_box = WebDriverWait(driver, 3).until(
                        EC.visibility_of_element_located((By.XPATH, selector))
                    )
                log("✅ Input field found")
                input_box.clear()
                input_box.send_keys(player_id)
                time.sleep(0.1)
                input_found = True
                break
            except:
                continue
        
        if not input_found:
            log("❌ No input field found")
            driver.save_screenshot(f"03_input_not_found_{player_id}.png")
            return False
        
        driver.save_screenshot(f"03_input_entered_{player_id}.png")
        
        # Login CTA detection
        login_cta_selectors = [
            "//button[contains(text(), 'Login') or contains(text(), 'Log in') or contains(text(), 'Sign in')]",
            "//button[@type='submit']",
            "//div[contains(@class, 'modal') or contains(@class, 'dialog')]//button[not(contains(text(), 'Cancel')) and not(contains(text(), 'Close'))]",
            "//button[contains(@class, 'primary') or contains(@class, 'submit')]",
        ]
        
        login_cta_clicked = False
        for selector in login_cta_selectors:
            try:
                btn = WebDriverWait(driver, 2).until(
                    EC.element_to_be_clickable((By.XPATH, selector))
                )
                btn.click()
                login_cta_clicked = True
                log("✅ Login CTA clicked")
                break
            except:
                continue
        
        if not login_cta_clicked:
            try:
                input_box.send_keys(Keys.ENTER)
                log("⏎ Enter key pressed")
            except:
                log("❌ Login CTA not found")
                driver.save_screenshot(f"04_cta_not_found_{player_id}.png")
                return False
        
        time.sleep(1)
        driver.save_screenshot(f"04_submitted_{player_id}.png")
        
        # Wait for login completion
        log("⏳ Waiting for login...")
        start_time = time.time()
        max_wait = 12
        
        while time.time() - start_time < max_wait:
            try:
                current_url = driver.current_url
                if "user" in current_url.lower() or "dashboard" in current_url.lower() or "daily-rewards" in current_url.lower():
                    log("✅ Login verified (URL)")
                    driver.save_screenshot(f"05_login_success_{player_id}.png")
                    return True
                
                user_elements = driver.find_elements(
                    By.XPATH,
                    "//button[contains(text(),'Logout') or contains(text(),'Profile') or contains(@class,'user')]"
                )
                if user_elements:
                    log("✅ Login verified (Logout button)")
                    driver.save_screenshot(f"05_login_success_{player_id}.png")
                    return True
                
                time.sleep(0.3)
            except:
                time.sleep(0.3)
        
        log("❌ Login verification timeout")
        driver.save_screenshot(f"05_login_timeout_{player_id}.png")
        return False
        
    except Exception as e:
        log(f"❌ Login exception: {e}")
        try:
            driver.save_screenshot(f"99_exception_{player_id}.png")
        except:
            pass
        return False

def close_popup(driver):
    """Multi-method popup closing strategy"""
    try:
        # log("Checking for popup...") # Reduced verbosity
        time.sleep(0.5)
        
        popup_selectors = [
            "//div[contains(@class, 'modal') and not(contains(@style, 'display: none'))]",
            "//div[contains(@class, 'popup') and not(contains(@style, 'display: none'))]",
            "//div[@data-testid='item-popup-content']",
            "//div[contains(@class, 'dialog') and not(contains(@style, 'display: none'))]",
        ]
        
        popup_found = False
        for selector in popup_selectors:
            try:
                popup_elements = driver.find_elements(By.XPATH, selector)
                visible_popups = [elem for elem in popup_elements if elem.is_displayed()]
                if visible_popups:
                    popup_found = True
                    # log(f"✓ Popup detected")
                    break
            except:
                continue
        
        if not popup_found:
            return True
        
        # METHOD 1: Continue button
        continue_selectors = [
            "//button[normalize-space()='Continue']",
            "//button[contains(text(), 'Continue')]",
            "//button[contains(@class, 'continue')]",
            "//*[contains(text(), 'Continue') and (self::button or self::a)]",
        ]
        
        for selector in continue_selectors:
            try:
                continue_btn = driver.find_element(By.XPATH, selector)
                if continue_btn.is_displayed() and continue_btn.is_enabled():
                    try:
                        continue_btn.click()
                    except:
                        driver.execute_script("arguments[0].click();", continue_btn)
                    
                    # log("✓ Continue clicked")
                    time.sleep(0.8)
                    return True
            except:
                continue
        
        # METHOD 2: Close button
        close_selectors = [
            "//button[normalize-space()='Close']",
            "//button[contains(@class, 'close')]",
            "//button[contains(@aria-label, 'Close')]",
            "//*[contains(@class, 'close') and (self::button or self::span or self::div[@role='button'])]",
            "//button[text()='×' or text()='X' or text()='✕']",
            "//*[@data-testid='close-button']",
            "//*[contains(@class, 'icon-close')]",
            "//*[name()='svg']/parent::button",
        ]
        
        for selector in close_selectors:
            try:
                close_btn = driver.find_element(By.XPATH, selector)
                if close_btn.is_displayed():
                    try:
                        close_btn.click()
                    except:
                        driver.execute_script("arguments[0].click();", close_btn)
                    
                    # log("✓ Close clicked")
                    time.sleep(0.8)
                    return True
            except:
                continue
        
        # METHOD 3: ESC key
        try:
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            time.sleep(0.5)
            # log("✓ ESC pressed")
            return True
        except:
            pass
        
        return False
        
    except Exception as e:
        # log(f"❌ Popup close error: {e}")
        return False

def ensure_store_page(driver):
    """Check if on Store page"""
    try:
        current_url = driver.current_url
        
        if "/store" in current_url.lower():
            log("✓ On Store page")
            return True
        
        log(f"⚠️  Not on Store, navigating...")
        driver.get("https://hub.vertigogames.co/store")
        time.sleep(0.7)
        
        if "/store" in driver.current_url.lower():
            log("✓ Back on Store")
            return True
        else:
            log("❌ Failed to reach Store")
            return False
            
    except Exception as e:
        log(f"❌ Error: {e}")
        return False

def click_daily_rewards_tab(driver):
    """Click Daily Rewards TAB with horizontal scroll"""
    log("Clicking Daily Rewards tab...")
    
    try:
        result = driver.execute_script("""
            let allElements = document.querySelectorAll('*');
            
            for (let elem of allElements) {
                if (elem.innerText && elem.innerText.includes('Daily Rewards')) {
                    let className = elem.className || '';
                    if (!className.toLowerCase().includes('tab')) {
                        let parent = elem.parentElement;
                        let parentClass = parent ? (parent.className || '') : '';
                        if (!parentClass.toLowerCase().includes('tab')) {
                            continue;
                        }
                    }
                    
                    // Skip sidebar
                    let parent = elem.parentElement;
                    let parentClass = parent ? (parent.className || '') : '';
                    if (parentClass.includes('sidebar') || parentClass.includes('menu') || parentClass.includes('side')) {
                        continue;
                    }
                    
                    // Scroll horizontally to make visible
                    elem.scrollIntoView({behavior: 'smooth', block: 'nearest', inline: 'center'});
                    
                    setTimeout(() => {
                        elem.click();
                    }, 800);
                    
                    return true;
                }
            }
            return false;
        """)
        
        if result:
            log("✅ Daily Rewards tab clicked")
            time.sleep(1.0)
            return True
    except Exception as e:
        log(f"❌ Tab click failed: {e}")
    
    return False

def navigate_to_daily_rewards_section_store(driver):
    """Navigate to Daily Rewards section in Store"""
    log("Navigating to Daily Rewards section...")
    ensure_store_page(driver)
    close_popup(driver)
    time.sleep(0.3)
    
    tab_clicked = click_daily_rewards_tab(driver)
    if tab_clicked:
        log("✅ In Daily Rewards section")
        time.sleep(0.7)
        return True
    else:
        log("⚠️  Tab navigation failed")
        return False

def claim_daily_rewards(driver, player_id):
    """Claim daily rewards page - WITH TIMER CHECK"""
    log("🎁 Claiming Daily Rewards...")
    claimed = 0
    
    try:
        driver.get("https://hub.vertigogames.co/daily-rewards")
        bypass_cloudflare(driver) # Check here too
        time.sleep(1.5)
        
        for _ in range(2):
            close_popup(driver)
        
        for attempt in range(10):
            result = driver.execute_script("""
                let buttons = document.querySelectorAll('button');
                for (let btn of buttons) {
                    let text = btn.innerText.trim().toLowerCase();
                    if ((text === 'claim' || text === 'free') && btn.offsetParent !== null) {
                        
                        // Check for timer in parent hierarchy
                        let isBlocked = false;
                        let parent = btn.parentElement;
                        for(let i=0; i<5; i++) { 
                            if(parent) {
                                let pText = parent.innerText || '';
                                if (pText.includes("Next in") || pText.match(/\\d{2}:\\d{2}:\\d{2}/)) { 
                                    isBlocked = true;
                                    break;
                                }
                                parent = parent.parentElement;
                            }
                        }
                        if (isBlocked) { continue; }

                        if (!btn.innerText.toLowerCase().includes('buy') && 
                            !btn.innerText.toLowerCase().includes('purchase')) {
                            btn.click();
                            return true;
                        }
                    }
                }
                return false;
            """)
            
            if result:
                log(f"✅ Daily #{claimed + 1}")
                claimed += 1
                time.sleep(1.5)
                close_popup(driver)
            else:
                log("ℹ️  No more daily rewards")
                break
        
        driver.save_screenshot(f"daily_final_{player_id}.png")
        
    except Exception as e:
        log(f"❌ Daily error: {e}")
    
    return claimed

def claim_store_rewards(driver, player_id):
    """Claim Store Daily Rewards - FALSE CLAIM FIX"""
    log("🏪 Claiming Store...")
    claimed = 0
    max_claims = 3
    
    try:
        driver.get("https://hub.vertigogames.co/store")
        bypass_cloudflare(driver)
        time.sleep(2)
        
        for _ in range(2):
            close_popup(driver)
        
        if not ensure_store_page(driver):
            log("❌ Cannot access Store")
            return 0
        
        if not navigate_to_daily_rewards_section_store(driver):
            log("⚠️  Navigation failed")
        
        time.sleep(0.5)
        driver.save_screenshot(f"store_01_ready_{player_id}.png")
        
        # Claim loop
        for attempt in range(max_claims):
            log(f"\n--- Store Claim Attempt {attempt + 1}/{max_claims} ---")
            
            if attempt > 0:
                log("Re-navigating to Daily Rewards section...")
                if not navigate_to_daily_rewards_section_store(driver):
                    log("⚠️  Re-navigation failed")
                    break
                time.sleep(0.5)
            
            # Find and click ONLY green "Free" buttons (SKIP buttons with timers)
            result = driver.execute_script("""
                let allButtons = document.querySelectorAll('button');
                
                for (let btn of allButtons) {
                    let btnText = btn.innerText.trim().toLowerCase();
                    
                    if ((btnText === 'claim' || btnText === 'free') && btn.offsetParent !== null && !btn.disabled) {
                        
                        // Check for timer in parent hierarchy (up to 5 levels up)
                        let isBlocked = false;
                        let parent = btn.parentElement;
                        
                        for(let i=0; i<5; i++) { 
                            if(parent) {
                                let pText = parent.innerText || '';
                                // Look for "Next in" or typical timer format 00:00:00
                                if (pText.includes("Next in") || pText.match(/\\d{2}:\\d{2}:\\d{2}/)) { 
                                    isBlocked = true;
                                    break;
                                }
                                parent = parent.parentElement;
                            }
                        }
                        
                        if (isBlocked) {
                            console.log("Skipping button with active timer");
                            continue;
                        }

                        // If we are here, it's valid
                        btn.scrollIntoView({behavior: 'smooth', block: 'center'});
                        
                        setTimeout(function() {
                            btn.click();
                            console.log('✅ Clicked Valid Claim/Free button');
                        }, 500);
                        
                        return true;
                    }
                }
                
                console.log('No valid claim buttons found');
                return false;
            """)
            
            if result:
                log(f"✅ Store Claim #{claimed + 1} SUCCESS")
                claimed += 1
                time.sleep(1.5)
                
                log("Handling post-claim popup...")
                close_popup(driver)
                time.sleep(0.5)
                
                if not ensure_store_page(driver):
                    log("⚠️  Lost Store page")
                    break
                
                time.sleep(0.3)
            else:
                log(f"ℹ️  No more available claims (attempt {attempt + 1})")
                break
        
        log(f"\n{'='*60}")
        log(f"Store Claims Complete: {claimed}/{max_claims}")
        log(f"{'='*60}")
        
        driver.save_screenshot(f"store_final_{player_id}.png")
        
    except Exception as e:
        log(f"❌ Store error: {e}")
        try:
            driver.save_screenshot(f"store_error_{player_id}.png")
        except:
            pass
    
    return claimed

def claim_progression_program_rewards(driver, player_id):
    """Claim Progression Program rewards - WITH TIMER CHECK"""
    log("🎯 Claiming Progression Program...")
    claimed = 0
    
    try:
        driver.get("https://hub.vertigogames.co/progression-program")
        bypass_cloudflare(driver)
        time.sleep(2)
        
        for _ in range(2):
            close_popup(driver)
        
        time.sleep(0.5)
        driver.save_screenshot(f"progression_01_ready_{player_id}.png")
        
        max_attempts = 8
        for attempt in range(max_attempts):
            log(f"\n--- Progression Claim Attempt {attempt + 1}/{max_attempts} ---")
            
            result = driver.execute_script("""
                let allButtons = document.querySelectorAll('button');
                let claimButtons = [];
                
                for (let btn of allButtons) {
                    let btnText = btn.innerText.trim().toLowerCase();
                    
                    if (btnText === 'claim') {
                        if (btn.offsetParent !== null && !btn.disabled) {
                            
                            // Check for timer (Safety)
                            let isBlocked = false;
                            let parent = btn.parentElement;
                            for(let i=0; i<5; i++) { 
                                if(parent) {
                                    let pText = parent.innerText || '';
                                    if (pText.includes("Next in") || pText.match(/\\d{2}:\\d{2}:\\d{2}/)) { 
                                        isBlocked = true;
                                        break;
                                    }
                                    parent = parent.parentElement;
                                }
                            }
                            if (isBlocked) { continue; }

                            let parentText = btn.parentElement ? (btn.parentElement.innerText || '') : '';
                            if (!parentText.includes('Delivered')) {
                                claimButtons.push(btn);
                            }
                        }
                    }
                }
                
                console.log('Found ' + claimButtons.length + ' claim buttons');
                
                if (claimButtons.length > 0) {
                    let btn = claimButtons[0];
                    btn.scrollIntoView({behavior: 'smooth', block: 'center', inline: 'center'});
                    
                    setTimeout(function() {
                        btn.click();
                        console.log('✅ Clicked Progression Claim button');
                    }, 600);
                    
                    return true;
                }
                
                console.log('No more claim buttons found');
                return false;
            """)
            
            if result:
                log(f"✅ Progression Claim #{claimed + 1} SUCCESS")
                claimed += 1
                time.sleep(1.5)
                
                log("Handling post-claim popup...")
                close_popup(driver)
                time.sleep(0.5)
            else:
                log(f"ℹ️  No more claim buttons (attempt {attempt + 1})")
                
                if attempt < max_attempts - 1:
                    log("Scrolling horizontally...")
                    try:
                        driver.execute_script("""
                            let containers = document.querySelectorAll('div');
                            for (let container of containers) {
                                if (container.scrollWidth > container.clientWidth) {
                                    container.scrollLeft += 400;
                                    console.log('Scrolled right');
                                    break;
                                }
                            }
                        """)
                        log("✓ Scrolled right")
                        time.sleep(1)
                    except:
                        log("⚠️  Scroll failed")
                        break
                else:
                    break
        
        log(f"\n{'='*60}")
        log(f"Progression Claims Complete: {claimed}")
        log(f"{'='*60}")
        
        driver.save_screenshot(f"progression_final_{player_id}.png")
        
    except Exception as e:
        log(f"❌ Progression error: {e}")
        try:
            driver.save_screenshot(f"progression_error_{player_id}.png")
        except:
            pass
    
    return claimed

def process_player(player_id):
    """Process single player - ALL reward pages IN CORRECT ORDER"""
    driver = None
    stats = {"player_id": player_id, "daily": 0, "store": 0, "progression": 0, "status": "Failed"}
    
    try:
        log(f"\n{'='*60}")
        log(f"🚀 {player_id}")
        log(f"{'='*60}")
        
        driver = create_driver()
        
        if not login_to_hub(driver, player_id):
            stats['status'] = "Login Failed"
            return stats
        
        # CORRECT ORDER: Daily → Store (earn grenades) → Progression (use grenades)
        stats['daily'] = claim_daily_rewards(driver, player_id)
        stats['store'] = claim_store_rewards(driver, player_id)
        stats['progression'] = claim_progression_program_rewards(driver, player_id)
        
        total = stats['daily'] + stats['store'] + stats['progression']
        
        if total > 0:
            stats['status'] = "Success"
            log(f"🎉 Total: {total} (D:{stats['daily']} S:{stats['store']} P:{stats['progression']})")
        else:
            stats['status'] = "No Rewards"
            log("⚠️  None claimed")
        
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
    """Send email with daily tracking stats"""
    try:
        sender = os.environ.get("SENDER_EMAIL")
        recipient = os.environ.get("RECIPIENT_EMAIL")
        password = os.environ.get("GMAIL_APP_PASSWORD")
        
        if not all([sender, recipient, password]):
            log("⚠️  Email env vars missing")
            return
        
        # Calculate totals
        total_d = sum(r['daily'] for r in results)
        total_s = sum(r['store'] for r in results)
        total_p = sum(r['progression'] for r in results)
        total_all = total_d + total_s + total_p
        
        success_count = sum(1 for r in results if r['status'] == 'Success')
        
        # Daily tracking calculations
        expected_store_total = num_players * EXPECTED_STORE_PER_PLAYER
        store_progress_pct = int((total_s / expected_store_total) * 100) if expected_store_total > 0 else 0
        
        # Time calculations
        ist_now = get_ist_time()
        window_start = get_current_daily_window_start()
        next_reset = get_next_daily_reset()
        time_until_reset = format_time_until_reset(next_reset)
        hours_since_reset = int((ist_now - window_start).total_seconds() // 3600)
        
        # Build enhanced HTML email
        html = f"""
        <html>
        <body style="font-family: Arial, sans-serif;">
        <h2>🎮 Hub Rewards Summary</h2>
        
        <div style="background-color: #f0f8ff; padding: 15px; border-radius: 8px; margin-bottom: 20px;">
            <h3 style="margin-top: 0;">📊 Daily Window Tracking (5:30 AM IST Reset)</h3>
            <table style="width: 100%; border-collapse: collapse;">
                <tr>
                    <td style="padding: 5px;"><strong>Current Time:</strong></td>
                    <td>{ist_now.strftime('%Y-%m-%d %I:%M %p IST')}</td>
                </tr>
                <tr>
                    <td style="padding: 5px;"><strong>Window Started:</strong></td>
                    <td>{window_start.strftime('%Y-%m-%d %I:%M %p IST')} ({hours_since_reset}h ago)</td>
                </tr>
                <tr>
                    <td style="padding: 5px;"><strong>Next Reset:</strong></td>
                    <td>{next_reset.strftime('%Y-%m-%d %I:%M %p IST')} (in {time_until_reset})</td>
                </tr>
            </table>
        </div>
        
        <div style="background-color: #fff3cd; padding: 15px; border-radius: 8px; margin-bottom: 20px;">
            <h3 style="margin-top: 0;">📈 Today's Cumulative Stats (Since 5:30 AM)</h3>
            <table style="width: 100%; border-collapse: collapse;">
                <tr>
                    <td style="padding: 5px;"><strong>💰 Total Daily:</strong></td>
                    <td><strong>{total_d}</strong> (varies per player)</td>
                </tr>
                <tr style="background-color: {'#d4edda' if total_s == expected_store_total else '#fff3cd'};">
                    <td style="padding: 5px;"><strong>🏪 Total Store:</strong></td>
                    <td><strong>{total_s} / {expected_store_total}</strong> ({store_progress_pct}%) {'✅ COMPLETE' if total_s == expected_store_total else f'⚠️ {expected_store_total - total_s} remaining'}</td>
                </tr>
                <tr>
                    <td style="padding: 5px;"><strong>🎯 Total Progression:</strong></td>
                    <td><strong>{total_p}</strong> (grenade-dependent)</td>
                </tr>
                <tr style="background-color: #e7f3ff;">
                    <td style="padding: 5px;"><strong>🎁 TOTAL ALL:</strong></td>
                    <td><strong style="font-size: 1.2em;">{total_all}</strong> claims</td>
                </tr>
            </table>
        </div>
        
        <h3>👥 Per-Player Breakdown (This Run)</h3>
        <table border="1" cellpadding="5" cellspacing="0" style="border-collapse: collapse; width: 100%;">
        <tr style="background-color: #f0f0f0;">
            <th>ID</th><th>Daily</th><th>Store</th><th>Progression</th><th>Total</th><th>Status</th>
        </tr>
        """
        
        for r in results:
            total_player = r['daily'] + r['store'] + r['progression']
            
            if r['status'] == 'Success':
                status_color = "#90EE90"
            elif r['status'] == 'No Rewards':
                status_color = "#FFE4B5"
            else:
                status_color = "#FFB6C1"
            
            row_html = f"""<tr>
                <td>{r['player_id']}</td>
                <td>{r['daily']}</td>
                <td>{r['store']}{' ✅' if r['store'] == EXPECTED_STORE_PER_PLAYER else ''}</td>
                <td>{r['progression']}</td>
                <td><strong>{total_player}</strong></td>
                <td style="background-color: {status_color};">{r['status']}</td>
            </tr>"""
            html += row_html
        
        html += f"""
        <tr style="background-color: #e0e0e0; font-weight: bold;">
            <td>TOTAL</td>
            <td>{total_d}</td>
            <td>{total_s}</td>
            <td>{total_p}</td>
            <td>{total_all}</td>
            <td>{success_count}/{len(results)}</td>
        </tr>
        </table>
        
        <div style="margin-top: 20px; padding: 10px; background-color: #f9f9f9; border-left: 4px solid #4CAF50;">
            <p style="margin: 5px 0;"><strong>💡 Note:</strong></p>
            <ul style="margin: 5px 0;">
                <li><strong>Store Rewards:</strong> Exactly 3 per player per day (Available after exactly 24 hours of Claiming)</li>
                <li><strong>Daily Rewards:</strong> Variable (resets at 5:30 AM IST)</li>
                <li><strong>Progression:</strong> Unlimited (requires Grenades/Bullets from Store claims)</li>
            </ul>
        </div>
        
        <p style="margin-top: 20px; color: #666; font-size: 0.9em;">
            🤖 Automated run at {ist_now.strftime('%Y-%m-%d %I:%M %p IST')}
        </p>
        </body>
        </html>
        """
        
        msg = MIMEMultipart('alternative')
        msg['Subject'] = f"Hub Rewards - {ist_now.strftime('%d-%b %I:%M %p')} IST ({total_all} claims)"
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
    """Main orchestrator"""
    log("="*60)
    log("CS HUB AUTO-CLAIMER v2.8 (Universal Timer Fix)")
    log("="*60)
    
    # Show IST tracking info
    ist_now = get_ist_time()
    window_start = get_current_daily_window_start()
    next_reset = get_next_daily_reset()
    
    log(f"🕐 IST: {ist_now.strftime('%Y-%m-%d %I:%M %p')}")
    log(f"⏰ Next Reset: {format_time_until_reset(next_reset)}")
    log("")
    
    # Read players
    players = []
    try:
        with open(PLAYER_ID_FILE, 'r') as f:
            reader = csv.DictReader(f)
            players = [row['player_id'].strip() for row in reader if row['player_id'].strip()]
    except Exception as e:
        log(f"❌ Cannot read {PLAYER_ID_FILE}: {e}")
        return
    
    num_players = len(players)
    log(f"📋 {num_players} player(s)")
    log("")
    
    results = []
    
    # Process each player
    for player_id in players:
        stats = process_player(player_id)
        results.append(stats)
        time.sleep(3)
    
    # Final summary
    log("")
    log("="*60)
    log("FINAL SUMMARY")
    log("="*60)
    
    total_d = sum(r['daily'] for r in results)
    total_s = sum(r['store'] for r in results)
    total_p = sum(r['progression'] for r in results)
    
    log(f"Daily: {total_d}, Store: {total_s}/{num_players * EXPECTED_STORE_PER_PLAYER}, Progression: {total_p}")
    
    for r in results:
        total = r['daily'] + r['store'] + r['progression']
        log(f"{r['player_id']}: D={r['daily']}, S={r['store']}, P={r['progression']}, Total={total} → {r['status']}")
    
    # Send email with num_players parameter
    send_email_summary(results, num_players)
    
    log("")
    log("🏁 Done!")

if __name__ == "__main__":
    main()
