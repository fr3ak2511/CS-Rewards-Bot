#!/usr/bin/env python3
# ABOVE LINE IS CRITICAL for GitHub Actions
import sys
import os
import csv
import time
import threading
import smtplib
import traceback
from datetime import datetime
from email.mime.text import MIMEText
from concurrent.futures import ThreadPoolExecutor, as_completed

# EMERGENCY: Print to both stdout and stderr so GitHub Actions always sees it
def emergency_log(message):
    timestamp = datetime.utcnow().strftime('%H:%M:%S')
    msg = f"[{timestamp}] {message}"
    print(msg, flush=True)
    print(msg, file=sys.stderr, flush=True)

# Force flush all output
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

emergency_log("=" * 70)
emergency_log("SCRIPT STARTING - EMERGENCY DEBUGGING ENABLED")
emergency_log("=" * 70)

try:
    # All imports wrapped in try/except to catch import errors
    emergency_log("Importing modules...")
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
    emergency_log("✓ All imports successful")
except Exception as e:
    emergency_log(f"✗ IMPORT ERROR: {str(e)}")
    emergency_log(traceback.format_exc())
    sys.exit(1)

# =====================================================
# GitHub Secrets Configuration
# =====================================================
try:
    emergency_log("Loading environment variables...")
    SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USERNAME = os.getenv("SMTP_USERNAME")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
    SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USERNAME)
    SMTP_TO = os.getenv("SMTP_TO", SMTP_FROM)
    
    # Validate critical vars
    if not SMTP_USERNAME:
        emergency_log("✗ ERROR: SMTP_USERNAME not set!")
        sys.exit(1)
    if not SMTP_PASSWORD:
        emergency_log("✗ ERROR: SMTP_PASSWORD not set!")
        sys.exit(1)
        
    emergency_log("✓ Environment variables loaded")
except Exception as e:
    emergency_log(f"✗ ENVIRONMENT ERROR: {str(e)}")
    sys.exit(1)

# =====================================================
# Screenshot Setup
# =====================================================
try:
    emergency_log("Setting up screenshot directory...")
    SCREENSHOT_DIR = os.path.join(os.getcwd(), "screenshots")
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    emergency_log(f"✓ Screenshot directory: {SCREENSHOT_DIR}")
except Exception as e:
    emergency_log(f"✗ SCREENSHOT DIR ERROR: {str(e)}")
    sys.exit(1)

# Thread-safe printing
print_lock = threading.Lock()

def log(message):
    with print_lock:
        emergency_log(message)

def take_screenshot(driver, filename):
    """Save screenshot for debugging"""
    try:
        path = os.path.join(SCREENSHOT_DIR, filename)
        driver.save_screenshot(path)
        log(f"📸 Screenshot saved: {filename}")
    except Exception as e:
        log(f"⚠️ Could not save screenshot: {str(e)}")

# =====================================================
# Selenium Driver Setup
# =====================================================
def create_driver():
    try:
        log("Creating Chrome driver...")
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
        log("✓ Chrome driver created")
        return driver
    except Exception as e:
        log(f"✗ DRIVER CREATION ERROR: {str(e)}")
        log(traceback.format_exc())
        return None

# =====================================================
# Cookie & Popup Handling
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
# Login Functions
# =====================================================
def wait_for_login_complete(driver, max_wait=10):
    try:
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
    except Exception as e:
        log(f"✗ Error in wait_for_login_complete: {str(e)}")
        return False

def login_with_player(driver, player_id, thread_id):
    try:
        log(f"[Thread-{thread_id}] 🔄 Logging in with ID: {player_id}")
        
        driver.get("https://hub.vertigogames.co/daily-rewards")
        time.sleep(3)
        accept_cookies(driver, WebDriverWait(driver, 10))
        
        # Take initial screenshot
        take_screenshot(driver, f"{player_id}_00_initial_page.png")
        
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
                        log(f"[Thread-{thread_id}] ✓ Login button clicked")
                        time.sleep(3)
                        break
                if login_clicked:
                    break
            except Exception as e:
                log(f"[Thread-{thread_id}] Login selector failed: {str(e)}")
                continue
        
        if not login_clicked:
            log(f"[Thread-{thread_id}] ✗ No login button found")
            take_screenshot(driver, f"{player_id}_01_no_login_button.png")
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
                log(f"[Thread-{thread_id}] ✓ Player ID entered")
                time.sleep(1)
                break
            except Exception as e:
                log(f"[Thread-{thread_id}] Input selector failed: {str(e)}")
                continue
        
        if not input_found:
            log(f"[Thread-{thread_id}] ✗ Input field not found")
            take_screenshot(driver, f"{player_id}_02_no_input_field.png")
            return False
        
        # Submit
        try:
            input_box.send_keys(Keys.ENTER)
            log(f"[Thread-{thread_id}] ✓ Enter key pressed")
            time.sleep(2)
        except Exception as e:
            log(f"[Thread-{thread_id}] Enter key failed: {str(e)}")
        
        # Wait for login
        if wait_for_login_complete(driver):
            log(f"[Thread-{thread_id}] ✓ Login successful")
            take_screenshot(driver, f"{player_id}_03_login_success.png")
            return True
        else:
            log(f"[Thread-{thread_id}] ✗ Login timeout")
            take_screenshot(driver, f"{player_id}_04_login_timeout.png")
            return False
    except Exception as e:
        log(f"[Thread-{thread_id}] ✗ Login CRITICAL ERROR: {str(e)}")
        log(traceback.format_exc())
        take_screenshot(driver, f"{player_id}_05_login_error.png")
        return False

