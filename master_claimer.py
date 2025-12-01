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
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager

HEADLESS = True
PLAYER_ID_FILE = "players.csv"

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def create_driver():
    """Create Chrome driver with GitHub Actions stability fixes"""
    options = Options()
    
    if HEADLESS:
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1920,1080")
    
    # Critical stability flags for GitHub Actions
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-software-rasterizer")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--single-process")
    options.add_argument("--disable-background-networking")
    options.add_argument("--disable-background-timer-throttling")
    options.add_argument("--disable-backgrounding-occluded-windows")
    options.add_argument("--disable-breakpad")
    options.add_argument("--disable-component-extensions-with-background-pages")
    options.add_argument("--disable-features=TranslateUI,BlinkGenPropertyTrees")
    options.add_argument("--disable-ipc-flooding-protection")
    options.add_argument("--disable-renderer-backgrounding")
    options.add_argument("--enable-features=NetworkService,NetworkServiceInProcess")
    options.add_argument("--force-color-profile=srgb")
    options.add_argument("--hide-scrollbars")
    options.add_argument("--metrics-recording-only")
    options.add_argument("--mute-audio")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    prefs = {
        "profile.default_content_setting_values.notifications": 2,
        "profile.managed_default_content_settings.images": 1
    }
    options.add_experimental_option("prefs", prefs)
    options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    options.add_experimental_option("useAutomationExtension", False)
    
    try:
        driver_path = ChromeDriverManager().install()
    except:
        driver_path = "/usr/bin/chromedriver"
    
    service = Service(driver_path)
    service.log_path = "/dev/null"
    
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(90)
    driver.set_script_timeout(30)
    
    return driver

def login_to_hub(driver, player_id):
    """Login using TOP-RIGHT Login button, not banner button"""
    log(f"🔐 Logging in: {player_id}")
    
    try:
        driver.get("https://hub.vertigogames.co/daily-rewards")
        log("📄 Page loaded")
        time.sleep(8)
        
        driver.save_screenshot(f"page_loaded_{player_id}.png")
        
        # Accept cookies
        try:
            cookie_btn = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Accept')]"))
            )
            cookie_btn.click()
            log("✅ Accepted cookies")
            time.sleep(2)
        except:
            log("ℹ️  No cookie banner")
        
        # Check if already logged in
        try:
            driver.find_element(By.XPATH, "//button[contains(text(), 'Logout')]")
            log("✅ Already logged in")
            return True
        except:
            pass
        
        # Click TOP-RIGHT Login button using JavaScript
        # This finds the button in the header/navigation, not the banner
        try:
            log("🔍 Looking for top-right Login button...")
            
            # Use JavaScript to find and click the CORRECT login button
            # The top-right login button is usually in a nav/header element
            clicked = driver.execute_script("""
                // Method 1: Find login button in header/nav area (top of page)
                let buttons = document.querySelectorAll('button');
                for (let btn of buttons) {
                    if (btn.innerText.trim() === 'Login') {
                        let rect = btn.getBoundingClientRect();
                        // Top-right button should be in the top 100px and right side
                        if (rect.top < 100 && rect.right > window.innerWidth / 2) {
                            btn.click();
                            return 'top-right';
                        }
                    }
                }
                
                // Method 2: Find the LAST login button (usually the header one)
                let loginButtons = Array.from(buttons).filter(b => b.innerText.trim() === 'Login');
                if (loginButtons.length > 1) {
                    // If multiple login buttons, the last one is usually the header
                    loginButtons[loginButtons.length - 1].click();
                    return 'last-button';
                }
                
                // Method 3: Find login button NOT inside a banner/promo div
                for (let btn of buttons) {
                    if (btn.innerText.trim() === 'Login') {
                        let parent = btn.closest('div');
                        // Skip if parent contains "claim" or "reward" text (banner buttons)
                        if (parent && !parent.innerText.toLowerCase().includes('claim')) {
                            btn.click();
                            return 'non-banner';
                        }
                    }
                }
                
                return null;
            """)
            
            if clicked:
                log(f"✅ Clicked Login button (method: {clicked})")
                time.sleep(5)
            else:
                log("❌ Could not find top-right Login button")
                driver.save_screenshot(f"login_button_not_found_{player_id}.png")
                return False
                
        except Exception as e:
            log(f"❌ Failed to click Login button: {e}")
            driver.save_screenshot(f"login_button_fail_{player_id}.png")
            return False
        
        # Find input field
        try:
            input_field = WebDriverWait(driver, 15).until(
                EC.visibility_of_element_located((By.XPATH, "//input[@type='text']"))
            )
            log("✅ Found input field")
            time.sleep(2)
            
            input_field.click()
            time.sleep(0.5)
            input_field.clear()
            time.sleep(0.5)
            input_field.send_keys(player_id)
            log(f"⌨️  Entered: {player_id}")
            time.sleep(2)
            
            driver.save_screenshot(f"id_entered_{player_id}.png")
            
            input_field.send_keys(Keys.ENTER)
            log("⏎ Submitted")
            time.sleep(8)
            
        except Exception as e:
            log(f"❌ Input field error: {e}")
            driver.save_screenshot(f"input_fail_{player_id}.png")
            return False
        
        # Verify login
        try:
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.XPATH, "//button[contains(text(), 'Logout')]"))
            )
            log("✅ Login verified!")
            driver.save_screenshot(f"login_success_{player_id}.png")
            return True
        except:
            log("❌ Login verification failed")
            driver.save_screenshot(f"login_verify_fail_{player_id}.png")
            with open(f"page_source_{player_id}.html", "w", encoding="utf-8") as f:
                f.write(driver.page_source)
            return False
            
    except Exception as e:
        log(f"❌ Login exception: {e}")
        try:
            driver.save_screenshot(f"login_error_{player_id}.png")
        except:
            pass
        return False

