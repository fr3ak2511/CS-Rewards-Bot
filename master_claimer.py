import csv
import time
import os
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager

PLAYER_ID_FILE = "players.csv"
STORE_LOG_FILE = "store_claims_log.csv"
HEADLESS = True

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def create_driver():
    """GitHub Actions-compatible driver"""
    options = Options()
    if HEADLESS:
        options.add_argument("--headless=new")
    
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--incognito")
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-logging")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-web-security")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-default-apps")
    options.add_argument("--disable-features=VizDisplayCompositor")
    options.add_argument("--disable-background-timer-throttling")
    options.add_argument("--disable-renderer-backgrounding")
    options.add_argument("--disable-backgrounding-occluded-windows")
    
    prefs = {
        "profile.default_content_setting_values": {
            "images": 2,
            "notifications": 2,
            "popups": 2,
        },
        "profile.default_content_settings.popups": 0,
        "profile.managed_default_content_settings.popups": 0,
    }
    options.add_experimental_option("prefs", prefs)
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    
    try:
        driver_path = ChromeDriverManager().install()
    except:
        driver_path = "/usr/bin/chromedriver"
    
    service = Service(driver_path)
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(30)
    driver.set_script_timeout(30)
    return driver

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
        log("ℹ️ No cookie banner")

def login_to_hub(driver, player_id):
    """Login using multi-selector strategy"""
    log(f"🔐 Logging in: {player_id}")
    try:
        driver.get("https://hub.vertigogames.co/daily-rewards")
        time.sleep(0.4)
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
        log("Checking for popup...")
        time.sleep(0.8)
        
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
                    log(f"✓ Popup detected")
                    break
            except:
                continue
        
        if not popup_found:
            log("No popup detected")
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
                    
                    log("✓ Continue clicked")
                    time.sleep(0.8)
                    
                    popup_still_visible = False
                    for ps in popup_selectors:
                        try:
                            popup_elements = driver.find_elements(By.XPATH, ps)
                            if any(elem.is_displayed() for elem in popup_elements):
                                popup_still_visible = True
                                break
                        except:
                            continue
                    
                    if not popup_still_visible:
                        log("✅ Popup closed via Continue")
                        return True
                    break
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
                    
                    log("✓ Close clicked")
                    time.sleep(0.8)
                    
                    popup_still_visible = False
                    for ps in popup_selectors:
                        try:
                            popup_elements = driver.find_elements(By.XPATH, ps)
                            if any(elem.is_displayed() for elem in popup_elements):
                                popup_still_visible = True
                                break
                        except:
                            continue
                    
                    if not popup_still_visible:
                        log("✅ Popup closed via Close button")
                        return True
                    break
            except:
                continue
        
        # METHOD 3: ESC key
        try:
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            time.sleep(0.5)
            log("✓ ESC pressed")
            return True
        except:
            pass
        
        log("⚠️ Popup may still be visible")
        return False
        
    except Exception as e:
        log(f"❌ Popup close error: {e}")
        return False

def ensure_store_page(driver):
    """Check if on Store page"""
    try:
        current_url = driver.current_url
        if "/store" in current_url.lower():
            log("✓ On Store page")
            return True
        
        log(f"⚠️ Not on Store, navigating...")
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
        log("⚠️ Tab navigation failed")
        return False

