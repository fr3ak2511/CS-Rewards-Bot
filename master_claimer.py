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
from webdriver_manager.chrome import ChromeDriverManager

# --- CONFIG ---
PLAYER_ID_FILE = "players.csv"
HEADLESS = True
DAILY_RESET_HOUR_IST = 5
DAILY_RESET_MINUTE_IST = 30
EXPECTED_STORE_PER_PLAYER = 3

def safe_print(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
    import sys
    sys.stdout.flush()

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

# --- DRIVER (V24 CONFIG - VERIFIED STABLE) ---
def create_driver():
    options = Options()
    if HEADLESS:
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1920,1080")
    
    # CRITICAL STABILITY FLAGS
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-software-rasterizer")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--disable-blink-features=AutomationControlled")
    
    # THE MAGIC FLAGS (V24 Configuration)
    options.add_argument("--single-process")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    options.page_load_strategy = 'eager'
    
    try:
        path = ChromeDriverManager().install()
    except:
        path = "/usr/bin/chromedriver"
    
    service = Service(path)
    driver = webdriver.Chrome(service=service, options=options)
    
    # Anti-detection
    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        })
    except: pass
    
    driver.set_page_load_timeout(60)
    driver.implicitly_wait(5)
    return driver

# --- HELPERS ---
def close_popups_safe(driver):
    try:
        # JS Close
        driver.execute_script("""
            document.querySelectorAll('.modal, .popup, .dialog, button').forEach(btn => {
                let text = btn.innerText.toLowerCase();
                if(text.includes('close') || text === '×' || text === 'x' || text.includes('continue')) {
                    if(btn.offsetParent !== null) btn.click();
                }
            });
        """)
        # Safe Area
        ActionChains(driver).move_by_offset(10, 10).click().perform()
    except: pass
    return True

def accept_cookies(driver, wait):
    try:
        btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Accept')]")))
        btn.click()
    except: pass

# --- LOGIN (PHYSICAL CLICK PRIORITY) ---
def verify_login_success(driver):
    try:
        if driver.find_elements(By.XPATH, "//button[contains(text(), 'Logout')]"): return True
        if driver.find_elements(By.XPATH, "//button[contains(text(), 'Claim')]"): return True
        return False
    except: return False

def login(driver, wait, player_id):
    driver.get("https://hub.vertigogames.co/daily-rewards")
    time.sleep(5)
    accept_cookies(driver, wait)
    close_popups_safe(driver)
    
    if verify_login_success(driver):
        return True
    
    # 1. Click Login (PHYSICAL CLICK PRIORITY)
    login_clicked = False
    login_selectors = ["//button[contains(text(),'Login')]", "//a[contains(text(),'Login')]"]
    for selector in login_selectors:
        try:
            btns = driver.find_elements(By.XPATH, selector)
            for btn in btns:
                if btn.is_displayed():
                    # Use ActionChains for a "real" click
                    ActionChains(driver).move_to_element(btn).click().perform()
                    safe_print("Clicked Login (Physical)")
                    login_clicked = True
                    time.sleep(3)
                    break
            if login_clicked: break
        except: continue
    
    if not login_clicked:
        # Fallback to JS
        try:
            driver.execute_script("document.querySelector('button.login')?.click()")
            safe_print("Clicked Login (JS)")
            time.sleep(3)
        except: pass
    
    # 2. Input
    inp = None
    try:
        inp = WebDriverWait(driver, 10).until(
            EC.visibility_of_element_located((By.XPATH, "//input[@type='text' or contains(@placeholder, 'ID')]"))
        )
    except:
        driver.save_screenshot(f"login_fail_{player_id}.png")
        raise Exception("Input not found")
    
    inp.clear()
    inp.send_keys(player_id)
    time.sleep(0.5)
    
    # 3. Submit
    try:
        submit_btn = driver.find_element(By.XPATH, "//button[@type='submit']")
        ActionChains(driver).move_to_element(submit_btn).click().perform()
    except:
        inp.send_keys(Keys.ENTER)
    
    time.sleep(5)
    
    if verify_login_success(driver): return True
    
    try:
        if not inp.is_displayed(): return True
    except: return True
    
    raise Exception("Login verification failed")

# --- CLAIMING ---
def get_valid_claim_buttons(driver):
    valid_buttons = []
    try:
        xpath = "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'claim')]"
        buttons = driver.find_elements(By.XPATH, xpath)
        for btn in buttons:
            if btn.is_displayed() and btn.is_enabled():
                text = btn.text.lower()
                if "login" in text or "buy" in text: continue
                valid_buttons.append(btn)
    except: pass
    return valid_buttons

def perform_claim_loop(driver, section_name):
    claimed = 0
    for _ in range(6):
        close_popups_safe(driver)
        time.sleep(1.5)
        
        buttons = get_valid_claim_buttons(driver)
        if not buttons: break
        
        btn = buttons[0]
        try:
            driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", btn)
            time.sleep(0.5)
            
            # Double Tap: Physical then JS
            try: ActionChains(driver).move_to_element(btn).click().perform()
            except: driver.execute_script("arguments[0].click();", btn)
            
            safe_print(f"Clicked {section_name}...")
            time.sleep(3)
            
            if close_popups_safe(driver):
                claimed += 1
                safe_print(f"{section_name} Reward {claimed} VERIFIED")
            else:
                try:
                    if not btn.is_displayed():
                        claimed += 1
                        safe_print(f"{section_name} Reward {claimed} VERIFIED (Gone)")
                except:
                    claimed += 1
                    safe_print(f"{section_name} Reward {claimed} VERIFIED (Stale)")
        except Exception: continue
    
    return claimed

def claim_daily(driver):
    return perform_claim_loop(driver, "Daily")

