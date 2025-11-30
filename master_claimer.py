import csv
import time
import threading
import os
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from concurrent.futures import ThreadPoolExecutor, as_completed

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.desired_capabilities import DesiredCapabilities
from webdriver_manager.chrome import ChromeDriverManager

# --- CONFIGURATION ---
BATCH_SIZE = 1 # Keep 1 to prevent OOM
HEADLESS = True

# --- GLOBAL DRIVER ---
try:
    DRIVER_PATH = ChromeDriverManager().install()
except:
    DRIVER_PATH = "/usr/bin/chromedriver"

print_lock = threading.Lock()
def safe_print(msg):
    with print_lock:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

# --- EMAIL ---
def send_summary_email(summary_data):
    sender_email = os.environ.get("SENDER_EMAIL")
    recipient_email = os.environ.get("RECIPIENT_EMAIL")
    gmail_password = os.environ.get("GMAIL_APP_PASSWORD")

    if not all([sender_email, recipient_email, gmail_password]):
        safe_print("⚠️ Secrets missing. Email will NOT be sent.")
        return

    subject = f"Hub Rewards Summary - {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    
    html_body = f"""
    <h2>Hub Rewards Automation Report</h2>
    <p><b>Run Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
    <table border="1" cellpadding="5" cellspacing="0" style="border-collapse: collapse;">
        <tr style="background-color: #f2f2f2;">
            <th>Player ID</th>
            <th>Daily</th>
            <th>Store</th>
            <th>Progression</th>
            <th>Status</th>
        </tr>
    """
    
    for row in summary_data:
        color = "green" if row['status'] == 'Success' else "red"
        html_body += f"""
        <tr>
            <td>{row['player_id']}</td>
            <td>{row['daily']}</td>
            <td>{row['store']}</td>
            <td>{row['progression']}</td>
            <td style="color: {color};">{row['status']}</td>
        </tr>
        """
    html_body += "</table>"

    msg = MIMEMultipart()
    msg['From'] = sender_email
    msg['To'] = recipient_email
    msg['Subject'] = subject
    msg.attach(MIMEText(html_body, 'html'))

    try:
        server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        server.login(sender_email, gmail_password)
        server.sendmail(sender_email, recipient_email, msg.as_string())
        server.quit()
        safe_print("✅ Summary email sent successfully.")
    except Exception as e:
        safe_print(f"❌ Failed to send email: {str(e)}")

# --- DRIVER (STABILITY FIX) ---
def create_driver():
    options = Options()
    if HEADLESS:
        options.add_argument("--headless=new")
    
    # CRITICAL: Core stability flags for CI
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")  # Fixes shared memory crash
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-software-rasterizer")
    options.add_argument("--disable-setuid-sandbox")
    
    # Window and display
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")
    
    # Anti-detection
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-extensions")
    
    # Performance & Stability
    options.add_argument("--disable-logging")
    options.add_argument("--disable-web-security")
    options.add_argument("--disable-default-apps")
    options.add_argument("--disable-features=VizDisplayCompositor")
    options.add_argument("--disable-background-timer-throttling")
    options.add_argument("--disable-renderer-backgrounding")
    options.add_argument("--disable-backgrounding-occluded-windows")
    
    # NEW: Additional stability flags for CI
    options.add_argument("--disable-dev-tools")
    options.add_argument("--disable-crash-reporter")
    options.add_argument("--disable-in-process-stack-traces")
    options.add_argument("--disable-breakpad")
    options.add_argument("--disable-features=IsolateOrigins,site-per-process")
    options.add_argument("--single-process")  # Run in single process mode
    options.add_argument("--no-zygote")  # No zygote process
    
    # Block images for faster load
    prefs = {
        "profile.default_content_setting_values": {
            "images": 2,
            "notifications": 2,
            "popups": 2,
        },
        "profile.default_content_settings.popups": 0,
        "profile.managed_default_content_settings.popups": 0,
    }
    options.add_experimental_option("prefs", prefs)
    options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    options.add_experimental_option("useAutomationExtension", False)
    
    # Page load strategy
    caps = DesiredCapabilities.CHROME.copy()
    caps["pageLoadStrategy"] = "eager"
    for k, v in caps.items():
        options.set_capability(k, v)
    
    service = Service(DRIVER_PATH)
    driver = webdriver.Chrome(service=service, options=options)
    
    # Anti-detection CDP
    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        })
    except: pass
    
    # Shorter timeouts
    driver.set_page_load_timeout(45)
    driver.set_script_timeout(45)
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

# --- LOGIN ---
def verify_login_success(driver):
    try:
        # Simple check for logged-in state
        return "daily-rewards" in driver.current_url
    except: return False

def login(driver, wait, player_id):
    driver.get("https://hub.vertigogames.co/daily-rewards")
    time.sleep(2)
    accept_cookies(driver, wait)
    close_popups_safe(driver)

    # 1. Click Login
    login_selectors = ["//button[contains(text(),'Login')]", "//a[contains(text(),'Login')]"]
    for selector in login_selectors:
        try:
            btns = driver.find_elements(By.XPATH, selector)
            for btn in btns:
                if btn.is_displayed():
                    driver.execute_script("arguments[0].click();", btn)
                    time.sleep(2)
                    break
        except: continue
    
    # 2. Input
    inp = None
    try:
        inp = WebDriverWait(driver, 10).until(
            EC.visibility_of_element_located((By.XPATH, "//input[@type='text' or contains(@placeholder, 'ID')]"))
        )
    except:
        # Fallback JS click
        driver.execute_script("document.querySelector('button.login')?.click()")
        time.sleep(2)
        try:
            inp = driver.find_element(By.XPATH, "//input[@type='text']")
        except: pass

    if not inp:
        driver.save_screenshot(f"login_fail_{player_id}.png")
        raise Exception("Input not found")

    inp.clear()
    inp.send_keys(player_id)
    time.sleep(0.5)
    
    # 3. Submit
    try:
        submit_btn = driver.find_element(By.XPATH, "//button[@type='submit']")
        driver.execute_script("arguments[0].click();", submit_btn)
    except:
        inp.send_keys(Keys.ENTER)
    
    time.sleep(3)
    
    if not verify_login_success(driver):
        driver.save_screenshot(f"login_fail_verify_{player_id}.png")
        raise Exception("Login verification failed")
        
    return True

