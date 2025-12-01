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
from webdriver_manager.chrome import ChromeDriverManager

HEADLESS = True
PLAYER_ID_FILE = "players.csv"

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def create_driver():
    """Stable Chrome for GitHub Actions"""
    options = Options()
    
    if HEADLESS:
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1920,1080")
    
    # Minimal stable flags
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
    
    prefs = {"profile.default_content_setting_values.notifications": 2}
    options.add_experimental_option("prefs", prefs)
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    
    try:
        driver_path = ChromeDriverManager().install()
    except:
        driver_path = "/usr/bin/chromedriver"
    
    service = Service(driver_path)
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(60)
    
    return driver

def login_to_hub(driver, player_id):
    """Login using TOP-RIGHT button"""
    log(f"🔐 Logging in: {player_id}")
    
    try:
        driver.get("https://hub.vertigogames.co/daily-rewards")
        log("📄 Page loaded")
        time.sleep(8)
        
        driver.save_screenshot(f"01_page_loaded_{player_id}.png")
        
        # Accept cookies
        try:
            cookie_btn = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Accept')]"))
            )
            driver.execute_script("arguments[0].click();", cookie_btn)
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
        
        # Click TOP-RIGHT Login button
        try:
            log("🔍 Finding top-right Login button...")
            
            clicked = driver.execute_script("""
                let buttons = document.querySelectorAll('button');
                for (let btn of buttons) {
                    if (btn.innerText.trim() === 'Login') {
                        let rect = btn.getBoundingClientRect();
                        if (rect.top < 100 && rect.right > window.innerWidth / 2) {
                            btn.click();
                            return 'top-right';
                        }
                    }
                }
                
                let loginButtons = Array.from(buttons).filter(b => b.innerText.trim() === 'Login');
                if (loginButtons.length > 1) {
                    loginButtons[loginButtons.length - 1].click();
                    return 'last-button';
                }
                
                for (let btn of buttons) {
                    if (btn.innerText.trim() === 'Login') {
                        let parent = btn.closest('div');
                        if (parent && !parent.innerText.toLowerCase().includes('claim')) {
                            btn.click();
                            return 'non-banner';
                        }
                    }
                }
                
                return null;
            """)
            
            if clicked:
                log(f"✅ Clicked Login ({clicked})")
                time.sleep(6)
                driver.save_screenshot(f"02_login_clicked_{player_id}.png")
            else:
                log("❌ Login button not found")
                driver.save_screenshot(f"02_login_not_found_{player_id}.png")
                return False
                
        except Exception as e:
            log(f"❌ Login button error: {e}")
            driver.save_screenshot(f"02_login_error_{player_id}.png")
            return False
        
        # Find and fill input field using JavaScript
        try:
            log("⌨️  Entering player ID...")
            
            # Use JavaScript to find and fill input
            success = driver.execute_script("""
                let inputs = document.querySelectorAll('input[type="text"]');
                if (inputs.length > 0) {
                    inputs[0].value = arguments[0];
                    inputs[0].dispatchEvent(new Event('input', { bubbles: true }));
                    inputs[0].dispatchEvent(new Event('change', { bubbles: true }));
                    return true;
                }
                return false;
            """, player_id)
            
            if success:
                log(f"✅ Entered: {player_id}")
                time.sleep(2)
                driver.save_screenshot(f"03_id_entered_{player_id}.png")
                
                # Submit using JavaScript
                driver.execute_script("""
                    let loginButtons = document.querySelectorAll('button');
                    for (let btn of loginButtons) {
                        if (btn.innerText.toLowerCase().includes('login') && btn.offsetParent !== null) {
                            btn.click();
                            break;
                        }
                    }
                """)
                log("⏎ Submitted")
                time.sleep(8)
                
            else:
                log("❌ Input field not found")
                driver.save_screenshot(f"03_input_not_found_{player_id}.png")
                return False
            
        except Exception as e:
            log(f"❌ Input error: {e}")
            driver.save_screenshot(f"03_input_error_{player_id}.png")
            return False
        
        # Verify login
        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.XPATH, "//button[contains(text(), 'Logout')]"))
            )
            log("✅ Login verified!")
            driver.save_screenshot(f"04_login_success_{player_id}.png")
            return True
        except:
            log("❌ Login verification failed")
            driver.save_screenshot(f"04_verification_fail_{player_id}.png")
            return False
            
    except Exception as e:
        log(f"❌ Login exception: {e}")
        try:
            driver.save_screenshot(f"99_exception_{player_id}.png")
        except:
            pass
        return False

def claim_daily_rewards(driver, player_id):
    """Claim daily rewards"""
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
                log(f"✅ Daily #{claimed + 1}")
                claimed += 1
                time.sleep(4)
            else:
                log(f"ℹ️  No more daily rewards")
                break
        
        driver.save_screenshot(f"daily_final_{player_id}.png")
        
    except Exception as e:
        log(f"❌ Daily error: {e}")
    
    return claimed

def claim_store_rewards(driver, player_id):
    """Claim store rewards"""
    log("🏪 Claiming Store...")
    claimed = 0
    
    try:
        driver.get("https://hub.vertigogames.co/store")
        time.sleep(6)
        
        try:
            driver.execute_script("""
                let all = document.querySelectorAll('*');
                for (let el of all) {
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
                log(f"✅ Store #{claimed + 1}")
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
    """Claim progression"""
    log("📊 Claiming Progression...")
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
                log(f"✅ Progression #{claimed + 1}")
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
                log("ℹ️  No more progression")
                break
        
        driver.save_screenshot(f"prog_final_{player_id}.png")
        
    except Exception as e:
        log(f"❌ Progression error: {e}")
    
    return claimed

def process_player(player_id):
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
        
        stats['daily'] = claim_daily_rewards(driver, player_id)
        stats['store'] = claim_store_rewards(driver, player_id)
        stats['progression'] = claim_progression_rewards(driver, player_id)
        
        total = stats['daily'] + stats['store'] + stats['progression']
        if total > 0:
            stats['status'] = "Success"
            log(f"🎉 Total: {total}")
        else:
            stats['status'] = "No Rewards"
            log("⚠️  None claimed")
        
    except Exception as e:
        log(f"❌ Error: {e}")
        stats['status'] = f"Error"
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass
    
    return stats

def send_email_summary(results):
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
<tr><th>ID</th><th>Daily</th><th>Store</th><th>Prog</th><th>Status</th></tr>
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
        time.sleep(5)
    
    log("\n" + "="*60)
    log("SUMMARY")
    log("="*60)
    for r in results:
        log(f"{r['player_id']}: D={r['daily']}, S={r['store']}, P={r['progression']} → {r['status']}")
    
    send_email_summary(results)
    log("\n🏁 Done!")

if __name__ == "__main__":
    main()