def get_last_store_claim_time(player_id):
    """Get last claim timestamp from CSV log"""
    try:
        if not os.path.exists(STORE_LOG_FILE):
            return None
        
        with open(STORE_LOG_FILE, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row['player_id'] == player_id:
                    return datetime.fromisoformat(row['last_claim_timestamp'])
        return None
    except Exception as e:
        log(f"⚠️ Error reading store log: {e}")
        return None

def log_store_claim(player_id):
    """Log successful store claim with timestamp"""
    try:
        file_exists = os.path.exists(STORE_LOG_FILE)
        
        # Read existing data
        existing_data = {}
        if file_exists:
            with open(STORE_LOG_FILE, 'r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    existing_data[row['player_id']] = row['last_claim_timestamp']
        
        # Update timestamp for this player
        existing_data[player_id] = datetime.now().isoformat()
        
        # Write updated data
        with open(STORE_LOG_FILE, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['player_id', 'last_claim_timestamp'])
            writer.writeheader()
            for pid, timestamp in existing_data.items():
                writer.writerow({'player_id': pid, 'last_claim_timestamp': timestamp})
        
        log(f"📝 Updated store log: {player_id}")
    except Exception as e:
        log(f"⚠️ Error writing store log: {e}")

def check_store_availability(driver, player_id):
    """
    CRITICAL FIX: Check if Store rewards are actually available before attempting claims
    Returns: (is_available: bool, hours_since_last_claim: float)
    """
    try:
        # Check last claim time from log
        last_claim = get_last_store_claim_time(player_id)
        if last_claim:
            hours_since = (datetime.now() - last_claim).total_seconds() / 3600
            log(f"ℹ️ Last store claim: {hours_since:.1f}h ago")
        else:
            hours_since = 100.0  # No record = assume available
            log(f"ℹ️ No store claim history")
        
        # Check for green "Claim" buttons on page
        available_count = driver.execute_script("""
            let allDivs = document.querySelectorAll('div');
            let storeBonusCards = [];
            
            for (let div of allDivs) {
                let text = div.innerText || '';
                if (text.includes('Store Bonus') && text.includes('+1')) {
                    let parent = div.parentElement;
                    let attempts = 0;
                    while (parent && attempts < 5) {
                        let parentText = parent.innerText || '';
                        if (parentText.includes('Gold (Daily)') || 
                            parentText.includes('Cash (Daily)') || 
                            parentText.includes('Luckyloon (Daily)')) {
                            storeBonusCards.push(parent);
                            break;
                        }
                        parent = parent.parentElement;
                        attempts++;
                    }
                }
            }
            
            // Count cards WITH green "Claim" button (NO timer)
            let availableCount = 0;
            for (let card of storeBonusCards) {
                let cardText = card.innerText || '';
                
                // SKIP cards with timer
                if (cardText.includes('Next in') || cardText.match(/\\d+h\\s+\\d+m/)) {
                    continue;
                }
                
                // Check for green "Claim" button
                let buttons = card.querySelectorAll('button');
                for (let btn of buttons) {
                    let btnText = btn.innerText.trim().toLowerCase();
                    if (btnText === 'claim' && btn.offsetParent !== null && !btn.disabled) {
                        availableCount++;
                        break;
                    }
                }
            }
            
            return availableCount;
        """)
        
        is_available = available_count > 0
        
        if is_available:
            log(f"✅ Store available: {available_count} rewards (last claim: {hours_since:.1f}h ago)")
        else:
            log(f"⏳ Store unavailable: 0 rewards (last claim: {hours_since:.1f}h ago)")
        
        return is_available, hours_since
        
    except Exception as e:
        log(f"❌ Store check error: {e}")
        return False, 0.0

def claim_daily_rewards(driver, player_id):
    """Claim daily rewards page"""
    log("🎁 Claiming Daily Rewards...")
    claimed = 0
    
    try:
        driver.get("https://hub.vertigogames.co/daily-rewards")
        time.sleep(1.5)
        
        for _ in range(2):
            close_popup(driver)
        
        for attempt in range(10):
            result = driver.execute_script("""
                let buttons = document.querySelectorAll('button');
                for (let btn of buttons) {
                    let text = btn.innerText.trim().toLowerCase();
                    if (text === 'claim' && btn.offsetParent !== null) {
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
                log("ℹ️ No more daily rewards")
                break
        
        driver.save_screenshot(f"daily_final_{player_id}.png")
        
    except Exception as e:
        log(f"❌ Daily error: {e}")
    
    return claimed

def claim_store_rewards(driver, player_id):
    """
    Claim Store Daily Rewards - WITH VERIFICATION
    CRITICAL FIX: Check availability BEFORE claiming
    """
    log("🏪 Claiming Store...")
    claimed = 0
    max_claims = 3
    
    try:
        driver.get("https://hub.vertigogames.co/store")
        time.sleep(2)
        
        for _ in range(2):
            close_popup(driver)
        
        if not ensure_store_page(driver):
            log("❌ Cannot access Store")
            return 0
        
        if not navigate_to_daily_rewards_section_store(driver):
            log("⚠️ Navigation failed")
        
        time.sleep(0.5)
        driver.save_screenshot(f"store_01_ready_{player_id}.png")
        
        # CRITICAL FIX: Check availability BEFORE claiming
        is_available, hours_since = check_store_availability(driver, player_id)
        
        if not is_available:
            log(f"⏭️ Skipping store (no rewards available, last claim: {hours_since:.1f}h ago)")
            driver.save_screenshot(f"store_final_{player_id}.png")
            return 0
        
        # Claim loop
        for attempt in range(max_claims):
            log(f"\n--- Store Claim Attempt {attempt + 1}/{max_claims} ---")
            
            if attempt > 0:
                log("Re-navigating to Daily Rewards section...")
                if not navigate_to_daily_rewards_section_store(driver):
                    log("⚠️ Re-navigation failed")
                    break
                time.sleep(0.5)
            
            # Find and click ONLY green "Claim" buttons (SKIP buttons with timers)
            result = driver.execute_script("""
                // Find Store Bonus cards
                let allDivs = document.querySelectorAll('div');
                let storeBonusCards = [];
                
                for (let div of allDivs) {
                    let text = div.innerText || '';
                    if (text.includes('Store Bonus') && text.includes('+1')) {
                        let parent = div.parentElement;
                        let attempts = 0;
                        while (parent && attempts < 5) {
                            let parentText = parent.innerText || '';
                            if (parentText.includes('Gold (Daily)') || 
                                parentText.includes('Cash (Daily)') || 
                                parentText.includes('Luckyloon (Daily)')) {
                                storeBonusCards.push(parent);
                                break;
                            }
                            parent = parent.parentElement;
                            attempts++;
                        }
                    }
                }
                
                console.log('Found ' + storeBonusCards.length + ' Store Bonus cards');
                
                // Find buttons with "Claim" text (NO timer)
                for (let card of storeBonusCards) {
                    let cardText = card.innerText || '';
                    
                    // SKIP cards with timer
                    if (cardText.includes('Next in') || cardText.match(/\\d+h\\s+\\d+m/)) {
                        console.log('⏭️ Skipping card with timer');
                        continue;
                    }
                    
                    // Find button
                    let buttons = card.querySelectorAll('button');
                    for (let btn of buttons) {
                        let btnText = btn.innerText.trim().toLowerCase();
                        if (btnText === 'claim' && btn.offsetParent !== null && !btn.disabled) {
                            btn.scrollIntoView({behavior: 'smooth', block: 'center'});
                            setTimeout(function() {
                                btn.click();
                                console.log('✅ Clicked GREEN Claim button');
                            }, 500);
                            return true;
                        }
                    }
                }
                
                console.log('No more available claim buttons found');
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
                    log("⚠️ Lost Store page")
                    break
                
                time.sleep(0.3)
            else:
                log(f"ℹ️ No more available claims (attempt {attempt + 1})")
                break
        
        # Log successful claim ONLY if we actually claimed something
        if claimed > 0:
            log_store_claim(player_id)
        
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
    """
    Claim Progression Program rewards with horizontal scrolling
    FIXED: Removed X > 400px filter, better button detection
    """
    log("🎯 Claiming Progression Program...")
    claimed = 0
    
    try:
        # Navigate to Progression Program page
        driver.get("https://hub.vertigogames.co/progression-program")
        time.sleep(2)
        
        # Close initial popups
        for _ in range(2):
            close_popup(driver)
        
        time.sleep(0.5)
        driver.save_screenshot(f"progression_01_ready_{player_id}.png")
        
        # Claim loop with scrolling
        max_attempts = 8
        
        for attempt in range(max_attempts):
            log(f"\n--- Progression Claim Attempt {attempt + 1}/{max_attempts} ---")
            
            # Find and click Claim button (NO X position filter)
            result = driver.execute_script("""
                // Find ALL buttons with "Claim" text
                let allButtons = document.querySelectorAll('button');
                let claimButtons = [];
                
                for (let btn of allButtons) {
                    let btnText = btn.innerText.trim();
                    
                    // Must be exactly "Claim"
                    if (btnText === 'Claim' && btn.offsetParent !== null && !btn.disabled) {
                        // Get parent to check if it's in main content (not sidebar)
                        let parent = btn.closest('div');
                        let parentText = parent ? parent.innerText : '';
                        
                        // Skip sidebar buttons (they have menu-related text)
                        if (parentText.includes('Progression Program') && parentText.length < 50) {
                            // This is sidebar menu item
                            continue;
                        }
                        
                        // Skip already claimed/delivered
                        if (parentText.includes('Delivered') || parentText.includes('Claimed')) {
                            continue;
                        }
                        
                        // Check if button is green (available to claim)
                        let btnStyle = window.getComputedStyle(btn);
                        let bgColor = btnStyle.backgroundColor;
                        
                        // Green buttons have rgb values with high green component
                        // Skip gray/disabled buttons
                        if (bgColor.includes('128, 128, 128') || bgColor.includes('64, 64, 64')) {
                            continue;
                        }
                        
                        claimButtons.push(btn);
                    }
                }
                
                console.log('Found ' + claimButtons.length + ' claimable Progression buttons');
                
                if (claimButtons.length > 0) {
                    let btn = claimButtons[0];
                    // Scroll into view
                    btn.scrollIntoView({behavior: 'smooth', block: 'center'});
                    setTimeout(function() {
                        btn.click();
                        console.log('✅ Clicked Progression Claim button');
                    }, 500);
                    return true;
                }
                
                return false;
            """)
            
            if result:
                log(f"✅ Progression Claim #{claimed + 1} SUCCESS")
                claimed += 1
                time.sleep(1.5)
                
                # Handle confirmation popup
                log("Handling post-claim popup...")
                close_popup(driver)
                time.sleep(0.5)
                
                # Verify still on Progression Program page
                if "/progression-program" not in driver.current_url.lower():
                    log("⚠️ Lost Progression Program page, re-navigating...")
                    driver.get("https://hub.vertigogames.co/progression-program")
                    time.sleep(1)
                
                time.sleep(0.3)
            else:
                # No more claims found, try scrolling
                log(f"ℹ️ No more claim buttons (attempt {attempt + 1})")
                
                if attempt < 7:  # Try scrolling for first 7 attempts
                    log("Scrolling horizontally...")
                    scroll_result = driver.execute_script("""
                        // Find scroll container or next button
                        let allButtons = document.querySelectorAll('button');
                        for (let btn of allButtons) {
                            let className = btn.className || '';
                            let ariaLabel = btn.getAttribute('aria-label') || '';
                            
                            if (className.includes('right') || className.includes('next') || 
                                ariaLabel.toLowerCase().includes('next')) {
                                if (btn.offsetParent !== null && !btn.disabled) {
                                    btn.scrollIntoView({behavior: 'smooth', block: 'center'});
                                    setTimeout(function() {
                                        btn.click();
                                    }, 300);
                                    return true;
                                }
                            }
                        }
                        return false;
                    """)
                    
                    if scroll_result:
                        log("✓ Scrolled right")
                        time.sleep(1)
                        continue
                
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
        log("✅ Driver ready")
        
        if not login_to_hub(driver, player_id):
            stats['status'] = "Login Failed"
            return stats
        
        # ✅ CORRECT ORDER: Daily → Store (earn grenades) → Progression (use grenades)
        stats['daily'] = claim_daily_rewards(driver, player_id)
        stats['store'] = claim_store_rewards(driver, player_id)
        stats['progression'] = claim_progression_program_rewards(driver, player_id)
        
        total = stats['daily'] + stats['store'] + stats['progression']
        if total > 0:
            stats['status'] = "Success"
            log(f"🎉 Total: {total} (D:{stats['daily']} S:{stats['store']} P:{stats['progression']})")
        else:
            stats['status'] = "No Rewards"
            log("⚠️ None claimed")
            
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

def send_email_summary(results):
    """Send email with results"""
    sender = os.environ.get("SENDER_EMAIL")
    recipient = os.environ.get("RECIPIENT_EMAIL")
    password = os.environ.get("GMAIL_APP_PASSWORD")
    
    if not all([sender, recipient, password]):
        log("⚠️ Email not configured")
        return
    
    subject = f"CS Hub Rewards - {datetime.now().strftime('%d-%b %I:%M %p')} IST"
    
    # Build HTML table
    rows = []
    for r in results:
        total = r['daily'] + r['store'] + r['progression']
        rows.append(f"""
        <tr>
            <td>{r['player_id']}</td>
            <td>{r['daily']}</td>
            <td>{r['store']}</td>
            <td>{r['progression']}</td>
            <td><b>{total}</b></td>
            <td>{r['status']}</td>
        </tr>
        """)
    
    html = f"""
    <html>
    <body style="font-family: Arial, sans-serif;">
        <h2>🎮 CS Hub Rewards Claimer Report</h2>
        <p><b>Run:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} IST</p>
        
        <table border="1" cellpadding="8" cellspacing="0" style="border-collapse: collapse;">
            <thead style="background-color: #4CAF50; color: white;">
                <tr>
                    <th>Player ID</th>
                    <th>Daily</th>
                    <th>Store</th>
                    <th>Progression</th>
                    <th>Total</th>
                    <th>Status</th>
                </tr>
            </thead>
            <tbody>
                {"".join(rows)}
            </tbody>
        </table>
        
        <p style="color: #666; font-size: 12px; margin-top: 20px;">
            <i>Note: Store rewards refresh every 24 hours per player.</i>
        </p>
    </body>
    </html>
    """
    
    try:
        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = sender
        msg['To'] = recipient
        
        msg.attach(MIMEText(html, 'html'))
        
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(sender, password)
            server.send_message(msg)
        
        log("✅ Email sent")
    except Exception as e:
        log(f"❌ Email failed: {e}")

def main():
    log("="*60)
    log("🎮 CS HUB AUTO-CLAIMER v2.4 (Store Verification Fix)")
    log("="*60)
    log(f"\n🕐 IST: {datetime.now().strftime('%Y-%m-%d %I:%M %p')}")
    
    # Calculate next reset
    now = datetime.now()
    next_reset = now.replace(hour=5, minute=30, second=0, microsecond=0)
    if now >= next_reset:
        from datetime import timedelta
        next_reset += timedelta(days=1)
    time_until = next_reset - now
    hours, remainder = divmod(time_until.seconds, 3600)
    minutes = remainder // 60
    log(f"⏰ Next Reset: {hours}h {minutes}m")
    log("")
    
    if not os.path.exists(PLAYER_ID_FILE):
        log(f"❌ {PLAYER_ID_FILE} not found")
        return
    
    players = []
    with open(PLAYER_ID_FILE, 'r') as f:
        reader = csv.DictReader(f)
        players = [row['id'] for row in reader if row['id'].strip()]
    
    log(f"📋 {len(players)} players")
    log("="*60)
    log("")
    
    results = []
    for player_id in players:
        result = process_player(player_id)
        results.append(result)
        time.sleep(2)
    
    send_email_summary(results)
    
    log("\n" + "="*60)
    log("✅ COMPLETE")
    log("="*60)

if __name__ == "__main__":
    main()