# =====================================================
# Reward Claiming
# =====================================================
def claim_daily_rewards(driver, thread_id, player_id):
    try:
        log(f"[Thread-{thread_id}] 🔄 Claiming Daily Rewards...")
        
        driver.get("https://hub.vertigogames.co/daily-rewards")
        time.sleep(3)
        close_popups_safe(driver)
        
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
                    log(f"[Thread-{thread_id}] ✓ Claimed daily reward {claimed}/{len(claim_buttons)}")
                    time.sleep(1.5)
                    close_popups_safe(driver)
            except Exception as e:
                log(f"[Thread-{thread_id}] ✗ Failed daily claim {i+1}: {str(e)}")
                continue
        
        return claimed
    except Exception as e:
        log(f"[Thread-{thread_id}] ✗ Daily rewards CRITICAL ERROR: {str(e)}")
        take_screenshot(driver, f"{player_id}_06_daily_error.png")
        return 0

def claim_store_rewards(driver, thread_id, player_id):
    try:
        log(f"[Thread-{thread_id}] 🔄 Claiming Store Rewards...")
        
        driver.get("https://hub.vertigogames.co/store")
        time.sleep(3)
        close_popups_safe(driver)
        
        claim_buttons = driver.find_elements(By.XPATH, "//button[contains(text(),'Claim')]")
        log(f"[Thread-{thread_id}] Found {len(claim_buttons)} store claim buttons")
        
        claimed = 0
        for i, btn in enumerate(claim_buttons[:3]):
            try:
                if btn.is_displayed() and btn.is_enabled():
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                    time.sleep(0.5)
                    btn.click()
                    claimed += 1
                    log(f"[Thread-{thread_id}] ✓ Claimed store reward {claimed}/3")
                    time.sleep(1.5)
                    close_popups_safe(driver)
            except Exception as e:
                log(f"[Thread-{thread_id}] ✗ Failed store claim {i+1}: {str(e)}")
                continue
        
        return claimed
    except Exception as e:
        log(f"[Thread-{thread_id}] ✗ Store rewards CRITICAL ERROR: {str(e)}")
        take_screenshot(driver, f"{player_id}_07_store_error.png")
        return 0

# =====================================================
# Process Single Player
# =====================================================
def process_player(player_id, thread_id):
    driver = None
    try:
        log(f"\n{'='*60}")
        log(f"[Thread-{thread_id}] STARTING PLAYER: {player_id}")
        log(f"{'='*60}")
        
        driver = create_driver()
        if not driver:
            log(f"[Thread-{thread_id}] ✗ Failed to create driver")
            return {
                "player_id": player_id,
                "login_success": False,
                "daily_claimed": 0,
                "store_claimed": 0,
                "status": "driver_failed"
            }
        
        # Login
        login_success = login_with_player(driver, player_id, thread_id)
        if not login_success:
            log(f"[Thread-{thread_id}] ✗ Login failed, skipping claims")
            return {
                "player_id": player_id,
                "login_success": False,
                "daily_claimed": 0,
                "store_claimed": 0,
                "status": "login_failed"
            }
        
        # Claim rewards
        daily_claimed = claim_daily_rewards(driver, thread_id, player_id)
        store_claimed = claim_store_rewards(driver, thread_id, player_id)
        
        total_claimed = daily_claimed + store_claimed
        status = "success" if total_claimed > 0 else "no_claims"
        
        log(f"[Thread-{thread_id}] ✓ COMPLETED: Daily={daily_claimed}, Store={store_claimed}, Total={total_claimed}")
        
        return {
            "player_id": player_id,
            "login_success": True,
            "daily_claimed": daily_claimed,
            "store_claimed": store_claimed,
            "status": status
        }
        
    except Exception as e:
        log(f"[Thread-{thread_id}] ✗ UNEXPECTED CRITICAL ERROR: {str(e)}")
        log(traceback.format_exc())
        if driver:
            take_screenshot(driver, f"{player_id}_99_critical_error.png")
        return {
            "player_id": player_id,
            "login_success": False,
            "daily_claimed": 0,
            "store_claimed": 0,
            "status": "critical_error",
            "error": str(e)
        }
    finally:
        if driver:
            try:
                driver.quit()
                log(f"[Thread-{thread_id}] ✓ Driver closed")
            except Exception as e:
                log(f"[Thread-{thread_id}] ⚠️ Error closing driver: {str(e)}")

