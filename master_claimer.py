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
                    break
            except:
                continue
        
        if not popup_found:
            return True # If no popup, proceed
        
        continue_selectors = ["//button[normalize-space()='Continue']", "//button[contains(text(), 'Continue')]"]
        for selector in continue_selectors:
            try:
                continue_btn = driver.find_element(By.XPATH, selector)
                if continue_btn.is_displayed():
                    driver.execute_script("arguments[0].click();", continue_btn)
                    time.sleep(0.8)
                    return True
            except: continue
        
        close_selectors = ["//button[normalize-space()='Close']", "//button[contains(@class, 'close')]", "//*[name()='svg']/parent::button"]
        for selector in close_selectors:
            try:
                close_btn = driver.find_element(By.XPATH, selector)
                if close_btn.is_displayed():
                    driver.execute_script("arguments[0].click();", close_btn)
                    time.sleep(0.8)
                    return True
            except: continue
                
        try:
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            time.sleep(0.5)
            return True
        except: pass
            
        return False
    except: return False

def ensure_store_page(driver):
    """Check if on Store page"""
    try:
        if "/store" in driver.current_url.lower(): return True
        driver.get("https://hub.vertigogames.co/store")
        time.sleep(1.0)
        return "/store" in driver.current_url.lower()
    except: return False

def click_daily_rewards_tab(driver):
    """Click Daily Rewards TAB (UPDATED FOR Daily Rewards-2)"""
    log("Clicking Daily Rewards tab...")
    
    try:
        result = driver.execute_script("""
            let allElements = document.querySelectorAll('*');
            for (let elem of allElements) {
                if (elem.innerText && (elem.innerText.trim() === 'Daily Rewards' || elem.innerText.trim() === 'Daily Rewards-2')) {
                    if (!elem.className.includes('sidebar') && !elem.parentElement.className.includes('sidebar')) {
                        elem.scrollIntoView({behavior: 'smooth', block: 'nearest', inline: 'center'});
                        setTimeout(() => { elem.click(); }, 800);
                        return true;
                    }
                }
            }
            return false;
        """)
        
        if result:
            log("✅ Daily Rewards tab clicked")
            time.sleep(1.5)
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
        time.sleep(1.0)
        return True
    else:
        log("⚠️  Tab navigation failed")
        return False

def claim_daily_rewards(driver, player_id):
    """Claim daily rewards page - LIMIT 1"""
    log("🎁 Claiming Daily Rewards...")
    claimed = 0
    
    try:
        driver.get("https://hub.vertigogames.co/daily-rewards")
        bypass_cloudflare(driver)
        time.sleep(3) # Wait for load
        close_popup(driver)
        
        for attempt in range(1): # Max 1
            result = driver.execute_script("""
                let buttons = document.querySelectorAll('button');
                for (let btn of buttons) {
                    let text = btn.innerText.trim().toLowerCase();
                    if ((text === 'claim' || text === 'free') && btn.offsetParent !== null) {
                        let parent = btn.parentElement;
                        let hasTimer = false;
                        if(parent && (parent.innerText.includes('Next in') || parent.innerText.match(/\\d+h\\s+\\d+m/))) {
                            hasTimer = true;
                        }
                        if (!hasTimer && !btn.innerText.toLowerCase().includes('buy')) {
                            ['mousedown', 'mouseup', 'click'].forEach(evt => 
                                btn.dispatchEvent(new MouseEvent(evt, {bubbles: true, cancelable: true, view: window}))
                            );
                            return true;
                        }
                    }
                }
                return false;
            """)
            if result:
                log(f"✅ Daily Claimed (1/1)")
                claimed = 1
                time.sleep(3.0)
                close_popup(driver)
            else:
                log("ℹ️  No daily rewards available")
        
        driver.save_screenshot(f"daily_final_{player_id}.png")
        
    except Exception as e:
        log(f"❌ Daily error: {e}")
    
    return claimed

