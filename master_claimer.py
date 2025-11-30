import csv
import time
import threading
import os
import smtplib
import traceback
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
BATCH_SIZE = 2  # GitHub runners have 2 vCPUs, keep this at 2
HEADLESS = True # Must be True for GitHub

# Thread-safe printing
print_lock = threading.Lock()
def safe_print(msg):
    with print_lock:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

# --- EMAIL FUNCTIONS ---
def send_summary_email(summary_data):
    """Sends the consolidated log via email using GitHub Secrets."""
    sender_email = os.environ.get("SENDER_EMAIL")
    recipient_email = os.environ.get("RECIPIENT_EMAIL")
    gmail_user = os.environ.get("GMAIL_USER")
    gmail_password = os.environ.get("GMAIL_APP_PASSWORD")

    if not all([sender_email, recipient_email, gmail_user, gmail_password]):
        safe_print("⚠️ Email secrets missing. Skipping email report.")
        return

    subject = f"Hub Rewards Summary - {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    
    # Create HTML Body
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
        server.login(gmail_user, gmail_password)
        server.sendmail(sender_email, recipient_email, msg.as_string())
        server.quit()
        safe_print("✅ Summary email sent successfully.")
    except Exception as e:
        safe_print(f"❌ Failed to send email: {str(e)}")

# --- DRIVER SETUP ---
def create_driver():
    options = Options()
    
    # Critical for GitHub Actions
    if HEADLESS:
        options.add_argument("--headless=new")
    
    options.add_argument("--window-size=1920,1080") # Force desktop resolution
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")
    
    # Anti-detection: Spoof User Agent
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    # Speed optimizations
    options.page_load_strategy = 'eager' # Don't wait for all images
    
    # Auto-install driver compatible with the environment
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(30)
    
    return driver

# --- HELPER ACTIONS ---
def close_popups(driver):
    """Aggressively tries to close popups using multiple selectors."""
    popup_closers = [
        "//button[contains(@class, 'close')]",
        "//button[contains(text(), 'Close')]",
        "//*[name()='svg' and contains(@class, 'close')]/parent::button",
        "//div[contains(@class, 'modal')]//button",
        "//button[normalize-space()='×']"
    ]
    
    for selector in popup_closers:
        try:
            elements = driver.find_elements(By.XPATH, selector)
            for btn in elements:
                if btn.is_displayed():
                    driver.execute_script("arguments[0].click();", btn)
                    time.sleep(0.2)
        except:
            pass
            
    # Click safe area (top left) to dismiss backend drops
    try:
        ActionChains(driver).move_by_offset(10, 10).click().perform()
        ActionChains(driver).move_by_offset(-10, -10).perform() # Reset
    except:
        pass

def safe_click(driver, element):
    """Tries 3 methods to click an element."""
    try:
        # Method 1: Standard
        element.click()
        return True
    except:
        try:
            # Method 2: JS
            driver.execute_script("arguments[0].click();", element)
            return True
        except:
            try:
                # Method 3: Actions
                ActionChains(driver).move_to_element(element).click().perform()
                return True
            except:
                return False

def accept_cookies(driver):
    try:
        btn = WebDriverWait(driver, 3).until(EC.element_to_be_clickable(
            (By.XPATH, "//button[contains(text(), 'Accept') or contains(text(), 'Allow')]")))
        safe_click(driver, btn)
    except:
        pass

# --- CORE LOGIC ---

