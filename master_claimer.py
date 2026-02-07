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
                "//button[normalize-space()='Accept All' or contains(text(), 'Accept') or contains(text(), 'Allow') or contains(text(), 'Consent')]"
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
            "//button[contains(text(),'Login')]", 
            "//button[contains(text(),'Log in')]",
            "//a[contains(text(),'Login')]"
        ]
        
        login_clicked = False
        for selector in login_selectors:
            try:
                elements = driver.find_elements(By.XPATH, selector)
                for element in elements:
                    if element.is_displayed() and element.is_enabled():
                        element.click()
                        login_clicked = True
                        log(f"✅ Login button clicked")
                        break
                if login_clicked: break
            except: continue
        
        if not login_clicked:
            log("❌ No login button found")
            return False
        
        time.sleep(0.5)
        
        input_selectors = ["#user-id-input", "//input[@placeholder='User ID']", "//input[@type='text']"]
        input_box = None
        for selector in input_selectors:
            try:
                input_box = WebDriverWait(driver, 3).until(EC.visibility_of_element_located((By.XPATH if "//" in selector else By.CSS_SELECTOR, selector)))
                input_box.clear()
                input_box.send_keys(player_id)
                time.sleep(0.1)
                break
            except: continue
            
        if not input_box:
            log("❌ No input field found")
            return False
            
        try:
            cta = driver.find_element(By.XPATH, "//button[contains(text(), 'Login') or contains(text(), 'Log in')]")
            cta.click()
            log("✅ Login CTA clicked")
        except:
            input_box.send_keys(Keys.ENTER)
            log("⏎ Enter key pressed")
            
        time.sleep(1)
        
        start_time = time.time()
        while time.time() - start_time < 12:
            if "daily-rewards" in driver.current_url or "dashboard" in driver.current_url:
                log("✅ Login verified")
                return True
            time.sleep(0.5)
            
        return False
    except Exception as e:
        log(f"❌ Login error: {e}")
        return False

def close_popup(driver):
    try:
        time.sleep(0.5)
        buttons = driver.find_elements(By.XPATH, "//button[contains(text(), 'Continue') or contains(text(), 'Close')]")
        for btn in buttons:
            if btn.is_displayed():
                try:
                    btn.click()
                except:
                    driver.execute_script("arguments[0].click();", btn)
                time.sleep(0.5)
                return True
        
        icons = driver.find_elements(By.XPATH, "//*[name()='svg']/parent::button")
        for icon in icons:
            if icon.is_displayed():
                driver.execute_script("arguments[0].click();", icon)
                time.sleep(0.5)
                return True
                
        ActionChains(driver).send_keys(Keys.ESCAPE).perform()
        return True
    except:
        return False

def native_click(driver, element):
    """Performs a robust, human-like click using ActionChains"""
    try:
        driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", element)
        time.sleep(0.5)
        actions = ActionChains(driver)
        actions.move_to_element(element).pause(0.2).click().perform()
        return True
    except Exception as e:
        try:
            driver.execute_script("arguments[0].click();", element)
            return True
        except:
            return False

