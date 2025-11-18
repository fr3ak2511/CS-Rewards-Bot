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
    start_time = time.time()
    while time.time() - start_time < max_wait:
        try:
            current_url = driver.current_url
            if "user" in current_url.lower() or "dashboard" in current_url.lower() or "store" in current_url.lower():
                return True
            user_elements = driver.find_elements(By.XPATH, "//button[contains(text(),'Logout') or contains(text(),'Profile') or contains(@class,'user')]")
            if user_elements:
                return True
            time.sleep(0.2)
        except:
            time.sleep(0.2)
    return True

def ensure_store_page(driver):
    """Ensures we're on store page and navigates back if not"""
    current_url = driver.current_url
    if "store" not in current_url.lower():
        thread_safe_print(f"WARNING: Not on store page ({current_url}), navigating back...")
        driver.get("https://hub.vertigogames.co/store")
        time.sleep(2)
        return True
    return False

def close_popups_safe(driver):
    """Safely closes popups by clicking on safe areas OUTSIDE the popup window"""
    try:
        # Check if any popups exist (quick check)
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
            
            # Get window size for safe clicking areas
            window_size = driver.get_window_size()
            width = window_size['width']
            height = window_size['height']
            
            # Try fewer safe areas for speed (most effective ones)
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
            
            # Quick ESC key backup
            try:
                driver.find_element(By.TAG_NAME, 'body').send_keys(Keys.ESCAPE)
                time.sleep(0.5)
                return True
            except Exception:
                pass
            
    except Exception:
        pass
    
    return False

def navigate_to_daily_rewards_section(driver):
    """Navigates back to Daily Rewards section - either by tab click or scroll"""
    thread_safe_print("Navigating to Daily Rewards section...")
    
    # Ensure we're on store page first
    ensure_store_page(driver)
    close_popups_safe(driver)
    time.sleep(0.5)
    
    # First try clicking the Daily Rewards TAB
    tab_clicked = click_daily_rewards_tab(driver)
    
    if tab_clicked:
        thread_safe_print("Successfully navigated to Daily Rewards section via tab")
        time.sleep(1.5)
        return True
    else:
        thread_safe_print("Tab click failed, trying scroll method...")
        
        # Fallback: scroll to Daily Rewards section
        try:
            # Scroll to top first
            driver.execute_script("window.scrollTo(0, 0);")
            time.sleep(0.5)
            
            # Then scroll down progressively to find Daily Rewards section
            max_scrolls = 4  # Reduced from 5
            for scroll_attempt in range(max_scrolls):
                thread_safe_print(f"Scroll attempt {scroll_attempt + 1}/{max_scrolls}...")
                
                driver.execute_script("window.scrollBy(0, 400);")
                time.sleep(1)
                
                # Look for Daily Rewards text
                daily_text_elements = driver.find_elements(By.XPATH, "//*[contains(text(), 'Daily Reward') and not(self::a) and not(self::button)]")
                
                if daily_text_elements:
                    for element in daily_text_elements:
                        try:
                            element_text = element.text.strip()
                            thread_safe_print(f"Found Daily Rewards text: '{element_text}'")
                            
                            # Scroll this element into view
                            driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", element)
                            time.sleep(1.5)
                            
                            thread_safe_print("Successfully navigated to Daily Rewards section via scroll")
                            return True
                        except Exception:
                            continue
                            
        except Exception as e:
            thread_safe_print(f"Scroll navigation failed: {e}")
            
        thread_safe_print("Failed to navigate to Daily Rewards section")
        return False

