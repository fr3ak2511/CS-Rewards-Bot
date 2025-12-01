import csv
import time
import os
import smtplib
import sys
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

# Configuration
HEADLESS = True
PLAYER_ID_FILE = "players.csv"

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def create_driver():
    options = Options()
    if HEADLESS:
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1920,1080")
    
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-notifications")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
    
    try:
        driver_path = ChromeDriverManager().install()
    except:
        driver_path = "/usr/bin/chromedriver"
    
    service = Service(driver_path)
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(60)
    return driver

def login_to_hub(driver, player_id):
    """Login using the same method as working script"""
    log(f"🔐 Logging in with ID: {player_id}")
    
    driver.get("https://hub.vertigogames.co/daily-rewards")
    wait = WebDriverWait(driver, 30)
    
    try:
        # Wait for page load
        time.sleep(5)
        
        # Try to accept cookies
        try:
            accept_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Accept')]")))
            accept_btn.click()
            time.sleep(1)
        except:
            pass
        
        # Check if already logged in
        try:
            driver.find_element(By.XPATH, "//button[contains(text(), 'Logout')]")
            log("✅ Already logged in")
            return True
        except:
            pass
        
        # Find and click login button
        login_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Login')]")))
        login_btn.click()
        log("✅ Clicked Login button")
        time.sleep(3)
        
        # Enter player ID
        input_field = wait.until(EC.visibility_of_element_located((By.XPATH, "//input[@type='text']")))
        input_field.clear()
        input_field.send_keys(player_id)
        log(f"⌨️  Entered player ID")
        time.sleep(1)
        
        # Submit
        input_field.send_keys(Keys.ENTER)
        log("⏎ Submitted login")
        time.sleep(5)
        
        # Verify login
        try:
            wait.until(EC.presence_of_element_located((By.XPATH, "//button[contains(text(), 'Logout')]")))
            log("✅ Login successful!")
            driver.save_screenshot(f"login_success_{player_id}.png")
            return True
        except:
            log("❌ Login verification failed")
            driver.save_screenshot(f"login_failed_{player_id}.png")
            return False
            
    except Exception as e:
        log(f"❌ Login error: {e}")
        driver.save_screenshot(f"login_error_{player_id}.png")
        return False

def claim_daily_rewards(driver, player_id):
    """Claim daily rewards using JavaScript like working script"""
    log("🎁 Claiming Daily Rewards...")
    claimed = 0
    
    try:
        driver.get("https://hub.vertigogames.co/daily-rewards")
        time.sleep(5)
        
        for attempt in range(5):
            # Use JavaScript to find and click claim buttons (same as working script)
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
                log(f"✅ Claimed daily reward {claimed + 1}")
                claimed += 1
                time.sleep(3)
            else:
                log(f"ℹ️  No more daily rewards to claim (attempt {attempt + 1})")
                break
        
        driver.save_screenshot(f"daily_final_{player_id}.png")
        
    except Exception as e:
        log(f"❌ Daily rewards error: {e}")
    
    return claimed

def claim_store_rewards(driver, player_id):
    """Claim store rewards"""
    log("🏪 Claiming Store Rewards...")
    claimed = 0
    
    try:
        driver.get("https://hub.vertigogames.co/store")
        time.sleep(5)
        
        # Click Daily Rewards tab
        try:
            tab = driver.find_element(By.XPATH, "//*[contains(text(), 'Daily Rewards')]")
            tab.click()
            time.sleep(2)
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
                log(f"✅ Claimed store reward {claimed + 1}")
                claimed += 1
                time.sleep(3)
            else:
                log(f"ℹ️  No more store rewards to claim")
                break
        
        driver.save_screenshot(f"store_final_{player_id}.png")
        
    except Exception as e:
        log(f"❌ Store rewards error: {e}")
    
    return claimed

def claim_progression_rewards(driver, player_id):
    """Claim progression rewards using same logic as working script"""
    log("📊 Claiming Progression Rewards...")
    claimed = 0
    
    try:
        driver.get("https://hub.vertigogames.co/progression-program")
        time.sleep(5)
        
        for attempt in range(6):
            # Same JavaScript as working progression script
            result = driver.execute_script("""
                let buttons = document.querySelectorAll('button');
                for (let btn of buttons) {
                    let text = btn.innerText.trim().toLowerCase();
                    if (text === 'claim') {
                        // Check if button is on the right side (not in left nav)
                        let rect = btn.getBoundingClientRect();
                        if (rect.left > 300) {
                            // Check it's not already delivered
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
                log(f"✅ Claimed progression reward {claimed + 1}")
                claimed += 1
                time.sleep(4)
                
                # Close any success popups
                try:
                    driver.execute_script("""
                        document.querySelectorAll('button').forEach(btn => {
                            if (btn.innerText.toLowerCase().includes('continue') || 
                                btn.innerText.toLowerCase().includes('close')) {
                                btn.click();
                            }
                        });
                    """)
                except:
                    pass
            else:
                log(f"ℹ️  No more progression rewards to claim")
                break
        
        driver.save_screenshot(f"progression_final_{player_id}.png")
        
    except Exception as e:
        log(f"❌ Progression rewards error: {e}")
    
    return claimed

def process_player(player_id):
    """Process a single player"""
    driver = None
    stats = {"player_id": player_id, "daily": 0, "store": 0, "progression": 0, "status": "Failed"}
    
    try:
        log(f"\n{'='*60}")
        log(f"🚀 Processing: {player_id}")
        log(f"{'='*60}")
        
        driver = create_driver()
        
        if not login_to_hub(driver, player_id):
            stats['status'] = "Login Failed"
            return stats
        
        # Claim rewards
        stats['daily'] = claim_daily_rewards(driver, player_id)
        stats['store'] = claim_store_rewards(driver, player_id)
        stats['progression'] = claim_progression_rewards(driver, player_id)
        
        total = stats['daily'] + stats['store'] + stats['progression']
        if total > 0:
            stats['status'] = "Success"
            log(f"🎉 Total claimed: {total} rewards")
        else:
            stats['status'] = "No Rewards"
            log(f"⚠️  No rewards claimed (might be already claimed today)")
        
    except Exception as e:
        log(f"❌ Error processing {player_id}: {e}")
        stats['status'] = f"Error: {str(e)[:30]}"
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
        log("⚠️  Email secrets not set")
        return
    
    subject = f"Hub Rewards Summary - {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    html = f"""
<html>
<body>
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
        log("✅ Email sent successfully")
    except Exception as e:
        log(f"❌ Email failed: {e}")

def main():
    log("="*60)
    log("CS HUB REWARDS CLAIMER")
    log("="*60)
    
    # Read player IDs
    try:
        with open(PLAYER_ID_FILE, 'r') as f:
            player_ids = [row[0].strip() for row in csv.reader(f) if row and row[0].strip()]
    except Exception as e:
        log(f"❌ Failed to read {PLAYER_ID_FILE}: {e}")
        return
    
    log(f"📋 Loaded {len(player_ids)} player(s)")
    
    # Process each player
    results = []
    for player_id in player_ids:
        result = process_player(player_id)
        results.append(result)
        time.sleep(5)  # Wait between players
    
    # Send summary
    log("\n" + "="*60)
    log("SUMMARY")
    log("="*60)
    for r in results:
        log(f"{r['player_id']}: Daily={r['daily']}, Store={r['store']}, Prog={r['progression']}, Status={r['status']}")
    
    send_email_summary(results)
    log("\n🏁 Complete!")

if __name__ == "__main__":
    main()
