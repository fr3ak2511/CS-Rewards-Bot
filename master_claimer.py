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
from selenium.common.exceptions import TimeoutException, NoSuchElementException, StaleElementReferenceException

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
        
        time.sleep(0.5)
        driver.save_screenshot(f"02_login_clicked_{player_id}.png")
        
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
        if "/store" in driver.current_url.lower():
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
        # MODIFIED SELECTOR: Looks for "Daily Rewards" OR "Daily Rewards-2"
        result = driver.execute_script("""
            let allElements = document.querySelectorAll('*');
            
            for (let elem of allElements) {
                // Flexible match: "Daily Rewards" or "Daily Rewards-2"
                if (elem.innerText && (elem.innerText.includes('Daily Rewards') || elem.innerText.includes('Daily Rewards-2'))) {
                    
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

# ==========================================================
#  PHYSICAL INTERACTION HELPER (ActionChains)
# ==========================================================
def physical_click(driver, element):
    """Moves mouse to element coordinates and clicks physically"""
    try:
        # 1. Scroll into view (Center)
        driver.execute_script("arguments[0].scrollIntoView({behavior: 'instant', block: 'center'});", element)
        time.sleep(0.5)
        
        # 2. Physical Move & Click
        actions = ActionChains(driver)
        actions.move_to_element(element)
        actions.pause(0.2) # Hover
        actions.click()
        actions.perform()
        return True
    except Exception as e:
        # Fallback to JS
        try:
            driver.execute_script("arguments[0].click();", element)
            return True
        except:
            return False

def claim_daily_rewards(driver, player_id):
    """Claim daily rewards page"""
    log("🎁 Claiming Daily Rewards...")
    claimed = 0
    try:
        driver.get("https://hub.vertigogames.co/daily-rewards")
        bypass_cloudflare(driver)
        time.sleep(3)
        close_popup(driver)
        
        for _ in range(10):
            # Refresh button list every time
            buttons = driver.find_elements(By.TAG_NAME, "button")
            clicked_any = False
            
            for btn in buttons:
                try:
                    text = btn.text.lower()
                    if ("claim" in text or "free" in text) and "buy" not in text:
                        # Timer check
                        try:
                            parent = btn.find_element(By.XPATH, "./..")
                            if "next in" in parent.text.lower(): continue
                        except: pass
                        
                        if physical_click(driver, btn):
                            log("🖱️ Clicked Daily Reward")
                            time.sleep(3)
                            
                            # Assume Success & Close Popup
                            close_popup(driver)
                            claimed += 1
                            clicked_any = True
                            break 
                except: continue
            
            if not clicked_any: break
            
        driver.save_screenshot(f"daily_final_{player_id}.png")
    except Exception as e:
        log(f"❌ Daily error: {e}")
    return claimed

def claim_store_rewards(driver, player_id):
    """Claim Store Daily Rewards - UPDATED FOR NEW TAB NAME"""
    log("🏪 Claiming Store...")
    claimed = 0
    
    try:
        driver.get("https://hub.vertigogames.co/store")
        bypass_cloudflare(driver)
        time.sleep(2)
        close_popup(driver)
        
        if not ensure_store_page(driver): return 0
        navigate_to_daily_rewards_section_store(driver)
        time.sleep(5) # Wait for page load
        
        driver.save_screenshot(f"store_01_ready_{player_id}.png")
        
        for attempt in range(3):
            # Strict Finder: Find ANY button with "Free" text
            buttons = driver.find_elements(By.TAG_NAME, "button")
            found_btn = None
            
            for btn in buttons:
                try:
                    txt = (btn.text or btn.get_attribute("textContent")).lower()
                    
                    if "free" in txt:
                        if btn.is_enabled() and btn.is_displayed():
                            # Timer Check
                            try:
                                parent = btn.find_element(By.XPATH, "./..")
                                if "next in" in parent.text.lower(): continue
                            except: pass
                            
                            found_btn = btn
                            break # Click one at a time
                except: continue
            
            if found_btn:
                log(f"🖱️ Found Free Button. Clicking...")
                if physical_click(driver, found_btn):
                    time.sleep(4)
                    
                    # Assume success (User preference: Better false positive than missed)
                    close_popup(driver)
                    log(f"✅ Store Claim #{claimed + 1} (Assumed Success)")
                    claimed += 1
                    
                    # Refresh page state
                    ensure_store_page(driver)
            else:
                log(f"ℹ️  No 'Free' buttons found (attempt {attempt+1})")
                break
                
        log(f"Store Claims Complete: {claimed}/3")
        driver.save_screenshot(f"store_final_{player_id}.png")
        
    except Exception as e:
        log(f"❌ Store error: {e}")
    
    return claimed

def claim_progression_program_rewards(driver, player_id):
    """Claim Progression"""
    log("🎯 Claiming Progression...")
    claimed = 0
    try:
        driver.get("https://hub.vertigogames.co/progression-program")
        bypass_cloudflare(driver)
        time.sleep(3)
        close_popup(driver)
        
        for _ in range(8):
            buttons = driver.find_elements(By.TAG_NAME, "button")
            clicked = False
            for btn in buttons:
                try:
                    if "claim" in btn.text.lower():
                        # Delivered check
                        try:
                            parent = btn.find_element(By.XPATH, "./..")
                            if "delivered" in parent.text.lower(): continue
                        except: pass
                        
                        if physical_click(driver, btn):
                            log("🖱️ Clicked Progression")
                            time.sleep(2)
                            close_popup(driver)
                            claimed += 1
                            clicked = True
                            break
                except: continue
            
            if not clicked:
                # Scroll logic
                try:
                    driver.execute_script("let c=document.querySelectorAll('div');for(let i of c){if(i.scrollWidth>i.clientWidth){i.scrollLeft+=400;}}")
                    time.sleep(1)
                except: break
            else:
                pass 
                
    except: pass
    return claimed

def process_player(player_id):
    """Process single player"""
    driver = None
    stats = {"player_id": player_id, "daily": 0, "store": 0, "progression": 0, "status": "Failed"}
    try:
        log(f"\n🚀 {player_id}")
        driver = create_driver()
        if not login_to_hub(driver, player_id):
            stats['status'] = "Login Failed"
            return stats
        
        stats['daily'] = claim_daily_rewards(driver, player_id)
        stats['store'] = claim_store_rewards(driver, player_id)
        stats['progression'] = claim_progression_program_rewards(driver, player_id)
        
        total = stats['daily'] + stats['store'] + stats['progression']
        if total > 0:
            stats['status'] = "Success"
        else:
            stats['status'] = "No Rewards"
            
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
    try:
        sender = os.environ.get("SENDER_EMAIL")
        recipient = os.environ.get("RECIPIENT_EMAIL")
        password = os.environ.get("GMAIL_APP_PASSWORD")
        if not all([sender, recipient, password]): return
        
        total_d = sum(r['daily'] for r in results)
        total_s = sum(r['store'] for r in results)
        total_p = sum(r['progression'] for r in results)
        total_all = total_d + total_s + total_p
        success_count = sum(1 for r in results if r['status'] == 'Success')
        expected_store_total = num_players * EXPECTED_STORE_PER_PLAYER
        store_progress_pct = int((total_s / expected_store_total) * 100) if expected_store_total > 0 else 0
        ist_now = get_ist_time()
        
        html = f"""
        <html>
        <body style="font-family: Arial, sans-serif;">
        <h2>🎮 Hub Rewards Summary</h2>
        <div style="background-color: #f0f8ff; padding: 15px; border-radius: 8px; margin-bottom: 20px;">
            <h3 style="margin-top: 0;">📊 Daily Window Tracking (5:30 AM IST Reset)</h3>
            <p><strong>Time:</strong> {ist_now.strftime('%Y-%m-%d %I:%M %p IST')}</p>
        </div>
        <div style="background-color: #fff3cd; padding: 15px; border-radius: 8px; margin-bottom: 20px;">
            <h3 style="margin-top: 0;">📈 Today's Stats</h3>
            <p><strong>Total Daily:</strong> {total_d}</p>
            <p><strong>Total Store:</strong> {total_s}/{expected_store_total} ({store_progress_pct}%)</p>
            <p><strong>Total Progression:</strong> {total_p}</p>
            <p><strong>GRAND TOTAL: {total_all}</strong></p>
        </div>
        <h3>👥 Per-Player Breakdown</h3>
        <table border="1" cellpadding="5" cellspacing="0" style="border-collapse: collapse; width: 100%;">
        <tr style="background-color: #f0f0f0;"><th>ID</th><th>Daily</th><th>Store</th><th>Progression</th><th>Total</th><th>Status</th></tr>
        """
        for r in results:
            status_color = "#90EE90" if r['status'] == 'Success' else "#FFE4B5" if r['status'] == 'No Rewards' else "#FFB6C1"
            html += f"""<tr>
                <td>{r['player_id']}</td><td>{r['daily']}</td><td>{r['store']}</td>
                <td>{r['progression']}</td><td><strong>{r['daily']+r['store']+r['progression']}</strong></td><td style="background-color: {status_color};">{r['status']}</td>
            </tr>"""
        html += """</table>
        <div style="margin-top: 20px; padding: 10px; background-color: #f9f9f9; border-left: 4px solid #4CAF50;">
            <p style="margin: 5px 0;"><strong>💡 Note:</strong></p>
            <ul style="margin: 5px 0;">
                <li><strong>Store Rewards:</strong> Exactly 3 per player per day.</li>
                <li><strong>Daily Rewards:</strong> Variable.</li>
                <li><strong>Progression:</strong> Unlimited.</li>
            </ul>
        </div>
        </body></html>"""
        
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
    log("="*60)
    log("CS HUB AUTO-CLAIMER v4.3 (New Tab Name Fix)")
    log("="*60)
    
    players = []
    try:
        with open(PLAYER_ID_FILE, 'r') as f:
            reader = csv.DictReader(f)
            players = [row['player_id'].strip() for row in reader if row['player_id'].strip()]
    except: return
    
    results = []
    for player_id in players:
        stats = process_player(player_id)
        results.append(stats)
        time.sleep(3)
    
    send_email_summary(results, len(players))
    log("🏁 Done!")

if __name__ == "__main__":
    main()