def click_daily_rewards_tab(driver):
    """Clicks on the Daily Rewards TAB in the Store page (not the left menu)"""
    
    # Most common/effective selectors first for speed
    tab_selectors = [
        "//div[contains(@class, 'tab')]//span[contains(text(), 'Daily Rewards')]",
        "//button[contains(@class, 'tab')][contains(text(), 'Daily Rewards')]",
        "//*[text()='Daily Rewards' and (contains(@class, 'tab') or parent::*[contains(@class, 'tab')])]",
        "//div[contains(@class, 'Tab')]//div[contains(text(), 'Daily Rewards')]",
        "//a[contains(@class, 'tab')][contains(text(), 'Daily Rewards')]"
    ]
    
    for i, selector in enumerate(tab_selectors):
        try:
            tab_elements = driver.find_elements(By.XPATH, selector)
            
            for j, tab in enumerate(tab_elements):
                try:
                    if tab.is_displayed():
                        tab_text = tab.text.strip()
                        
                        # Quick check to ensure it's NOT from left sidebar/menu
                        try:
                            parent = tab.find_element(By.XPATH, "./..")
                            parent_classes = parent.get_attribute("class") or ""
                            
                            # Skip if parent contains sidebar/menu related classes
                            if any(word in parent_classes.lower() for word in ['sidebar', 'menu', 'nav', 'side']):
                                continue
                                
                        except:
                            pass
                        
                        # Try to click the Daily Rewards tab
                        try:
                            # Scroll tab into view first
                            driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", tab)
                            time.sleep(0.5)
                            
                            # Try regular click first
                            tab.click()
                            thread_safe_print(f"Successfully clicked Daily Rewards tab (regular click)")
                            time.sleep(1.5)
                            return True
                            
                        except Exception:
                            try:
                                # Try JavaScript click
                                driver.execute_script("arguments[0].click();", tab)
                                thread_safe_print(f"Successfully clicked Daily Rewards tab (JS click)")
                                time.sleep(1.5)
                                return True
                            except Exception:
                                continue
                        
                except Exception:
                    continue
                    
        except Exception:
            continue
    
    return False

def get_claim_buttons(driver):
    """Gets all available claim buttons"""
    
    claim_buttons = []
    
    # Most effective selectors first for speed
    specific_claim_selectors = [
        "//button[normalize-space()='Claim']",
        "//button[contains(text(), 'Claim') and not(contains(text(), 'Buy')) and not(contains(text(), 'Purchase'))]",
        "//div[contains(@class, 'reward')]//button[contains(text(), 'Claim')]"
    ]
    
    for selector in specific_claim_selectors:
        try:
            found_buttons = driver.find_elements(By.XPATH, selector)
            for btn in found_buttons:
                if btn.is_displayed() and btn not in claim_buttons:
                    btn_text = btn.text.strip()
                    # Safety check - avoid payment buttons
                    if any(word in btn_text.lower() for word in ['buy', 'purchase', 'payment', 'pay', '$']):
                        continue
                    claim_buttons.append(btn)
                    thread_safe_print(f"Found claim button: '{btn_text}' - Enabled: {btn.is_enabled()}")
        except Exception:
            continue
    
    return claim_buttons

def claim_store_daily_rewards(driver, wait):
    """Improved daily rewards claiming - continues until all 3 are complete or no more available"""
    claimed = 0
    max_claim_attempts = 5  # Increased to ensure we get all possible claims
    
    try:
        time.sleep(1.5)
        thread_safe_print("Processing Store page Daily Rewards section...")
        
        # Initial navigation to Daily Rewards section
        if not navigate_to_daily_rewards_section(driver):
            thread_safe_print("Failed to navigate to Daily Rewards section initially")
            return 0
        
        # Continue claiming until we have 3 claims OR no more buttons available
        for claim_round in range(max_claim_attempts):
            thread_safe_print(f"\n--- CLAIM ROUND {claim_round + 1} ---")
            
            # Ensure we're on store page and in Daily Rewards section before each round
            ensure_store_page(driver)
            close_popups_safe(driver)
            
            # Re-navigate to Daily Rewards section before each claim attempt
            if claim_round > 0:  # Skip initial navigation as it's already done
                thread_safe_print("Re-navigating to Daily Rewards section before next claim...")
                if not navigate_to_daily_rewards_section(driver):
                    thread_safe_print(f"Failed to re-navigate to Daily Rewards section in round {claim_round + 1}")
                    continue
            
            time.sleep(1.5)
            
            # Get available claim buttons
            claim_buttons = get_claim_buttons(driver)
            
            if not claim_buttons:
                thread_safe_print(f"No claim buttons found in round {claim_round + 1}")
                
                # Double check for no claimable rewards
                thread_safe_print("Performing double check for claim buttons...")
                time.sleep(1.5)
                claim_buttons = get_claim_buttons(driver)
                
                if not claim_buttons:
                    thread_safe_print("Double check confirmed: No more claim buttons available")
                    break
            
            # Try to claim one button in this round
            button_claimed_this_round = False
            
            for idx, btn in enumerate(claim_buttons):
                try:
                    thread_safe_print(f"Attempting to claim button {idx + 1} in round {claim_round + 1}")
                    
                    btn_text = btn.text.strip()
                    thread_safe_print(f"Button text: '{btn_text}' - Enabled: {btn.is_enabled()}")
                    
                    if btn.is_enabled():
                        # Scroll button into view
                        driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", btn)
                        time.sleep(0.5)
                        
                        # Close any popups before clicking
                        close_popups_safe(driver)
                        
                        clicked = False
                        
                        # Try different click methods
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
                                pass
                        
                        if clicked:
                            claimed += 1
                            button_claimed_this_round = True
                            thread_safe_print(f"REWARD {claimed} CLAIMED SUCCESSFULLY!")
                            time.sleep(2.5)
                            
                            # Verify we're still on store page after claim
                            if ensure_store_page(driver):
                                thread_safe_print("Had to navigate back to store page after claim")
                            
                            # Handle post-claim popups safely
                            close_popups_safe(driver)
                            
                            # Break from button loop - only one claim per round
                            break
                        else:
                            thread_safe_print(f"All click methods failed for button {idx + 1}")
                    else:
                        thread_safe_print(f"Button {idx + 1} not enabled")
                        
                except Exception as e:
                    thread_safe_print(f"Error processing claim button {idx + 1}: {e}")
                    continue
            
            # If no button was claimed in this round, we're done
            if not button_claimed_this_round:
                thread_safe_print(f"No buttons claimed in round {claim_round + 1} - all available claims completed")
                break
            
            thread_safe_print(f"Round {claim_round + 1} completed. Total claimed so far: {claimed}")
            
            # If we've claimed 3 rewards, we're done (maximum possible)
            if claimed >= 3:
                thread_safe_print("All 3 daily rewards claimed successfully!")
                break
        
        # Final summary
        thread_safe_print(f"\nFINAL RESULT: Claimed {claimed} rewards in this session")
        
    except Exception as e:
        thread_safe_print(f"Error in store daily rewards: {e}")
        
    return claimed

