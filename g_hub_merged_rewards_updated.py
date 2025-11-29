import time
import csv
import os
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
from datetime import datetime

# =====================================================
# Configuration from GitHub Secrets
# =====================================================
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USERNAME)
SMTP_TO = os.getenv("SMTP_TO", SMTP_FROM)

# =====================================================
# Utility: Make Chrome Driver
# =====================================================
def make_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_argument("--lang=en-US")
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    driver.set_page_load_timeout(30)
    return driver

# =====================================================
# Utility: Accept Cookies
# =====================================================
def accept_cookies(driver):
    try:
        cookie_btn = driver.find_element(By.XPATH, "//button[contains(text(),'Accept') or contains(text(),'I Agree')]")
        cookie_btn.click()
        time.sleep(1)
    except:
        pass

# =====================================================
# Utility: Claim Rewards for Invite Code
# =====================================================
def claim_rewards_for_invite(player_id):
    driver = make_driver()
    start_time = time.time()
    
    daily_claimed = 0
    store_claimed = 0
    success = False
    
    try:
        # Use invite code to access rewards directly
        driver.get(f"https://hub.vertigogames.co/?inviteCode={player_id}")
        time.sleep(5)
        accept_cookies(driver)
        
        # Check if we're on a valid page by looking for claim buttons
        try:
            # Try to find any claim button on the page
            claim_buttons = driver.find_elements(By.XPATH, "//button[contains(text(),'Claim')]")
            if claim_buttons:
                success = True
                # Claim any visible rewards
                for btn in claim_buttons[:5]:  # Limit to prevent over-clicking
                    try:
                        btn.click()
                        time.sleep(1)
                        daily_claimed += 1
                    except:
                        pass
        except:
            pass
        
        # Navigate to daily rewards page
        if success:
            driver.get("https://hub.vertigogames.co/daily-rewards")
            time.sleep(4)
            try:
                claim_buttons = driver.find_elements(By.XPATH, "//button[contains(text(),'Claim')]")
                for btn in claim_buttons:
                    btn.click()
                    time.sleep(1.5)
                    daily_claimed += 1
            except:
                pass
            
            # Navigate to store rewards
            driver.get("https://hub.vertigogames.co/store")
            time.sleep(5)
            try:
                claim_buttons = driver.find_elements(By.XPATH, "//button[contains(text(),'Claim')]")
                for btn in claim_buttons[:3]:
                    btn.click()
                    time.sleep(1.5)
                    store_claimed += 1
            except:
                pass
            
    except Exception as e:
        print(f"[ERROR] {player_id}: {str(e)}")
    finally:
        driver.quit()
    
    elapsed_time = round(time.time() - start_time, 1)
    return success, daily_claimed, store_claimed, elapsed_time

# =====================================================
# Utility: Send Email Summary
# =====================================================
def send_email(summary):
    if not SMTP_USERNAME or not SMTP_PASSWORD:
        print("[WARN] Email not configured; printing summary only")
        print(summary)
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
        print("[INFO] Email sent successfully")
    except Exception as e:
        print(f"[ERROR] Email failed: {str(e)}")

# =====================================================
# MAIN SCRIPT EXECUTION
# =====================================================
def main():
    start_time = time.time()
    print(f"[INFO] Run started at {datetime.utcnow()} UTC")
    
    # Load player IDs (repo-relative)
    csv_path = os.path.join(os.getcwd(), "players.csv")
    player_ids = []
    
    with open(csv_path, newline="") as f:
        reader = csv.reader(f)
        player_ids = [row[0].strip() for row in reader if row and row[0].strip()]
    
    if not player_ids:
        print("[ERROR] No player IDs found in players.csv")
        return
    
    results = []
    total_daily = total_store = total_success = 0
    
    for pid in player_ids:
        success, daily, store, duration = claim_rewards_for_invite(pid)
        results.append((pid, success, daily, store, duration))
        
        if success:
            total_success += 1
        total_daily += daily
        total_store += store
    
    total_rewards = total_daily + total_store
    total_time = round(time.time() - start_time, 1)
    avg_time = round(total_time / len(player_ids), 1)
    
    summary_lines = [
        "===================================================",
        "HUB MERGED REWORDS SUMMARY",
        "===================================================",
        f"Total Players: {len(player_ids)}",
        f"Successful Sessions: {total_success}",
        f"Daily Rewards Claimed: {total_daily}",
        f"Store Rewards Claimed: {total_store}",
        f"Total Rewards Claimed: {total_rewards}",
        f"Total Time Taken: {total_time}s ({round(total_time/60,1)} min)",
        f"Avg Time per ID: {avg_time}s",
        "---------------------------------------------------",
        "",
        "Per-ID details:"
    ]
    
    for pid, success, daily, store, duration in results:
        summary_lines.append(
            f"ID: {pid} | Success: {'Yes' if success else 'No'} | Daily: {daily} | Store: {store} | Time: {duration}s"
        )
    
    summary_lines.append("===================================================")
    summary = "\n".join(summary_lines)
    
    print(summary)
    send_email(summary)

if __name__ == "__main__":
    main()
