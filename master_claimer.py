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

    # Debug print to check if secrets are loaded (masked)
    if not sender_email:
        safe_print("⚠️ SENDER_EMAIL is missing/empty.")
    if not gmail_password:
        safe_print("⚠️ GMAIL_APP_PASSWORD is missing/empty.")

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
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(45) # Increased timeout
    return driver

# --- HELPERS ---
def close_popups(driver):
    try:
        # Aggressive popup closer
        driver.execute_script("""
            var popups = document.querySelectorAll('.modal, .popup, button[class*="close"]');
            popups.forEach(p => {
                if(p.offsetParent !== null) p.remove();
            });
        """)
        ActionChains(driver).move_by_offset(10, 10).click().perform()
        ActionChains(driver).move_by_offset(-10, -10).perform()
    except:
        pass

def safe_click(driver, element):
    try:
        element.click()
        return True
    except:
        try:
            driver.execute_script("arguments[0].click();", element)
            return True
        except:
            return False

def accept_cookies(driver):
    try:
        btn = WebDriverWait(driver, 5).until(EC.element_to_be_clickable(
            (By.XPATH, "//button[contains(text(), 'Accept') or contains(text(), 'Allow')]")))
        safe_click(driver, btn)
    except:
        pass

# --- CORE LOGIC ---
def process_player(player_id, thread_name):
    driver = None
    stats = {"player_id": player_id, "daily": 0, "store": 0, "progression": 0, "status": "Failed"}
    
    try:
        safe_print(f"[{thread_name}] Starting {player_id}")
        driver = create_driver()
        wait = WebDriverWait(driver, 20) # Increased wait
        
        # 1. LOGIN
        driver.get("https://hub.vertigogames.co/daily-rewards")
        time.sleep(3) # Wait for page load
        accept_cookies(driver)
        
        # DEBUG: Take screenshot if login fails
        try:
            # Look for ANY login button
            login_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Login') or contains(text(), 'Sign in')]")))
            safe_click(driver, login_btn)
        except Exception:
            # Save screenshot for debugging
            safe_print(f"[{thread_name}] 📸 Login button not found. Saving screenshot...")
            driver.save_screenshot(f"error_{player_id}.png")
            raise Exception("Login button not found - See screenshot")

        # Enter ID
        try:
            inp = wait.until(EC.visibility_of_element_located((By.XPATH, "//input[@type='text']")))
            inp.clear()
            inp.send_keys(player_id)
            time.sleep(0.5)
            
            submit_btn = driver.find_element(By.XPATH, "//button[contains(text(), 'Login') or @type='submit']")
            safe_click(driver, submit_btn)
            
            wait.until(EC.url_contains("daily-rewards"))
            time.sleep(2)
        except Exception:
             driver.save_screenshot(f"error_input_{player_id}.png")
             raise Exception("Input/Submit failed")

        close_popups(driver)
        
        # 2. DAILY
        safe_print(f"[{thread_name}] Daily Rewards...")
        daily_btns = driver.find_elements(By.XPATH, "//button[contains(text(), 'Claim') and not(contains(text(), 'Buy'))]")
        for btn in daily_btns:
            if btn.is_displayed() and safe_click(driver, btn):
                stats['daily'] += 1
                time.sleep(1)
        
        # 3. STORE
        safe_print(f"[{thread_name}] Store...")
        driver.get("https://hub.vertigogames.co/store")
        time.sleep(3)
        close_popups(driver)
        
        store_btns = driver.find_elements(By.XPATH, "//button[contains(text(), 'Claim') and not(contains(text(), 'Buy'))]")
        for btn in store_btns:
            if btn.is_displayed() and safe_click(driver, btn):
                stats['store'] += 1
                time.sleep(1)

        # 4. PROGRESSION
        safe_print(f"[{thread_name}] Progression...")
        driver.get("https://hub.vertigogames.co/progression-program")
        time.sleep(3)
        close_popups(driver)
        
        # Force scroll
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight/2);")
        time.sleep(1)

        js_script = """
        return Array.from(document.querySelectorAll('button'))
            .filter(b => b.innerText.trim() === 'Claim');
        """
        prog_btns = driver.execute_script(js_script)
        
        for i in range(len(prog_btns)):
            current_btns = driver.execute_script(js_script)
            if i < len(current_btns):
                btn = current_btns[i]
                driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", btn)
                time.sleep(0.5)
                driver.execute_script("arguments[0].click();", btn)
                stats['progression'] += 1
                time.sleep(1)
                close_popups(driver)

        stats['status'] = "Success"
        safe_print(f"[{thread_name}] Finished {player_id}")

    except Exception as e:
        safe_print(f"[{thread_name}] Error on {player_id}: {str(e)}")
        stats['status'] = f"Error: {str(e)[:30]}"
    finally:
        if driver: driver.quit()
    return stats

# --- MAIN ---
def main():
    players = []
    try:
        with open('players.csv', 'r') as f:
            reader = csv.reader(f)
            for row in reader:
                # FIX: Robust check for empty lines/rows
                if row and len(row) > 0 and row[0].strip():
                    players.append(row[0].strip())
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
        time.sleep(1)

    send_summary_email(results)

if __name__ == "__main__":
    main()
