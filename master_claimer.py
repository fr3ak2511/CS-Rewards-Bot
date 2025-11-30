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
def force_click(driver, element):
    """Executes a JavaScript click directly on the element"""
    try:
        driver.execute_script("arguments[0].click();", element)
        return True
    except:
        return False

def close_overlays(driver):
    # Aggressive JS closer for any element with 'close' or 'x' text
    try:
        driver.execute_script("""
            document.querySelectorAll('button, div[role="button"]').forEach(btn => {
                if(btn.innerText.toLowerCase().includes('close') || btn.innerText === '×' || btn.innerText === 'X') {
                    btn.click();
                }
            });
        """)
        # Safe area click
        ActionChains(driver).move_by_offset(10, 10).click().perform()
        ActionChains(driver).move_by_offset(-10, -10).perform()
    except: pass

def login(driver, wait, player_id, thread_name):
    driver.get("https://hub.vertigogames.co/daily-rewards")
    time.sleep(3)
    
    # Accept cookies first
    try:
        cookie = driver.find_element(By.XPATH, "//button[contains(text(), 'Accept') or contains(text(), 'Allow')]")
        force_click(driver, cookie)
    except: pass
    
    close_overlays(driver)

    # 1. Try to find input directly
    try:
        inp = WebDriverWait(driver, 5).until(EC.visibility_of_element_located((By.XPATH, "//input[@type='text']")))
    except:
        # 2. Try clicking Login button to reveal input
        try:
            login_btn = driver.find_element(By.XPATH, "//*[contains(text(), 'Login') or contains(text(), 'Sign in')]")
            force_click(driver, login_btn)
            inp = WebDriverWait(driver, 5).until(EC.visibility_of_element_located((By.XPATH, "//input[@type='text']")))
        except Exception as e:
            driver.save_screenshot(f"login_fail_{player_id}.png")
            raise Exception("Login inputs not found")

    try:
        inp.clear()
        inp.send_keys(player_id)
        time.sleep(0.5)
        
        # Submit
        submit_btn = driver.find_element(By.XPATH, "//button[contains(text(), 'Login') or contains(text(), 'LOGIN')]")
        force_click(driver, submit_btn)
        
        wait.until(EC.url_contains("daily-rewards"))
    except Exception as e:
        driver.save_screenshot(f"login_submit_fail_{player_id}.png")
        raise Exception(f"Login submission failed: {e}")

# --- CLAIMING ---
def claim_page_generic(driver, section_name, player_id):
    count = 0
    # Wait for page load
    time.sleep(3)
    close_overlays(driver)
    
    # Take DEBUG Screenshot to see what the bot sees
    if section_name == "Daily":
        driver.save_screenshot(f"debug_view_{player_id}.png")

    # Find ALL elements with "Claim" text (wildcard *)
    # Exclude "Buy" buttons
    xpath = "//*[contains(text(), 'Claim') and not(contains(text(), 'Buy'))]"
    elements = driver.find_elements(By.XPATH, xpath)
    
    for elm in elements:
        try:
            if elm.is_displayed():
                # Highlight element for JS (optional but helps stability)
                driver.execute_script("arguments[0].style.border='3px solid red'", elm)
                
                # Scroll and Force Click
                driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", elm)
                time.sleep(0.5)
                
                if force_click(driver, elm):
                    count += 1
                    time.sleep(1.5) # Wait for animation
                    close_overlays(driver)
        except: continue
        
    return count

def claim_progression(driver):
    count = 0
    driver.get("https://hub.vertigogames.co/progression-program")
    time.sleep(3)
    close_overlays(driver)
    
    # Try clicking "Next" arrows to scroll carousel
    try:
        arrows = driver.find_elements(By.XPATH, "//*[contains(@class, 'next') or contains(@class, 'right')]")
        for arrow in arrows:
            force_click(driver, arrow)
            time.sleep(0.5)
    except: pass

    # JS Claimer for Progression
    script = """
    var clicked = 0;
    document.querySelectorAll('button').forEach(btn => {
        if(btn.innerText.includes('Claim') && !btn.innerText.includes('Delivered')) {
            btn.click();
            clicked++;
        }
    });
    return clicked;
    """
    try:
        count = driver.execute_script(script)
    except: pass
    
    return count

# --- PROCESS ---
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
        stats['daily'] = claim_page_generic(driver, "Daily", player_id)
        
        # 3. Store
        safe_print(f"[{thread_name}] Store...")
        driver.get("https://hub.vertigogames.co/store")
        # Ensure we are on Daily tab
        try:
            tab = driver.find_element(By.XPATH, "//*[contains(text(), 'Daily Rewards')]")
            force_click(driver, tab)
        except: pass
        stats['store'] = claim_page_generic(driver, "Store", player_id)
        
        # 4. Progression
        safe_print(f"[{thread_name}] Progression...")
        stats['progression'] = claim_progression(driver)
        
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
