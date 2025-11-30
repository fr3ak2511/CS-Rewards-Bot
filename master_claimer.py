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
    driver.set_page_load_timeout(60)
    return driver

# --- HELPERS ---
def close_popups_safe(driver):
    try:
        driver.execute_script("""
            document.querySelectorAll('.modal, .popup, .dialog, button').forEach(btn => {
                let text = btn.innerText.toLowerCase();
                if(text.includes('close') || text === '×' || text === 'x' || text.includes('continue')) {
                    if(btn.offsetParent !== null) btn.click();
                }
            });
        """)
        popup_selectors = [
            "//button[contains(@class, 'close')]",
            "//*[name()='svg' and contains(@class, 'close')]/parent::button",
            "//button[normalize-space()='×']"
        ]
        for sel in popup_selectors:
            btns = driver.find_elements(By.XPATH, sel)
            for btn in btns:
                if btn.is_displayed():
                    driver.execute_script("arguments[0].click();", btn)
        
        actions = ActionChains(driver)
        safe_areas = [(30, 30), (1870, 30), (30, 1030)]
        for x, y in safe_areas:
            try:
                actions.move_by_offset(x - 960, y - 540).click().perform()
                actions.move_by_offset(-(x - 960), -(y - 540)).perform()
            except: pass
    except: pass

def accept_cookies(driver, wait):
    try:
        btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Accept') or contains(text(), 'Allow')]")))
        btn.click()
    except: pass

# --- LOGIN ---
def login(driver, wait, player_id):
    driver.get("https://hub.vertigogames.co/daily-rewards")
    time.sleep(3)
    accept_cookies(driver, wait)
    close_popups_safe(driver)

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
    
    try:
        inp = wait.until(EC.visibility_of_element_located((By.XPATH, "//input[@type='text' or contains(@placeholder, 'ID')]")))
        inp.clear()
        inp.send_keys(player_id)
        time.sleep(0.5)
        
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
            inp.send_keys(Keys.ENTER)
            
        wait.until(EC.url_contains("daily-rewards"))
    except Exception as e:
        driver.save_screenshot(f"login_err_{player_id}.png")
        raise Exception(f"Login sequence failed: {e}")

# --- CLAIMING ---

def claim_daily(driver, player_id):
    claimed = 0
    max_rounds = 5
    for round_num in range(max_rounds):
        close_popups_safe(driver)
        time.sleep(1)
        buttons = driver.find_elements(By.XPATH, "//button[contains(text(), 'Claim') and not(contains(text(), 'Buy'))]")
        visible_buttons = [b for b in buttons if b.is_displayed()]
        if not visible_buttons: break
        
        btn = visible_buttons[0]
        try:
            driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", btn)
            time.sleep(0.5)
            driver.execute_script("arguments[0].click();", btn)
            claimed += 1
            safe_print(f"[{player_id}] Daily Reward {claimed} claimed")
            time.sleep(2.5)
            close_popups_safe(driver)
        except: continue
    return claimed

def claim_store(driver, player_id):
    claimed = 0
    driver.get("https://hub.vertigogames.co/store")
    time.sleep(3)
    close_popups_safe(driver)
    
    # IMPROVED NAVIGATION: Scroll + Click Tab
    try:
        driver.execute_script("window.scrollTo(0, 300);") # Scroll slightly
        time.sleep(1)
        tab = driver.find_element(By.XPATH, "//*[contains(text(), 'Daily Rewards')]")
        driver.execute_script("arguments[0].click();", tab)
        time.sleep(1.5)
    except:
        # Fallback: Scroll further down if tab not found
        driver.execute_script("window.scrollTo(0, 600);")
        time.sleep(1)

    max_rounds = 5
    for round_num in range(max_rounds):
        close_popups_safe(driver)
        time.sleep(1)
        
        # Only target Store Daily Rewards (exclude purchases)
        buttons = driver.find_elements(By.XPATH, "//button[contains(text(), 'Claim') and not(contains(text(), 'Buy'))]")
        visible_buttons = [b for b in buttons if b.is_displayed()]
        
        if not visible_buttons: break
            
        btn = visible_buttons[0]
        try:
            driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", btn)
            time.sleep(0.5)
            driver.execute_script("arguments[0].click();", btn)
            claimed += 1
            safe_print(f"[{player_id}] Store Reward {claimed} claimed")
            time.sleep(2.5)
            close_popups_safe(driver)
        except: continue
        
    return claimed

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
                claimed += 1
                safe_print(f"[{player_id}] Progression Reward {claimed} claimed")
                time.sleep(2.5)
                close_popups_safe(driver)
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
        wait = WebDriverWait(driver, 30)
        
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
