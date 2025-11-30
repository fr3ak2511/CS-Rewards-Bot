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
from selenium.webdriver.common.desired_capabilities import DesiredCapabilities
from webdriver_manager.chrome import ChromeDriverManager

# --- CONFIG ---
BATCH_SIZE = 2
HEADLESS = True

# --- DRIVER PATH ---
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
    
    # Core flags (matching working script)
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-logging")
    options.add_argument("--disable-web-security")
    options.add_argument("--disable-default-apps")
    options.add_argument("--disable-features=VizDisplayCompositor")
    options.add_argument("--disable-background-timer-throttling")
    options.add_argument("--disable-renderer-backgrounding")
    options.add_argument("--disable-backgrounding-occluded-windows")
    
    # CRITICAL: Block images for faster page load (from working script)
    prefs = {
        "profile.default_content_setting_values": {
            "images": 2,  # block images
            "notifications": 2,
            "popups": 2,
        },
        "profile.default_content_settings.popups": 0,
        "profile.managed_default_content_settings.popups": 0,
    }
    options.add_experimental_option("prefs", prefs)
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    
    # CRITICAL: Use "eager" not "none" (from working script)
    caps = DesiredCapabilities.CHROME.copy()
    caps["pageLoadStrategy"] = "eager"
    for k, v in caps.items():
        options.set_capability(k, v)
    
    service = Service(DRIVER_PATH)
    driver = webdriver.Chrome(service=service, options=options)
    
    # Shorter timeouts (from working script)
    driver.set_page_load_timeout(30)
    driver.set_script_timeout(30)
    
    # Anti-detection
    try:
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        })
    except: pass
    
    return driver

# --- HELPERS ---
def close_popups_safe(driver):
    """Enhanced popup closing (from working script)"""
    try:
        # Try Close button
        try:
            close_btn = driver.find_element(By.XPATH, "//button[normalize-space(text())='Close']")
            if close_btn.is_displayed():
                close_btn.click()
                time.sleep(0.5)
                return
        except: pass
        
        # Try X button
        try:
            x_buttons = driver.find_elements(By.XPATH, "//*[name()='svg']/parent::button")
            for x_btn in x_buttons:
                if x_btn.is_displayed():
                    x_btn.click()
                    time.sleep(0.5)
                    return
        except: pass
        
        # Safe area click as last resort
        try:
            ActionChains(driver).send_keys(Keys.ESCAPE).perform()
            time.sleep(0.3)
        except: pass
        
    except Exception as e:
        pass

# --- LOGIN ---
def login(driver, wait, player_id):
    try:
        safe_print(f"[{player_id}] Loading page...")
        driver.get("https://hub.vertigogames.co/daily-rewards")
        time.sleep(2)
        
        # Accept cookies
        try:
            cookie_btn = WebDriverWait(driver, 3).until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Accept')]"))
            )
            cookie_btn.click()
            time.sleep(0.3)
        except: pass
        
        close_popups_safe(driver)
        
        # Click Login button (multiple selectors)
        login_selectors = [
            "//button[contains(text(),'Login')]",
            "//button[contains(text(),'Log in')]",
            "//a[contains(text(),'Login')]",
        ]
        
        login_clicked = False
        for selector in login_selectors:
            try:
                elements = driver.find_elements(By.XPATH, selector)
                for element in elements:
                    if element.is_displayed() and element.is_enabled():
                        element.click()
                        safe_print(f"[{player_id}] Clicked login")
                        login_clicked = True
                        break
                if login_clicked: break
            except: continue
        
        if not login_clicked:
            # Try JS click if normal failed
            try:
                 js_click = "document.querySelector('button:contains(\"Login\")').click()"
                 driver.execute_script(js_click)
            except:
                 safe_print(f"[{player_id}] Login button not found")
                 raise Exception("Login button not found")
        
        time.sleep(1)
        
        # Find input field
        input_selectors = [
            "//input[contains(@placeholder, 'ID')]",
            "//input[@type='text']",
            "//input[contains(@class, 'input')]",
        ]
        
        input_box = None
        for selector in input_selectors:
            try:
                input_box = WebDriverWait(driver, 5).until(
                    EC.visibility_of_element_located((By.XPATH, selector))
                )
                break
            except: continue
        
        if not input_box:
            raise Exception("Input field not found")
        
        input_box.clear()
        input_box.send_keys(player_id)
        time.sleep(0.2)
        
        # Submit
        try:
            submit_btn = driver.find_element(By.XPATH, "//button[@type='submit']")
            submit_btn.click()
        except:
            input_box.send_keys(Keys.ENTER)
        
        safe_print(f"[{player_id}] Submitted login")
        
        # Wait for login complete
        start_time = time.time()
        while time.time() - start_time < 15:
            try:
                current_url = driver.current_url.lower()
                if "daily-rewards" in current_url or "user" in current_url:
                    safe_print(f"[{player_id}] Login successful")
                    time.sleep(1)
                    return True
                time.sleep(0.3)
            except: pass
        
        safe_print(f"[{player_id}] Login timeout")
        return False
        
    except Exception as e:
        safe_print(f"[{player_id}] Login error: {str(e)[:50]}")
        raise e