# =====================================================
# Main Execution
# =====================================================
def main():
    try:
        log("=" * 70)
        log("MAIN FUNCTION STARTED")
        log("=" * 70)
        
        start_time = time.time()
        
        # Load players
        csv_path = os.path.join(os.getcwd(), "players.csv")
        log(f"Looking for CSV at: {csv_path}")
        
        if not os.path.exists(csv_path):
            log(f"✗ ✗ ✗ CSV FILE NOT FOUND ✗ ✗ ✗")
            log(f"Current directory contents: {os.listdir(os.getcwd())}")
            sys.exit(1)
        
        player_ids = []
        with open(csv_path, newline="") as f:
            reader = csv.reader(f)
            for i, row in enumerate(reader):
                if row and row[0].strip():
                    player_ids.append(row[0].strip())
                    log(f"Found player {i+1}: {row[0].strip()[:8]}...")
        
        if not player_ids:
            log("✗ ✗ ✗ NO PLAYER IDS FOUND IN CSV ✗ ✗ ✗")
            log(f"CSV contents: {list(csv.reader(open(csv_path)))}")
            sys.exit(1)
        
        log(f"✓ Loaded {len(player_ids)} player IDs")
        
        # Process in batches of 2
        BATCH_SIZE = 2
        batches = [player_ids[i:i+BATCH_SIZE] for i in range(0, len(player_ids), BATCH_SIZE)]
        
        log(f"✓ Created {len(batches)} batches of size {BATCH_SIZE}")
        
        all_results = []
        
        # Initial run
        for batch_num, batch in enumerate(batches, 1):
            log(f"\n{'='*70}")
            log(f"BATCH {batch_num}/{len(batches)}: {', '.join(batch)}")
            log(f"{'='*70}")
            
            with ThreadPoolExecutor(max_workers=BATCH_SIZE) as executor:
                futures = []
                for idx, pid in enumerate(batch):
                    future = executor.submit(process_player, pid, f"{batch_num}-{idx+1}")
                    futures.append((future, pid))
                
                for future, pid in futures:
                    try:
                        result = future.result(timeout=120)  # 2 minute timeout
                        all_results.append(result)
                        log(f"[Thread] ✓ Result: {result}")
                    except Exception as e:
                        log(f"[Thread] ✗ Player {pid} timed out: {str(e)}")
                        all_results.append({
                            "player_id": pid,
                            "login_success": False,
                            "daily_claimed": 0,
                            "store_claimed": 0,
                            "status": "timeout"
                        })
            
            # Rate limit between batches
            time.sleep(3)
        
        # Generate summary
        total_time = time.time() - start_time
        successful_logins = sum(1 for r in all_results if r["login_success"])
        total_daily = sum(r["daily_claimed"] for r in all_results)
        total_store = sum(r["store_claimed"] for r in all_results)
        
        summary = f"""
{'='*70}
HUB MERGED REWARDS - FINAL SUMMARY
{'='*70}
Total Players: {len(player_ids)}
Successful Logins: {successful_logins}
Daily Rewards Claimed: {total_daily}
Store Rewards Claimed: {total_store}
Total Rewards Claimed: {total_daily + total_store}
Total Time: {total_time:.1f}s
{'='*70}

Per-ID Results:
"""
        
        for r in all_results:
            status_symbol = "✓" if r["status"] == "success" else "✗"
            summary += f"{status_symbol} {r['player_id']}: {r['status']} | Daily: {r['daily_claimed']} | Store: {r['store_claimed']}\n"
        
        summary += "=" * 70
        
        log("SUMMARY GENERATED:")
        log(summary)
        
        # Ensure summary is printed to stdout for email capture
        print(summary, flush=True)
        
        return summary
        
    except Exception as e:
        log("=" * 70)
        log("✗ ✗ ✗ UNRECOVERABLE ERROR IN MAIN ✗ ✗ ✗")
        log(f"Error: {str(e)}")
        log(traceback.format_exc())
        log("=" * 70)
        sys.exit(1)

if __name__ == "__main__":
    try:
        result = main()
        if result:
            print(result, flush=True)
    except Exception as e:
        emergency_log(f"✗ SCRIPT CRASHED: {str(e)}")
        emergency_log(traceback.format_exc())
        sys.exit(1)
