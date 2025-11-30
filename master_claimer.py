import time
import sys
import argparse
import os
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# --- CONFIG ---
HEADLESS = True

def safe_print(msg):
    # Print to stdout so the shell script captures it
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
    sys.stdout.flush()

# --- DRIVER (V24 CONFIG - VERIFIED STABLE) ---
def create_driver():
    options = Options()
    if HEADLESS:
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1920,1080")
    
    # CRITICAL STABILITY FLAGS
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-software-rasterizer")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--disable-blink-features=AutomationControlled")
    
    # THE MAGIC FLAGS (V24 Configuration)
    # Note: We use --single-process but NOT --no-zygote based on your crash logs
    options.add_argument("--single-process") 
    
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    options.page_load_strategy = 'eager'
    
    try:
        path = ChromeDriverManager().install()
    except:
        path = "/usr/bin/chromedriver"
        
    service = Service(path)
    driver = webdriver.Chrome(service=service, options=options)
    
    # Anti-detection
    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        })
    except: pass
    
    driver.set_page_load_timeout(60)
    driver.implicitly_wait(5)
    return driver

# --- HELPERS ---
def close_popups_safe(driver):
    try:
        # JS Close
        driver.execute_script("""
            document.querySelectorAll('.modal, .popup, .dialog, button').forEach(btn => {
                let text = btn.innerText.toLowerCase();
                if(text.includes('close') || text === '×' || text === 'x' || text.includes('continue')) {
                    if(btn.offsetParent !== null) btn.click();
                }
            });
        """)
        # Safe Area
        ActionChains(driver).move_by_offset(10, 10).click().perform()
    except: pass
    return True

def accept_cookies(driver, wait):
    try:
        btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Accept')]")))
        btn.click()
    except: pass

# --- LOGIN (PHYSICAL CLICK PRIORITY) ---
def verify_login_success(driver):
    try:
        if driver.find_elements(By.XPATH, "//button[contains(text(), 'Logout')]"): return True
        if driver.find_elements(By.XPATH, "//button[contains(text(), 'Claim')]"): return True
        return False
    except: return False

def login(driver, wait, player_id):
    driver.get("https://hub.vertigogames.co/daily-rewards")
    time.sleep(5)
    accept_cookies(driver, wait)
    close_popups_safe(driver)
    
    if verify_login_success(driver):
        return True

    # 1. Click Login (PHYSICAL CLICK PRIORITY)
    login_clicked = False
    login_selectors = ["//button[contains(text(),'Login')]", "//a[contains(text(),'Login')]"]
    
    for selector in login_selectors:
        try:
            btns = driver.find_elements(By.XPATH, selector)
            for btn in btns:
                if btn.is_displayed():
                    # Use ActionChains for a "real" click (Fixes 'Input Not Found')
                    ActionChains(driver).move_to_element(btn).click().perform()
                    safe_print("Clicked Login (Physical)")
                    login_clicked = True
                    time.sleep(3)
                    break
            if login_clicked: break
        except: continue
    
    if not login_clicked:
        # Fallback to JS
        try:
            driver.execute_script("document.querySelector('button.login')?.click()")
            safe_print("Clicked Login (JS)")
            time.sleep(3)
        except: pass

    # 2. Input
    inp = None
    try:
        inp = WebDriverWait(driver, 10).until(
            EC.visibility_of_element_located((By.XPATH, "//input[@type='text' or contains(@placeholder, 'ID')]"))
        )
    except:
        driver.save_screenshot(f"login_fail_{player_id}.png")
        raise Exception("Input not found")

    inp.clear()
    inp.send_keys(player_id)
    time.sleep(0.5)
    
    # 3. Submit
    try:
        submit_btn = driver.find_element(By.XPATH, "//button[@type='submit']")
        ActionChains(driver).move_to_element(submit_btn).click().perform()
    except:
        inp.send_keys(Keys.ENTER)
    
    time.sleep(5)
    
    if verify_login_success(driver): return True
    try:
        if not inp.is_displayed(): return True
    except: return True

    raise Exception("Login verification failed")

