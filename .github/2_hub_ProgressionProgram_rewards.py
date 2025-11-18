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
    except:
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

def wait_for_login_complete(driver, wait, max_wait=15):
    start_time = time.time()
    while time.time() - start_time < max_wait:
        try:
            current_url = driver.current_url
            if "user" in current_url.lower() or "dashboard" in current_url.lower() or "progression-program" in current_url.lower():
                return True
            user_elements = driver.find_elements(By.XPATH, "//button[contains(text(),'Logout') or contains(text(),'Profile') or contains(@class,'user')]")
            if user_elements:
                return True
            time.sleep(0.2)
        except:
            time.sleep(0.2)
    return True

def close_initial_popups_only(driver):
    """Close ONLY the initial popup after login - NO unnecessary clicks"""
    thread_safe_print("\n--- Closing initial popup ---")
    
    # Try Close button first
    try:
        close_btn = driver.find_element(By.XPATH, "//button[normalize-space(text())='Close']")
        if close_btn.is_displayed():
            close_btn.click()
            thread_safe_print("✓ Closed popup with Close button")
            time.sleep(2)
            return
    except:
        pass
    
    # Try X button
    try:
        x_buttons = driver.find_elements(By.XPATH, "//*[name()='svg']/parent::button")
        for x_btn in x_buttons:
            if x_btn.is_displayed():
                x_btn.click()
                thread_safe_print("✓ Closed popup with X button")
                time.sleep(2)
                return
    except:
        pass
    
    # Only if above failed, do ONE safe click
    try:
        window_size = driver.get_window_size()
        width = window_size['width']
        height = window_size['height']
        
        safe_x = int(width * 0.90)
        safe_y = int(height * 0.50)
        
        actions = ActionChains(driver)
        x_offset = safe_x - (width // 2)
        y_offset = safe_y - (height // 2)
        
        actions.move_by_offset(x_offset, y_offset).click().perform()
        actions.move_by_offset(-x_offset, -y_offset).perform()
        
        thread_safe_print("✓ Popup closed with safe click")
        time.sleep(1.5)
    except:
        pass
    
    thread_safe_print("✓ Popup handling complete\n")

def find_and_click_claim_buttons(driver):
    """
    Find and click green Claim buttons using JavaScript position filtering.
    NO unnecessary clicks - ONLY on Claim buttons.
    """
    
    thread_safe_print("\n" + "="*70)
    thread_safe_print("CLAIMING REWARDS")
    thread_safe_print("="*70)
    
    total_claimed = 0
    max_attempts = 20
    
    for attempt in range(1, max_attempts + 1):
        thread_safe_print(f"\n>>> Attempt #{attempt} <<<")
        
        # JavaScript to find Claim buttons in content area only (X > 400px)
        get_elements_script = """
        let allButtons = document.querySelectorAll('button');
        let claimButtons = [];
        
        allButtons.forEach(function(btn) {
            let text = btn.innerText.trim();
            if (text === 'Claim') {
                let rect = btn.getBoundingClientRect();
                let x = rect.left;
                
                // Only buttons in content area (right of sidebar)
                if (x > 400) {
                    let parent = btn.closest('div');
                    let parentText = parent ? parent.innerText : '';
                    
                    // Exclude already delivered cards
                    if (!parentText.includes('Delivered')) {
                        claimButtons.push(btn);
                    }
                }
            }
        });
        
        return claimButtons;
        """
        
        try:
            claimable_elements = driver.execute_script(get_elements_script)
            thread_safe_print(f"Found {len(claimable_elements)} claimable buttons")
        except Exception as e:
            thread_safe_print(f"Error: {e}")
            break
        
        if not claimable_elements:
            if total_claimed > 0:
                thread_safe_print(f"\n✓✓ ALL CLAIMS COMPLETE! Total: {total_claimed}")
                break
            
            if attempt <= 3:
                time.sleep(2)
                continue
            else:
                break
        
        # CLAIM FIRST BUTTON
        claimed_this_round = False
        for btn in claimable_elements[:1]:
            try:
                # Scroll into view
                driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", btn)
                time.sleep(0.5)
                
                # Click
                driver.execute_script("arguments[0].click();", btn)
                
                total_claimed += 1
                claimed_this_round = True
                
                thread_safe_print(f"✓ Reward #{total_claimed} claimed!")
                
                time.sleep(2)
                
                # Close confirmation popup if appears
                try:
                    close_btn = driver.find_element(By.XPATH, "//button[normalize-space(text())='Close']")
                    if close_btn.is_displayed():
                        close_btn.click()
                        time.sleep(1)
                except:
                    pass
                
                break
                
            except Exception as e:
                thread_safe_print(f"Error clicking: {e}")
                continue
        
        if not claimed_this_round and attempt >= 5:
            break
        
        time.sleep(1)
    
    thread_safe_print(f"\n{'='*70}")
    thread_safe_print(f"TOTAL REWARDS CLAIMED: {total_claimed}")
    thread_safe_print(f"{'='*70}\n")
    
    return total_claimed

def claim_monthly_rewards(driver, wait):
    """Main reward claiming function"""
    try:
        thread_safe_print("\nWaiting for page to load...")
        time.sleep(5)
        
        # Close initial popup ONCE - no extra clicks
        close_initial_popups_only(driver)
        
        thread_safe_print("Waiting for reward cards...")
        time.sleep(2)
        
        # Claim rewards - ONLY clicks on Claim buttons
        total_claimed = find_and_click_claim_buttons(driver)
        
        return total_claimed
        
    except Exception as e:
        thread_safe_print(f"\n❌ ERROR: {e}")
        return 0

def automate_player(player_id, thread_id, is_retry=False):
    thread_safe_print(f"\n[Thread-{thread_id}] ===== PROCESSING PLAYER: {player_id} =====")
    driver = create_driver()
    wait = WebDriverWait(driver, 15)
    login_successful = False
    
    try:
        driver.get("https://hub.vertigogames.co/progression-program")
        time.sleep(2)
        
        accept_cookies(driver, wait)
        
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
        for selector in login_selectors:
            try:
                elements = driver.find_elements(By.XPATH, selector)
                if elements:
                    for element in elements:
                        try:
                            if element.is_displayed() and element.is_enabled():
                                element.click()
                                login_clicked = True
                                break
                        except:
                            continue
                    if login_clicked:
                        break
            except:
                continue
        
        if not login_clicked:
            return {"player_id": player_id, "monthly_rewards": 0, "status": "login_button_not_found", "login_successful": False}
        
        try:
            time.sleep(0.5)
            
            input_selectors = [
                "#user-id-input",
                "//input[contains(@placeholder, 'ID') or contains(@placeholder, 'User') or contains(@name, 'user') or contains(@placeholder, 'id')]",
                "//input[@type='text']",
                "//input[contains(@class, 'input')]",
                "//div[contains(@class, 'modal') or contains(@class, 'dialog')]//input[@type='text']"
            ]
            
            input_found = False
            input_box = None
            for selector in input_selectors:
                try:
                    if selector.startswith('#'):
                        input_box = WebDriverWait(driver, 3).until(EC.visibility_of_element_located((By.ID, selector[1:])))
                    else:
                        input_box = WebDriverWait(driver, 3).until(EC.visibility_of_element_located((By.XPATH, selector)))
                    
                    input_box.clear()
                    input_box.send_keys(player_id)
                    time.sleep(0.2)
                    input_found = True
                    break
                except:
                    continue
            
            if not input_found:
                return {"player_id": player_id, "monthly_rewards": 0, "status": "input_field_not_found", "login_successful": False}
            
            login_cta_selectors = [
                "//button[contains(text(), 'Login') or contains(text(), 'Log in') or contains(text(), 'Sign in')]",
                "//button[@type='submit']",
                "//div[contains(@class, 'modal') or contains(@class, 'dialog')]//button[not(contains(text(), 'Cancel')) and not(contains(text(), 'Close'))]",
                "//button[contains(@class, 'primary') or contains(@class, 'submit')]"
            ]
            
            login_cta_clicked = False
            for selector in login_cta_selectors:
                try:
                    if click_element_or_coords(driver, wait, (By.XPATH, selector), None, timeout=3):
                        login_cta_clicked = True
                        break
                except:
                    continue
            
            if not login_cta_clicked:
                try:
                    input_box.send_keys(Keys.ENTER)
                    time.sleep(0.5)
                except:
                    return {"player_id": player_id, "monthly_rewards": 0, "status": "login_cta_not_found", "login_successful": False}
            
            wait_for_login_complete(driver, wait, max_wait=15)
            time.sleep(4)
            
            thread_safe_print(f"[Thread-{thread_id}] ✓ Login successful!")
            login_successful = True
            
        except TimeoutException:
            return {"player_id": player_id, "monthly_rewards": 0, "status": "login_timeout", "login_successful": False}
        
        # CLAIM REWARDS
        monthly_claimed = claim_monthly_rewards(driver, wait)
        
        thread_safe_print(f"\n[Thread-{thread_id}] ===== PLAYER {player_id} COMPLETE: {monthly_claimed} REWARDS =====\n")
        
        status = "success" if monthly_claimed > 0 else "no_claims"
        return {"player_id": player_id, "monthly_rewards": monthly_claimed, "status": status, "login_successful": True}
        
    except Exception as e:
        thread_safe_print(f"[Thread-{thread_id}] ERROR: {e}")
        return {"player_id": player_id, "monthly_rewards": 0, "status": "error", "login_successful": login_successful}
    
    finally:
        try:
            driver.quit()
        except:
            pass

def process_batch(player_batch, batch_number, is_retry=False):
    results = []
    with ThreadPoolExecutor(max_workers=len(player_batch)) as executor:
        future_to_player = {executor.submit(automate_player, player_id, f"{batch_number}-{idx+1}", is_retry): player_id for idx, player_id in enumerate(player_batch)}
        
        for future in as_completed(future_to_player):
            player_id = future_to_player[future]
            try:
                result = future.result(timeout=180)
                results.append(result)
            except Exception as e:
                thread_safe_print(f"Player {player_id} failed: {e}")
                results.append({"player_id": player_id, "monthly_rewards": 0, "status": "failed", "login_successful": False})
    
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
    
    all_results = []
    start_time = time.time()
    
    for batch_num, batch in enumerate(batches, 1):
        batch_results = process_batch(batch, batch_num)
        all_results.extend(batch_results)
        
        if batch_num < len(batches):
            time.sleep(2)
    
    total_time = time.time() - start_time
    total_players = len(all_results)
    successful_logins = sum(1 for r in all_results if r["login_successful"])
    total_monthly = sum(r["monthly_rewards"] for r in all_results)
    avg_time_per_id = total_time / total_players if total_players > 0 else 0
    
    thread_safe_print(f"\n{'='*70}")
    thread_safe_print(f"FINAL SUMMARY")
    thread_safe_print(f"{'='*70}")
    thread_safe_print(f"Total Players: {total_players}")
    thread_safe_print(f"Total Successful Logins: {successful_logins}")
    thread_safe_print(f"Total Monthly Rewards Claimed: {total_monthly}")
    thread_safe_print(f"Total Time Taken: {total_time:.1f}s ({total_time/60:.1f} minutes)")
    thread_safe_print(f"Avg. Time per ID: {avg_time_per_id:.1f}s")
    thread_safe_print(f"{'='*70}")

if __name__ == "__main__":
    main()