# ==============================================================================
# STORE NAVIGATION HELPERS (From master_claimer_Store.py)
# ==============================================================================

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
        result = driver.execute_script("""
            let allElements = document.querySelectorAll('*');
            
            for (let elem of allElements) {
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

# ==============================================================================
# CLAIM LOGIC
# ==============================================================================

def claim_daily_rewards(driver, player_id):
    """Claim daily rewards page - FROM master_claimer_Daily.py"""
    log("🎁 Claiming Daily Rewards...")
    claimed = 0
    try:
        driver.get("https://hub.vertigogames.co/daily-rewards")
        bypass_cloudflare(driver)
        time.sleep(3)
        close_popup(driver)
        
        # LOGIC FROM master_claimer_Daily.py
        buttons = driver.find_elements(By.XPATH, "//button")
        for btn in buttons:
            try:
                text = btn.text.lower()
                if "claim" in text or "free" in text:
                    if "buy" not in text and "purchase" not in text:
                        if native_click(driver, btn):
                            log("✅ Daily Reward Clicked")
                            claimed += 1
                            time.sleep(2)
                            close_popup(driver)
            except: continue
            
        driver.save_screenshot(f"daily_final_{player_id}.png")
    except Exception as e:
        log(f"❌ Daily error: {e}")
    return claimed

def claim_store_rewards(driver, player_id):
    """Claim Store Daily Rewards - FROM master_claimer_Store.py"""
    log("🏪 Claiming Store...")
    claimed = 0
    max_claims = 3
    
    try:
        driver.get("https://hub.vertigogames.co/store")
        bypass_cloudflare(driver)
        time.sleep(3)
        close_popup(driver)
        
        try:
            # Note: This is part of the Store script's logic to click the tab
            tab = driver.find_element(By.XPATH, "//*[contains(text(), 'Daily Rewards')]")
            native_click(driver, tab)
            log("✅ Clicked Daily Rewards Tab")
            time.sleep(1)
        except:
            log("⚠️ Could not find Daily Rewards tab (trying navigation helper)")
            
        # Ensure we are in the right section using the robust helper
        navigate_to_daily_rewards_section_store(driver)
            
        driver.save_screenshot(f"store_01_ready_{player_id}.png")
        
        # LOGIC FROM master_claimer_Store.py
        for i in range(3):
            buttons = driver.find_elements(By.TAG_NAME, "button")
            clicked_this_round = False
            
            for btn in buttons:
                try:
                    text = (btn.text or btn.get_attribute("innerText")).lower()
                    if text == "free" or text == "claim":
                        parent = btn.find_element(By.XPATH, "./..")
                        parent_text = parent.text.lower()
                        
                        if "next in" in parent_text:
                            continue
                            
                        if native_click(driver, btn):
                            log(f"🖱️ Clicked Store Reward #{claimed+1}")
                            time.sleep(3)
                            
                            if close_popup(driver):
                                log("✅ Popup closed - Verified")
                                claimed += 1
                                clicked_this_round = True
                                time.sleep(1)
                                break 
                except StaleElementReferenceException:
                    continue
                except Exception as e:
                    continue
            
            if not clicked_this_round:
                log("ℹ️ No clickable buttons found this pass")
                break
                
        log(f"Store Claims Complete: {claimed}/3")
        driver.save_screenshot(f"store_final_{player_id}.png")
        
    except Exception as e:
        log(f"❌ Store error: {e}")
    
    return claimed

def claim_progression_program_rewards(driver, player_id):
    """Claim Progression - FROM master_claimer_Store.py"""
    log("🎯 Claiming Progression...")
    claimed = 0
    try:
        driver.get("https://hub.vertigogames.co/progression-program")
        bypass_cloudflare(driver)
        time.sleep(3)
        close_popup(driver)
        
        driver.save_screenshot(f"progression_01_ready_{player_id}.png")
        
        # LOGIC FROM master_claimer_Store.py
        for _ in range(8):
            buttons = driver.find_elements(By.TAG_NAME, "button")
            clicked_any = False
            
            for btn in buttons:
                try:
                    text = btn.text.lower()
                    if text == "claim":
                        try:
                            parent = btn.find_element(By.XPATH, "./..")
                            if "delivered" in parent.text.lower():
                                continue
                        except: pass
                        
                        if native_click(driver, btn):
                            log("✅ Clicked Progression Reward")
                            claimed += 1
                            time.sleep(2)
                            close_popup(driver)
                            clicked_any = True
                            break
                except: continue
            
            if not clicked_any:
                try:
                    driver.execute_script("""
                        let containers = document.querySelectorAll('div');
                        for (let c of containers) {
                            if (c.scrollWidth > c.clientWidth) {
                                c.scrollLeft += 400;
                            }
                        }
                    """)
                    time.sleep(1)
                except: break
            
    except Exception as e:
        log(f"❌ Progression error: {e}")
    return claimed

def process_player(player_id):
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
        stats['status'] = "Success" if total > 0 else "No Rewards"
        log(f"🎉 Total: {total}")
            
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
        
        total_all = sum(r['daily'] + r['store'] + r['progression'] for r in results)
        
        html = f"""
        <h2>🎮 Hub Rewards (Strict Merge)</h2>
        <p><strong>Total Claims: {total_all}</strong></p>
        <table border="1" cellpadding="5" cellspacing="0">
        <tr><th>ID</th><th>Daily</th><th>Store</th><th>Prog</th></tr>
        """
        for r in results:
            html += f"<tr><td>{r['player_id']}</td><td>{r['daily']}</td><td>{r['store']}</td><td>{r['progression']}</td></tr>"
        html += "</table>"
        
        msg = MIMEMultipart('alternative')
        msg['Subject'] = f"Hub Rewards - {total_all} Claims"
        msg['From'] = sender
        msg['To'] = recipient
        msg.attach(MIMEText(html, 'html'))
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(sender, password)
            server.send_message(msg)
    except: pass

def main():
    log("CS HUB AUTO-CLAIMER v5.8 (Strict Merge)")
    
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

if __name__ == "__main__":
    main()