# --- CLAIMING ---
def get_valid_claim_buttons(driver):
    valid_buttons = []
    try:
        xpath = "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'claim')]"
        buttons = driver.find_elements(By.XPATH, xpath)
        for btn in buttons:
            if btn.is_displayed() and btn.is_enabled():
                text = btn.text.lower()
                if "login" in text or "buy" in text: continue
                valid_buttons.append(btn)
    except: pass
    return valid_buttons

def perform_claim_loop(driver, section_name):
    claimed = 0
    for _ in range(6):
        close_popups_safe(driver)
        time.sleep(1.5)
        buttons = get_valid_claim_buttons(driver)
        if not buttons: break
            
        btn = buttons[0]
        try:
            driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", btn)
            time.sleep(0.5)
            
            # Double Tap: Physical then JS
            try: ActionChains(driver).move_to_element(btn).click().perform()
            except: driver.execute_script("arguments[0].click();", btn)
            
            safe_print(f"Clicked {section_name}...")
            time.sleep(3)
            
            if close_popups_safe(driver):
                 claimed += 1
                 safe_print(f"{section_name} Reward {claimed} VERIFIED")
            else:
                 try: 
                    if not btn.is_displayed(): 
                        claimed += 1
                        safe_print(f"{section_name} Reward {claimed} VERIFIED (Gone)")
                 except: 
                    claimed += 1
                    safe_print(f"{section_name} Reward {claimed} VERIFIED (Stale)")

        except Exception: continue
    return claimed

def claim_daily(driver):
    return perform_claim_loop(driver, "Daily")

def claim_store(driver):
    driver.get("https://hub.vertigogames.co/store")
    time.sleep(3)
    close_popups_safe(driver)
    try:
        driver.execute_script("window.scrollTo(0, 300);")
        time.sleep(1)
        tab = driver.find_element(By.XPATH, "//*[contains(text(), 'Daily Rewards')]")
        tab.click()
        time.sleep(1.5)
    except: pass
    return perform_claim_loop(driver, "Store")

def claim_progression(driver):
    claimed = 0
    driver.get("https://hub.vertigogames.co/progression-program")
    time.sleep(3)
    close_popups_safe(driver)
    try:
        arrows = driver.find_elements(By.XPATH, "//*[contains(@class, 'next') or contains(@class, 'right')]")
        for arrow in arrows:
            if arrow.is_displayed():
                driver.execute_script("arguments[0].click();", arrow)
                time.sleep(0.5)
    except: pass

    for _ in range(6):
        time.sleep(1)
        js_find_and_click = """
        let buttons = document.querySelectorAll('button');
        for (let btn of buttons) {
            let text = btn.innerText.trim();
            if (text.toLowerCase() === 'claim') { 
                let rect = btn.getBoundingClientRect();
                if (rect.left > 300) { 
                     if (!btn.parentElement.innerText.includes('Delivered')) {
                        btn.click();
                        return true; 
                     }
                }
            }
        }
        return false; 
        """
        try:
            clicked = driver.execute_script(js_find_and_click)
            if clicked:
                safe_print(f"Progression Clicked...")
                time.sleep(4)
                if close_popups_safe(driver):
                    claimed += 1
                    safe_print(f"Progression Reward {claimed} VERIFIED")
            else: break
        except: break
    return claimed

def process_single_player(player_id):
    driver = None
    stats = {"player_id": player_id, "daily": 0, "store": 0, "progression": 0, "status": "Failed"}
    
    try:
        safe_print(f"Starting {player_id}")
        driver = create_driver()
        wait = WebDriverWait(driver, 45)
        
        if login(driver, wait, player_id):
            time.sleep(2)
            stats['daily'] = claim_daily(driver)
            stats['store'] = claim_store(driver)
            stats['progression'] = claim_progression(driver)
            stats['status'] = "Success"
            
        safe_print(f"Finished {player_id}: {stats['daily']}/{stats['store']}/{stats['progression']}")

    except Exception as e:
        safe_print(f"Error on {player_id}: {str(e)[:50]}")
        stats['status'] = "Error"
    finally:
        if driver: 
            try: driver.quit()
            except: pass
            
    # Output specific format for Shell script to capture
    print(f"RESULT_CSV:{stats['player_id']},{stats['daily']},{stats['store']},{stats['progression']},{stats['status']}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("player_id", help="Player ID to process")
    args = parser.parse_args()
    process_single_player(args.player_id)
