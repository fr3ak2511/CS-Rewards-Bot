import csv
import time
import threading
import os
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from concurrent.futures import ThreadPoolExecutor, as_completed

# Selenium Imports
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# --- CONFIGURATION ---
BATCH_SIZE = 2
HEADLESS = True

# --- GLOBAL DRIVER INSTALLATION (Fixes Zip Error) ---
try:
    DRIVER_PATH = ChromeDriverManager().install()
except:
    DRIVER_PATH = "/usr/bin/chromedriver"

# Thread-safe printing
print_lock = threading.Lock()
def safe_print(msg):
    with print_lock:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

# --- EMAIL FUNCTIONS ---
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

# --- DRIVER SETUP ---
def create_driver():
    options = Options()
    if HEADLESS:
        options.add_argument("--headless=new")
    
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    service = Service(DRIVER_PATH)
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(45)
    return driver

# --- MANUAL SCRIPT HELPERS (PORTED) ---

def close_popups_safe(driver):
    """Ported from manual script: Tries buttons, then safe area clicks."""
    try:
        # 1. Close Buttons
        popup_selectors = [
            "//button[contains(@class, 'close')]",
            "//button[contains(text(), 'Close')]",
            "//*[name()='svg' and contains(@class, 'close')]/parent::button",
            "//div[contains(@class, 'modal')]//button",
            "//button[normalize-space()='×']"
        ]
        for selector in popup_selectors:
            elements = driver.find_elements(By.XPATH, selector)
            for btn in elements:
                if btn.is_displayed():
                    driver.execute_script("arguments[0].click();", btn)
                    time.sleep(0.2)
        
        # 2. Safe Area Clicks (The "Magic" Fix)
        width = 1920
        height = 1080
        safe_areas = [(30, 30), (width - 50, 30), (30, height - 50)]
        
        actions = ActionChains(driver)
        for x, y in safe_areas:
            try:
                actions.move_by_offset(x - width//2, y - height//2).click().perform()
                actions.move_by_offset(-(x - width//2), -(y - height//2)).perform() # Reset
            except: pass
    except: pass

def accept_cookies(driver, wait):
    try:
        btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Accept') or contains(text(), 'Allow')]")))
        btn.click()
    except: pass

# --- CLAIM LOGIC (PORTED) ---

def login(driver, wait, player_id, thread_name):
    # Ported Login Logic: Try Selectors -> Type ID -> Submit
    driver.get("https://hub.vertigogames.co/daily-rewards")
    time.sleep(3)
    accept_cookies(driver, wait)
    close_popups_safe(driver)

    # 1. Click Login Button
    login_selectors = [
        "//button[contains(text(),'Login') or contains(text(),'Sign in')]",
        "//a[contains(text(),'Login') or contains(text(),'Sign in')]",
        "//*[contains(text(), 'Login')]"
    ]
    
    clicked_login = False
    for selector in login_selectors:
        try:
            btns = driver.find_elements(By.XPATH, selector)
            for btn in btns:
                if btn.is_displayed():
                    btn.click()
                    clicked_login = True
                    break
            if clicked_login: break
        except: continue
    
    # 2. Find Input (Wait up to 5s)
    try:
        inp = wait.until(EC.visibility_of_element_located((By.XPATH, "//input[@type='text' or contains(@placeholder, 'ID')]")))
        inp.clear()
        inp.send_keys(player_id)
        time.sleep(0.5)
        
        # 3. Submit
        submit_selectors = ["//button[@type='submit']", "//button[contains(text(), 'Login')]", "//button[contains(text(), 'LOGIN')]"]
        submitted = False
        for sel in submit_selectors:
            try:
                btns = driver.find_elements(By.XPATH, sel)
                for btn in btns:
                    if btn.is_displayed():
                        btn.click()
                        submitted = True
                        break
                if submitted: break
            except: continue
            
        if not submitted:
            inp.send_keys(Keys.ENTER) # Manual script fallback
            
        wait.until(EC.url_contains("daily-rewards"))
    except Exception as e:
        driver.save_screenshot(f"login_fail_{player_id}.png")
        raise Exception(f"Login sequence failed: {e}")

def claim_daily_page(driver, wait):
    count = 0
    # Use JS to find buttons exactly like manual script
    buttons = driver.find_elements(By.XPATH, "//button[contains(text(), 'Claim')]")
    for btn in buttons:
        try:
            if btn.is_displayed() and "buy" not in btn.text.lower():
                driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", btn)
                time.sleep(0.5)
                try:
                    btn.click()
                except:
                    driver.execute_script("arguments[0].click();", btn)
                count += 1
                time.sleep(1)
                close_popups_safe(driver)
        except: continue
    return count

def claim_store_page(driver, wait):
    count = 0
    driver.get("https://hub.vertigogames.co/store")
    time.sleep(3)
    close_popups_safe(driver)
    
    # Navigate to Daily Section
    try:
        tab = driver.find_element(By.XPATH, "//*[contains(text(), 'Daily Rewards')]")
        driver.execute_script("arguments[0].click();", tab)
        time.sleep(1.5)
    except: pass
    
    buttons = driver.find_elements(By.XPATH, "//button[contains(text(), 'Claim')]")
    for btn in buttons:
        try:
            if btn.is_displayed() and "buy" not in btn.text.lower():
                driver.execute_script("arguments[0].click();", btn)
                count += 1
                time.sleep(1)
                close_popups_safe(driver)
        except: continue
    return count

def claim_progression_page(driver, wait):
    count = 0
    driver.get("https://hub.vertigogames.co/progression-program")
    time.sleep(3)
    close_popups_safe(driver)
    
    # Ported: Click Right Arrow/Scroll Button
    try:
        scroll_selectors = [
            "//button[contains(@class, 'right')]",
            "//button[contains(@class, 'next')]",
            "//*[name()='svg' and contains(@class, 'right')]/parent::button"
        ]
        for sel in scroll_selectors:
            btns = driver.find_elements(By.XPATH, sel)
            for btn in btns:
                if btn.is_displayed():
                    driver.execute_script("arguments[0].click();", btn)
                    time.sleep(0.5)
    except: pass
    
    # Ported: JS Selection for claims
    js_script = """
    var buttons = document.querySelectorAll('button');
    var claimed = 0;
    buttons.forEach(btn => {
        if(btn.innerText.trim() === 'Claim') {
            // Only click if it looks like a card (check parent)
            if(btn.offsetParent !== null) {
                btn.click();
                claimed++;
            }
        }
    });
    return claimed;
    """
    try:
        count = driver.execute_script(js_script)
    except: pass
    
    return count

# --- MAIN PROCESS ---

def process_player(player_id, thread_name):
    driver = None
    stats = {"player_id": player_id, "daily": 0, "store": 0, "progression": 0, "status": "Failed"}
    
    try:
        safe_print(f"[{thread_name}] Starting {player_id}")
        driver = create_driver()
        wait = WebDriverWait(driver, 25)
        
        # 1. Login
        login(driver, wait, player_id, thread_name)
        time.sleep(2)
        
        # 2. Daily
        safe_print(f"[{thread_name}] Daily...")
        stats['daily'] = claim_daily_page(driver, wait)
        
        # 3. Store
        safe_print(f"[{thread_name}] Store...")
        stats['store'] = claim_store_page(driver, wait)
        
        # 4. Progression
        safe_print(f"[{thread_name}] Progression...")
        stats['progression'] = claim_progression_page(driver, wait)
        
        stats['status'] = "Success"
        safe_print(f"[{thread_name}] Finished {player_id}")

    except Exception as e:
        safe_print(f"[{thread_name}] Error on {player_id}: {str(e)}")
        stats['status'] = f"Error: {str(e)[:30]}"
    finally:
        if driver: driver.quit()
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
        time.sleep(2)

    send_summary_email(results)

if __name__ == "__main__":
    main()
