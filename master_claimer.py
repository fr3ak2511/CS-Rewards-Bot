import csv
import time
import os
import smtplib
from datetime import datetime, timedelta
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
                    let parent = elem.parentElement;
                    let parentClass = parent ? (parent.className || '') : '';
                    if (parentClass.includes('sidebar') || parentClass.includes('menu') || parentClass.includes('side')) {
                        continue;
                    }
                    elem.scrollIntoView({behavior: 'smooth', block: 'nearest', inline: 'center'});
                    setTimeout(() => { elem.click(); }, 800);
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
    """Claim Store Daily Rewards - ENHANCED TIMER DETECTION"""
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
            return 0
        
        driver.save_screenshot(f"store_01_ready_{player_id}.png")
        
        for attempt in range(max_claims):
            log(f"\n--- Store Claim Attempt {attempt + 1}/{max_claims} ---")
            
            if attempt > 0:
                log("Re-navigating to Daily Rewards section...")
                if not navigate_to_daily_rewards_section_store(driver):
                    log("⚠️ Re-navigation failed")
                    break
                time.sleep(0.5)
            
            # ENHANCED: More aggressive timer detection
            result = driver.execute_script("""
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
                
                for (let card of storeBonusCards) {
                    let cardText = card.innerText || '';
                    
                    // ENHANCED: Multiple timer pattern checks
                    let hasTimer = false;
                    
                    // Pattern 1: "Next in"
                    if (cardText.includes('Next in')) {
                        console.log('⏭️ Skipping - has "Next in"');
                        hasTimer = true;
                    }
                    
                    // Pattern 2: Time format "19h 31m" or "19h" or "31m"
                    if (/\\d+h\\s*\\d*m?/.test(cardText) || /\\d+m/.test(cardText)) {
                        console.log('⏭️ Skipping - has time pattern');
                        hasTimer = true;
                    }
                    
                    // Pattern 3: Word "hour" or "minute"
                    if (cardText.toLowerCase().includes('hour') || cardText.toLowerCase().includes('minute')) {
                        console.log('⏭️ Skipping - has hour/minute text');
                        hasTimer = true;
                    }
                    
                    // Pattern 4: Check if button is disabled
                    let buttons = card.querySelectorAll('button');
                    for (let btn of buttons) {
                        let btnText = btn.innerText.trim().toLowerCase();
                        if (btnText === 'claim') {
                            if (btn.disabled || btn.hasAttribute('disabled')) {
                                console.log('⏭️ Skipping - button disabled');
                                hasTimer = true;
                            }
                        }
                    }
                    
                    if (hasTimer) {
                        continue;
                    }
                    
                    // No timer detected - try to claim
                    for (let btn of buttons) {
                        let btnText = btn.innerText.trim().toLowerCase();
                        if (btnText === 'claim' && btn.offsetParent !== null && !btn.disabled) {
                            btn.scrollIntoView({behavior: 'smooth', block: 'center'});
                            setTimeout(function() { btn.click(); }, 500);
                            console.log('✅ Clicked Store Claim button');
                            return true;
                        }
                    }
                }
                
                console.log('No more claimable Store rewards found');
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
    """Claim Progression Program rewards"""
    log("🎯 Claiming Progression Program...")
    claimed = 0
    
    try:
        driver.get("https://hub.vertigogames.co/progression-program")
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
                    setTimeout(function() { btn.click(); }, 600);
                    return true;
                }
                
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
                log(f"ℹ️ No more claim buttons (attempt {attempt + 1})")
                if attempt < max_attempts - 1:
                    log("Scrolling horizontally...")
                    try:
                        driver.execute_script("""
                            let containers = document.querySelectorAll('div');
                            for (let container of containers) {
                                if (container.scrollWidth > container.clientWidth) {
                                    container.scrollLeft += 400;
                                    break;
                                }
                            }
                        """)
                        log("✓ Scrolled right")
                        time.sleep(1)
                    except:
                        log("⚠️ Scroll failed")
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
        log("✅ Driver ready")
        
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

def send_email_summary(results, num_players):
    """Send email with daily tracking stats - SENT FOR EVERY RUN"""
    try:
        sender = os.environ.get("SENDER_EMAIL")
        recipient = os.environ.get("RECIPIENT_EMAIL")
        password = os.environ.get("GMAIL_APP_PASSWORD")
        
        if not all([sender, recipient, password]):
            log("⚠️ Email env vars missing")
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
<!DOCTYPE html>
<html>
<head>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif; background: #f5f5f5; padding: 20px; }}
.container {{ max-width: 800px; margin: 0 auto; background: white; border-radius: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); overflow: hidden; }}
.header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 30px; text-align: center; }}
.header h1 {{ margin: 0; font-size: 28px; font-weight: 600; }}
.header p {{ margin: 10px 0 0; opacity: 0.9; font-size: 14px; }}
.content {{ padding: 30px; }}
table {{ width: 100%; border-collapse: collapse; margin: 20px 0; }}
th {{ background: #f8f9fa; padding: 12px; text-align: left; font-weight: 600; color: #495057; border-bottom: 2px solid #dee2e6; }}
td {{ padding: 12px; border-bottom: 1px solid #dee2e6; }}
.success {{ background: #d4edda; color: #155724; }}
.warning {{ background: #fff3cd; color: #856404; }}
.total-row {{ background: #e9ecef; font-weight: 600; }}
.note {{ background: #f8f9fa; padding: 20px; border-radius: 8px; margin-top: 20px; border-left: 4px solid #667eea; }}
.note h3 {{ margin: 0 0 10px; color: #667eea; font-size: 16px; }}
.note ul {{ margin: 5px 0; padding-left: 20px; }}
.note li {{ margin: 5px 0; color: #495057; }}
.emoji {{ font-size: 18px; }}
</style>
</head>
<body>
<div class="container">
<div class="header">
<h1>🎮 Hub Rewards Summary</h1>
<p>Run: {ist_now.strftime('%Y-%m-%d %I:%M %p IST')}</p>
</div>
<div class="content">

<table>
<tr><th>⏰ Timing Info</th><th>Details</th></tr>
<tr><td><strong>Current Time:</strong></td><td>{ist_now.strftime('%Y-%m-%d %I:%M %p IST')}</td></tr>
<tr><td><strong>Window Started:</strong></td><td>{window_start.strftime('%Y-%m-%d %I:%M %p IST')} ({hours_since_reset}h ago)</td></tr>
<tr><td><strong>Next Reset:</strong></td><td>{next_reset.strftime('%Y-%m-%d %I:%M %p IST')} (in {time_until_reset})</td></tr>
</table>

<table>
<tr><th>📊 Summary</th><th>Claims</th></tr>
<tr><td class="emoji">💰 <strong>Total Daily:</strong></td><td>{total_d} (varies per player)</td></tr>
<tr><td class="emoji">🏪 <strong>Total Store:</strong></td><td>{total_s} / {expected_store_total} ({store_progress_pct}%) {'✅ COMPLETE' if total_s == expected_store_total else f'⚠️ {expected_store_total - total_s} remaining'}</td></tr>
<tr><td class="emoji">🎯 <strong>Total Progression:</strong></td><td>{total_p} (grenade-dependent)</td></tr>
<tr class="total-row"><td class="emoji">🎁 <strong>TOTAL ALL:</strong></td><td>{total_all} claims</td></tr>
</table>

<h3 style="margin-top: 30px; color: #495057;">📋 Per-Player Breakdown (This Run)</h3>
<table>
<tr>
<th>ID</th>
<th>Daily</th>
<th>Store</th>
<th>Progression</th>
<th>Total</th>
<th>Status</th>
</tr>
"""
        
        for r in results:
            total_player = r['daily'] + r['store'] + r['progression']
            status_class = 'success' if r['status'] == 'Success' else 'warning'
            store_check = ' ✅' if r['store'] == EXPECTED_STORE_PER_PLAYER else ''
            
            html += f"""
<tr class="{status_class if r['status'] == 'Success' else ''}">
<td>{r['player_id']}</td>
<td>{r['daily']}</td>
<td>{r['store']}{store_check}</td>
<td>{r['progression']}</td>
<td><strong>{total_player}</strong></td>
<td>{r['status']}</td>
</tr>
"""
        
        html += f"""
<tr class="total-row">
<td><strong>TOTAL</strong></td>
<td><strong>{total_d}</strong></td>
<td><strong>{total_s}</strong></td>
<td><strong>{total_p}</strong></td>
<td><strong>{total_all}</strong></td>
<td><strong>{success_count}/{len(results)}</strong></td>
</tr>
</table>

<div class="note">
<h3>💡 Note:</h3>
<ul>
<li><strong>Store Rewards:</strong> Exactly 3 per player per day (resets 24h after claim)</li>
<li><strong>Daily Rewards:</strong> Variable (~1 per hour, player-dependent)</li>
<li><strong>Progression:</strong> Unlimited (requires Grenades from Store claims)</li>
</ul>
<p style="margin-top: 15px; color: #6c757d; font-size: 13px;">🤖 Automated run at {ist_now.strftime('%Y-%m-%d %I:%M %p IST')}</p>
</div>

</div>
</div>
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
    log("CS HUB AUTO-CLAIMER v2.2 (Store Timer Fix)")
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
    
    # Send email for EVERY run (including 0 claims)
    send_email_summary(results, num_players)
    
    log("")
    log("🏁 Done!")

if __name__ == "__main__":
    main()