def process_player(player_id, thread_name):
    driver = None
    stats = {
        "player_id": player_id,
        "daily": 0,
        "store": 0,
        "progression": 0,
        "status": "Failed"
    }
    
    try:
        safe_print(f"[{thread_name}] Starting {player_id}")
        driver = create_driver()
        wait = WebDriverWait(driver, 15)
        
        # 1. LOGIN
        driver.get("https://hub.vertigogames.co/daily-rewards")
        accept_cookies(driver)
        
        # Find Login Button (Scanning multiple variants)
        login_btn = None
        possible_logins = ["//button[contains(text(), 'Login')]", "//a[contains(text(), 'Login')]"]
        for sel in possible_logins:
            try:
                btns = driver.find_elements(By.XPATH, sel)
                for b in btns:
                    if b.is_displayed():
                        login_btn = b
                        break
                if login_btn: break
            except: continue
            
        if not login_btn:
            raise Exception("Login button not found")
            
        safe_click(driver, login_btn)
        
        # Enter ID
        inp = wait.until(EC.visibility_of_element_located((By.XPATH, "//input[@type='text']")))
        inp.clear()
        inp.send_keys(player_id)
        time.sleep(0.5)
        
        # Click Submit
        submit_btn = driver.find_element(By.XPATH, "//button[contains(text(), 'Login') or @type='submit']")
        safe_click(driver, submit_btn)
        
        # Wait for Login Success
        wait.until(EC.url_contains("daily-rewards"))
        time.sleep(2)
        close_popups(driver)
        
        # 2. CLAIM DAILY REWARDS
        safe_print(f"[{thread_name}] Checking Daily Rewards...")
        daily_btns = driver.find_elements(By.XPATH, "//button[contains(text(), 'Claim') and not(contains(text(), 'Buy'))]")
        for btn in daily_btns:
            if btn.is_displayed() and btn.is_enabled():
                if safe_click(driver, btn):
                    stats['daily'] += 1
                    time.sleep(1)
                    close_popups(driver)
        
        # 3. CLAIM STORE REWARDS
        safe_print(f"[{thread_name}] navigating to Store...")
        driver.get("https://hub.vertigogames.co/store")
        time.sleep(3)
        close_popups(driver)
        
        # Find "Daily Rewards" tab/section in store
        try:
            tab = driver.find_element(By.XPATH, "//*[contains(text(), 'Daily Rewards')]")
            driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", tab)
            safe_click(driver, tab)
            time.sleep(1)
        except:
            pass # Might already be visible
            
        store_btns = driver.find_elements(By.XPATH, "//button[contains(text(), 'Claim') and not(contains(text(), 'Buy'))]")
        for btn in store_btns:
            if btn.is_displayed() and btn.is_enabled():
                if safe_click(driver, btn):
                    stats['store'] += 1
                    time.sleep(1)
                    close_popups(driver)

        # 4. PROGRESSION PROGRAM
        safe_print(f"[{thread_name}] navigating to Progression...")
        driver.get("https://hub.vertigogames.co/progression-program")
        time.sleep(3)
        close_popups(driver)
        
        # Scroll right to reveal hidden cards
        try:
            next_arrow = driver.find_element(By.XPATH, "//button[contains(@class, 'right') or contains(@class, 'next')]")
            safe_click(driver, next_arrow)
            time.sleep(1)
        except:
            pass
            
        # JS approach to find valid claim buttons (from your original script)
        # We use JS to find buttons because they might be obscured
        js_script = """
        return Array.from(document.querySelectorAll('button'))
            .filter(b => b.innerText.trim() === 'Claim' && b.offsetParent !== null);
        """
        prog_btns = driver.execute_script(js_script)
        
        for i in range(len(prog_btns)):
            # Re-fetch elements to avoid StaleElementReference
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
        safe_print(f"[{thread_name}] Finished {player_id}. Total: {stats['daily'] + stats['store'] + stats['progression']}")

    except Exception as e:
        safe_print(f"[{thread_name}] Error on {player_id}: {str(e)}")
        stats['status'] = f"Error: {str(e)[:20]}"
    finally:
        if driver:
            driver.quit()
            
    return stats

# --- MAIN EXECUTION ---
def main():
    start_time = time.time()
    
    # Load Players
    players = []
    try:
        with open('players.csv', 'r') as f:
            reader = csv.reader(f)
            for row in reader:
                if row and row[0].strip():
                    players.append(row[0].strip())
    except FileNotFoundError:
        safe_print("❌ players.csv not found!")
        return

    safe_print(f"Loaded {len(players)} players. Processing in batches of {BATCH_SIZE}...")
    
    results = []
    
    # Process in Batches
    for i in range(0, len(players), BATCH_SIZE):
        batch = players[i:i + BATCH_SIZE]
        with ThreadPoolExecutor(max_workers=BATCH_SIZE) as executor:
            futures = {executor.submit(process_player, pid, f"Thread-{i+idx}"): pid for idx, pid in enumerate(batch)}
            
            for future in as_completed(futures):
                results.append(future.result())
        
        time.sleep(2) # Cool down between batches

    # Send Email
    send_summary_email(results)
    
    safe_print(f"Run Complete in {time.time() - start_time:.2f}s")

if __name__ == "__main__":
    main()