def automate_player(player_id, thread_id, is_retry=False):
    retry_text = " (RETRY)" if is_retry else ""
    thread_safe_print(f"[Thread-{thread_id}] Processing player{retry_text}: {player_id}")
    driver = create_driver()
    wait = WebDriverWait(driver, 10)
    login_successful = False
    
    try:
        driver.get("https://hub.vertigogames.co/store")
        time.sleep(1.5)
        accept_cookies(driver, wait)
        
        # Skip button enumeration for retry runs (performance optimization)
        if not is_retry:
            try:
                all_buttons = driver.find_elements(By.TAG_NAME, "button")
                thread_safe_print(f"[Thread-{thread_id}] Found {len(all_buttons)} buttons on page")
            except Exception as e:
                thread_safe_print(f"[Thread-{thread_id}] Error getting page buttons: {e}")
        
        login_selectors = [
            "//button[contains(text(),'Login') or contains(text(),'Log in') or contains(text(), 'Sign in')]",
            "//a[contains(text(),'Login') or contains(text(),'Log in') or contains(text(), 'Sign in')]",
            "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'login')]",
            "//button[contains(text(), 'claim')]",
            "//div[contains(text(), 'Store') or contains(text(), 'store')]//button",
            "//button[contains(@class, 'btn') or contains(@class, 'button')]",
            "//*[contains(text(), 'Login') or contains(text(), 'login')][@onclick or @href or self::button or self::a]"
        ]
        
        login_clicked = False
        for i, selector in enumerate(login_selectors):
            thread_safe_print(f"[Thread-{thread_id}] Trying login selector {i+1}: {selector}")
            try:
                elements = driver.find_elements(By.XPATH, selector)
                
                if elements:
                    for j, element in enumerate(elements):
                        try:
                            element_text = element.text.strip()
                            if not is_retry:
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
            return {"player_id": player_id, "store_daily": 0, "status": "login_button_not_found", "login_successful": False}
        
        try:
            time.sleep(0.3)
            
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
                return {"player_id": player_id, "store_daily": 0, "status": "input_field_not_found", "login_successful": False}
            
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
                    return {"player_id": player_id, "store_daily": 0, "status": "login_cta_not_found", "login_successful": False}
            
            thread_safe_print(f"[Thread-{thread_id}] Waiting for login to complete...")
            wait_for_login_complete(driver, wait, max_wait=12)
            time.sleep(2)
            
            thread_safe_print(f"[Thread-{thread_id}] Login completed successfully, page ready")
            login_successful = True
            
        except TimeoutException:
            thread_safe_print(f"[Thread-{thread_id}] Login timeout for {player_id}")
            return {"player_id": player_id, "store_daily": 0, "status": "login_timeout", "login_successful": False}
        
        # Ensure we're on store page after login
        ensure_store_page(driver)
        
        thread_safe_print(f"[Thread-{thread_id}] Handling post-login popups safely...")
        close_popups_safe(driver)
        time.sleep(1.5)
        
        # Process daily rewards - continue until all possible claims are done
        store_claimed = claim_store_daily_rewards(driver, wait)
        
        thread_safe_print(f"[Thread-{thread_id}] Player {player_id}: Store Daily Claims: {store_claimed}")
        
        # Updated status logic - based on completion vs availability
        if store_claimed >= 3:
            # All 3 claims completed in this session
            status = "success"
        elif store_claimed > 0:
            # Some claims were made, check if more are available
            thread_safe_print(f"[Thread-{thread_id}] {store_claimed} claims made, checking if all available claims are completed...")
            
            # Do a final check to see if any more claims are available
            if navigate_to_daily_rewards_section(driver):
                final_buttons = get_claim_buttons(driver)
                if final_buttons:
                    # More claims are still available
                    status = "partial_success"
                    thread_safe_print(f"[Thread-{thread_id}] Partial success - {len(final_buttons)} more claims available")
                else:
                    # No more claims available - all done
                    status = "success"  # Changed to success even if < 3 because all available are done
                    thread_safe_print(f"[Thread-{thread_id}] All available claims completed - marking as success")
            else:
                status = "partial_success"
        else:
            # No claims made - check if any were available
            thread_safe_print(f"[Thread-{thread_id}] No claims made, checking if claims were available...")
            status = "no_claims"  # All claims already done previously
        
        return {"player_id": player_id, "store_daily": store_claimed, "status": status, "login_successful": True}
        
    except Exception as e:
        thread_safe_print(f"[Thread-{thread_id}] Exception for player {player_id}: {e}")
        return {"player_id": player_id, "store_daily": 0, "status": "error", "login_successful": login_successful}
    finally:
        try:
            driver.quit()
            thread_safe_print(f"[Thread-{thread_id}] Driver closed for player {player_id}")
        except:
            pass

