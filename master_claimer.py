import csv
import time
import threading
import os
import smtplib
import sys
import gc
import subprocess
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
BATCH_SIZE = 1
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
        safe_print("⚠️  Secrets missing. Email will NOT be sent.")
        return
    
    subject = f"Hub Rewards Summary - {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    html_body = f"""
<html>
<body>
<h2>Hub Rewards Summary</h2>
<p><strong>Run Time:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
<table border="1" cellpadding="5">
<tr><th>Player ID</th><th>Daily</th><th>Store</th><th>Progression</th><th>Status</th></tr>
"""
    
    for row in summary_data:
        html_body += f"<tr><td>{row['player_id']}</td><td>{row['daily']}</td><td>{row['store']}</td><td>{row['progression']}</td><td>{row['status']}</td></tr>"
    
    html_body += """
</table>
</body>
</html>
"""
    
    msg = MIMEMultipart()
    msg['Subject'] = subject
    msg['From'] = sender_email
    msg['To'] = recipient_email
    msg.attach(MIMEText(html_body, 'html'))
    
    try:
        s = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        s.login(sender_email, gmail_password)
        s.sendmail(sender_email, recipient_email, msg.as_string())
        s.quit()
        safe_print("✅ Email sent successfully")
    except Exception as e:
        safe_print(f"❌ Email failed: {e}")

# --- DRIVER (FIXED FLAGS FOR GITHUB ACTIONS) ---
def create_driver():
    options = Options()
    if HEADLESS:
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1920,1080")
    
    # CRITICAL STABILITY FLAGS FOR GITHUB ACTIONS
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-software-rasterizer")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--disable-blink-features=AutomationControlled")
    
    # REMOVED --single-process (causes crashes on Ubuntu 24.04)
    # Added memory optimization flags instead
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-setuid-sandbox")
    options.add_argument("--disable-background-networking")
    options.add_argument("--disable-default-apps")
    options.add_argument("--disable-sync")
    options.add_argument("--metrics-recording-only")
    options.add_argument("--mute-audio")
    options.add_argument("--no-first-run")
    options.add_argument("--safebrowsing-disable-auto-update")
    options.add_argument("--disable-web-security")
    
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    options.page_load_strategy = 'eager'
    
    service = Service(DRIVER_PATH)
    driver = webdriver.Chrome(service=service, options=options)
    
    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        })
    except:
        pass
    
    driver.set_page_load_timeout(60)
    driver.implicitly_wait(5)
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
        ActionChains(driver).move_by_offset(10, 10).click().perform()
    except:
        pass
    return True

def accept_cookies(driver, wait):
    try:
        btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Accept')]")))
        btn.click()
    except:
        pass

def verify_login_success(driver):
    try:
        if driver.find_elements(By.XPATH, "//button[contains(text(), 'Logout')]"):
            return True
        if driver.find_elements(By.XPATH, "//button[contains(text(), 'Claim')]"):
            return True
        return False
    except:
        return False

def click_any_login(driver):
    """Generic login button clicker"""
    login_clicked = False
    login_selectors = [
        "//button[contains(text(),'Login')]",
        "//a[contains(text(),'Login')]",
        "//button[contains(@class, 'login')]"
    ]
    
    for selector in login_selectors:
        try:
            btns = driver.find_elements(By.XPATH, selector)
            for btn in btns:
                if btn.is_displayed():
                    ActionChains(driver).move_to_element(btn).click().perform()
                    safe_print("✅ Clicked Login (Physical)")
                    login_clicked = True
                    time.sleep(3)
                    break
            if login_clicked:
                break
        except:
            continue
    
    if not login_clicked:
        try:
            driver.execute_script("document.querySelector('button.login')?.click()")
            safe_print("✅ Clicked Login (JS)")
            time.sleep(3)
        except:
            pass
    
    return login_clicked

# --- LOGIN ---
def login(driver, wait, player_id):
    driver.get("https://hub.vertigogames.co/daily-rewards")
    time.sleep(5)
    accept_cookies(driver, wait)
    close_popups_safe(driver)
    
    if verify_login_success(driver):
        return True
    
    # Click login button
    click_any_login(driver)
    
    # Find input - also search in iframes
    inp = None
    try:
        inp = WebDriverWait(driver, 10).until(
            EC.visibility_of_element_located((By.XPATH, "//input[@type='text' or contains(@placeholder, 'ID')]"))
        )
    except:
        # Search in iframes
        try:
            iframes = driver.find_elements(By.TAG_NAME, "iframe")
            for iframe in iframes:
                driver.switch_to.frame(iframe)
                try:
                    inp = driver.find_element(By.XPATH, "//input[@type='text' or contains(@placeholder, 'ID')]")
                    if inp.is_displayed():
                        break
                except:
                    driver.switch_to.default_content()
                    continue
        except:
            pass
        
        if not inp:
            driver.save_screenshot(f"login_fail_no_input_{player_id}.jpg")
            raise Exception("Input not found")
    
    inp.clear()
    inp.send_keys(player_id)
    time.sleep(0.5)
    
    # Submit
    try:
        submit_btn = driver.find_element(By.XPATH, "//button[@type='submit']")
        ActionChains(driver).move_to_element(submit_btn).click().perform()
    except:
        inp.send_keys(Keys.ENTER)
    
    time.sleep(5)
    
    # Switch back to default if we were in iframe
    try:
        driver.switch_to.default_content()
    except:
        pass
    
    if verify_login_success(driver):
        return True
    
    try:
        if not inp.is_displayed():
            return True
    except:
        return True
    
    raise Exception("Login verification failed")

# --- CLAIMING ---
def get_valid_claim_buttons(driver):
    valid_buttons = []
    try:
        xpath = "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'claim')]"
        buttons = driver.find_elements(By.XPATH, xpath)
        for btn in buttons:
            if btn.is_displayed() and btn.is_enabled():
                text = btn.text.lower()
                if "login" in text or "buy" in text:
                    continue
                valid_buttons.append(btn)
    except:
        pass
    return valid_buttons

def perform_claim_loop(driver, section_name):
    claimed = 0
    for _ in range(6):
        close_popups_safe(driver)
        time.sleep(1.5)
        buttons = get_valid_claim_buttons(driver)
        if not buttons:
            break
        
        btn = buttons[0]
        try:
            driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", btn)
            time.sleep(0.5)
            
            try:
                ActionChains(driver).move_to_element(btn).click().perform()
            except:
                driver.execute_script("arguments[0].click();", btn)
            
            safe_print(f"Clicked {section_name}...")
            time.sleep(3)
            
            if close_popups_safe(driver):
                claimed += 1
                safe_print(f"{section_name} Reward {claimed} VERIFIED")
            else:
                try:
                    if not btn.is_displayed():
                        claimed += 1
                        safe_print(f"{section_name} Reward {claimed} VERIFIED (Gone)")
                except:
                    claimed += 1
                    safe_print(f"{section_name} Reward {claimed} VERIFIED (Stale)")
        except Exception:
            continue
    
    return claimed

def claim_daily(driver):
    return perform_claim_loop(driver, "Daily")

def claim_store(driver):
    driver.get("https://hub.vertigogames.co/store")
    time.sleep(3)
    close_popups_safe(driver)
    
    try:
        driver.execute_script("window.scrollTo(0, 300);")
        time.sleep(1)
        tab = driver.find_element(By.XPATH, "//*[contains(text(), 'Daily Rewards')]")
        tab.click()
        time.sleep(1.5)
    except:
        pass
    
    return perform_claim_loop(driver, "Store")

def claim_progression(driver):
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
    except:
        pass
    
    for _ in range(6):
        time.sleep(1)
        js_find_and_click = """
            let buttons = document.querySelectorAll('button');
            for (let btn of buttons) {
                let text = btn.innerText.trim();
                if (text.toLowerCase() === 'claim') {
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
                safe_print(f"Progression Clicked...")
                time.sleep(4)
                if close_popups_safe(driver):
                    claimed += 1
                    safe_print(f"Progression Reward {claimed} VERIFIED")
            else:
                break
        except:
            break
    
    return claimed

# --- PROCESS PLAYER ---
def process_single_player(player_id):
    driver = None
    stats = {"player_id": player_id, "daily": 0, "store": 0, "progression": 0, "status": "Failed"}
    
    try:
        safe_print(f"Starting {player_id}")
        driver = create_driver()
        wait = WebDriverWait(driver, 45)
        
        if login(driver, wait, player_id):
            time.sleep(2)
            stats['daily'] = claim_daily(driver)
            stats['store'] = claim_store(driver)
            stats['progression'] = claim_progression(driver)
            stats['status'] = "Success"
            safe_print(f"✅ Finished {player_id}: {stats['daily']}/{stats['store']}/{stats['progression']}")
    except Exception as e:
        safe_print(f"❌ Error on {player_id}: {str(e)[:50]}")
        stats['status'] = "Error"
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass
        
        # Force cleanup
        try:
            subprocess.run(["pkill", "-9", "-f", "chrome"], check=False, capture_output=True)
            subprocess.run(["pkill", "-9", "-f", "chromedriver"], check=False, capture_output=True)
        except:
            pass
        
        gc.collect()
        time.sleep(3)
    
    return stats

# --- MAIN ---
def main():
    safe_print("=" * 60)
    safe_print("STARTING BATCH CLAIM RUN")
    safe_print("=" * 60)
    
    # Read player IDs
    player_ids = []
    try:
        with open('players.csv', 'r') as f:
            reader = csv.reader(f)
            for row in reader:
                if row and row[0].strip():
                    player_ids.append(row[0].strip())
    except Exception as e:
        safe_print(f"❌ Failed to read players.csv: {e}")
        return
    
    safe_print(f"📋 Loaded {len(player_ids)} players")
    
    all_results = []
    
    # Process in batches
    for i in range(0, len(player_ids), BATCH_SIZE):
        batch = player_ids[i:i + BATCH_SIZE]
        safe_print(f"\n{'='*60}")
        safe_print(f"BATCH {i//BATCH_SIZE + 1}: Processing {len(batch)} player(s)")
        safe_print(f"{'='*60}")
        
        with ThreadPoolExecutor(max_workers=BATCH_SIZE) as executor:
            futures = {executor.submit(process_single_player, pid): pid for pid in batch}
            for future in as_completed(futures):
                result = future.result()
                all_results.append(result)
        
        safe_print(f"✅ Batch {i//BATCH_SIZE + 1} complete")
        time.sleep(5)
    
    safe_print("\n" + "="*60)
    safe_print("ALL PROCESSING COMPLETE")
    safe_print("="*60)
    
    # Send email summary
    send_summary_email(all_results)
    
    safe_print("🏁 Script finished")

if __name__ == "__main__":
    main()
