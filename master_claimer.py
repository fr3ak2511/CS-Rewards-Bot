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
        log("ℹ️  No cookie banner")

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

def close_store_popup_after_claim(driver):
    """
    Multi-method popup closing strategy
    Method 1: Continue button
    Method 2: Close/X button
    Method 3: Safe-area clicks (multiple locations)
    Method 4: ESC key
    """
    try:
        log("Checking for popup after Store claim...")
        time.sleep(0.5)
        
        # Check if popup exists
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
                    log(f"Popup detected with selector: {selector}")
                    break
            except:
                continue
        
        if not popup_found:
            log("No popup detected after claim")
            return True
        
        # METHOD 1: Try Continue button
        log("Method 1: Attempting to click Continue button...")
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
                        log("Continue button clicked successfully")
                        time.sleep(0.5)
                    except:
                        driver.execute_script("arguments[0].click();", continue_btn)
                        log("Continue button clicked via JavaScript")
                        time.sleep(0.5)
                    
                    # Check if popup closed
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
                        log("Popup closed successfully via Continue button")
                        return True
                    else:
                        log("Popup still visible after Continue button")
                        break
            except:
                continue
        
        # METHOD 2: Try Close/X button
        log("Method 2: Attempting to click close/cross button...")
        close_selectors = [
            "//button[contains(@class, 'close')]",
            "//button[contains(@aria-label, 'Close')]",
            "//*[contains(@class, 'close') and (self::button or self::span or self::div[@role='button'])]",
            "//button[text()='×' or text()='X' or text()='✕']",
            "//*[@data-testid='close-button']",
            "//button[contains(@class, 'modal')]//span[contains(@class, 'close')]",
            "//*[contains(@class, 'icon-close')]",
        ]
        
        for selector in close_selectors:
            try:
                close_btn = driver.find_element(By.XPATH, selector)
                if close_btn.is_displayed():
                    try:
                        close_btn.click()
                        log("Close button clicked successfully")
                        time.sleep(0.5)
                    except:
                        driver.execute_script("arguments[0].click();", close_btn)
                        log("Close button clicked via JavaScript")
                        time.sleep(0.5)
                    
                    # Check if popup closed
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
                        log("Popup closed successfully via close button")
                        return True
                    else:
                        log("Popup still visible after close button")
                        break
            except:
                continue
        
        # METHOD 3: Safe-area clicks (multiple locations)
        log("Method 3: Attempting safe-click to dismiss popup...")
        window_size = driver.get_window_size()
        width = window_size["width"]
        height = window_size["height"]
        
        # Multiple safe click areas
        safe_click_areas = [
            (30, 30),                    # Top-left corner
            (width - 50, 30),            # Top-right corner
            (30, height - 50),           # Bottom-left corner
            (width - 50, height - 50),   # Bottom-right corner
            (width // 4, 30),            # Left-center top
            (3 * width // 4, 30),        # Right-center top
        ]
        
        for i, (x, y) in enumerate(safe_click_areas):
            try:
                actions = ActionChains(driver)
                # Move relative to center
                x_offset = x - (width // 2)
                y_offset = y - (height // 2)
                
                actions.move_by_offset(x_offset, y_offset).click().perform()
                actions.move_by_offset(-x_offset, -y_offset).perform()  # Reset
                
                log(f"Safe-clicked area {i+1} at coordinates ({x}, {y})")
                time.sleep(0.5)
                
                # Check if popup closed
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
                    log(f"Popup closed successfully via safe-click at area {i+1}")
                    return True
            except Exception as e:
                log(f"Safe-click area {i+1} failed: {e}")
                continue
        
        # METHOD 4: ESC key fallback
        log("Final attempt: Pressing ESC key...")
        try:
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            time.sleep(0.5)
            log("ESC key pressed")
            return True
        except:
            pass
        
        log("WARNING: Could not close popup with any method")
        return False
        
    except Exception as e:
        log(f"Exception in close_store_popup_after_claim: {e}")
        return False

def ensure_store_page(driver):
    """Check if on Store page, navigate back if not"""
    try:
        current_url = driver.current_url
        
        if "/store" in current_url.lower():
            log("✓ Confirmed on Store page")
            return True
        
        log(f"⚠️  Not on Store page (URL: {current_url}), navigating back...")
        driver.get("https://hub.vertigogames.co/store")
        time.sleep(0.7)
        
        if "/store" in driver.current_url.lower():
            log("✓ Navigated back to Store page")
            return True
        else:
            log("❌ Failed to navigate back to Store page")
            return False
            
    except Exception as e:
        log(f"❌ Error checking page: {e}")
        return False

def click_daily_rewards_tab(driver):
    """
    Click Daily Rewards TAB (top navigation, NOT sidebar)
    """
    tab_selectors = [
        "//div[contains(@class, 'tab')]//span[contains(text(), 'Daily Rewards')]",
        "//button[contains(@class, 'tab')][contains(text(), 'Daily Rewards')]",
        "//*[text()='Daily Rewards' and (contains(@class, 'tab') or parent::*[contains(@class, 'tab')])]",
        "//div[contains(@class, 'Tab')]//div[contains(text(), 'Daily Rewards')]",
        "//a[contains(@class, 'tab')][contains(text(), 'Daily Rewards')]",
    ]
    
    for i, selector in enumerate(tab_selectors):
        try:
            tab_elements = driver.find_elements(By.XPATH, selector)
            for j, tab in enumerate(tab_elements):
                try:
                    if tab.is_displayed():
                        tab_text = tab.text.strip()
                        
                        # Check if parent is sidebar/menu (skip if true)
                        try:
                            parent = tab.find_element(By.XPATH, "..")
                            parent_classes = parent.get_attribute("class") or ""
                            if any(word in parent_classes.lower() for word in ["sidebar", "menu", "nav", "side"]):
                                log(f"Skipping sidebar element: {tab_text}")
                                continue
                        except:
                            pass
                        
                        # Try to click
                        try:
                            driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", tab)
                            time.sleep(0.3)
                            tab.click()
                            log("✓ Successfully clicked Daily Rewards tab (regular click)")
                            time.sleep(0.7)
                            return True
                        except:
                            try:
                                driver.execute_script("arguments[0].click();", tab)
                                log("✓ Successfully clicked Daily Rewards tab (JS click)")
                                time.sleep(0.7)
                                return True
                            except:
                                continue
                except:
                    continue
        except:
            continue
    
    return False

def navigate_to_daily_rewards_section_store(driver):
    """Navigate to Daily Rewards section in Store"""
    log("Navigating to Daily Rewards section in Store...")
    ensure_store_page(driver)
    close_store_popup_after_claim(driver)
    time.sleep(0.3)
    
    tab_clicked = click_daily_rewards_tab(driver)
    if tab_clicked:
        log("Successfully navigated to Daily Rewards section via tab")
        time.sleep(0.7)
        return True
    else:
        log("⚠️  Tab click failed")
        return False

def claim_daily_rewards(driver, player_id):
    """Claim daily rewards page"""
    log("🎁 Claiming Daily Rewards...")
    claimed = 0
    
    try:
        driver.get("https://hub.vertigogames.co/daily-rewards")
        time.sleep(1.5)
        
        # Close initial popups
        for _ in range(2):
            close_store_popup_after_claim(driver)
        
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
                close_store_popup_after_claim(driver)
            else:
                log("ℹ️  No more daily rewards")
                break
        
        driver.save_screenshot(f"daily_final_{player_id}.png")
        
    except Exception as e:
        log(f"❌ Daily error: {e}")
    
    return claimed

def claim_store_rewards(driver, player_id):
    """
    Claim Store Daily Rewards
    MUST find the Daily Rewards section with FREE claim buttons
    """
    log("🏪 Claiming Store...")
    claimed = 0
    max_claims = 3
    
    try:
        # Navigate to store
        driver.get("https://hub.vertigogames.co/store")
        time.sleep(2)
        
        # Close initial popups
        for _ in range(2):
            close_store_popup_after_claim(driver)
        
        # Ensure on Store page
        if not ensure_store_page(driver):
            log("❌ Cannot access Store page")
            return 0
        
        # Navigate to Daily Rewards section
        if not navigate_to_daily_rewards_section_store(driver):
            log("⚠️  Could not navigate to Daily Rewards section")
        
        time.sleep(0.5)
        driver.save_screenshot(f"store_01_ready_{player_id}.png")
        
        # Claim loop
        for attempt in range(max_claims):
            log(f"Store attempt {attempt + 1}/{max_claims}")
            
            # CRITICAL: Ensure still on Store page
            if not ensure_store_page(driver):
                log("⚠️  Lost Store page, recovery failed")
                break
            
            # Close any popups
            close_store_popup_after_claim(driver)
            
            # Re-navigate to Daily Rewards section
            navigate_to_daily_rewards_section_store(driver)
            time.sleep(0.5)
            
            # Find and click claim button ONLY in Daily Rewards section
            result = driver.execute_script("""
                // Find Daily Rewards section by heading
                let dailySection = null;
                let allSections = document.querySelectorAll('div, section, article');
                
                for (let section of allSections) {
                    let text = section.innerText || '';
                    // Look for section with "Daily Rewards" heading AND "Store Bonus" text
                    if (text.includes('Daily Rewards') && text.includes('Store Bonus')) {
                        dailySection = section;
                        break;
                    }
                }
                
                if (!dailySection) {
                    return false;
                }
                
                // Find Claim buttons WITHIN this section
                let buttons = dailySection.querySelectorAll('button');
                for (let btn of buttons) {
                    let btnText = btn.innerText.trim().toLowerCase();
                    
                    // MUST be exactly "Claim"
                    if (btnText === 'claim' && btn.offsetParent !== null) {
                        // Avoid purchase buttons
                        let parentText = btn.parentElement.innerText || '';
                        if (parentText.includes('₹') || parentText.includes('Buy') || parentText.includes('Purchase')) {
                            continue;
                        }
                        
                        btn.scrollIntoView({behavior: 'smooth', block: 'center'});
                        setTimeout(function() {
                            btn.click();
                        }, 300);
                        return true;
                    }
                }
                return false;
            """)
            
            if result:
                log(f"✅ Store #{claimed + 1}")
                claimed += 1
                time.sleep(2)
                
                # Close confirmation popup
                close_store_popup_after_claim(driver)
                time.sleep(0.5)
                
                # Verify still on Store page
                ensure_store_page(driver)
                time.sleep(0.3)
            else:
                log(f"ℹ️  No more store rewards (attempt {attempt + 1})")
                break
        
        driver.save_screenshot(f"store_final_{player_id}.png")
        
    except Exception as e:
        log(f"❌ Store error: {e}")
        try:
            driver.save_screenshot(f"store_error_{player_id}.png")
        except:
            pass
    
    return claimed

def process_player(player_id):
    """Process single player"""
    driver = None
    stats = {"player_id": player_id, "daily": 0, "store": 0, "status": "Failed"}
    
    try:
        log(f"\n{'='*60}")
        log(f"🚀 {player_id}")
        log(f"{'='*60}")
        
        driver = create_driver()
        log("✅ Driver ready")
        
        if not login_to_hub(driver, player_id):
            stats['status'] = "Login Failed"
            return stats
        
        stats['daily'] = claim_daily_rewards(driver, player_id)
        stats['store'] = claim_store_rewards(driver, player_id)
        
        total = stats['daily'] + stats['store']
        if total > 0:
            stats['status'] = "Success"
            log(f"🎉 Total: {total}")
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

def send_email_summary(results):
    """Send email with results"""
    sender = os.environ.get("SENDER_EMAIL")
    recipient = os.environ.get("RECIPIENT_EMAIL")
    password = os.environ.get("GMAIL_APP_PASSWORD")
    
    if not all([sender, recipient, password]):
        log("⚠️  Email not configured")
        return
    
    subject = f"Hub Rewards - {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    html = f"""
<html><body>
<h2>Hub Rewards Summary</h2>
<p><strong>Run:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
<table border="1" cellpadding="5">
<tr><th>ID</th><th>Daily</th><th>Store</th><th>Status</th></tr>
"""
    
    for r in results:
        html += f"<tr><td>{r['player_id']}</td><td>{r['daily']}</td><td>{r['store']}</td><td>{r['status']}</td></tr>"
    
    html += "</table></body></html>"
    
    msg = MIMEMultipart()
    msg['Subject'] = subject
    msg['From'] = sender
    msg['To'] = recipient
    msg.attach(MIMEText(html, 'html'))
    
    try:
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        server.login(sender, password)
        server.sendmail(sender, recipient, msg.as_string())
        server.quit()
        log("✅ Email sent")
    except Exception as e:
        log(f"❌ Email failed: {e}")

def main():
    log("="*60)
    log("CS HUB AUTO-CLAIMER")
    log("="*60)
    
    try:
        with open(PLAYER_ID_FILE, 'r') as f:
            player_ids = [row[0].strip() for row in csv.reader(f) if row and row[0].strip()]
    except Exception as e:
        log(f"❌ CSV error: {e}")
        return
    
    log(f"📋 {len(player_ids)} player(s)")
    
    results = []
    for player_id in player_ids:
        result = process_player(player_id)
        results.append(result)
        time.sleep(3)
    
    log("\n" + "="*60)
    log("SUMMARY")
    log("="*60)
    for r in results:
        log(f"{r['player_id']}: D={r['daily']}, S={r['store']} → {r['status']}")
    
    send_email_summary(results)
    log("\n🏁 Done!")

if __name__ == "__main__":
    main()