def process_batch(player_batch, batch_number, is_retry=False):
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
                results.append({"player_id": player_id, "store_daily": 0, "status": "failed", "login_successful": False})
    
    thread_safe_print(f"Batch {batch_number}{retry_text} completed")
    return results

def main():
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
    
    # Check for FAILED cases that need retry (ONLY login/system failures)
    failed_players = []
    for result in all_results:
        # ONLY retry actual login/system failures, NOT claim-related issues
        if result["status"] in ["login_button_not_found", "input_field_not_found", "login_cta_not_found", "login_timeout", "error", "failed"]:
            failed_players.append(result["player_id"])
    
    # RETRY RUN - Only truly failed players (login/system issues)
    if failed_players:
        thread_safe_print("\n" + "="*60)
        thread_safe_print(f"STARTING RETRY RUN FOR {len(failed_players)} FAILED PLAYERS")
        thread_safe_print("="*60)
        thread_safe_print(f"Failed Player IDs (Login/System Issues): {', '.join(failed_players)}")
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
    total_store_daily = sum(r["store_daily"] for r in all_results)
    
    # Collect final failed cases
    final_failed = [r["player_id"] for r in all_results if r["status"] in ["login_button_not_found", "input_field_not_found", "login_cta_not_found", "login_timeout", "error", "failed"]]
    final_partial_success = [r["player_id"] for r in all_results if r["status"] == "partial_success"]
    final_no_claims = [r["player_id"] for r in all_results if r["status"] == "no_claims"]
    
    thread_safe_print("\n" + "="*60)
    thread_safe_print("STORE MODULE - FINAL SUMMARY")
    thread_safe_print("="*60)
    thread_safe_print(f"Total players processed: {len(all_results)}")
    thread_safe_print(f"Successful logins: {successful_logins}")
    thread_safe_print(f"Successful claim processes: {successful_processes}")
    thread_safe_print(f"Store Daily Rewards claims: {total_store_daily}")
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
        thread_safe_print(f"\nFailed Player IDs (Login/System Issues): {', '.join(final_failed)}")
        
    if final_partial_success:
        thread_safe_print(f"Partial Success Player IDs (More Claims Available): {', '.join(final_partial_success)}")
    
    if final_no_claims:
        thread_safe_print(f"No Claims Available Player IDs (Already Completed): {', '.join(final_no_claims)}")
    
    thread_safe_print("="*60)

if __name__ == "__main__":
    main()