def claim_store(driver):
    driver.get("https://hub.vertigogames.co/store")
    time.sleep(3)
    close_popups_safe(driver)
    
    try:
        driver.execute_script("window.scrollTo(0, 300);")
        time.sleep(1)
        tab = driver.find_element(By.XPATH, "//*[contains(text(), 'Daily Rewards')]")
        tab.click()
        time.sleep(1.5)
    except: pass
    
    return perform_claim_loop(driver, "Store")

def claim_progression(driver):
    claimed = 0
    driver.get("https://hub.vertigogames.co/progression-program")
    time.sleep(3)
    close_popups_safe(driver)
    
    try:
        arrows = driver.find_elements(By.XPATH, "//*[contains(@class, 'next') or contains(@class, 'right')]")
        for arrow in arrows:
            if arrow.is_displayed():
                driver.execute_script("arguments[0].click();", arrow)
                time.sleep(0.5)
    except: pass
    
    for _ in range(6):
        time.sleep(1)
        
        # FIXED: Removed rect.left > 300 filter
        js_find_and_click = """
            let buttons = document.querySelectorAll('button');
            for (let btn of buttons) {
                let text = btn.innerText.trim();
                if (text.toLowerCase() === 'claim') {
                    if (!btn.parentElement.innerText.includes('Delivered')) {
                        if (btn.offsetParent !== null && !btn.disabled) {
                            btn.click();
                            return true;
                        }
                    }
                }
            }
            return false;
        """
        
        try:
            clicked = driver.execute_script(js_find_and_click)
            if clicked:
                safe_print(f"Progression Clicked...")
                time.sleep(4)
                if close_popups_safe(driver):
                    claimed += 1
                    safe_print(f"Progression Reward {claimed} VERIFIED")
            else: break
        except: break
    
    return claimed

def send_email_summary(results, num_players):
    """Send email summary with daily tracking"""
    try:
        sender = os.environ.get("SENDER_EMAIL")
        recipient = os.environ.get("RECIPIENT_EMAIL")
        password = os.environ.get("GMAIL_APP_PASSWORD")
        
        if not all([sender, recipient, password]):
            safe_print("⚠️  Email env vars missing")
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
        
        # Build email body
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
                <li><strong>Store Rewards:</strong> Exactly 3 per player per day (resets at 5:30 AM IST)</li>
                <li><strong>Daily Rewards:</strong> Variable (~1 per hour, player-dependent)</li>
                <li><strong>Progression:</strong> Unlimited (requires Grenades from Store claims)</li>
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
        
        safe_print("✅ Email sent")
        
    except Exception as e:
        safe_print(f"❌ Email error: {e}")

def main():
    """Main orchestrator - NEW: Loops through CSV"""
    safe_print("="*60)
    safe_print("CS HUB AUTO-CLAIMER v2.1")
    safe_print("="*60)
    
    # Show IST tracking
    ist_now = get_ist_time()
    window_start = get_current_daily_window_start()
    next_reset = get_next_daily_reset()
    
    safe_print(f"🕐 IST: {ist_now.strftime('%Y-%m-%d %I:%M %p')}")
    safe_print(f"⏰ Next Reset: {format_time_until_reset(next_reset)}")
    safe_print("")
    
    # Read all players from CSV
    players = []
    try:
        with open(PLAYER_ID_FILE, 'r') as f:
            reader = csv.DictReader(f)
            players = [row['player_id'].strip() for row in reader if row['player_id'].strip()]
    except Exception as e:
        safe_print(f"❌ Cannot read {PLAYER_ID_FILE}: {e}")
        return
    
    num_players = len(players)
    safe_print(f"📋 {num_players} player(s) loaded")
    safe_print("")
    
    results = []
    
    # Process each player
    for player_id in players:
        safe_print("="*60)
        safe_print(f"🚀 Processing: {player_id}")
        safe_print("="*60)
        
        driver = None
        stats = {"player_id": player_id, "daily": 0, "store": 0, "progression": 0, "status": "Failed"}
        
        try:
            driver = create_driver()
            wait = WebDriverWait(driver, 45)
            
            if login(driver, wait, player_id):
                time.sleep(2)
                stats['daily'] = claim_daily(driver)
                stats['store'] = claim_store(driver)
                stats['progression'] = claim_progression(driver)
                
                total = stats['daily'] + stats['store'] + stats['progression']
                stats['status'] = "Success" if total > 0 else "No Rewards"
                safe_print(f"✅ Finished {player_id}: {stats['daily']}/{stats['store']}/{stats['progression']}")
            else:
                stats['status'] = "Login Failed"
                safe_print(f"❌ Login failed for {player_id}")
        
        except Exception as e:
            safe_print(f"❌ Error on {player_id}: {str(e)[:50]}")
            stats['status'] = "Error"
        
        finally:
            if driver:
                try: driver.quit()
                except: pass
        
        results.append(stats)
        time.sleep(3)  # Pause between players
    
    # Final summary
    safe_print("")
    safe_print("="*60)
    safe_print("FINAL SUMMARY")
    safe_print("="*60)
    
    total_d = sum(r['daily'] for r in results)
    total_s = sum(r['store'] for r in results)
    total_p = sum(r['progression'] for r in results)
    
    safe_print(f"Daily: {total_d}, Store: {total_s}/{num_players * EXPECTED_STORE_PER_PLAYER}, Progression: {total_p}")
    
    for r in results:
        total = r['daily'] + r['store'] + r['progression']
        safe_print(f"{r['player_id']}: D={r['daily']}, S={r['store']}, P={r['progression']}, Total={total} → {r['status']}")
    
    # Send email
    send_email_summary(results, num_players)
    
    safe_print("")
    safe_print("🏁 Done!")

if __name__ == "__main__":
    main()
