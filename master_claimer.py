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
    # Mask automation signals
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(45)
    return driver

# --- HELPER ACTIONS ---
def safe_click(driver, element):
    """Robust clicker using 3 methods"""
    try:
        element.click()
        return True
    except:
        try:
            driver.execute_script("arguments[0].click();", element)
            return True
        except:
            try:
                ActionChains(driver).move_to_element(element).click().perform()
                return True
            except:
                return False

def close_overlays(driver):
    """Closes cookies and popups"""
    # 1. Accept Cookies (Multiple text variations)
    try:
        cookie_xpath = "//*[contains(translate(text(), 'AC', 'ac'), 'accept') or contains(text(), 'Allow')]"
        buttons = driver.find_elements(By.XPATH, cookie_xpath)
        for btn in buttons:
            if btn.is_displayed():
                safe_click(driver, btn)
                time.sleep(0.5)
    except: pass

    # 2. Close Popups
    try:
        close_xpath = "//*[contains(@class, 'close') or text()='×' or text()='Close']"
        buttons = driver.find_elements(By.XPATH, close_xpath)
        for btn in buttons:
            if btn.is_displayed():
                driver.execute_script("arguments[0].click();", btn)
    except: pass

def login_procedure(driver, wait, player_id, thread_name):
    """Dedicated login function with universal selector"""
    # 1. Look for ANY element with Login text (Button, Link, Span)
    # This XPath looks for "Login", "Log in", "Sign in" case-insensitive
    login_xpath = "//*[contains(translate(text(), 'L', 'l'), 'login') or contains(translate(text(), 'S', 's'), 'sign in')]"
    
    found_btn = False
    elements = driver.find_elements(By.XPATH, login_xpath)
    
    for btn in elements:
        if btn.is_displayed():
            # Filter out small hidden elements
            if btn.size['height'] > 0 and btn.size['width'] > 0:
                safe_click(driver, btn)
                found_btn = True
                break
    
    if not found_btn:
        # Fallback: Try finding by href
        try:
            link = driver.find_element(By.CSS_SELECTOR, "a[href*='login'], a[href*='signin']")
            safe_click(driver, link)
            found_btn = True
        except:
            pass

    if not found_btn:
        raise Exception("Login button not found")

    # 2. Enter ID
    try:
        inp = wait.until(EC.visibility_of_element_located((By.XPATH, "//input")))
        inp.clear()
        inp.send_keys(player_id)
        time.sleep(0.5)
        
        # Click ANY submit button or the Login button again
        submit_btns = driver.find_elements(By.XPATH, "//button[@type='submit'] | //button[contains(text(), 'Login')]")
        if submit_btns:
            safe_click(driver, submit_btns[0])
        else:
            inp.send_keys(Keys.ENTER)
            
        wait.until(EC.url_contains("daily-rewards"))
    except Exception as e:
        raise Exception(f"Input/Submit failed: {str(e)}")

# --- CORE LOGIC ---
def process_player(player_id, thread_name):
    driver = None
    stats = {"player_id": player_id, "daily": 0, "store": 0, "progression": 0, "status": "Failed"}
    
    try:
        safe_print(f"[{thread_name}] Starting {player_id}")
        driver = create_driver()
        wait = WebDriverWait(driver, 20)
        
        # --- LOGIN ---
        driver.get("https://hub.vertigogames.co/daily-rewards")
        time.sleep(3)
        close_overlays(driver)
        
        try:
            login_procedure(driver, wait, player_id, thread_name)
        except Exception as e:
            safe_print(f"[{thread_name}] Login failed. Saving screenshot.")
            driver.save_screenshot(f"error_{player_id}.png")
            raise e

        time.sleep(2)
        close_overlays(driver)

        # --- DAILY ---
        safe_print(f"[{thread_name}] Daily Rewards...")
        # Search for buttons with "Claim" text
        daily_btns = driver.find_elements(By.XPATH, "//button[contains(text(), 'Claim')]")
        for btn in daily_btns:
            # Check if it's not a "Buy" button
            if btn.is_displayed() and "buy" not in btn.text.lower():
                if safe_click(driver, btn):
                    stats['daily'] += 1
                    time.sleep(1)
        
        # --- STORE ---
        safe_print(f"[{thread_name}] Store...")
        driver.get("https://hub.vertigogames.co/store")
        time.sleep(3)
        close_overlays(driver)
        
        # Find Daily Rewards tab in store
        try:
            daily_tab = driver.find_element(By.XPATH, "//*[contains(text(), 'Daily Rewards')]")
            safe_click(driver, daily_tab)
            time.sleep(1)
        except: pass

        store_btns = driver.find_elements(By.XPATH, "//button[contains(text(), 'Claim')]")
        for btn in store_btns:
            if btn.is_displayed() and "buy" not in btn.text.lower():
                if safe_click(driver, btn):
                    stats['store'] += 1
                    time.sleep(1)

        # --- PROGRESSION ---
        safe_print(f"[{thread_name}] Progression...")
        driver.get("https://hub.vertigogames.co/progression-program")
        time.sleep(3)
        close_overlays(driver)

        # JS claimer for progression (handles carousel/hidden items)
        js_script = """
        var buttons = document.querySelectorAll('button');
        var clicked = 0;
        buttons.forEach(btn => {
            if(btn.innerText.includes('Claim') && !btn.innerText.includes('Delivered')) {
                btn.click();
                clicked++;
            }
        });
        return clicked;
        """
        try:
            claims = driver.execute_script(js_script)
            stats['progression'] = claims
        except:
            pass

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
            lines = f.readlines()
            for line in lines:
                # FIX: Strict cleaning to prevent Tuple errors
                clean_line = line.strip().replace(',', '')
                if len(clean_line) > 5: # Assuming IDs are longer than 5 chars
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
        time.sleep(1)

    send_summary_email(results)

if __name__ == "__main__":
    main()