# --- CLAIMING ---
def get_valid_claim_buttons(driver, player_id):
    valid_buttons = []
    try:
        # Find all buttons
        all_buttons = driver.find_elements(By.TAG_NAME, "button")
        for btn in all_buttons:
            if btn.is_displayed() and btn.is_enabled():
                text = btn.text.strip().lower()
                # Strict Match: "Claim" only
                if text == "claim":
                    valid_buttons.append(btn)
    except: pass
    return valid_buttons

def perform_claim_loop(driver, player_id, section_name):
    claimed = 0
    max_rounds = 6
    
    for round_num in range(max_rounds):
        close_popups_safe(driver)
        time.sleep(1)
        
        buttons = get_valid_claim_buttons(driver, player_id)
        if not buttons: break
            
        btn = buttons[0]
        try:
            driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", btn)
            time.sleep(0.5)
            
            # JS Click
            driver.execute_script("arguments[0].click();", btn)
            safe_print(f"[{player_id}] Clicked {section_name}...")
            time.sleep(3)
            
            # Verification: Did button disappear?
            try:
                if not btn.is_displayed():
                    claimed += 1
                    safe_print(f"[{player_id}] {section_name} Reward {claimed} VERIFIED")
            except:
                claimed += 1 # Stale
                safe_print(f"[{player_id}] {section_name} Reward {claimed} VERIFIED")
                
            close_popups_safe(driver)

        except Exception: continue
            
    return claimed

def claim_daily(driver, player_id):
    return perform_claim_loop(driver, player_id, "Daily")

def claim_store(driver, player_id):
    driver.get("https://hub.vertigogames.co/store")
    time.sleep(3)
    close_popups_safe(driver)
    
    try:
        driver.execute_script("window.scrollTo(0, 300);")
        time.sleep(1)
        tab = driver.find_element(By.XPATH, "//*[contains(text(), 'Daily Rewards')]")
        driver.execute_script("arguments[0].click();", tab)
        time.sleep(1.5)
    except: pass

    return perform_claim_loop(driver, player_id, "Store")

def claim_progression(driver, player_id):
    claimed = 0
    driver.get("https://hub.vertigogames.co/progression-program")
    time.sleep(3)
    close_popups_safe(driver)
    
    # JS Filter for Progression
    for round_num in range(6):
        time.sleep(1)
        js_find_and_click = """
        let buttons = document.querySelectorAll('button');
        for (let btn of buttons) {
            let text = btn.innerText.trim();
            if (text === 'Claim') { 
                let rect = btn.getBoundingClientRect();
                if (rect.left > 300) { 
                     btn.click();
                     return true;
                }
            }
        }
        return false; 
        """
        try:
            clicked = driver.execute_script(js_find_and_click)
            if clicked:
                safe_print(f"[{player_id}] Progression Clicked...")
                time.sleep(3)
                close_popups_safe(driver)
                claimed += 1
            else:
                break
        except: break
    return claimed

# --- PROCESS ---
def process_player(player_id, thread_name):
    driver = None
    stats = {"player_id": player_id, "daily": 0, "store": 0, "progression": 0, "status": "Failed"}
    
    try:
        safe_print(f"[{thread_name}] Starting {player_id}")
        driver = create_driver()
        wait = WebDriverWait(driver, 45)
        
        try:
            if login(driver, wait, player_id):
                time.sleep(2)
                safe_print(f"[{thread_name}] Checking Daily...")
                stats['daily'] = claim_daily(driver, player_id)
                
                safe_print(f"[{thread_name}] Checking Store...")
                stats['store'] = claim_store(driver, player_id)
                
                safe_print(f"[{thread_name}] Checking Progression...")
                stats['progression'] = claim_progression(driver, player_id)
                
                stats['status'] = "Success"
        except Exception as e:
            safe_print(f"[{player_id}] Login Failed: {e}")
            stats['status'] = "Login Failed"

        safe_print(f"[{thread_name}] Finished {player_id}")

    except WebDriverException as e:
        safe_print(f"[{thread_name}] Chrome Crash: {str(e)[:50]}")
        stats['status'] = "Chrome Crash"
    except Exception as e:
        safe_print(f"[{thread_name}] Error: {str(e)}")
    finally:
        if driver: 
            try: driver.quit()
            except: pass
    return stats

def main():
    players = []
    try:
        with open('players.csv', 'r') as f:
            lines = f.readlines()
            for line in lines:
                clean_line = line.strip().replace(',', '')
                if len(clean_line) > 4: 
                    players.append(clean_line)
    except FileNotFoundError:
        safe_print("❌ players.csv not found!")
        return

    safe_print(f"Loaded {len(players)} players.")
    results = []
    
    for i in range(0, len(players), BATCH_SIZE):
        batch = players[i:i + BATCH_SIZE]
        with ThreadPoolExecutor(max_workers=BATCH_SIZE) as executor:
            futures = {executor.submit(process_player, pid, f"Thread-{i+idx}"): pid for idx, pid in enumerate(batch)}
            for future in as_completed(futures):
                results.append(future.result())
        time.sleep(3)

    send_summary_email(results)

if __name__ == "__main__":
    main()