def claim_daily_rewards(driver, player_id):
    """Claim daily rewards using JavaScript"""
    log("🎁 Claiming Daily Rewards...")
    claimed = 0
    
    try:
        driver.get("https://hub.vertigogames.co/daily-rewards")
        time.sleep(6)
        
        for attempt in range(5):
            result = driver.execute_script("""
                let buttons = document.querySelectorAll('button');
                for (let btn of buttons) {
                    if (btn.innerText.trim().toLowerCase() === 'claim' && btn.offsetParent !== null) {
                        btn.click();
                        return true;
                    }
                }
                return false;
            """)
            
            if result:
                log(f"✅ Daily reward {claimed + 1}")
                claimed += 1
                time.sleep(4)
            else:
                log(f"ℹ️  No more daily rewards (try {attempt + 1})")
                break
        
        driver.save_screenshot(f"daily_final_{player_id}.png")
        
    except Exception as e:
        log(f"❌ Daily error: {e}")
    
    return claimed

def claim_store_rewards(driver, player_id):
    """Claim store rewards"""
    log("🏪 Claiming Store Rewards...")
    claimed = 0
    
    try:
        driver.get("https://hub.vertigogames.co/store")
        time.sleep(6)
        
        try:
            driver.execute_script("""
                let tabs = document.querySelectorAll('*');
                for (let el of tabs) {
                    if (el.innerText.includes('Daily Rewards')) {
                        el.click();
                        break;
                    }
                }
            """)
            time.sleep(3)
        except:
            pass
        
        for attempt in range(3):
            result = driver.execute_script("""
                let buttons = document.querySelectorAll('button');
                for (let btn of buttons) {
                    if (btn.innerText.trim().toLowerCase() === 'claim' && btn.offsetParent !== null) {
                        btn.click();
                        return true;
                    }
                }
                return false;
            """)
            
            if result:
                log(f"✅ Store reward {claimed + 1}")
                claimed += 1
                time.sleep(4)
            else:
                log("ℹ️  No more store rewards")
                break
        
        driver.save_screenshot(f"store_final_{player_id}.png")
        
    except Exception as e:
        log(f"❌ Store error: {e}")
    
    return claimed

