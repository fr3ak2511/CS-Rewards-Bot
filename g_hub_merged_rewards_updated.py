import csv
import time
import threading
import os
import smtplib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from email.mime.text import MIMEText
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager
import re

# =====================================================
# GitHub Secrets Configuration
# =====================================================
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USERNAME)
SMTP_TO = os.getenv("SMTP_TO", SMTP_FROM)

# Screenshot directory for debugging
SCREENSHOT_DIR = os.path.join(os.getcwd(), "screenshots")
os.makedirs(SCREENSHOT_DIR, exist_ok=True)

# Thread-safe printing
print_lock = threading.Lock()

def log(message):
    with print_lock:
        print(f"[{datetime.utcnow().strftime('%H:%M:%S')}] {message}")

def take_screenshot(driver, filename):
    """Save screenshot for debugging in GitHub Actions"""
    try:
        path = os.path.join(SCREENSHOT_DIR, filename)
        driver.save_screenshot(path)
        log(f"📸 Screenshot saved: {filename}")
    except Exception as e:
        log(f"⚠️ Could not save screenshot: {str(e)}")

# =====================================================
# Selenium Driver Setup (for GitHub Actions)
# =====================================================
def create_driver():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--lang=en-US")
    
    # Disable images for faster loading
    prefs = {
        "profile.default_content_setting_values": {
            "images": 2,
            "notifications": 2,
            "popups": 2,
        }
    }
    options.add_experimental_option("prefs", prefs)
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(20)
    driver.set_script_timeout(20)
    return driver

# =====================================================
# Cookie & Popup Handling (Your Working Logic)
# =====================================================
def accept_cookies(driver, wait):
    try:
        btn = WebDriverWait(driver, 3).until(
            EC.element_to_be_clickable((
                By.XPATH,
                "//button[normalize-space()='Accept All' or contains(text(), 'Accept') or "
                "contains(text(), 'Allow') or contains(text(), 'Consent')]",
            ))
        )
        btn.click()
        time.sleep(0.5)
        log("✓ Cookies accepted")
    except TimeoutException:
        log("→ No cookies popup found")

