import csv
import time
import os
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager

PLAYER_ID_FILE = "players.csv"
HEADLESS = True

DAILY_RESET_HOUR_IST = 5
DAILY_RESET_MINUTE_IST = 30
EXPECTED_STORE_PER_PLAYER = 3

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def get_ist_time():
    utc_now = datetime.utcnow()
    ist_offset = timedelta(hours=5, minutes=30)
    return utc_now + ist_offset

def get_current_daily_window_start():
    ist_now = get_ist_time()
    if ist_now.hour < DAILY_RESET_HOUR_IST or (ist_now.hour == DAILY_RESET_HOUR_IST and ist_now.minute < DAILY_RESET_MINUTE_IST):
        window_start = ist_now.replace(hour=DAILY_RESET_HOUR_IST, minute=DAILY_RESET_MINUTE_IST, second=0, microsecond=0) - timedelta(days=1)
    else:
        window_start = ist_now.replace(hour=DAILY_RESET_HOUR_IST, minute=DAILY_RESET_MINUTE_IST, second=0, microsecond=0)
    return window_start

def get_next_daily_reset():
    ist_now = get_ist_time()
    if ist_now.hour < DAILY_RESET_HOUR_IST or (ist_now.hour == DAILY_RESET_HOUR_IST and ist_now.minute < DAILY_RESET_MINUTE_IST):
        next_reset = ist_now.replace(hour=DAILY_RESET_HOUR_IST, minute=DAILY_RESET_MINUTE_IST, second=0, microsecond=0)
    else:
        next_reset = ist_now.replace(hour=DAILY_RESET_HOUR_IST, minute=DAILY_RESET_MINUTE_IST, second=0, microsecond=0) + timedelta(days=1)
    return next_reset

def format_time_until_reset(next_reset):
    ist_now = get_ist_time()
    delta = next_reset - ist_now
    hours, remainder = divmod(int(delta.total_seconds()), 3600)
    minutes, _ = divmod(remainder, 60)
    return f"{hours}h {minutes}m"

def create_driver():
    options = Options()
    if HEADLESS:
        options.add_argument("--headless=new")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--incognito")
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-logging")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-web-security")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-default-apps")
    options.add_argument("--disable-features=VizDisplayCompositor")
    options.add_argument("--disable-background-timer-throttling")
    options.add_argument("--disable-renderer-backgrounding")
    options.add_argument("--disable-backgrounding-occluded-windows")
    prefs = {
        "profile.default_content_setting_values": {
            "images": 2,
            "notifications": 2,
            "popups": 2,
        },
        "profile.default_content_settings.popups": 0,
        "profile.managed_default_content_settings.popups": 0,
    }
    options.add_experimental_option("prefs", prefs)
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    try:
        driver_path = ChromeDriverManager().install()
    except:
        driver_path = "/usr/bin/chromedriver"
    service = Service(driver_path)
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(30)
    driver.set_script_timeout(30)
    return driver

def claim_store_rewards(driver, player_id):
    log("🏪 Claiming Store...")
    claimed = 0
    max_claims = 3
    try:
        driver.get("https://hub.vertigogames.co/store")
        time.sleep(2)
        # ... your popup closing/section navigation unchanged ...

        for attempt in range(max_claims):
            log(f"\n--- Store Claim Attempt {attempt + 1}/{max_claims} ---")
            result = driver.execute_script("""
                let allDivs = document.querySelectorAll('div');
                let storeBonusCards = [];
                for (let div of allDivs) {
                    let text = div.innerText || '';
                    if (text.includes('Store Bonus') && text.includes('+1')) {
                        let parent = div.parentElement;
                        let attempts = 0;
                        while (parent && attempts < 5) {
                            let parentText = parent.innerText || '';
                            if (parentText.includes('Gold (Daily)') || parentText.includes('Cash (Daily)') || parentText.includes('Luckyloon (Daily)')) {
                                storeBonusCards.push(parent);
                                break;
                            }
                            parent = parent.parentElement;
                            attempts++;
                        }
                    }
                }
                for (let card of storeBonusCards) {
                    let cardText = card.innerText || '';
                    // ENHANCED: Multiple timer pattern checks
                    let hasTimer = false;
                    if (cardText.includes('Next in')) { hasTimer = true; }
                    if (/\\d+h\\s*\\d*m?/.test(cardText) || /\\d+m/.test(cardText)) { hasTimer = true; }
                    if (cardText.toLowerCase().includes('hour') || cardText.toLowerCase().includes('minute')) { hasTimer = true; }
                    let buttons = card.querySelectorAll('button');
                    for (let btn of buttons) {
                        let btnText = btn.innerText.trim().toLowerCase();
                        if (btnText === 'claim') {
                            let btnStyle = window.getComputedStyle(btn);
                            if (btn.disabled || btn.hasAttribute('disabled')) hasTimer = true;
                        }
                    }
                    if (hasTimer) { continue; }
                    // Actually claim
                    for (let btn of buttons) {
                        let btnText = btn.innerText.trim().toLowerCase();
                        if (btnText === 'claim' && btn.offsetParent !== null && !btn.disabled) {
                            btn.scrollIntoView({behavior: 'smooth', block: 'center'});
                            setTimeout(function() { btn.click(); }, 500);
                            return true;
                        }
                    }
                }
                return false;
            """)
            if result:
                log(f"✅ Store Claim #{claimed + 1} SUCCESS")
                claimed += 1
                time.sleep(1.5)
                # ... your pop-up closing and checks unchanged ...
            else:
                log(f"ℹ️ No more available claims (attempt {attempt + 1})")
                break
        log(f"\n{'='*60}")
        log(f"Store Claims Complete: {claimed}/{max_claims}")
        log(f"{'='*60}")
    except Exception as e:
        log(f"❌ Store error: {e}")
    return claimed

# ... The rest of your script (login, popup, progression, reporting, send_email_summary, process_player, main)... 
# DO NOT CHANGE any other core logic or reporting format.

# Only the function claim_store_rewards and associated call-sites need to be updated as above.

# Be sure to replace only the claim_store_rewards function in your current script!