def claim_progression_rewards(driver, player_id):
    """Claim progression rewards"""
    log("📊 Claiming Progression Rewards...")
    claimed = 0
    
    try:
        driver.get("https://hub.vertigogames.co/progression-program")
        time.sleep(6)
        
        for attempt in range(6):
            result = driver.execute_script("""
                let buttons = document.querySelectorAll('button');
                for (let btn of buttons) {
                    let text = btn.innerText.trim().toLowerCase();
                    if (text === 'claim') {
                        let rect = btn.getBoundingClientRect();
                        if (rect.left > 300) {
                            let parent = btn.closest('div');
                            if (parent && !parent.innerText.includes('Delivered')) {
                                btn.click();
                                return true;
                            }
                        }
                    }
                }
                return false;
            """)
            
            if result:
                log(f"✅ Progression reward {claimed + 1}")
                claimed += 1
                time.sleep(5)
                
                driver.execute_script("""
                    setTimeout(() => {
                        document.querySelectorAll('button').forEach(btn => {
                            if (btn.innerText.toLowerCase().includes('continue') || 
                                btn.innerText.toLowerCase().includes('close')) {
                                btn.click();
                            }
                        });
                    }, 1000);
                """)
                time.sleep(2)
            else:
                log("ℹ️  No more progression rewards")
                break
        
        driver.save_screenshot(f"progression_final_{player_id}.png")
        
    except Exception as e:
        log(f"❌ Progression error: {e}")
    
    return claimed

def process_player(player_id):
    """Process single player"""
    driver = None
    stats = {"player_id": player_id, "daily": 0, "store": 0, "progression": 0, "status": "Failed"}
    
    try:
        log(f"\n{'='*60}")
        log(f"🚀 Processing: {player_id}")
        log(f"{'='*60}")
        
        driver = create_driver()
        log("✅ Driver created")
        
        if not login_to_hub(driver, player_id):
            stats['status'] = "Login Failed"
            return stats
        
        stats['daily'] = claim_daily_rewards(driver, player_id)
        stats['store'] = claim_store_rewards(driver, player_id)
        stats['progression'] = claim_progression_rewards(driver, player_id)
        
        total = stats['daily'] + stats['store'] + stats['progression']
        if total > 0:
            stats['status'] = "Success"
            log(f"🎉 Total: {total} rewards")
        else:
            stats['status'] = "No Rewards"
            log("⚠️  No rewards claimed")
        
    except Exception as e:
        log(f"❌ Error: {e}")
        stats['status'] = f"Error: {str(e)[:30]}"
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass
    
    return stats

def send_email_summary(results):
    """Send email summary"""
    sender = os.environ.get("SENDER_EMAIL")
    recipient = os.environ.get("RECIPIENT_EMAIL")
    password = os.environ.get("GMAIL_APP_PASSWORD")
    
    if not all([sender, recipient, password]):
        log("⚠️  Email secrets not set")
        return
    
    subject = f"Hub Rewards Summary - {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    html = f"""
<html><body>
<h2>Hub Rewards Summary</h2>
<p><strong>Run Time:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
<table border="1" cellpadding="5">
<tr><th>Player ID</th><th>Daily</th><th>Store</th><th>Progression</th><th>Status</th></tr>
"""
    
    for r in results:
        html += f"<tr><td>{r['player_id']}</td><td>{r['daily']}</td><td>{r['store']}</td><td>{r['progression']}</td><td>{r['status']}</td></tr>"
    
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
    log("CS HUB REWARDS CLAIMER")
    log("="*60)
    
    try:
        with open(PLAYER_ID_FILE, 'r') as f:
            player_ids = [row[0].strip() for row in csv.reader(f) if row and row[0].strip()]
    except Exception as e:
        log(f"❌ Failed to read {PLAYER_ID_FILE}: {e}")
        return
    
    log(f"📋 Loaded {len(player_ids)} player(s)")
    
    results = []
    for player_id in player_ids:
        result = process_player(player_id)
        results.append(result)
        time.sleep(5)
    
    log("\n" + "="*60)
    log("SUMMARY")
    log("="*60)
    for r in results:
        log(f"{r['player_id']}: D={r['daily']}, S={r['store']}, P={r['progression']}, Status={r['status']}")
    
    send_email_summary(results)
    log("\n🏁 Complete!")

if __name__ == "__main__":
    main()
