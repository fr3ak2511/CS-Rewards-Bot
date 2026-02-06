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
from selenium.common.exceptions import TimeoutException, NoSuchElementException, StaleElementReferenceException, ElementClickInterceptedException

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
    utc_now = datetime.utcnow()
    ist_offset = timedelta(hours=5, minutes=30)
    return utc_now + ist_offset

def get_current_daily_window_start():
    ist_now = get_ist_time()
    if ist_now.hour < DAILY_RESET_HOUR_IST or (ist_now.hour == DAILY_RESET_HOUR_IST and ist_now.minute < DAILY_RESET_MINUTE_IST):
        window_start = ist_now.replace(hour=DAILY_RESET_HOUR_IST, minute=DAILY_RESET_MINUTE_IST, second=0, microsecond=0) - timedelta(days=1)
    else:
        window_start = ist_now.replace(hour=DAILY_RESET_HOUR_IST, minute=DAILY_RESET_MINUTE_IST, second=0, microsecond=0)
    return window_start

def get_next_daily_reset():
    ist_now = get_ist_time()
    if ist_now.hour < DAILY_RESET_HOUR_IST or (ist_now.hour == DAILY_RESET_HOUR_IST and ist_now.minute < DAILY_RESET_MINUTE_IST):
        next_reset = ist_now.replace(hour=DAILY_RESET_HOUR_IST, minute=DAILY_RESET_MINUTE_IST, second=0, microsecond=0)
    else:
        next_reset = ist_now.replace(hour=DAILY_RESET_HOUR_IST, minute=DAILY_RESET_MINUTE_IST, second=0, microsecond=0) + timedelta(days=1)
    return next_reset

def format_time_until_reset(next_reset):
    ist_now = get_ist_time()
    delta = next_reset - ist_now
    hours, remainder = divmod(delta.seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    return f"{hours}h {minutes}m"

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
    try:
        btn = WebDriverWait(driver, 3).until(
            EC.element_to_be_clickable((By.XPATH, "//button[normalize-space()='Accept All' or contains(text(), 'Accept')]"))
        )
        btn.click()
        time.sleep(0.3)
        log("✅ Cookies accepted")
    except TimeoutException:
        log("ℹ️  No cookie banner")

def login_to_hub(driver, player_id):
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
            
        # Try clicking Login CTA
        try:
            cta = driver.find_element(By.XPATH, "//button[contains(text(), 'Login') or contains(text(), 'Log in')]")
            cta.click()
            log("✅ Login CTA clicked")
        except:
            input_box.send_keys(Keys.ENTER)
            log("⏎ Enter key pressed")
            
        time.sleep(1)
        
        # Verify
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
        # 1. Look for 'Continue' or 'Close' buttons
        buttons = driver.find_elements(By.XPATH, "//button[contains(text(), 'Continue') or contains(text(), 'Close')]")
        for btn in buttons:
            if btn.is_displayed():
                try:
                    btn.click() # Try native click first
                except:
                    driver.execute_script("arguments[0].click();", btn) # Fallback to JS
                time.sleep(0.5)
                return True
        
        # 2. Try SVG close icons
        icons = driver.find_elements(By.XPATH, "//*[name()='svg']/parent::button")
        for icon in icons:
            if icon.is_displayed():
                driver.execute_script("arguments[0].click();", icon)
                time.sleep(0.5)
                return True
                
        # 3. ESC
        ActionChains(driver).send_keys(Keys.ESCAPE).perform()
        return True
    except:
        return False

def native_click(driver, element):
    """Performs a robust, human-like click using ActionChains"""
    try:
        # Scroll into view
        driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", element)
        time.sleep(0.5)
        
        # ActionChains Click (Most reliable)
        actions = ActionChains(driver)
        actions.move_to_element(element).pause(0.2).click().perform()
        return True
    except Exception as e:
        # Fallback to JS Click if intercepted
        try:
            driver.execute_script("arguments[0].click();", element)
            return True
        except:
            return False

def claim_daily_rewards(driver, player_id):
    log("🎁 Claiming Daily Rewards...")
    claimed = 0
    try:
        driver.get("https://hub.vertigogames.co/daily-rewards")
        bypass_cloudflare(driver)
        time.sleep(3)
        close_popup(driver)
        
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
    log("🏪 Claiming Store...")
    claimed = 0
    
    try:
        driver.get("https://hub.vertigogames.co/store")
        bypass_cloudflare(driver)
        time.sleep(3)
        close_popup(driver)
        
        # Navigate tab
        try:
            # Find the div/span with 'Daily Rewards'
            tab = driver.find_element(By.XPATH, "//*[contains(text(), 'Daily Rewards')]")
            native_click(driver, tab)
            log("✅ Clicked Daily Rewards Tab")
            time.sleep(1)
        except:
            log("⚠️ Could not find Daily Rewards tab")
            
        driver.save_screenshot(f"store_01_ready_{player_id}.png")
        
        # Main Loop
        for i in range(3):
            # Find all buttons again to avoid stale references
            buttons = driver.find_elements(By.TAG_NAME, "button")
            clicked_this_round = False
            
            for btn in buttons:
                try:
                    text = (btn.text or btn.get_attribute("innerText")).lower()
                    
                    # Logic: Must be "Free" or "Claim"
                    # Must NOT have a timer in parent text (basic check)
                    if text == "free" or text == "claim":
                        # Check parent for timer
                        parent = btn.find_element(By.XPATH, "./..")
                        parent_text = parent.text.lower()
                        
                        if "next in" in parent_text:
                            continue # Skip cooldowns
                            
                        # Perform NATIVE CLICK
                        if native_click(driver, btn):
                            log(f"🖱️ Clicked Store Reward #{claimed+1}")
                            time.sleep(3) # Wait for popup
                            
                            if close_popup(driver):
                                log("✅ Popup closed - Verified")
                                claimed += 1
                                clicked_this_round = True
                                time.sleep(1)
                                break # Break inner loop to refresh DOM
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
    log("🎯 Claiming Progression...")
    claimed = 0
    try:
        driver.get("https://hub.vertigogames.co/progression-program")
        bypass_cloudflare(driver)
        time.sleep(3)
        close_popup(driver)
        
        driver.save_screenshot(f"progression_01_ready_{player_id}.png")
        
        # Try scanning for buttons multiple times as we scroll
        for _ in range(8):
            buttons = driver.find_elements(By.TAG_NAME, "button")
            clicked_any = False
            
            for btn in buttons:
                try:
                    text = btn.text.lower()
                    if text == "claim":
                        # Check if delivered
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
                            break # Refresh list
                except: continue
            
            if not clicked_any:
                # Scroll right
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
        <h2>🎮 Hub Rewards (Native Click v3.8)</h2>
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
    log("CS HUB AUTO-CLAIMER v3.8 (Native Clicks)")
    
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
