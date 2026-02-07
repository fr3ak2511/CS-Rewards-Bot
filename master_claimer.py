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
    """Claim Daily Rewards - From master_claimer_Daily.py"""
    log("🎁 Claiming Daily Rewards...")
    claimed = 0
    try:
        driver.get("https://hub.vertigogames.co/daily-rewards")
        bypass_cloudflare(driver)
        time.sleep(2)
        close_popup(driver)
        
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
                break
            else:
                log(f"ℹ️  No claimable daily rewards (attempt {attempt + 1})")
                time.sleep(1)
        
        driver.save_screenshot(f"daily_final_{player_id}.png")
        
    except Exception as e:
        log(f"❌ Daily error: {e}")
    
    return claimed

def physical_click(driver, element):
    """Physical click helper from master_claimer_Store.py"""
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
    """Ensure we're on store page from master_claimer_Store.py"""
    try:
        if "store" not in driver.current_url:
            driver.get("https://hub.vertigogames.co/store")
            bypass_cloudflare(driver)
            time.sleep(2)
        return True
    except:
        return False

def claim_store_rewards(driver, player_id):
    """HYBRID Store Claims - First 2 with physical_click, 3rd with JavaScript"""
    log("🏪 Claiming Store Rewards (HYBRID MODE)...")
    claimed = 0
    max_claims = 3
    
    try:
        driver.get("https://hub.vertigogames.co/store")
        bypass_cloudflare(driver)
        time.sleep(2)
        close_popup(driver)
        
        # FIRST 2 CLAIMS: Use physical_click method from master_claimer_Store.py
        log("🔹 Phase 1: Physical Click Method (Claims 1-2)")
        for attempt in range(5):  # Increased attempts
            if claimed >= 2:
                break
                
            ensure_store_page(driver)
            time.sleep(1.5)
            
            found_btn = None
            try:
                buttons = driver.find_elements(By.TAG_NAME, "button")
                for btn in buttons:
                    try:
                        btn_text = btn.text.strip().lower()
                        if btn_text == "free" and btn.is_displayed() and btn.is_enabled():
                            # Timer Check
                            try:
                                parent = btn.find_element(By.XPATH, "./..")
                                if "next in" in parent.text.lower(): continue
                            except: pass
                            
                            found_btn = btn
                            break # Click one at a time
                    except: continue
            except: continue
            
            if found_btn:
                log(f"🖱️ Found Free Button. Clicking...")
                if physical_click(driver, found_btn):
                    time.sleep(4)
                    
                    # Assume success
                    close_popup(driver)
                    claimed += 1
                    log(f"✅ Store Claim #{claimed} (Physical Click)")
                    
                    # Refresh page state
                    ensure_store_page(driver)
                    time.sleep(1)
            else:
                log(f"ℹ️  No 'Free' buttons found in Phase 1 (attempt {attempt+1})")
                if attempt < 4:
                    time.sleep(2)
                else:
                    break
        
        # THIRD CLAIM: Use JavaScript method from master_claimer_v2_6.py
        if claimed < max_claims:
            log("🔹 Phase 2: JavaScript Method (Claim 3)")
            for attempt in range(5):  # Increased attempts
                if claimed >= max_claims:
                    break
                
                ensure_store_page(driver)
                time.sleep(1.5)
                
                result = driver.execute_script("""
                    // Target Store Bonus Cards
                    let storeBonusCards = document.querySelectorAll('[class*="StoreBonus"]');
                    if (storeBonusCards.length === 0) {
                        storeBonusCards = document.querySelectorAll('div');
                    }
                    
                    // Find buttons with "Free" text (NO timer)
                    for (let card of storeBonusCards) {
                        let cardText = card.innerText || '';
                        
                        // SKIP cards with timer
                        if (cardText.includes('Next in') || cardText.match(/\\d+h\\s+\\d+m/)) {
                            continue;
                        }
                        
                        // Find button
                        let buttons = card.querySelectorAll('button');
                        for (let btn of buttons) {
                            let btnText = btn.innerText.trim().toLowerCase();
                            if ((btnText === 'free' || btnText === 'claim') && btn.offsetParent !== null && !btn.disabled) {
                                btn.scrollIntoView({behavior: 'smooth', block: 'center'});
                                setTimeout(function() {
                                    btn.click();
                                }, 500);
                                return true;
                            }
                        }
                    }
                    return false;
                """)
                
                if result:
                    claimed += 1
                    log(f"✅ Store Claim #{claimed} (JavaScript)")
                    time.sleep(3.0)
                    close_popup(driver)
                    time.sleep(1)
                    if not ensure_store_page(driver): break
                else:
                    log(f"ℹ️  No more available claims in Phase 2 (attempt {attempt + 1})")
                    if attempt < 4:
                        time.sleep(2)
                    else:
                        break
        
        log(f"📊 Store Claims Complete: {claimed}/{max_claims}")
        driver.save_screenshot(f"store_final_{player_id}.png")
        
    except Exception as e:
        log(f"❌ Store error: {e}")
    
    return claimed

def claim_progression_program_rewards(driver, player_id):
    """Claim Progression - From master_claimer_v2_6.py"""
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
                # Scroll if nothing found
                driver.execute_script("let c=document.querySelectorAll('div');for(let i of c){if(i.scrollWidth>i.clientWidth){i.scrollLeft+=400;}}")
                time.sleep(1)
        
    except: pass
    return claimed

def process_player(player_id):
    """Process single player with retry logic"""
    driver = None
    stats = {"player_id": player_id, "daily": 0, "store": 0, "progression": 0, "status": "Failed"}
    try:
        log(f"\n🚀 {player_id}")
        driver = create_driver()
        if not login_to_hub(driver, player_id):
            stats['status'] = "Login Failed"
            return stats
        
        # Claim Daily Rewards
        stats['daily'] = claim_daily_rewards(driver, player_id)
        
        # Claim Store Rewards with retry logic
        max_store_expected = 3
        store_retry_attempts = 3
        for retry in range(store_retry_attempts):
            stats['store'] = claim_store_rewards(driver, player_id)
            if stats['store'] >= max_store_expected:
                log(f"✅ All {max_store_expected} Store rewards claimed!")
                break
            elif retry < store_retry_attempts - 1:
                log(f"⚠️ Only {stats['store']}/{max_store_expected} Store claimed. Retry {retry + 1}/{store_retry_attempts - 1}...")
                time.sleep(2)
        
        # Claim Progression with retry logic
        progression_retry_attempts = 2
        for retry in range(progression_retry_attempts):
            claimed = claim_progression_program_rewards(driver, player_id)
            stats['progression'] += claimed
            if claimed == 0 and retry < progression_retry_attempts - 1:
                log(f"⚠️ No progression claimed. Retry {retry + 1}/{progression_retry_attempts - 1}...")
                time.sleep(2)
            elif claimed == 0:
                break
        
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
    """Send email summary - Rich HTML format from master_claimer_Store.py"""
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
                <li><strong>Daily Rewards:</strong> Exactly 1 per player per day.</li>
                <li><strong>Progression:</strong> Varies (Dependent on Bullets / Grenades claimed from the Store Rewards)</li>
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
    log("CS HUB AUTO-CLAIMER UNIFIED (Hybrid Store Logic)")
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
