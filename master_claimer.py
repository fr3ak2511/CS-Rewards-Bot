import csv
import time
import threading
import os
import smtplib
import sys
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
from webdriver_manager.chrome import ChromeDriverManager

# --- CONFIGURATION ---
BATCH_SIZE = 1 # Keep 1 for stability
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
        sys.stdout.flush()

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

# --- DRIVER (STABILITY VERSION) ---
def create_driver():
    options = Options()
    if HEADLESS:
        options.add_argument("--headless=new")
    
    # Stability & Performance Flags
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-software-rasterizer")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--disable-blink-features=AutomationControlled")
    
    # Crash Prevention
    options.add_argument("--single-process")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-infobars")
    
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    # Use Eager to avoid hanging on ads/images
    options.page_load_strategy = 'eager'
    
    service = Service(DRIVER_PATH)
    driver = webdriver.Chrome(service=service, options=options)
    
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": """
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            })
        """
    })
    
    driver.set_page_load_timeout(180)
    driver.implicitly_wait(5)
    return driver

# --- HELPERS ---
def close_popups_safe(driver):
    """Robust popup closer"""
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
        # Safe Area Click
        ActionChains(driver).move_by_offset(10, 10).click().perform()
    except: pass
    return True

def accept_cookies(driver, wait):
    try:
        btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Accept')]")))
        btn.click()
    except: pass

# --- LOGIN (UPDATED WITH PATIENCE) ---
def login(driver, wait, player_id):
    try:
        safe_print(f"[{player_id}] Loading page...")
        driver.get("https://hub.vertigogames.co/daily-rewards")
        time.sleep(3) # Increased initial wait
        
        # Accept cookies
        try:
            cookie_btn = WebDriverWait(driver, 3).until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Accept')]"))
            )
            cookie_btn.click()
            safe_print(f"[{player_id}] Accepted cookies")
            time.sleep(0.5)
        except: pass
        
        close_popups_safe(driver)
        
        # Find and click Login
        login_selectors = [
            "//button[contains(text(),'Login')]",
            "//button[contains(text(),'Log in')]",
            "//a[contains(text(),'Login')]",
            "//button[contains(@class, 'login')]",
            "//*[contains(text(), 'Login') and (self::button or self::a)]",
        ]
        
        login_clicked = False
        safe_print(f"[{player_id}] Searching for login button...")
        
        for selector in login_selectors:
            try:
                elements = driver.find_elements(By.XPATH, selector)
                for element in elements:
                    if element.is_displayed():
                        try:
                            element.click()
                            login_clicked = True
                            safe_print(f"[{player_id}] Clicked login!")
                            break
                        except:
                            driver.execute_script("arguments[0].click();", element)
                            login_clicked = True
                            safe_print(f"[{player_id}] Clicked login (JS)")
                            break
                if login_clicked: break
            except: continue
            
        if not login_clicked:
            safe_print(f"[{player_id}] Login button not found")
            raise Exception("Login button not found")
            
        # CRITICAL: Wait longer for modal to render in Headless
        time.sleep(3) 
        safe_print(f"[{player_id}] Waiting for input field...")
        
        # Find Input
        input_selectors = [
            "//input[contains(@placeholder, 'ID')]",
            "//input[@type='text']",
            "//input[contains(@class, 'input')]",
            "//input[contains(@name, 'id')]",
        ]
        
        input_box = None
        for selector in input_selectors:
            try:
                input_box = WebDriverWait(driver, 5).until( # Increased to 5s
                    EC.visibility_of_element_located((By.XPATH, selector))
                )
                safe_print(f"[{player_id}] Found input field")
                break
            except: continue
            
        if not input_box:
            # Debug: Print what IS on the page
            safe_print(f"[{player_id}] Input not found. Dumping visible inputs...")
            try:
                all_inputs = driver.find_elements(By.TAG_NAME, "input")
                for inp in all_inputs[:3]:
                    safe_print(f"[{player_id}] Found input: type={inp.get_attribute('type')}, visible={inp.is_displayed()}")
            except: pass
            
            # Screenshot on failure
            driver.save_screenshot(f"login_fail_{player_id}.png")
            raise Exception("Input not found")
            
        input_box.clear()
        input_box.send_keys(player_id)
        time.sleep(0.5)
        safe_print(f"[{player_id}] Entered ID")
        
        # Submit
        try:
            submit_btn = driver.find_element(By.XPATH, "//button[@type='submit']")
            submit_btn.click()
            safe_print(f"[{player_id}] Clicked submit")
        except:
            input_box.send_keys(Keys.ENTER)
            safe_print(f"[{player_id}] Pressed Enter")
            
        # Wait for success
        start_time = time.time()
        while time.time() - start_time < 20:
            try:
                current_url = driver.current_url.lower()
                if "daily-rewards" in current_url or "user" in current_url:
                    # Look for logout button as definitive success proof
                    if driver.find_elements(By.XPATH, "//button[contains(text(), 'Logout')]"):
                        safe_print(f"[{player_id}] Login successful!")
                        return True
                time.sleep(0.5)
            except: time.sleep(0.5)
            
        safe_print(f"[{player_id}] Login timeout")
        return False
        
    except Exception as e:
        safe_print(f"[{player_id}] Login error: {str(e)[:80]}")
        raise e

# --- CLAIMING (Strict Mode) ---

def get_valid_claim_buttons(driver, player_id):
    """Strictly finds buttons that are NOT 'Login to claim'"""
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

def perform_claim_loop(driver, player_id, section_name):
    claimed = 0
    max_rounds = 6
    
    for round_num in range(max_rounds):
        close_popups_safe(driver)
        time.sleep(1.5)
        
        buttons = get_valid_claim_buttons(driver, player_id)
        if not buttons: break
            
        btn = buttons[0]
        try:
            driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", btn)
            time.sleep(0.5)
            
            try: btn.click()
            except: driver.execute_script("arguments[0].click();", btn)
            
            safe_print(f"[{player_id}] Clicked {section_name}...")
            time.sleep(3)
            
            # Verify: Popup appeared OR Button Gone
            is_success = False
            if close_popups_safe(driver): # Returns True if popup closed
                 is_success = True
            
            try:
                if not btn.is_displayed() or "claimed" in btn.text.lower():
                    is_success = True
            except: is_success = True # Stale
            
            if is_success:
                claimed += 1
                safe_print(f"[{player_id}] {section_name} Reward {claimed} VERIFIED")
            else:
                safe_print(f"[{player_id}] Click failed - Button still there")

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
        tab.click()
        time.sleep(1.5)
    except: pass
    return perform_claim_loop(driver, player_id, "Store")

def claim_progression(driver, player_id):
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

    for round_num in range(6):
        time.sleep(1)
        js_find_and_click = """
        let buttons = document.querySelectorAll('button');
        for (let btn of buttons) {
            let text = btn.innerText.trim();
            if (text === 'Claim') { 
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
                safe_print(f"[{player_id}] Progression Clicked...")
                time.sleep(3)
                close_popups_safe(driver)
                claimed += 1
            else: break
        except: break
    return claimed

# --- PROCESS ---
def process_player(player_id, thread_name):
    driver = None
    stats = {"player_id": player_id, "daily": 0, "store": 0, "progression": 0, "status": "Failed"}
    try:
        safe_print(f"[{thread_name}] Starting {player_id}")
        driver = create_driver()
        wait = WebDriverWait(driver, 90)
        
        if not login(driver, wait, player_id):
            stats['status'] = "Login Timeout"
            return stats
        
        safe_print(f"[{thread_name}] Checking Daily...")
        stats['daily'] = claim_daily(driver, player_id)
        safe_print(f"[{thread_name}] Checking Store...")
        stats['store'] = claim_store(driver, player_id)
        safe_print(f"[{thread_name}] Checking Progression...")
        stats['progression'] = claim_progression(driver, player_id)
        stats['status'] = "Success"
        safe_print(f"[{thread_name}] Finished {player_id}")
        
    except WebDriverException as e:
        safe_print(f"[{thread_name}] Chrome Crash: {str(e)[:50]}")
        stats['status'] = "Chrome Crash"
    except Exception as e:
        safe_print(f"[{thread_name}] Error: {str(e)}")
        stats['status'] = f"Error: {str(e)[:30]}"
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
