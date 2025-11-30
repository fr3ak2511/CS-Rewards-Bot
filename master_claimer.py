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

# --- DRIVER ---
def create_driver():
    options = Options()
    if HEADLESS:
        options.add_argument("--headless=new")
    
    # STABILITY FLAGS (Prevent Crashes in CI)
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-software-rasterizer") # Fix graphics crash
    options.add_argument("--single-process") # Fix memory crash
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-extensions")
    
    # Anti-Detection
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

    # Strategy: Don't wait for full load (prevents timeout crashes)
    options.page_load_strategy = 'none'
    
    service = Service(DRIVER_PATH)
    driver = webdriver.Chrome(service=service, options=options)
    
    # CDP Anti-Detection
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": """
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            })
        """
    })
    
    # Increased Timeouts
    driver.set_page_load_timeout(180)
    driver.implicitly_wait(10)
    
    return driver

# --- HELPERS ---
def wait_for_page_ready(driver):
    """Manually wait for page load since strategy is 'none'"""
    try:
        WebDriverWait(driver, 30).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
        time.sleep(3) # Extra buffer
    except: pass

def handle_post_claim_popup(driver):
    """Checks for popup, closes it, returns True if found & closed."""
    popup_found = False
    
    # 1. Buttons
    confirm_selectors = [
        "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'continue')]",
        "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'close')]",
        "//button[text()='×']",
        "//button[text()='X']"
    ]
    
    for sel in confirm_selectors:
        try:
            btns = driver.find_elements(By.XPATH, sel)
            for btn in btns:
                if btn.is_displayed():
                    driver.execute_script("arguments[0].click();", btn)
                    time.sleep(0.5)
                    popup_found = True
        except: pass
    
    if popup_found: return True

    # 2. Fallback: Safe Area Click
    try:
        modals = driver.find_elements(By.XPATH, "//div[contains(@class, 'modal') or contains(@class, 'popup') or contains(@class, 'dialog')]")
        if any(m.is_displayed() for m in modals):
            popup_found = True
            actions = ActionChains(driver)
            actions.move_by_offset(30, 30).click().perform()
            actions.move_by_offset(-30, -30).perform()
            time.sleep(0.5)
    except: pass
    
    return popup_found

def close_overlays(driver):
    handle_post_claim_popup(driver)

def accept_cookies(driver, wait):
    try:
        xpath = "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'accept') or contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'allow')]"
        btn = wait.until(EC.element_to_be_clickable((By.XPATH, xpath)))
        btn.click()
    except: pass

# --- LOGIN ---
def login(driver, wait, player_id):
    driver.get("https://hub.vertigogames.co/daily-rewards")
    wait_for_page_ready(driver) # Critical due to 'none' strategy
    
    accept_cookies(driver, wait)
    close_overlays(driver)

    # 1. Click Login (Try JS first)
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
                    time.sleep(3)
                    break
        except: continue
    
    # 2. Input
    inp = None
    input_selectors = ["//input[@type='text']", "//input[contains(@placeholder, 'ID')]"]
    for _ in range(10): # Increased retry
        for sel in input_selectors:
            inputs = driver.find_elements(By.XPATH, sel)
            visible = [i for i in inputs if i.is_displayed()]
            if visible:
                inp = visible[0]
                break
        if inp: break
        time.sleep(1)
        
    if not inp: 
        driver.save_screenshot(f"login_fail_{player_id}.png")
        raise Exception("Input not found")

    inp.clear()
    inp.send_keys(player_id)
    time.sleep(0.5)
    
    # 3. Submit
    try:
        submit_btn = driver.find_element(By.XPATH, "//button[@type='submit'] | //button[contains(text(), 'LOGIN')]")
        driver.execute_script("arguments[0].click();", submit_btn)
    except:
        inp.send_keys(Keys.ENTER)
        
    wait.until(EC.url_contains("daily-rewards"))

# --- CLAIMING ---

def perform_claim_loop(driver, player_id, section_name):
    claimed = 0
    max_rounds = 6
    
    for round_num in range(max_rounds):
        close_overlays(driver)
        time.sleep(2)
        
        claim_xpath = "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'claim') and not(contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'buy'))]"
        
        buttons = driver.find_elements(By.XPATH, claim_xpath)
        visible_buttons = [b for b in buttons if b.is_displayed()]
        
        if not visible_buttons:
            break
            
        btn = visible_buttons[0]
        try:
            driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", btn)
            time.sleep(0.5)
            
            # Double-Tap Click Strategy (JS then Action)
            driver.execute_script("arguments[0].click();", btn)
            safe_print(f"[{player_id}] Clicked {section_name} (JS)...")
            time.sleep(3)
            
            if handle_post_claim_popup(driver):
                claimed += 1
                safe_print(f"[{player_id}] {section_name} Reward {claimed} VERIFIED (JS)")
                continue

            safe_print(f"[{player_id}] No popup, trying Physical Click...")
            ActionChains(driver).move_to_element(btn).click().perform()
            time.sleep(3)
            
            if handle_post_claim_popup(driver):
                claimed += 1
                safe_print(f"[{player_id}] {section_name} Reward {claimed} VERIFIED (Physical)")
            else:
                safe_print(f"[{player_id}] Click failed - No popup appeared")
                if round_num == 0: driver.save_screenshot(f"fail_{section_name}_{player_id}.png")

        except Exception: continue
            
    return claimed

def claim_daily(driver, player_id):
    return perform_claim_loop(driver, player_id, "Daily")

def claim_store(driver, player_id):
    driver.get("https://hub.vertigogames.co/store")
    wait_for_page_ready(driver)
    time.sleep(3)
    close_overlays(driver)
    
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
    close_overlays(driver)
    
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
                time.sleep(3)
                if handle_post_claim_popup(driver):
                    claimed += 1
                    safe_print(f"[{player_id}] Progression Reward {claimed} VERIFIED")
                else: pass
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
        wait = WebDriverWait(driver, 90) # Increased Timeout
        
        login(driver, wait, player_id)
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
        safe_print(f"[{thread_name}] Chrome Crash/Timeout on {player_id}: {str(e)[:50]}")
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
        time.sleep(3) # Rest between batches

    send_summary_email(results)

if __name__ == "__main__":
    main()
