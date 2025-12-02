import csv, time, os, smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager

PLAYER_ID_FILE = "players.csv"
HEADLESS = True
DAILY_RESET_HOUR_IST = 5
DAILY_RESET_MINUTE_IST = 30
EXPECTED_STORE_PER_PLAYER = 3
STORE_LOG_FILE = "store_claims_log.csv"

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
    hours, remainder = divmod(delta.seconds, 3600)
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

### TIMESTAMP HELPERS - CSV Tracking ###
def read_store_log():
    """Return {player_id: last_claim_datetime} dict from CSV log"""
    logdata = {}
    try:
        with open(STORE_LOG_FILE, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                logdata[row['player_id']] = datetime.fromisoformat(row['last_claim_timestamp'])
    except FileNotFoundError:
        pass
    return logdata

def get_last_store_claim_time(player_id):
    logdata = read_store_log()
    return logdata.get(player_id)

def update_store_claim_time(player_id, timestamp):
    logdata = read_store_log()
    logdata[player_id] = timestamp
    with open(STORE_LOG_FILE, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['player_id', 'last_claim_timestamp'])
        for pid, ts in logdata.items():
            writer.writerow([pid, ts.isoformat()])

########################################

# Accept cookies, login, page navigation, daily/progression claim functions unchanged.
# Claim Store rewards is UPDATED for v2.3:

def claim_store_rewards(driver, player_id):
    """Claim Store Daily Rewards with Hybrid Timestamp + Visual Verification"""
    log("🏪 Claiming Store...")
    claimed = 0
    max_claims = 3

    last_claim_time = get_last_store_claim_time(player_id)
    hours_since_last = None
    if last_claim_time:
        hours_since_last = (get_ist_time() - last_claim_time).total_seconds() / 3600
    else:
        hours_since_last = 100

    if hours_since_last < 23:
        remaining = 24 - hours_since_last
        log(f"⏭️ Store on cooldown: {remaining:.2f}h remaining")
        return 0

    try:
        driver.get("https://hub.vertigogames.co/store")
        time.sleep(2)
        for _ in range(2): close_popup(driver)
        if not ensure_store_page(driver): log("❌ Cannot access Store"); return 0
        if not navigate_to_daily_rewards_section_store(driver): log("⚠️ Navigation failed"); time.sleep(0.5)
        driver.save_screenshot(f"store_01_ready_{player_id}.png")

        for attempt in range(max_claims):
            log(f"\n--- Store Claim Attempt {attempt + 1}/{max_claims} ---")
            if attempt > 0:
                log("Re-navigating to Daily Rewards section...")
                if not navigate_to_daily_rewards_section_store(driver): log("⚠️ Re-navigation failed"); break
                time.sleep(0.5)

            result = driver.execute_script("""
                let allDivs = document.querySelectorAll('div');
                let storeBonusCards = [];
                for (let div of allDivs) {
                    let text = div.innerText || '';
                    if (text.includes('Store Bonus') && text.includes('+1')) {
                        let parent = div.parentElement; let attempts = 0;
                        while (parent && attempts < 5) {
                            let parentText = parent.innerText || '';
                            if (parentText.includes('Gold (Daily)') || parentText.includes('Cash (Daily)') || parentText.includes('Luckyloon (Daily)')) {
                                storeBonusCards.push(parent); break;
                            }
                            parent = parent.parentElement; attempts++;
                        }
                    }
                }
                for (let card of storeBonusCards) {
                    let cardText = card.innerText || '';
                    if (cardText.includes('Next in') || cardText.match(/\\d+h\\s+\\d+m/)) continue;
                    let buttons = card.querySelectorAll('button');
                    for (let btn of buttons) {
                        let btnText = btn.innerText.trim().toLowerCase();
                        if (btnText === 'claim' && btn.offsetParent !== null && !btn.disabled) {
                            btn.scrollIntoView({behavior: 'smooth', block: 'center'});
                            setTimeout(function(){ btn.click(); }, 500);
                            return true;
                        }
                    }
                }
                return false;
            """)
            if result:
                time.sleep(2)
                timer_appeared = driver.execute_script("""
                    let card = document.querySelector('[class*="reward-card"]');
                    if (!card) return false;
                    let text = card.innerText || '';
                    return text.includes('Next in');
                """)
                if timer_appeared:
                    log(f"❌ Store Claim #{claimed + 1} FAILED (reward on cooldown, DOM timer shown)")
                    break
                else:
                    log(f"✅ Store Claim #{claimed + 1} VERIFIED")
                    claimed += 1
                time.sleep(1.2)
                close_popup(driver)
                if not ensure_store_page(driver): log("⚠️ Lost Store page"); break
                time.sleep(0.3)
            else:
                log(f"ℹ️ No more available claims (attempt {attempt + 1})")
                break

        log(f"\n{'=' * 60}")
        log(f"Store Claims Complete: {claimed}/{max_claims}")
        log(f"{'=' * 60}")
        driver.save_screenshot(f"store_final_{player_id}.png")
        if claimed > 0:
            update_store_claim_time(player_id, get_ist_time())
    except Exception as e:
        log(f"❌ Store error: {e}")
        try: driver.save_screenshot(f"store_error_{player_id}.png")
        except: pass
    return claimed

# ...Rest of your original functions (accept_cookies, login_to_hub, close_popup, ensure_store_page, daily/progression claim, process_player, send_email_summary, main) remain unchanged.

if __name__ == "__main__":
    main()
