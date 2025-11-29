import time
import csv
import os
import smtplib
from email.mime.text import MIMEText
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# =====================================================
# Configuration from GitHub Secrets
# =====================================================
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USERNAME)
SMTP_TO = os.getenv("SMTP_TO", SMTP_FROM)

# Screenshot folder (GitHub Actions artifact)
SCREENSHOT_DIR = os.path.join(os.getcwd(), "screenshots")
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

def log(message):
    """Print with timestamp for debugging"""
    print(f"[{datetime.utcnow().strftime('%H:%M:%S')}] {message}")

# =====================================================
# Selenium Setup
# =====================================================
def make_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--disable-notifications")
    chrome_options.add_argument("--lang=en-US")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-logging"])
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    driver.set_page_load_timeout(30)
    return driver

def take_screenshot(driver, filename):
    """Save screenshot for debugging"""
    try:
        path = os.path.join(SCREENSHOT_DIR, filename)
        driver.save_screenshot(path)
        log(f"  📸 Screenshot saved: {filename}")
    except Exception as e:
        log(f"  ⚠️  Could not save screenshot: {str(e)}")

# =====================================================
# Core Reward Claiming Logic (Enhanced)
# =====================================================
def claim_rewards_for_id(player_id):
    driver = make_driver()
    start_time = time.time()
    
    daily_claimed = 0
    store_claimed = 0
    success = False
    error_msg = ""
    page_source_snippet = ""
    
    log(f"\n{'='*60}")
    log(f"Processing ID: {player_id}")
    
    try:
        # Step 1: Use invite code
        invite_url = f"https://hub.vertigogames.co/?inviteCode={player_id}"
        log(f"  → Navigating to: {invite_url}")
        driver.get(invite_url)
        time.sleep(8)  # Extended wait for auth
        take_screenshot(driver, f"01_invite_page_{player_id}.png")
        
        # Save page source for debugging
        page_source_snippet = driver.page_source[:500]
        
        # Step 2: Check current URL (does invite code redirect?)
        current_url = driver.current_url
        log(f"  → Current URL: {current_url}")
        
        # Step 3: Look for ANY interactive elements
        buttons = driver.find_elements(By.TAG_NAME, "button")
        links = driver.find_elements(By.TAG_NAME, "a")
        log(f"  → Found {len(buttons)} buttons, {len(links)} links")
        
        # Step 4: Check for claim buttons specifically
        claim_buttons = driver.find_elements(By.XPATH, "//button[contains(translate(text(), 'CLAI', 'clai'), 'claim')]")
        log(f"  → Found {len(claim_buttons)} claim buttons on invite page")
        
        if claim_buttons:
            success = True
            log(f"  ✓ SUCCESS: Invite code grants direct access!")
            
            # Claim rewards
            for i, btn in enumerate(claim_buttons[:5]):
                try:
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                    time.sleep(0.5)
                    btn.click()
                    log(f"    ✓ Claimed reward {i+1}")
                    time.sleep(1.5)
                    daily_claimed += 1
                except Exception as e:
                    log(f"    ✗ Failed: {str(e)}")
        else:
            log(f"  ✗ No claim buttons on invite page - invite code may be invalid")
            log(f"  → Page snippet: {page_source_snippet[:200]}...")
        
        # Step 5: Try manual navigation if invite page didn't work
        if not success:
            log(f"  → Attempting manual navigation to daily rewards...")
            driver.get("https://hub.vertigogames.co/daily-rewards")
            time.sleep(5)
            take_screenshot(driver, f"02_daily_page_{player_id}.png")
            
            daily_buttons = driver.find_elements(By.XPATH, "//button[contains(text(),'Claim')]")
            log(f"  → Found {len(daily_buttons)} claim buttons on daily page")
            
            if daily_buttons:
                success = True
                for i, btn in enumerate(daily_buttons):
                    try:
                        btn.click()
                        log(f"    ✓ Claimed daily reward {i+1}")
                        time.sleep(1.5)
                        daily_claimed += 1
                    except Exception as e:
                        log(f"    ✗ Failed: {str(e)}")
        
        # Step 6: Check store
        if success:
            log(f"  → Navigating to Store...")
            driver.get("https://hub.vertigogames.co/store")
            time.sleep(5)
            take_screenshot(driver, f"03_store_page_{player_id}.png")
            
            store_buttons = driver.find_elements(By.XPATH, "//button[contains(text(),'Claim')]")
            log(f"  → Found {len(store_buttons)} claim buttons on store page")
            
            for i, btn in enumerate(store_buttons[:3]):
                try:
                    btn.click()
                    log(f"    ✓ Claimed store reward {i+1}")
                    time.sleep(1.5)
                    store_claimed += 1
                except Exception as e:
                    log(f"    ✗ Failed: {str(e)}")
        
    except Exception as e:
        error_msg = str(e)
        log(f"  ✗ CRITICAL ERROR: {error_msg}")
        take_screenshot(driver, f"error_{player_id}.png")
    finally:
        driver.quit()
    
    elapsed_time = round(time.time() - start_time, 1)
    log(f"  → Completed: Success={success}, Daily={daily_claimed}, Store={store_claimed}, Time={elapsed_time}s")
    
    return success, daily_claimed, store_claimed, elapsed_time, error_msg