# --- CLAIMING (FROM WORKING SCRIPT) ---
def get_claim_buttons(driver, player_id):
    """Find claim buttons using working script method"""
    claim_buttons = []
    try:
        # Case insensitive XPath for all buttons containing 'Claim'
        xpath = "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'claim')]"
        all_buttons = driver.find_elements(By.XPATH, xpath)
        
        safe_print(f"[{player_id}] Found {len(all_buttons)} potential buttons")
        
        for btn in all_buttons:
            try:
                if btn.is_displayed() and btn.is_enabled():
                    btn_text = btn.text.lower()
                    # Filter out buy buttons
                    if any(word in btn_text for word in ["buy", "purchase", "payment", "pay", "$"]):
                        continue
                    claim_buttons.append(btn)
            except: continue
    except Exception as e:
        safe_print(f"[{player_id}] Error finding buttons: {str(e)[:50]}")
        return claim_buttons

def claim_rewards_page(driver, player_id, section_name):
    """Generic claim function for any page"""
    claimed = 0
    max_attempts = 5
    
    for attempt in range(max_attempts):
        close_popups_safe(driver)
        time.sleep(1)
        
        claim_buttons = get_claim_buttons(driver, player_id)
        
        if not claim_buttons:
            break
        
        # Click first available button
        btn = claim_buttons[0]
        try:
            driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", btn)
            time.sleep(0.3)
            
            # Try multiple click methods
            clicked = False
            try:
                btn.click()
                clicked = True
            except:
                try:
                    driver.execute_script("arguments[0].click();", btn)
                    clicked = True
                except: pass
            
            if clicked:
                claimed += 1
                safe_print(f"[{player_id}] {section_name} reward {claimed} CLAIMED!")
                time.sleep(2.5) # Wait for popup
                close_popups_safe(driver) # Close popup
            else:
                safe_print(f"[{player_id}] Click failed")
            
        except Exception as e:
            safe_print(f"[{player_id}] Exception: {str(e)[:50]}")
            continue
            
    return claimed

def claim_daily(driver, player_id):
    """Daily Rewards page"""
    driver.get("https://hub.vertigogames.co/daily-rewards")
    time.sleep(2)
    close_popups_safe(driver)
    return claim_rewards_page(driver, player_id, "Daily")

def claim_store(driver, player_id):
    """Store Daily Rewards section"""
    driver.get("https://hub.vertigogames.co/store")
    time.sleep(2)
    close_popups_safe(driver)
    
    # Click Daily Rewards tab (from working script)
    try:
        tab_selectors = [
            "//div[contains(@class, 'tab')]//span[contains(text(), 'Daily Rewards')]",
            "//button[contains(text(), 'Daily Rewards')]",
            "//*[text()='Daily Rewards' and contains(@class, 'tab')]",
        ]
        
        for selector in tab_selectors:
            try:
                tab = driver.find_element(By.XPATH, selector)
                if tab.is_displayed():
                    driver.execute_script("arguments[0].click();", tab)
                    time.sleep(1)
                    break
            except: continue
    except: pass
    
    return claim_rewards_page(driver, player_id, "Store")

def claim_progression(driver, player_id):
    """Progression Program (using JS from working script)"""
    driver.get("https://hub.vertigogames.co/progression-program")
    time.sleep(2)
    close_popups_safe(driver)
    
    claimed = 0
    max_attempts = 8
    
    # JavaScript from working script
    get_buttons_script = """
    let allButtons = document.querySelectorAll('button');
    let claimButtons = [];
    allButtons.forEach(function(btn) {
        let text = btn.innerText.trim();
        if (text === 'Claim') {
            let rect = btn.getBoundingClientRect();
            let x = rect.left;
            if (x > 400) {  // Right of sidebar
                let parent = btn.closest('div');
                let parentText = parent ? parent.innerText : '';
                if (!parentText.includes('Delivered')) {
                    claimButtons.push(btn);
                }
            }
        }
    });
    return claimButtons;
    """
    
    for attempt in range(max_attempts):
        try:
            claimable_elements = driver.execute_script(get_buttons_script)
            
            if not claimable_elements:
                break
            
            # Click first button
            btn = claimable_elements[0]
            driver.execute_script("arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", btn)
            time.sleep(0.3)
            driver.execute_script("arguments[0].click();", btn)
            
            claimed += 1
            safe_print(f"[{player_id}] Progression reward {claimed} CLAIMED!")
            time.sleep(2.5)
            close_popups_safe(driver)
            
        except Exception as e:
            safe_print(f"[{player_id}] Progression error: {str(e)[:50]}")
            break
            
    return claimed

# --- PROCESS ---
def process_player(player_id, thread_name):
    driver = None
    stats = {"player_id": player_id, "daily": 0, "store": 0, "progression": 0, "status": "Failed"}
    
    try:
        safe_print(f"[{thread_name}] Starting {player_id}")
        driver = create_driver()
        wait = WebDriverWait(driver, 20)
        
        if not login(driver, wait, player_id):
            stats['status'] = "Login Failed"
            return stats
        
        safe_print(f"[{thread_name}] Claiming Daily...")
        stats['daily'] = claim_daily(driver, player_id)
        
        safe_print(f"[{thread_name}] Claiming Store...")
        stats['store'] = claim_store(driver, player_id)
        
        safe_print(f"[{thread_name}] Claiming Progression...")
        stats['progression'] = claim_progression(driver, player_id)
        
        stats['status'] = "Success"
        safe_print(f"[{thread_name}] Finished {player_id}: {stats['daily']}/{stats['store']}/{stats['progression']}")

    except Exception as e:
        safe_print(f"[{thread_name}] Error on {player_id}: {str(e)[:50]}")
        stats['status'] = f"Error: {str(e)[:30]}"
    finally:
        if driver:
            try:
                driver.quit()
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
        time.sleep(2)

    send_summary_email(results)

if __name__ == "__main__":
    main()