def close_popups_safe(driver):
    """Enhanced popup closing for Daily Rewards promotional popups"""
    try:
        popup_selectors = [
            "//div[contains(@class, 'modal') and not(contains(@style, 'display: none'))]",
            "//div[contains(@class, 'popup') and not(contains(@style, 'display: none'))]",
            "//div[@data-testid='item-popup-content']",
            "//div[contains(@class, 'dialog') and(contains(@style, 'display: none'))]",
        ]

        popup_found = False
        for selector in popup_selectors:
            try:
                popup_elements = driver.find_elements(By.XPATH, selector)
                visible_popups = [elem for elem in popup_elements if elem.is_displayed()]
                if visible_popups:
                    popup_found = True
                    log("Promotional popup detected, attempting to close...")
                    break
            except:
                continue

        if popup_found:
            # Try clicking close button
            close_selectors = [
                "//button[contains(@class, 'close')]",
                "//button[contains(@aria-label, 'Close')]",
                "//*[contains(@class, 'close') and (self::button or self::span or self::div[@role='button'])]",
                "//button[text()='×' or text()='X' or text()='✕']",
                "//*[@data-testid='close-button']",
            ]

            for selector in close_selectors:
                try:
                    close_btn = driver.find_element(By.XPATH, selector)
                    if close_btn.is_displayed():
                        driver.execute_script("arguments[0].click();", close_btn)
                        log("Popup closed via close button")
                        time.sleep(0.5)
                        return True
                except:
                    continue

            # Fallback: click safe areas
            log("Trying safe area clicks...")
            window_size = driver.get_window_size()
            safe_areas = [
                (30, 30),
                (window_size["width"] - 50, 30),
                (30, window_size["height"] - 50),
                (window_size["width"] - 50, window_size["height"] - 50),
            ]

            for x, y in safe_areas:
                try:
                    actions = ActionChains(driver)
                    actions.move_by_offset(x - window_size["width"] // 2, y - window_size["height"] // 2).click().perform()
                    actions.move_by_offset(-(x - window_size["width"] // 2), -(y - window_size["height"] // 2)).perform()
                    time.sleep(0.5)
                    return True
                except:
                    continue

            # Final attempt: ESC key
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            time.sleep(0.3)
            log("ESC key pressed")
            return True

    except Exception as e:
        log(f"Popup close error: {e}")
    return False

# =====================================================
# Login Functions (Your Working Logic)
# =====================================================
def wait_for_login(driver, max_wait=10):
    start_time = time.time()
    while time.time() - start_time < max_wait:
        try:
            current_url = driver.current_url
            if any(x in current_url.lower() for x in ["user", "dashboard", "daily-rewards"]):
                return True
            user_elements = driver.find_elements(
                By.XPATH,
                "//button[contains(text(),'Logout') or contains(text(),'Profile') or contains(@class,'user')]",
            )
            if user_elements:
                return True
            time.sleep(0.2)
        except:
            time.sleep(0.2)
    return False

def login_with_player(driver, player_id, thread_id):
    log(f"[Thread-{thread_id}] Logging in with ID: {player_id}")
    
    driver.get("https://hub.vertigogames.co/daily-rewards")
    time.sleep(2)
    accept_cookies(driver, WebDriverWait(driver, 10))
    
    # Click login button
    login_selectors = [
        "//button[contains(text(),'Login') or contains(text(),'Log in')]",
        "//a[contains(text(),'Login') or contains(text(),'Log in')]",
        "//button[contains(@class, 'btn-primary')]",
    ]
    
    login_clicked = False
    for selector in login_selectors:
        try:
            elements = driver.find_elements(By.XPATH, selector)
            for element in elements:
                if element.is_displayed():
                    element.click()
                    login_clicked = True
                    log(f"[Thread-{thread_id}] Login button clicked")
                    time.sleep(3)
                    break
            if login_clicked:
                break
        except:
            continue
    
    if not login_clicked:
        log(f"[Thread-{thread_id}] No login button found")
        return False
    
    # Enter player ID
    input_selectors = [
        "//input[contains(@placeholder, 'Player ID')]",
        "//input[@type='text']",
        "//input[contains(@class, 'form-control')]",
    ]
    
    input_found = False
    for selector in input_selectors:
        try:
            input_box = WebDriverWait(driver, 5).until(
                EC.visibility_of_element_located((By.XPATH, selector))
            )
            input_box.clear()
            input_box.send_keys(player_id)
            input_found = True
            log(f"[Thread-{thread_id}] Player ID entered")
            time.sleep(1)
            break
        except:
            continue
    
    if not input_found:
        log(f"[Thread-{thread_id}] Input field not found")
        return False
    
    # Click submit/enter
    try:
        input_box.send_keys(Keys.ENTER)
        log(f"[Thread-{thread_id}] Enter key pressed")
        time.sleep(2)
    except:
        pass
    
    # Wait for login to complete
    if wait_for_login(driver):
        log(f"[Thread-{thread_id}] Login successful")
        return True
    else:
        log(f"[Thread-{thread_id}] Login timeout or failed")
        return False

# =====================================================
# Reward Claiming (Your Working Logic)
# =====================================================
def claim_daily_rewards(driver, thread_id):
    log(f"[Thread-{thread_id}] Claiming Daily Rewards...")
    
    try:
        driver.get("https://hub.vertigogames.co/daily-rewards")
        time.sleep(3)
        close_popups_safe(driver)
        
        # Get claim buttons
        claim_buttons = driver.find_elements(By.XPATH, "//button[contains(text(),'Claim')]")
        log(f"[Thread-{thread_id}] Found {len(claim_buttons)} claim buttons")
        
        claimed = 0
        for i, btn in enumerate(claim_buttons):
            try:
                if btn.is_displayed() and btn.is_enabled():
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                    time.sleep(0.5)
                    btn.click()
                    claimed += 1
                    log(f"[Thread-{thread_id}] ✓ Claimed daily reward {claimed}")
                    time.sleep(1.5)
                    close_popups_safe(driver)
            except Exception as e:
                log(f"[Thread-{thread_id}] ✗ Failed to claim: {str(e)}")
                continue
        
        return claimed
    except Exception as e:
        log(f"[Thread-{thread_id}] ✗ Error in daily rewards: {str(e)}")
        return 0

def claim_store_rewards(driver, thread_id):
    log(f"[Thread-{thread_id}] Claiming Store Rewards...")
    
    try:
        driver.get("https://hub.vertigogames.co/store")
        time.sleep(3)
        close_popups_safe(driver)
        
        claim_buttons = driver.find_elements(By.XPATH, "//button[contains(text(),'Claim')]")
        log(f"[Thread-{thread_id}] Found {len(claim_buttons)} store claim buttons")
        
        claimed = 0
        for i, btn in enumerate(claim_buttons[:3]):  # First 3 only
            try:
                if btn.is_displayed() and btn.is_enabled():
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                    time.sleep(0.5)
                    btn.click()
                    claimed += 1
                    log(f"[Thread-{thread_id}] ✓ Claimed store reward {claimed}")
                    time.sleep(1.5)
                    close_popups_safe(driver)
            except Exception as e:
                log(f"[Thread-{thread_id}] ✗ Failed to claim: {str(e)}")
                continue
        
        return claimed
    except Exception as e:
        log(f"[Thread-{thread_id}] ✗ Error in store rewards: {str(e)}")
        return 0

# =====================================================
# Process Single Player (Your Logic)
# =====================================================
def process_player(player_id, thread_id):
    driver = None
    try:
        driver = create_driver()
        
        # Login
        login_success = login_with_player(driver, player_id, thread_id)
        if not login_success:
            return {
                "player_id": player_id,
                "login_success": False,
                "daily_claimed": 0,
                "store_claimed": 0,
                "status": "login_failed"
            }
        
        # Claim rewards
        daily_claimed = claim_daily_rewards(driver, thread_id)
        store_claimed = claim_store_rewards(driver, thread_id)
        
        total_claimed = daily_claimed + store_claimed
        status = "success" if total_claimed > 0 else "no_claims"
        
        log(f"[Thread-{thread_id}] ✓ Completed: Daily={daily_claimed}, Store={store_claimed}")
        
        return {
            "player_id": player_id,
            "login_success": True,
            "daily_claimed": daily_claimed,
            "store_claimed": store_claimed,
            "status": status
        }
        
    except Exception as e:
        log(f"[Thread-{thread_id}] ✗ Critical error: {str(e)}")
        take_screenshot(driver, f"error_{player_id}.png")
        return {
            "player_id": player_id,
            "login_success": False,
            "daily_claimed": 0,
            "store_claimed": 0,
            "status": "error",
            "error": str(e)
        }
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass

# =====================================================
# Main Execution with Multi-threading
# =====================================================
def main():
    log("=" * 60)
    log("STARTING HUB MERGED REWARDS BOT")
    log("=" * 60)
    
    start_time = time.time()
    
    # Load players
    csv_path = os.path.join(os.getcwd(), "players.csv")
    if not os.path.exists(csv_path):
        log(f"✗ ERROR: players.csv not found at {csv_path}")
        return
    
    player_ids = []
    with open(csv_path, newline="") as f:
        reader = csv.reader(f)
        player_ids = [row[0].strip() for row in reader if row and row[0].strip()]
    
    if not player_ids:
        log("✗ ERROR: No player IDs in CSV")
        return
    
    log(f"Loaded {len(player_ids)} player IDs")
    
    # Process in batches of 2 (your working configuration)
    BATCH_SIZE = 2
    batches = [player_ids[i:i+BATCH_SIZE] for i in range(0, len(player_ids), BATCH_SIZE)]
    
    all_results = []
    
    # Initial run
    for batch_num, batch in enumerate(batches, 1):
        log(f"\n{'='*40}")
        log(f"PROCESSING BATCH {batch_num}/{len(batches)}")
        log(f"Players: {', '.join(batch)}")
        log(f"{'='*40}")
        
        with ThreadPoolExecutor(max_workers=BATCH_SIZE) as executor:
            futures = []
            for idx, pid in enumerate(batch):
                future = executor.submit(process_player, pid, f"{batch_num}-{idx+1}")
                futures.append((future, pid))
            
            for future, pid in futures:
                try:
                    result = future.result(timeout=60)
                    all_results.append(result)
                    time.sleep(2)  # Rate limit between players
                except Exception as e:
                    log(f"✗ Player {pid} timed out: {str(e)}")
                    all_results.append({
                        "player_id": pid,
                        "login_success": False,
                        "daily_claimed": 0,
                        "store_claimed": 0,
                        "status": "timeout"
                    })
        
        # Small delay between batches
        time.sleep(3)
    
    # Retry failed players
    failed_players = [r for r in all_results if r["status"] in ["login_failed", "error", "timeout"]]
    if failed_players:
        log(f"\n{'='*40}")
        log(f"RETRYING {len(failed_players)} FAILED PLAYERS")
        log(f"{'='*40}")
        
        for player in failed_players:
            log(f"Retrying:
