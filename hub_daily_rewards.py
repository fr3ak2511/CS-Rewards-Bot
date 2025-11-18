import csv
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

# Thread-safe printing
print_lock = threading.Lock()

def thread_safe_print(message):
    with print_lock:
        print(message)

def create_driver():
    options = Options()
    options.add_argument("--incognito")
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-logging")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-web-security")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-default-apps")
    options.add_argument("--disable-features=VizDisplayCompositor")

    prefs = {
        "profile.default_content_setting_values": {
            "notifications": 2,
            "popups": 2,
        },
        "profile.default_content_settings.popups": 0,
        "profile.managed_default_content_settings.popups": 0,
    }

    options.add_experimental_option("prefs", prefs)
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    # IMPORTANT: headless mode for cloud/server (no visible browser window)
    options.add_argument("--headless=new")

    # Selenium Manager will download/manage the correct ChromeDriver
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(60)
    driver.set_script_timeout(60)
    return driver


def accept_cookies(driver, wait):
    try:
        btn = WebDriverWait(driver, 2).until(EC.element_to_be_clickable((By.XPATH, "//button[normalize-space()='Accept All' or contains(text(), 'Accept') or contains(text(), 'Allow') or contains(text(), 'Consent')]")))
        btn.click()
        time.sleep(0.5)
        thread_safe_print("Cookies accepted")
    except TimeoutException:
        thread_safe_print("No cookies popup found - continuing")
        pass

def click_element_or_coords(driver, wait, locator, fallback_coords=None, description='', timeout=8):
    try:
        elm = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable(locator))
        elm.click()
        time.sleep(0.3)
        return True
    except Exception:
        if fallback_coords:
            try:
                actions = ActionChains(driver)
                actions.move_by_offset(fallback_coords[0], fallback_coords[1]).click().perform()
                actions.move_by_offset(-fallback_coords[0], -fallback_coords[1]).perform()
                time.sleep(0.3)
                return True
            except Exception:
                pass
        return False

def wait_for_login_complete(driver, wait, max_wait=8):
    """Wait for login to complete by checking for page changes or specific elements"""
    start_time = time.time()
    while time.time() - start_time < max_wait:
        try:
            current_url = driver.current_url
            if "user" in current_url.lower() or "dashboard" in current_url.lower() or "daily-rewards" in current_url.lower():
                return True
            user_elements = driver.find_elements(By.XPATH, "//button[contains(text(),'Logout') or contains(text(),'Profile') or contains(@class,'user')]")
            if user_elements:
                return True
            time.sleep(0.2)
        except:
            time.sleep(0.2)
    return True

def ensure_daily_rewards_page(driver):
    """Ensures we're on daily-rewards page and navigates back if not"""
    current_url = driver.current_url
    if "daily-rewards" not in current_url.lower():
        thread_safe_print(f"WARNING: Not on daily-rewards page ({current_url}), navigating back...")
        driver.get("https://hub.vertigogames.co/daily-rewards")
        time.sleep(2)
        return True
    return False

def close_popups_safe(driver):
    """Safely closes popups by clicking on safe areas OUTSIDE the popup window"""
    try:
        # Quick check for popups
        popup_selectors = [
            "//div[contains(@class, 'modal') and not(contains(@style, 'display: none'))]",
            "//div[contains(@class, 'popup') and not(contains(@style, 'display: none'))]", 
            "//div[@data-testid='item-popup-content']"
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
        
        if popup_found:
            thread_safe_print("Popup detected, closing safely...")
            
            # Get window size for safe clicking
            window_size = driver.get_window_size()
            width = window_size['width']
            height = window_size['height']
            
            # Try safe areas for speed (most effective ones)
            safe_areas = [
                (50, 50),           # Top-left corner
                (width - 100, 50),   # Top-right corner  
                (50, height - 100),  # Bottom-left corner
            ]
            
            for i, (x, y) in enumerate(safe_areas):
                try:
                    actions = ActionChains(driver)
                    actions.move_by_offset(x - width//2, y - height//2).click().perform()
                    actions.move_by_offset(-(x - width//2), -(y - height//2)).perform()
                    time.sleep(1)
                    
                    # Quick check if popup is closed
                    popup_still_visible = False
                    for selector in popup_selectors:
                        try:
                            popup_elements = driver.find_elements(By.XPATH, selector)
                            if any(elem.is_displayed() for elem in popup_elements):
                                popup_still_visible = True
                                break
                        except:
                            continue
                    
                    if not popup_still_visible:
                        thread_safe_print(f"Popup closed by safe area {i+1}")
                        return True
                        
                except Exception:
                    continue
            
            # ESC key backup
            try:
                driver.find_element(By.TAG_NAME, 'body').send_keys(Keys.ESCAPE)
                time.sleep(0.5)
                return True
            except Exception:
                pass
                
    except Exception:
        pass
    
    return False

def get_claim_buttons(driver):
    """Gets all available claim buttons on daily rewards page"""
    thread_safe_print("Searching for claim buttons on daily rewards page...")
    
    claim_buttons = []
    
    # Try getting all buttons first (most effective approach)
    try:
        all_buttons = driver.find_elements(By.TAG_NAME, "button")
        thread_safe_print(f"Found {len(all_buttons)} total buttons on page")
        
        for btn in all_buttons:
            try:
                btn_text = btn.text.strip()
                if btn_text and 'claim' in btn_text.lower():
                    if btn.is_displayed() and btn.is_enabled():
                        # Safety check - avoid payment buttons
                        if any(word in btn_text.lower() for word in ['buy', 'purchase', 'payment', 'pay', '$']):
                            continue
                        claim_buttons.append(btn)
                        thread_safe_print(f"Found claim button: '{btn_text}' - Enabled: {btn.is_enabled()}")
            except Exception:
                continue
                
    except Exception as e:
        thread_safe_print(f"Error getting all buttons: {e}")

    # If no buttons found with manual search, try XPath selectors as backup
    if not claim_buttons:
        thread_safe_print("No claim buttons found manually, trying XPath selectors...")
        xpath_selectors = [
            "//button[normalize-space()='Claim']",
            "//button[contains(text(), 'Claim') and not(contains(text(), 'Buy'))]",
            "//*[contains(text(), 'Claim') and (self::button or self::a)]"
        ]

        for selector in xpath_selectors:
            try:
                found_buttons = driver.find_elements(By.XPATH, selector)
                for btn in found_buttons:
                    if btn.is_displayed() and btn.is_enabled() and btn not in claim_buttons:
                        btn_text = btn.text.strip()
                        # Safety check
                        if any(word in btn_text.lower() for word in ['buy', 'purchase', 'payment', 'pay', '$']):
                            continue
                        claim_buttons.append(btn)
                        thread_safe_print(f"Added claim button via XPath: '{btn_text}'")
            except Exception:
                continue

    return claim_buttons

def claim_daily_rewards_page(driver, wait):
    """Claim rewards from the daily-rewards page with improved navigation control"""
    claimed = 0
    
    try:
        time.sleep(1.5)
        thread_safe_print("Processing Daily Rewards page...")
        
        # Ensure we're on the correct page before starting
        ensure_daily_rewards_page(driver)
        close_popups_safe(driver)
        time.sleep(1)
        
        # Get initial claim buttons
        claim_buttons = get_claim_buttons(driver)
        
        if not claim_buttons:
            thread_safe_print("No claim buttons found - performing double check...")
            time.sleep(1.5)
            
            # Double check by ensuring correct page and re-searching
            ensure_daily_rewards_page(driver)
            close_popups_safe(driver)
            claim_buttons = get_claim_buttons(driver)
            
            if not claim_buttons:
                thread_safe_print("Double check confirmed: No claimable rewards available")
                return 0

        thread_safe_print(f"Found {len(claim_buttons)} claimable rewards")
        
        # Process each claim button with navigation checks
        for idx, btn in enumerate(claim_buttons):
            try:
                thread_safe_print(f"Processing claim button {idx + 1} of {len(claim_buttons)}")
                
                # Ensure we're on correct page before each claim
                if ensure_daily_rewards_page(driver):
                    thread_safe_print("Had to navigate back to daily-rewards page before claim")
                    # Re-find buttons after navigation
                    updated_buttons = get_claim_buttons(driver)
                    if idx < len(updated_buttons):
                        btn = updated_buttons[idx]
                    else:
                        thread_safe_print(f"Button {idx + 1} no longer available after navigation")
                        continue
                
                close_popups_safe(driver)
                
                btn_text = btn.text.strip()
                thread_safe_print(f"Attempting to claim: '{btn_text}' - Enabled: {btn.is_enabled()}")
                
                if btn.is_displayed() and btn.is_enabled():
                    # Scroll button into view
                    driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", btn)
                    time.sleep(0.5)
                    
                    clicked = False
                    
                    # Try multiple click methods
                    try:
                        btn.click()
                        clicked = True
                        thread_safe_print(f"Successfully clicked claim button {idx + 1} (regular click)")
                    except Exception:
                        try:
                            driver.execute_script("arguments[0].click();", btn)
                            clicked = True
                            thread_safe_print(f"Successfully clicked claim button {idx + 1} (JavaScript click)")
                        except Exception:
                            try:
                                actions = ActionChains(driver)
                                actions.move_to_element(btn).click().perform()
                                clicked = True
                                thread_safe_print(f"Successfully clicked claim button {idx + 1} (ActionChains click)")
                            except Exception:
                                pass

                    if clicked:
                        claimed += 1
                        thread_safe_print(f"REWARD {claimed} CLAIMED SUCCESSFULLY!")
                        time.sleep(2)
                        
                        # Verify we're still on daily-rewards page after claim
                        if ensure_daily_rewards_page(driver):
                            thread_safe_print("Had to navigate back to daily-rewards page after claim")
                        
                        # Handle post-claim popups safely
                        close_popups_safe(driver)
                    else:
                        thread_safe_print(f"All click methods failed for reward {idx + 1}")
                else:
                    thread_safe_print(f"Claim button {idx + 1} not clickable - Displayed: {btn.is_displayed()}, Enabled: {btn.is_enabled()}")
                    
            except Exception as e:
                thread_safe_print(f"Error processing claim button {idx + 1}: {e}")
                continue
        
        thread_safe_print(f"Daily rewards page: claimed {claimed} rewards")
        
    except Exception as e:
        thread_safe_print(f"Error on daily rewards page: {e}")
        
    return claimed

def automate_player(player_id, thread_id, is_retry=False):
    """Process a single player with thread identification"""
    retry_text = " (RETRY)" if is_retry else ""
    thread_safe_print(f"[Thread-{thread_id}] Processing player{retry_text}: {player_id}")
    driver = create_driver()
    wait = WebDriverWait(driver, 10)
    login_successful = False
    
    try:
        # Navigate to daily rewards page
        driver.get("https://hub.vertigogames.co/daily-rewards")
        time.sleep(1.5)
        accept_cookies(driver, wait)

        # Skip button enumeration for retry runs (performance optimization)
        if not is_retry:
            try:
                all_buttons = driver.find_elements(By.TAG_NAME, "button")
                thread_safe_print(f"[Thread-{thread_id}] Found {len(all_buttons)} buttons on page")
            except Exception as e:
                thread_safe_print(f"[Thread-{thread_id}] Error getting page buttons: {e}")

        # Login process - try multiple approaches
        login_selectors = [
            "//button[contains(text(),'Login') or contains(text(),'Log in') or contains(text(), 'Sign in')]",
            "//a[contains(text(),'Login') or contains(text(),'Log in') or contains(text(), 'Sign in')]",
            "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'login')]",
            "//button[contains(text(), 'claim')]",
            "//div[contains(text(), 'Daily Rewards') or contains(text(), 'daily')]//button",
            "//button[contains(@class, 'btn') or contains(@class, 'button')]",
            "//*[contains(text(), 'Login') or contains(text(), 'login')][@onclick or @href or self::button or self::a]"
        ]

        login_clicked = False
        for i, selector in enumerate(login_selectors):
            try:
                elements = driver.find_elements(By.XPATH, selector)
                
                if elements:
                    for j, element in enumerate(elements):
                        try:
                            element_text = element.text.strip()
                            if not is_retry:  # Skip detailed logging for retries
                                thread_safe_print(f"[Thread-{thread_id}] Element {j+1} text: '{element_text}'")
                            
                            if element.is_displayed() and element.is_enabled():
                                element.click()
                                login_clicked = True
                                thread_safe_print(f"[Thread-{thread_id}] Successfully clicked login element")
                                break
                        except Exception:
                            continue
                
                if login_clicked:
                    break
            except Exception:
                continue

        if not login_clicked:
            thread_safe_print(f"[Thread-{thread_id}] No login button found for {player_id}")
            return {"player_id": player_id, "daily_page": 0, "status": "login_button_not_found", "login_successful": False}

        try:
            time.sleep(0.3)
            
            # Enter User ID - try multiple input field selectors
            input_selectors = [
                "#user-id-input",
                "//input[contains(@placeholder, 'ID') or contains(@placeholder, 'User') or contains(@name, 'user') or contains(@placeholder, 'id')]",
                "//input[@type='text']",
                "//input[contains(@class, 'input')]",
                "//div[contains(@class, 'modal') or contains(@class, 'dialog')]//input[@type='text']"
            ]

            input_found = False
            input_box = None
            
            for i, selector in enumerate(input_selectors):
                try:
                    if selector.startswith('#'):
                        input_box = WebDriverWait(driver, 2).until(EC.visibility_of_element_located((By.ID, selector[1:])))
                    else:
                        input_box = WebDriverWait(driver, 2).until(EC.visibility_of_element_located((By.XPATH, selector)))
                    
                    thread_safe_print(f"[Thread-{thread_id}] Input field found")
                    input_box.clear()
                    input_box.send_keys(player_id)
                    time.sleep(0.1)
                    input_found = True
                    break
                except Exception:
                    continue

            if not input_found:
                thread_safe_print(f"[Thread-{thread_id}] No input field found for {player_id}")
                return {"player_id": player_id, "daily_page": 0, "status": "input_field_not_found", "login_successful": False}

            # Click Login CTA (optimized timing)
            login_cta_selectors = [
                "//button[contains(text(), 'Login') or contains(text(), 'Log in') or contains(text(), 'Sign in')]",
                "//button[@type='submit']",
                "//div[contains(@class, 'modal') or contains(@class, 'dialog')]//button[not(contains(text(), 'Cancel')) and not(contains(text(), 'Close'))]",
                "//button[contains(@class, 'primary') or contains(@class, 'submit')]"
            ]

            login_cta_clicked = False
            for i, selector in enumerate(login_cta_selectors):
                try:
                    if click_element_or_coords(driver, wait, (By.XPATH, selector), None, f"Login CTA {i+1}", timeout=2):
                        login_cta_clicked = True
                        thread_safe_print(f"[Thread-{thread_id}] Login CTA clicked successfully")
                        break
                except Exception:
                    continue

            if not login_cta_clicked:
                try:
                    input_box.send_keys(Keys.ENTER)
                    time.sleep(0.3)
                    thread_safe_print(f"[Thread-{thread_id}] Enter key pressed successfully")
                except Exception:
                    thread_safe_print(f"[Thread-{thread_id}] Login CTA not found for {player_id}")
                    return {"player_id": player_id, "daily_page": 0, "status": "login_cta_not_found", "login_successful": False}

            # Wait for login to complete
            thread_safe_print(f"[Thread-{thread_id}] Waiting for login to complete...")
            wait_for_login_complete(driver, wait, max_wait=12)
            time.sleep(2)
            
            thread_safe_print(f"[Thread-{thread_id}] Login completed successfully, page ready")
            login_successful = True

        except TimeoutException:
            thread_safe_print(f"[Thread-{thread_id}] Login timeout for {player_id}")
            return {"player_id": player_id, "daily_page": 0, "status": "login_timeout", "login_successful": False}

        # Ensure we're on daily-rewards page after login
        ensure_daily_rewards_page(driver)
        
        # Handle post-login popups safely
        thread_safe_print(f"[Thread-{thread_id}] Handling post-login popups safely...")
        close_popups_safe(driver)
        time.sleep(1.5)

        # Claim rewards from daily rewards page with improved navigation control
        daily_page_claimed = claim_daily_rewards_page(driver, wait)

        thread_safe_print(f"[Thread-{thread_id}] Player {player_id}: Daily Page Claims: {daily_page_claimed}")

        # Status determination
        if daily_page_claimed > 0:
            status = "success"
        else:
            status = "no_claims"

        return {"player_id": player_id, "daily_page": daily_page_claimed, "status": status, "login_successful": True}

    except Exception as e:
        thread_safe_print(f"[Thread-{thread_id}] Exception for player {player_id}: {e}")
        return {"player_id": player_id, "daily_page": 0, "status": "error", "login_successful": login_successful}
    finally:
        try:
            driver.quit()
            thread_safe_print(f"[Thread-{thread_id}] Driver closed for player {player_id}")
        except:
            pass

def process_batch(player_batch, batch_number, is_retry=False):
    """Process a batch of players in parallel"""
    retry_text = " (RETRY)" if is_retry else ""
    thread_safe_print(f"Starting Batch {batch_number}{retry_text} with {len(player_batch)} players")
    results = []
    
    with ThreadPoolExecutor(max_workers=len(player_batch)) as executor:
        future_to_player = {
            executor.submit(automate_player, player_id, f"{batch_number}-{idx+1}", is_retry): player_id 
            for idx, player_id in enumerate(player_batch)
        }
        
        for future in as_completed(future_to_player):
            player_id = future_to_player[future]
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                thread_safe_print(f"Batch {batch_number}{retry_text} - Player {player_id} failed: {e}")
                results.append({"player_id": player_id, "daily_page": 0, "status": "failed", "login_successful": False})
    
    thread_safe_print(f"Batch {batch_number}{retry_text} completed")
    return results

def main():
    # Read all player IDs
    players = []
    with open(r"C:\Users\DELL\Desktop\players.csv", newline='') as csvfile:
        reader = csv.reader(csvfile)
        for row in reader:
            pid = row[0].strip()
            if pid:
                players.append(pid)
    
    thread_safe_print(f"Loaded {len(players)} player IDs")
    
    BATCH_SIZE = 2
    batches = [players[i:i + BATCH_SIZE] for i in range(0, len(players), BATCH_SIZE)]
    
    thread_safe_print(f"Processing {len(batches)} batches of up to {BATCH_SIZE} players each")
    
    all_results = []
    start_time = time.time()
    
    # FIRST RUN - All players
    thread_safe_print("\n" + "="*60)
    thread_safe_print("STARTING INITIAL RUN FOR ALL PLAYERS")
    thread_safe_print("="*60)
    
    for batch_num, batch in enumerate(batches, 1):
        batch_start = time.time()
        batch_results = process_batch(batch, batch_num, is_retry=False)
        all_results.extend(batch_results)
        
        batch_time = time.time() - batch_start
        thread_safe_print(f"Batch {batch_num} took {batch_time:.1f} seconds")
        
        if batch_num < len(batches):
            time.sleep(1.5)
    
    # Check for failed cases that need retry
    failed_players = []
    for result in all_results:
        if result["status"] in ["error", "login_button_not_found", "input_field_not_found", "login_cta_not_found", "login_timeout", "failed"]:
            failed_players.append(result["player_id"])
    
    # RETRY RUN - Only failed players
    if failed_players:
        thread_safe_print("\n" + "="*60)
        thread_safe_print(f"STARTING RETRY RUN FOR {len(failed_players)} FAILED PLAYERS")
        thread_safe_print("="*60)
        thread_safe_print(f"Failed Player IDs: {', '.join(failed_players)}")
        thread_safe_print("="*60)
        
        # Create retry batches
        retry_batches = [failed_players[i:i + BATCH_SIZE] for i in range(0, len(failed_players), BATCH_SIZE)]
        
        retry_results = []
        for batch_num, batch in enumerate(retry_batches, 1):
            batch_start = time.time()
            batch_results = process_batch(batch, f"R{batch_num}", is_retry=True)
            retry_results.extend(batch_results)
            
            batch_time = time.time() - batch_start
            thread_safe_print(f"Retry Batch R{batch_num} took {batch_time:.1f} seconds")
            
            if batch_num < len(retry_batches):
                time.sleep(1.5)
        
        # Update original results with retry results
        retry_dict = {r["player_id"]: r for r in retry_results}
        for i, result in enumerate(all_results):
            if result["player_id"] in retry_dict:
                # Replace with retry result
                all_results[i] = retry_dict[result["player_id"]]
    
    total_time = time.time() - start_time
    successful_logins = sum(1 for r in all_results if r["login_successful"])
    successful_processes = sum(1 for r in all_results if r["status"] == "success")
    total_daily_page = sum(r["daily_page"] for r in all_results)
    
    # Collect final failed cases
    final_failed = [r["player_id"] for r in all_results if r["status"] in ["error", "login_button_not_found", "input_field_not_found", "login_cta_not_found", "login_timeout", "failed"]]
    final_no_claims = [r["player_id"] for r in all_results if r["status"] == "no_claims"]
    
    thread_safe_print("\n" + "="*60)
    thread_safe_print("DAILY REWARDS MODULE - FINAL SUMMARY")
    thread_safe_print("="*60)
    thread_safe_print(f"Total players processed: {len(all_results)}")
    thread_safe_print(f"Successful logins: {successful_logins}")
    thread_safe_print(f"Successful claim processes: {successful_processes}")
    thread_safe_print(f"Daily Rewards page claims: {total_daily_page}")
    thread_safe_print(f"Total script execution time: {total_time:.1f} seconds")
    thread_safe_print(f"Average time per player: {total_time/len(players):.1f} seconds")
    
    if failed_players:
        thread_safe_print(f"Players that required retry: {len(failed_players)}")
    
    # Detailed status breakdown
    status_counts = {}
    for result in all_results:
        status = result["status"]
        status_counts[status] = status_counts.get(status, 0) + 1
    
    thread_safe_print("\nStatus Breakdown:")
    for status, count in status_counts.items():
        thread_safe_print(f" {status}: {count}")
    
    if final_failed:
        thread_safe_print(f"\nFinal Failed Player IDs: {', '.join(final_failed)}")
        
    if final_no_claims:
        thread_safe_print(f"No Claims Available Player IDs: {', '.join(final_no_claims)}")
    
    thread_safe_print("="*60)

if __name__ == "__main__":
    main()
