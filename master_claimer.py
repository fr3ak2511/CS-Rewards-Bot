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
    
    # Anti-detection CDP
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": """
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            })
        """
    })
    
    driver.set_page_load_timeout(60)
    return driver

# --- HELPERS ---

def zap_overlays(driver):
    """Deletes sticky headers/footers to prevent clicks being blocked"""
    try:
        driver.execute_script("""
            var selectors = [
                'header', 'footer', '#onetrust-banner-sdk', 
                '.cookie-banner', '.modal-backdrop', 
                '[class*="overlay"]', '[class*="header"]', '[class*="footer"]'
            ];
            selectors.forEach(sel => {
                document.querySelectorAll(sel).forEach(el => el.remove());
            });
        """)
    except: pass

def close_popups_safe(driver):
    """
    Robust popup closer:
    1. Sends ESC key.
    2. Clicks specific 'Dead Zones' (Safe Areas).
    3. Clicks 'Close' buttons via JS.
    Repeats 3 times to handle stacked popups.
    """
    for _ in range(3): # Loop to handle multiple popups
        try:
            # 1. ESC Key
            ActionChains(driver).send_keys(Keys.ESCAPE).perform()
            time.sleep(0.5)

            # 2. Safe Area Clicks (The "Backdrop" Strategy)
            # We use JS to click coordinates directly to be absolute
            width = 1920
            height = 1080
            safe_coords = [
                (10, 10),               # Top Left
                (width - 50, 10),       # Top Right
                (10, height - 50),      # Bottom Left
                (width - 50, height - 50), # Bottom Right
                (width // 2, 10)        # Top Center
            ]
            
            for x, y in safe_coords:
                try:
                    driver.execute_script(f"document.elementFromPoint({x}, {y}).click();")
                except: pass
                time.sleep(0.1)

            # 3. JS Close Button Click
            driver.execute_script("""
                document.querySelectorAll('button, div[role="button"]').forEach(btn => {
                    let text = btn.innerText.toLowerCase();
                    if(text.includes('close') || text === '×' || text === 'x' || text.includes('continue') || text.includes('ok')) {
                        if(btn.offsetParent !== null) btn.click();
                    }
                });
            """)
            time.sleep(0.5)
        except: pass

# --- LOGIN ---
def login(driver, wait, player_id):
    driver.get("https://hub.vertigogames.co/daily-rewards")
    time.sleep(5)
    zap_overlays(driver) 

    # 1. Click Login
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
                    time.sleep(2)
                    break
        except: continue
    
    # 2. Input
    try:
        inp = None
        input_selectors = ["//input[@type='text']", "//input[contains(@placeholder, 'ID')]"]
        for _ in range(5):
            for sel in input_selectors:
                inputs = driver.find_elements(By.XPATH, sel)
                visible = [i for i in inputs if i.is_displayed()]
                if visible:
                    inp = visible[0]
                    break
            if inp: break
            time.sleep(1)
            
        if not inp: raise Exception("Input not found")

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
    except Exception as e:
        driver.save_screenshot(f"login_fail_{player_id}.png")
        raise e

# --- CLAIMING ---

def perform_claim_loop(driver, player_id, section_name):
    claimed = 0
    max_rounds = 6
    
    for round_num in range(max_rounds):
        # Clean up before every attempt
        zap_overlays(driver)
        close_popups_safe(driver)
        time.sleep(1)
        
        claim_xpath = "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'claim') and not(contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'buy'))]"
        
        buttons = driver.find_elements(By.XPATH, claim_xpath)
        visible_buttons = [b for b in buttons if b.is_displayed()]
        
        if not visible_buttons: break
            
        btn = visible_buttons[0]
        try:
            driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", btn)
            time.sleep(0.5)
            
            # Click
            driver.execute_script("arguments[0].click();", btn)
            safe_print(f"[{player_id}] Clicked {section_name}...")
            
            # Wait for popup to appear
            time.sleep(3) 
            
            # VERIFICATION:
            # 1. Did a popup appear? If so, we are successful.
            # 2. Is the button gone? If so, we are successful.
            
            # Check visible popups BEFORE closing them
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
                safe_print(f"[{player_id}] Click failed - No popup and button still there")
                if round_num == 0: driver.save_screenshot(f"fail_{section_name}_{player_id}.png")
            
            # Always run the "Safe Area" closer to dismiss whatever appeared
            close_popups_safe(driver)
                
        except Exception: continue
            
    return claimed

def claim_daily(driver, player_id):
    return perform_claim_loop(driver, player_id, "Daily")

def claim_store(driver, player_id):
    driver.get("https://hub.vertigogames.co/store")
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
                time.sleep(3)
                
                # Verify by checking if we need to close a popup
                modals = driver.find_elements(By.XPATH, "//div[contains(@class, 'modal') or contains(@class, 'popup')]")
                if [m for m in modals if m.is_displayed()]:
                    claimed += 1
                    safe_print(f"[{player_id}] Progression Reward {claimed} VERIFIED")
                
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
        wait = WebDriverWait(driver, 60)
        
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