# =====================================================
# Email Sending
# =====================================================
def send_email(summary):
    if not SMTP_USERNAME or not SMTP_PASSWORD:
        log("[WARN] Email not configured; printing summary only")
        print("\n" + summary)
        return
    
    msg = MIMEText(summary, "plain", "utf-8")
    msg["Subject"] = "Hub Merged Rewards Summary"
    msg["From"] = SMTP_FROM
    msg["To"] = SMTP_TO
    
    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM, [SMTP_TO], msg.as_string())
        log("[INFO] Email sent successfully")
    except Exception as e:
        log(f"[ERROR] Email failed: {str(e)}")

# =====================================================
# Main
# =====================================================
def main():
    log("=" * 60)
    log("STARTING HUB MERGED REWARDS BOT")
    log("=" * 60)
    
    start_time = time.time()
    
    # Load player IDs
    csv_path = os.path.join(os.getcwd(), "players.csv")
    player_ids = []
    
    with open(csv_path, newline="") as f:
        reader = csv.reader(f)
        player_ids = [row[0].strip() for row in reader if row and row[0].strip()]
    
    if not player_ids:
        log("[ERROR] No player IDs found in players.csv")
        return
    
    log(f"Loaded {len(player_ids)} player IDs from CSV")
    
    results = []
    total_daily = total_store = total_success = 0
    
    # Process each player
    for idx, pid in enumerate(player_ids, 1):
        log(f"\n{'='*60}")
        log(f"Processing [{idx}/{len(player_ids)}]")
        
        success, daily, store, duration, error = claim_rewards_for_id(pid)
        results.append((pid, success, daily, store, duration, error))
        
        if success:
            total_success += 1
        total_daily += daily
        total_store += store
        
        time.sleep(3)  # Rate limiting
    
    # Generate summary
    total_time = round(time.time() - start_time, 1)
    avg_time = round(total_time / len(player_ids), 1)
    
    summary_lines = [
        "=" * 60,
        "HUB MERGED REWARDS SUMMARY",
        "=" * 60,
        f"Total Players: {len(player_ids)}",
        f"Successful Sessions: {total_success}",
        f"Daily Rewards Claimed: {total_daily}",
        f"Store Rewards Claimed: {total_store}",
        f"Total Rewards Claimed: {total_daily + total_store}",
        f"Total Time Taken: {total_time}s ({round(total_time/60,1)} min)",
        f"Avg Time per ID: {avg_time}s",
        "-" * 60,
        ""
    ]
    
    # Per-ID details
    for pid, success, daily, store, duration, error in results:
        status = "✓ SUCCESS" if success else "✗ FAILED"
        summary_lines.append(f"ID: {pid} | {status} | Daily: {daily} | Store: {store} | Time: {duration}s")
        if error:
            summary_lines.append(f"  Error: {error}")
    
    summary_lines.extend(["=" * 60, ""])
    summary = "\n".join(summary_lines)
    
    log("=" * 60)
    log("FINAL SUMMARY")
    log("=" * 60)
    print(summary)
    
    # Send email
    send_email(summary)
    
    # Save summary to file as artifact
    with open("summary.txt", "w") as f:
        f.write(summary)
    
    log("Bot execution completed")

if __name__ == "__main__":
    main()