def claim_store_rewards(driver, player_id):
    """Claim Store Daily Rewards - '1/1 LEFT' Targeting"""
    log("🏪 Claiming Store...")
    claimed = 0
    max_claims = 3
    
    try:
        driver.get("https://hub.vertigogames.co/store")
        bypass_cloudflare(driver)
        time.sleep(2)
        close_popup(driver)
        
        if not ensure_store_page(driver): return 0
        if not navigate_to_daily_rewards_section_store(driver):
            log("⚠️  Navigation failed")
        
        time.sleep(2)
        driver.save_screenshot(f"store_01_ready_{player_id}.png")
        
        for attempt in range(max_claims):
            log(f"\n--- Store Claim Attempt {attempt + 1}/{max_claims} ---")
            
            if attempt > 0:
                if not navigate_to_daily_rewards_section_store(driver): break
                time.sleep(0.5)
            
            # v4.9 SNIPER LOGIC: Find text "1/1 LEFT", then click its button
            result = driver.execute_script("""
                let allDivs = document.querySelectorAll('div');
                let foundButton = false;
                
                for (let div of allDivs) {
                    let text = (div.innerText || '').toUpperCase();
                    
                    // STRICT CHECK: The div must explicitly contain "1/1 LEFT"
                    if (text.includes('1/1 LEFT')) {
                        let parent = div.parentElement;
                        let attempts = 0;
                        
                        // Traverse up to find the container that holds the button
                        while (parent && attempts < 6) {
                            let btn = parent.querySelector('button');
                            if (btn) {
                                let btnText = btn.innerText.trim().toLowerCase();
                                // Ensure it is the correct button
                                if ((btnText === 'free' || btnText === 'claim') && !btn.disabled) {
                                    btn.scrollIntoView({behavior: 'smooth', block: 'center'});
                                    
                                    // NUCLEAR CLICK
                                    ['mousedown', 'mouseup', 'click'].forEach(evt => 
                                        btn.dispatchEvent(new MouseEvent(evt, {bubbles: true, cancelable: true, view: window}))
                                    );
                                    console.log('Clicked button for 1/1 LEFT item');
                                    foundButton = true;
                                    break;
                                }
                            }
                            parent = parent.parentElement;
                            attempts++;
                        }
                    }
                    if (foundButton) break;
                }
                return foundButton;
            """)
            
            if result:
                log(f"✅ Store Claim #{claimed + 1} SUCCESS")
                claimed += 1
                time.sleep(3.0)
                close_popup(driver)
                time.sleep(0.5)
                if not ensure_store_page(driver): break
            else:
                log(f"ℹ️  No more '1/1 LEFT' items found (attempt {attempt + 1})")
                break
        
        log(f"Store Claims Complete: {claimed}/{max_claims}")
        driver.save_screenshot(f"store_final_{player_id}.png")
        
    except Exception as e:
        log(f"❌ Store error: {e}")
    
    return claimed

def claim_progression_program_rewards(driver, player_id):
    """Claim Progression"""
    log("🎯 Claiming Progression Program...")
    claimed = 0
    try:
        driver.get("https://hub.vertigogames.co/progression-program")
        bypass_cloudflare(driver)
        time.sleep(2)
        close_popup(driver)
        
        for _ in range(8):
            result = driver.execute_script("""
                let allButtons = document.querySelectorAll('button');
                for (let btn of allButtons) {
                    let btnText = (btn.innerText || btn.textContent).trim().toLowerCase();
                    if (btnText === 'claim' && btn.offsetParent !== null && !btn.disabled) {
                         let pText = (btn.parentElement.innerText || btn.parentElement.textContent) || '';
                         if (!pText.includes('Delivered')) {
                             btn.scrollIntoView({behavior: 'smooth', block: 'center', inline: 'center'});
                             ['mousedown', 'mouseup', 'click'].forEach(evt => 
                                btn.dispatchEvent(new MouseEvent(evt, {bubbles: true, cancelable: true, view: window}))
                             );
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
    except: pass
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
        
        total_d = sum(r['daily'] for r in results)
        total_s = sum(r['store'] for r in results)
        total_p = sum(r['progression'] for r in results)
        total_all = total_d + total_s + total_p
        
        expected_store = num_players * 3
        ist_now = get_ist_time()
        
        html = f"""
        <html>
        <body style="font-family: Arial, sans-serif;">
        <h2>🎮 Hub Rewards Summary</h2>
        <div style="background-color: #f0f8ff; padding: 15px; border-radius: 8px; margin-bottom: 20px;">
            <h3 style="margin-top: 0;">📊 Run Statistics</h3>
            <p><strong>Time:</strong> {ist_now.strftime('%Y-%m-%d %I:%M %p IST')}</p>
        </div>
        <div style="background-color: #fff3cd; padding: 15px; border-radius: 8px; margin-bottom: 20px;">
            <h3 style="margin-top: 0;">📈 Totals</h3>
            <p><strong>Daily:</strong> {total_d} (Max 1 per ID)</p>
            <p><strong>Store:</strong> {total_s}/{expected_store} (Max 3 per ID)</p>
            <p><strong>Progression:</strong> {total_p}</p>
            <p><strong>GRAND TOTAL: {total_all}</strong></p>
        </div>
        <h3>👥 Per-Player Breakdown</h3>
        <table border="1" cellpadding="5" cellspacing="0" style="border-collapse: collapse; width: 100%;">
        <tr style="background-color: #f0f0f0;"><th>ID</th><th>Daily</th><th>Store</th><th>Progression</th><th>Total</th><th>Status</th></tr>
        """
        for r in results:
            t = r['daily'] + r['store'] + r['progression']
            color = "#90EE90" if r['status'] == 'Success' else "#FFE4B5" if r['status'] == 'No Rewards' else "#FFB6C1"
            html += f"""<tr>
            <td>{r['player_id']}</td><td>{r['daily']}</td><td>{r['store']}</td><td>{r['progression']}</td>
            <td><strong>{t}</strong></td><td style="background-color: {color};">{r['status']}</td></tr>"""
            
        html += "</table><div style='margin-top: 20px; padding: 10px; background-color: #f9f9f9; border-left: 4px solid #4CAF50;'><p><strong>💡 Note:</strong></p><ul><li><strong>Daily Rewards:</strong> Max 1 per player per day.</li><li><strong>Store Rewards:</strong> Max 3 per player per day.</li><li><strong>Progression:</strong> Unlimited.</li></ul></div></body></html>"
        
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
    log("CS HUB AUTO-CLAIMER v4.9 ('1/1 LEFT' Sniper)")
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
