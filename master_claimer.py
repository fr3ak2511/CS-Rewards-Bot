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
BATCH_SIZE = 2
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

# --- DRIVER (Updated for Stability) ---
def create_driver():
    options = Options()
    if HEADLESS:
        options.add_argument("--headless=new")
    
    # Critical Stability Flags for GitHub Actions
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--disable-blink-features=AutomationControlled")
    
    # Prevent Crashes
    options.add_argument("--disable-software-rasterizer")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-infobars")
    options.add_argument("--single-process") 
    options.add_argument("--disable-background-networking")
    
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    # KEY CHANGE: Don't wait for full page load (Prevents 60s timeout crashes)
    options.page_load_strategy = 'none'
    
    service = Service(DRIVER_PATH)
    driver = webdriver.Chrome(service=service, options=options)
    
    # Anti-Detection CDP
    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": """
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                })
            """
        })
    except: pass
    
    # Massive timeouts to prevent premature aborts
    driver.set_page_load_timeout(180)
    driver.set_script_timeout(30)
    
    return driver

# --- HELPERS ---

def wait_for_page_ready(driver, timeout=30):
    """Manual wait since we used strategy='none'"""
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
        time.sleep(3) # Extra buffer for React/JS hydration
        return True
    except:
        return False

def zap_overlays(driver):
    try:
        driver.execute_script("""
            var selectors = ['header', 'footer', '#onetrust-banner-sdk', '.cookie-banner', '.modal-backdrop'];
            selectors.forEach(sel => {
                document.querySelectorAll(sel).forEach(el => el.remove());
            });
        """)
    except: pass

def close_popups_safe(driver):
    for _ in range(3):
        try:
            # ESC Key
            ActionChains(driver).send_keys(Keys.ESCAPE).perform()
            time.sleep(0.5)
            
            # Safe Area Clicks
            width = 1920
            height = 1080
            safe_coords = [(10, 10), (width-50, 10), (width//2, 10)]
            for x, y in safe_coords:
                try:
                    driver.execute_script(f"document.elementFromPoint({x}, {y}).click();")
                except: pass
            
            # JS Close Buttons
            driver.execute_script("""
                document.querySelectorAll('button').forEach(btn => {
                    let text = btn.innerText.toLowerCase();
                    if(text.includes('close') || text === '×' || text === 'x' || text.includes('continue')) {
                        if(btn.offsetParent !== null) btn.click();
                    }
                });
            """)
            time.sleep(0.5)
        except: pass

def accept_cookies(driver):
    try:
        # Broad cookie selector
        cookie_selectors = [
            "//button[contains(translate(., 'ACCEPT', 'accept'), 'accept')]",
            "//button[contains(@class, 'accept')]"
        ]
        for sel in cookie_selectors:
            try:
                driver.find_element(By.XPATH, sel).click()
                time.sleep(1)
                return
            except: continue
    except: pass

# --- ROBUST LOGIN (From Analysis) ---
def login(driver, wait, player_id):
    safe_print(f"[{player_id}] Navigating to daily-rewards...")
    driver.get("https://hub.vertigogames.co/daily-rewards")
    
    # Wait for page manually
    wait_for_page_ready(driver, 45)
    driver.save_screenshot(f"debug_01_login_load_{player_id}.png")
    
    accept_cookies(driver)
    close_popups_safe(driver)
    zap_overlays(driver)

    # STRATEGY 1: Click Login Button
    login_clicked = False
    login_selectors = [
        "//button[contains(translate(text(), 'LOGIN', 'login'), 'login')]",
        "//a[contains(translate(text(), 'LOGIN', 'login'), 'login')]",
        "//button[contains(@class, 'login')]"
    ]
    
    for selector in login_selectors:
        try:
            elements = driver.find_elements(By.XPATH, selector)
            for btn in elements:
                if btn.is_displayed():
                    driver.execute_script("arguments[0].click();", btn)
                    safe_print(f"[{player_id}] Clicked Login button")
                    login_clicked = True
                    time.sleep(3)
                    break
            if login_clicked: break
        except: continue
        
    driver.save_screenshot(f"debug_02_after_click_{player_id}.png")

    # STRATEGY 2: Find Input (Aggressive)
    inp = None
    # Wait loop for input to appear
    for i in range(10):
        input_selectors = [
            "//input[@type='text']",
            "//input[contains(@placeholder, 'ID')]",
            "//div[contains(@class, 'modal')]//input",
            "//form//input"
        ]
        for sel in input_selectors:
            try:
                inputs = driver.find_elements(By.XPATH, sel)
                visible = [x for x in inputs if x.is_displayed()]
                if visible:
                    inp = visible[0]
                    break
            except: continue
        if inp: break
        time.sleep(1)

    if not inp:
        # DUMP HTML if fails
        with open(f"source_{player_id}.html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        driver.save_screenshot(f"debug_03_no_input_{player_id}.png")
        raise Exception("Input field not found")

    safe_print(f"[{player_id}] Found input, entering ID...")
    try:
        inp.clear()
        inp.send_keys(player_id)
        time.sleep(1)
        
        # STRATEGY 3: Submit
        try:
            submit_btn = driver.find_element(By.XPATH, "//button[@type='submit'] | //button[contains(text(), 'LOGIN')]")
            driver.execute_script("arguments[0].click();", submit_btn)
        except:
            inp.send_keys(Keys.ENTER)
        
        # Wait for redirect
        time.sleep(3)
        if "daily-rewards" not in driver.current_url and len(driver.find_elements(By.XPATH, "//input")) > 0:
             raise Exception("Login submitted but still on input page")
             
    except Exception as e:
        driver.save_screenshot(f"debug_04_submit_fail_{player_id}.png")
        raise e
    
    safe_print(f"[{player_id}] Login Success")

# --- CLAIMING (Iterative) ---
def perform_claim_loop(driver, player_id, section_name):
    claimed = 0
    max_rounds = 6
    
    # DEBUG: Save initial state of section
    driver.save_screenshot(f"{section_name}_start_{player_id}.png")

    for round_num in range(max_rounds):
        zap_overlays(driver)
        close_popups_safe(driver)
        time.sleep(1)
        
        # Case-insensitive XPath
        claim_xpath = "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'claim') and not(contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'buy'))]"
        
        buttons = driver.find_elements(By.XPATH, claim_xpath)
        visible_buttons = [b for b in buttons if b.is_displayed()]
        
        if not visible_buttons:
            break
            
        btn = visible_buttons[0]
        try:
            driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", btn)
            time.sleep(0.5)
            
            # Double Tap
            driver.execute_script("arguments[0].click();", btn)
            safe_print(f"[{player_id}] Clicked {section_name}...")
            time.sleep(4)
            
            # Verification
            modals = driver.find_elements(By.XPATH, "//div[contains(@class, 'modal') or contains(@class, 'popup')]")
            visible_modals = [m for m in modals if m.is_displayed()]
            
            is_stale = False
            try:
                if not btn.is_displayed(): is_stale = True
            except: is_stale = True
            
            if visible_modals or is_stale:
                claimed += 1
                safe_print(f"[{player_id}] {section_name} Reward {claimed} VERIFIED")
            else:
                safe_print(f"[{player_id}] Click failed - No popup")
            
            close_popups_safe(driver)
            
        except Exception as e:
            safe_print(f"[{player_id}] Click error: {e}")
            continue
            
    return claimed

def claim_daily(driver, player_id):
    return perform_claim_loop(driver, player_id, "Daily")

def claim_store(driver, player_id):
    driver.get("https://hub.vertigogames.co/store")
    wait_for_page_ready(driver)
    time.sleep(3)
    zap_overlays(driver)
    
    try:
        driver.execute_script("window.scrollTo(0, 300);")
        time.sleep(1)
        tab = driver.find_element(By.XPATH, "//*[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'daily rewards')]")
        driver.execute_script("arguments[0].click();", tab)
        time.sleep(1.5)
    except:
        driver.execute_script("window.scrollTo(0, 600);")
        time.sleep(1)

    return perform_claim_loop(driver, player_id, "Store")

def claim_progression(driver, player_id):
    claimed = 0
    driver.get("https://hub.vertigogames.co/progression-program")
    wait_for_page_ready(driver)
    time.sleep(3)
    zap_overlays(driver)
    
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
            let text = btn.innerText.trim().toLowerCase();
            if (text.includes('claim')) {
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
                time.sleep(4)
                modals = driver.find_elements(By.XPATH, "//div[contains(@class, 'modal') or contains(@class, 'popup')]")
                if [m for m in modals if m.is_displayed()]:
                    claimed += 1
                    safe_print(f"[{player_id}] Progression Reward {claimed} VERIFIED")
                close_popups_safe(driver)
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
        
        if not login(driver, None, player_id): # Helper waits inside
             stats['status'] = "Login Failed"
             return stats
        
        time.sleep(2)
        safe_print(f"[{thread_name}] Checking Daily...")
        stats['daily'] = claim_daily(driver, player_id)
        
        safe_print(f"[{thread_name}] Checking Store...")
        stats['store'] = claim_store(driver, player_id)
        
        safe_print(f"[{thread_name}] Checking Progression...")
        stats['progression'] = claim_progression(driver, player_id)
        
        stats['status'] = "Success"
        safe_print(f"[{thread_name}] Finished {player_id}")

    except WebDriverException as e:
        safe_print(f"[{thread_name}] Chrome Crash on {player_id}: {str(e)[:100]}")
        stats['status'] = "Chrome Crash"
    except Exception as e:
        safe_print(f"[{thread_name}] Error on {player_id}: {str(e)}")
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
