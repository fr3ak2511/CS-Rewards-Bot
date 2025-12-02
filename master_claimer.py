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
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager

# =========================
# CONFIG & CONSTANTS
# =========================

PLAYER_ID_FILE = "players.csv"
STORE_LOG_FILE = "store_claims_log.csv"

HEADLESS = True

DAILY_RESET_HOUR_IST = 5
DAILY_RESET_MINUTE_IST = 30
EXPECTED_STORE_PER_PLAYER = 3

# Email configuration
SENDER_EMAIL = os.getenv("SENDER_EMAIL")
RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL", SENDER_EMAIL)
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")


# =========================
# BASIC LOGGING
# =========================

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# =========================
# IST TIME HELPERS
# =========================

def get_ist_time():
    """Get current time in IST (UTC+5:30)"""
    utc_now = datetime.utcnow()
    ist_offset = timedelta(hours=5, minutes=30)
    return utc_now + ist_offset


def get_current_daily_window_start():
    """Get the start of current daily window (5:30 AM IST)"""
    ist_now = get_ist_time()
    if ist_now.hour < DAILY_RESET_HOUR_IST or (
        ist_now.hour == DAILY_RESET_HOUR_IST
        and ist_now.minute < DAILY_RESET_MINUTE_IST
    ):
        window_start = ist_now.replace(
            hour=DAILY_RESET_HOUR_IST,
            minute=DAILY_RESET_MINUTE_IST,
            second=0,
            microsecond=0,
        ) - timedelta(days=1)
    else:
        window_start = ist_now.replace(
            hour=DAILY_RESET_HOUR_IST,
            minute=DAILY_RESET_MINUTE_IST,
            second=0,
            microsecond=0,
        )
    return window_start


def get_next_daily_reset():
    """Get next daily reset time (5:30 AM IST)"""
    ist_now = get_ist_time()
    if ist_now.hour < DAILY_RESET_HOUR_IST or (
        ist_now.hour == DAILY_RESET_HOUR_IST
        and ist_now.minute < DAILY_RESET_MINUTE_IST
    ):
        next_reset = ist_now.replace(
            hour=DAILY_RESET_HOUR_IST,
            minute=DAILY_RESET_MINUTE_IST,
            second=0,
            microsecond=0,
        )
    else:
        next_reset = ist_now.replace(
            hour=DAILY_RESET_HOUR_IST,
            minute=DAILY_RESET_MINUTE_IST,
            second=0,
            microsecond=0,
        ) + timedelta(days=1)
    return next_reset


def format_time_until_reset(next_reset):
    """Format time remaining until next reset"""
    ist_now = get_ist_time()
    delta = next_reset - ist_now
    hours, remainder = divmod(delta.seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    return f"{hours}h {minutes}m"


# =========================
# DRIVER CREATION
# =========================

def create_driver():
    """GitHub Actions-compatible driver"""
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
    except Exception:
        driver_path = "/usr/bin/chromedriver"

    service = Service(driver_path)
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(30)
    driver.set_script_timeout(30)
    return driver


# =========================
# STORE TIMESTAMP LOG
# =========================

def read_store_log():
    logdata = {}
    try:
        with open(STORE_LOG_FILE, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                logdata[row["player_id"]] = datetime.fromisoformat(
                    row["last_claim_timestamp"]
                )
    except FileNotFoundError:
        pass
    return logdata


def get_last_store_claim_time(player_id):
    logdata = read_store_log()
    return logdata.get(player_id)


def update_store_claim_time(player_id, timestamp):
    logdata = read_store_log()
    logdata[player_id] = timestamp
    with open(STORE_LOG_FILE, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["player_id", "last_claim_timestamp"])
        for pid, ts in logdata.items():
            writer.writerow([pid, ts.isoformat()])


# =========================
# COMMON UI HELPERS
# =========================

def accept_cookies(driver):
    """Accept cookie banner"""
    try:
        btn = WebDriverWait(driver, 3).until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "//button[normalize-space()='Accept All' or contains(text(), 'Accept') "
                    "or contains(text(), 'Allow') or contains(text(), 'Consent')]",
                )
            )
        )
        btn.click()
        time.sleep(0.3)
        log("✅ Cookies accepted")
    except TimeoutException:
        log("ℹ️ No cookie banner")


def login_to_hub(driver, player_id):
    """Login using multi-selector strategy"""
    log(f"🔐 Logging in: {player_id}")
    try:
        driver.get("https://hub.vertigogames.co/daily-rewards")
        time.sleep(0.4)
        driver.save_screenshot(f"01_page_loaded_{player_id}.png")
        accept_cookies(driver)

        # Login button detection
        login_selectors = [
            "//button[contains(text(),'Login') or contains(text(),'Log in') or contains(text(), 'Sign in')]",
            "//a[contains(text(),'Login') or contains(text(),'Log in') or contains(text(), 'Sign in')]",
            "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'login')]",
            "//button[contains(text(), 'claim')]",
            "//div[contains(text(), 'Daily Rewards') or contains(text(), 'daily')]//button",
            "//button[contains(@class, 'btn') or contains(@class, 'button')]",
            "//*[contains(text(), 'Login') or contains(text(), 'login')][@onclick or @href or self::button or self::a]",
        ]
        login_clicked = False
        for i, selector in enumerate(login_selectors):
            try:
                elements = driver.find_elements(By.XPATH, selector)
                if elements:
                    for element in elements:
                        try:
                            if element.is_displayed() and element.is_enabled():
                                element.click()
                                login_clicked = True
                                log(f"✅ Login button clicked (selector {i+1})")
                                break
                        except Exception:
                            continue
                if login_clicked:
                    break
            except Exception:
                continue

        if not login_clicked:
            log("❌ No login button found")
            driver.save_screenshot(f"02_login_not_found_{player_id}.png")
            return False

        time.sleep(0.5)
        driver.save_screenshot(f"02_login_clicked_{player_id}.png")

        # Input field detection
        input_selectors = [
            "#user-id-input",
            "//input[contains(@placeholder, 'ID') or contains(@placeholder, 'User') or contains(@name, 'user') or contains(@placeholder, 'id')]",
            "//input[@type='text']",
            "//input[contains(@class, 'input')]",
            "//div[contains(@class, 'modal') or contains(@class, 'dialog')]//input[@type='text']",
        ]
        input_found = False
        input_box = None
        for selector in input_selectors:
            try:
                if selector.startswith("#"):
                    input_box = WebDriverWait(driver, 3).until(
                        EC.visibility_of_element_located((By.ID, selector[1:]))
                    )
                else:
                    input_box = WebDriverWait(driver, 3).until(
                        EC.visibility_of_element_located((By.XPATH, selector))
                    )
                log("✅ Input field found")
                input_box.clear()
                input_box.send_keys(player_id)
                time.sleep(0.1)
                input_found = True
                break
            except Exception:
                continue

        if not input_found:
            log("❌ No input field found")
            driver.save_screenshot(f"03_input_not_found_{player_id}.png")
            return False

        driver.save_screenshot(f"03_input_entered_{player_id}.png")

        # Login CTA detection
        login_cta_selectors = [
            "//button[contains(text(), 'Login') or contains(text(), 'Log in') or contains(text(), 'Sign in')]",
            "//button[@type='submit']",
            "//div[contains(@class, 'modal') or contains(@class, 'dialog')]//button[not(contains(text(), 'Cancel')) and not(contains(text(), 'Close'))]",
            "//button[contains(@class, 'primary') or contains(@class, 'submit')]",
        ]
        login_cta_clicked = False
        for selector in login_cta_selectors:
            try:
                btn = WebDriverWait(driver, 2).until(
                    EC.element_to_be_clickable((By.XPATH, selector))
                )
                btn.click()
                login_cta_clicked = True
                log("✅ Login CTA clicked")
                break
            except Exception:
                continue

        if not login_cta_clicked:
            try:
                input_box.send_keys(Keys.ENTER)
                log("⏎ Enter key pressed")
            except Exception:
                log("❌ Login CTA not found")
                driver.save_screenshot(f"04_cta_not_found_{player_id}.png")
                return False

        time.sleep(1)
        driver.save_screenshot(f"04_submitted_{player_id}.png")

        # Wait for login completion
        log("⏳ Waiting for login...")
        start_time = time.time()
        max_wait = 12
        while time.time() - start_time < max_wait:
            try:
                current_url = driver.current_url
                if (
                    "user" in current_url.lower()
                    or "dashboard" in current_url.lower()
                    or "daily-rewards" in current_url.lower()
                ):
                    log("✅ Login verified (URL)")
                    driver.save_screenshot(f"05_login_success_{player_id}.png")
                    return True

                user_elements = driver.find_elements(
                    By.XPATH,
                    "//button[contains(text(),'Logout') or contains(text(),'Profile') or contains(@class,'user')]",
                )
                if user_elements:
                    log("✅ Login verified (Logout button)")
                    driver.save_screenshot(f"05_login_success_{player_id}.png")
                    return True

                time.sleep(0.3)
            except Exception:
                time.sleep(0.3)

        log("❌ Login verification timeout")
        driver.save_screenshot(f"05_login_timeout_{player_id}.png")
        return False

    except Exception as e:
        log(f"❌ Login exception: {e}")
        try:
            driver.save_screenshot(f"99_exception_{player_id}.png")
        except Exception:
            pass
        return False


def close_popup(driver):
    """Multi-method popup closing strategy"""
    try:
        log("Checking for popup...")
        time.sleep(0.8)

        popup_selectors = [
            "//div[contains(@class, 'modal') and not(contains(@style, 'display: none'))]",
            "//div[contains(@class, 'popup') and not(contains(@style, 'display: none'))]",
            "//div[@data-testid='item-popup-content']",
            "//div[contains(@class, 'dialog') and not(contains(@style, 'display: none'))]",
        ]
        popup_found = False
        for selector in popup_selectors:
            try:
                popup_elements = driver.find_elements(By.XPATH, selector)
                visible_popups = [
                    elem for elem in popup_elements if elem.is_displayed()
                ]
                if visible_popups:
                    popup_found = True
                    log("✓ Popup detected")
                    break
            except Exception:
                continue

        if not popup_found:
            log("No popup detected")
            return True

        # METHOD 1: Continue button
        continue_selectors = [
            "//button[normalize-space()='Continue']",
            "//button[contains(text(), 'Continue')]",
            "//button[contains(@class, 'continue')]",
            "//*[contains(text(), 'Continue') and (self::button or self::a)]",
        ]
        for selector in continue_selectors:
            try:
                continue_btn = driver.find_element(By.XPATH, selector)
                if continue_btn.is_displayed() and continue_btn.is_enabled():
                    try:
                        continue_btn.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", continue_btn)
                    log("✓ Continue clicked")
                    time.sleep(0.8)
                    popup_still_visible = False
                    for ps in popup_selectors:
                        try:
                            popup_elements = driver.find_elements(By.XPATH, ps)
                            if any(elem.is_displayed() for elem in popup_elements):
                                popup_still_visible = True
                                break
                        except Exception:
                            continue
                    if not popup_still_visible:
                        log("✅ Popup closed via Continue")
                        return True
                    break
            except Exception:
                continue

        # METHOD 2: Close button
        close_selectors = [
            "//button[normalize-space()='Close']",
            "//button[contains(@class, 'close')]",
            "//button[contains(@aria-label, 'Close')]",
            "//*[contains(@class, 'close') and (self::button or self::span or self::div[@role='button'])]",
            "//button[text()='×' or text()='X' or text()='✕']",
            "//*[@data-testid='close-button']",
            "//*[contains(@class, 'icon-close')]",
            "//*[name()='svg']/parent::button",
        ]
        for selector in close_selectors:
            try:
                close_btn = driver.find_element(By.XPATH, selector)
                if close_btn.is_displayed():
                    try:
                        close_btn.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", close_btn)
                    log("✓ Close clicked")
                    time.sleep(0.8)
                    popup_still_visible = False
                    for ps in popup_selectors:
                        try:
                            popup_elements = driver.find_elements(By.XPATH, ps)
                            if any(elem.is_displayed() for elem in popup_elements):
                                popup_still_visible = True
                                break
                        except Exception:
                            continue
                    if not popup_still_visible:
                        log("✅ Popup closed via Close button")
                        return True
                    break
            except Exception:
                continue

        # METHOD 3: ESC key
        try:
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            time.sleep(0.5)
            log("✓ ESC pressed")
            return True
        except Exception:
            pass

        log("⚠️ Popup may still be visible")
        return False

    except Exception as e:
        log(f"❌ Popup close error: {e}")
        return False


def ensure_store_page(driver):
    """Check if on Store page"""
    try:
        current_url = driver.current_url
        if "/store" in current_url.lower():
            log("✓ On Store page")
            return True
        log("⚠️ Not on Store, navigating...")
        driver.get("https://hub.vertigogames.co/store")
        time.sleep(0.7)
        if "/store" in driver.current_url.lower():
            log("✓ Back on Store")
            return True
        else:
            log("❌ Failed to reach Store")
            return False
    except Exception as e:
        log(f"❌ Error: {e}")
        return False


def click_daily_rewards_tab(driver):
    """Click Daily Rewards TAB with horizontal scroll"""
    log("Clicking Daily Rewards tab...")
    try:
        result = driver.execute_script(
            """
let allElements = document.querySelectorAll('*');
for (let elem of allElements) {
  if (elem.innerText && elem.innerText.includes('Daily Rewards')) {
    let className = elem.className || '';
    if (!className.toLowerCase().includes('tab')) {
      let parent = elem.parentElement;
      let parentClass = parent ? (parent.className || '') : '';
      if (!parentClass.toLowerCase().includes('tab')) {
        continue;
      }
    }
    let parent = elem.parentElement;
    let parentClass = parent ? (parent.className || '') : '';
    if (parentClass.includes('sidebar') || parentClass.includes('menu') || parentClass.includes('side')) {
      continue;
    }
    elem.scrollIntoView({behavior: 'smooth', block: 'nearest', inline: 'center'});
    setTimeout(() => { elem.click(); }, 800);
    return true;
  }
}
return false;
"""
        )
        if result:
            log("✅ Daily Rewards tab clicked")
            time.sleep(1.0)
            return True
    except Exception as e:
        log(f"❌ Tab click failed: {e}")
    return False


def navigate_to_daily_rewards_section_store(driver):
    """Navigate to Daily Rewards section in Store"""
    log("Navigating to Daily Rewards section...")
    ensure_store_page(driver)
    close_popup(driver)
    time.sleep(0.3)
    tab_clicked = click_daily_rewards_tab(driver)
    if tab_clicked:
        log("✅ In Daily Rewards section... ")
        time.sleep(0.7)
        return True
    log("⚠️ Tab navigation failed")
    return False


# =========================
# CLAIM FUNCTIONS
# =========================

def claim_daily_rewards(driver, player_id):
    """Claim daily rewards page"""
    log("🎁 Claiming Daily Rewards...")
    claimed = 0
    try:
        driver.get("https://hub.vertigogames.co/daily-rewards")
        time.sleep(1.5)
        for _ in range(2):
            close_popup(driver)

        for attempt in range(10):
            result = driver.execute_script(
                """
let buttons = document.querySelectorAll('button');
for (let btn of buttons) {
  let text = btn.innerText.trim().toLowerCase();
  if (text === 'claim' && btn.offsetParent !== null) {
    if (!btn.innerText.toLowerCase().includes('buy') && !btn.innerText.toLowerCase().includes('purchase')) {
      btn.click();
      return true;
    }
  }
}
return false;
"""
            )
            if result:
                log(f"✅ Daily #{claimed + 1}")
                claimed += 1
                time.sleep(1.5)
                close_popup(driver)
            else:
                log("ℹ️ No more daily rewards")
                break

        driver.save_screenshot(f"daily_final_{player_id}.png")
    except Exception as e:
        log(f"❌ Daily error: {e}")
    return claimed


def claim_store_rewards(driver, player_id):
    """Claim Store Daily Rewards - ONLY green available buttons"""
    log("🏪 Claiming Store...")
    claimed = 0
    max_claims = 3

    last_claim_time = get_last_store_claim_time(player_id)
    if last_claim_time:
        hours_since_last = (
            get_ist_time() - last_claim_time
        ).total_seconds() / 3600
    else:
        hours_since_last = 100.0
    if hours_since_last < 23:
        remaining = 24 - hours_since_last
        log(f"⏭️ Store on cooldown: {remaining:.2f}h remaining")
        return 0

    try:
        driver.get("https://hub.vertigogames.co/store")
        time.sleep(2)
        for _ in range(2):
            close_popup(driver)
        if not ensure_store_page(driver):
            log("❌ Cannot access Store")
            return 0
        if not navigate_to_daily_rewards_section_store(driver):
            log("⚠️ Navigation failed")
            time.sleep(0.5)

        driver.save_screenshot(f"store_01_ready_{player_id}.png")

        for attempt in range(max_claims):
            log(f"\n--- Store Claim Attempt {attempt + 1}/{max_claims} ---")
            if attempt > 0:
                log("Re-navigating to Daily Rewards section...")
                if not navigate_to_daily_rewards_section_store(driver):
                    log("⚠️ Re-navigation failed")
                    break
                time.sleep(0.5)

            result = driver.execute_script(
                """
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
  if (cardText.includes('Next in') || cardText.match(/\\d+h\\s+\\d+m/)) {
    continue;
  }
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
"""
            )

            if result:
                log(f"✅ Store Claim #{claimed + 1} SUCCESS")
                claimed += 1
                time.sleep(1.5)
                log("Handling post-claim popup...")
                close_popup(driver)
                time.sleep(0.5)
                if not ensure_store_page(driver):
                    log("⚠️ Lost Store page")
                    break
                time.sleep(0.3)
            else:
                log(f"ℹ️ No more available claims (attempt {attempt + 1})")
                break

        log("\n" + "=" * 60)
        log(f"Store Claims Complete: {claimed}/{max_claims}")
        log("=" * 60)
        driver.save_screenshot(f"store_final_{player_id}.png")

        if claimed > 0:
            update_store_claim_time(player_id, get_ist_time())

    except Exception as e:
        log(f"❌ Store error: {e}")
        try:
            driver.save_screenshot(f"store_error_{player_id}.png")
        except Exception:
            pass

    return claimed


def claim_progression_program_rewards(driver, player_id):
    """Claim Progression Program rewards - FIXED VERSION"""
    log("🎯 Claiming Progression Program...")
    claimed = 0
    try:
        driver.get("https://hub.vertigogames.co/progression-program")
        time.sleep(2)
        for _ in range(2):
            close_popup(driver)
        time.sleep(0.5)
        driver.save_screenshot(f"progression_01_ready_{player_id}.png")

        max_attempts = 8
        for attempt in range(max_attempts):
            log(f"\n--- Progression Claim Attempt {attempt + 1}/{max_attempts} ---")
            result = driver.execute_script(
                """
let allButtons = document.querySelectorAll('button');
let claimButtons = [];
for (let btn of allButtons) {
  let btnText = btn.innerText.trim().toLowerCase();
  if (btnText === 'claim') {
    if (btn.offsetParent !== null && !btn.disabled) {
      let parentText = btn.parentElement ? (btn.parentElement.innerText || '') : '';
      if (!parentText.includes('Delivered')) {
        claimButtons.push(btn);
      }
    }
  }
}
if (claimButtons.length > 0) {
  let btn = claimButtons[0];
  btn.scrollIntoView({behavior: 'smooth', block: 'center', inline: 'center'});
  setTimeout(function(){ btn.click(); }, 600);
  return true;
}
return false;
"""
            )
            if result:
                log(f"✅ Progression Claim #{claimed + 1} SUCCESS")
                claimed += 1
                time.sleep(1.5)
                log("Handling post-claim popup...")
                close_popup(driver)
                time.sleep(0.5)
            else:
                log(f"ℹ️ No more claim buttons (attempt {attempt + 1})")
                if attempt < max_attempts - 1:
                    log("Scrolling horizontally...")
                    try:
                        driver.execute_script(
                            """
let containers = document.querySelectorAll('div');
for (let container of containers) {
  if (container.scrollWidth > container.clientWidth) {
    container.scrollLeft += 400;
    break;
  }
}
"""
                        )
                        log("✓ Scrolled right")
                        time.sleep(1)
                    except Exception:
                        log("⚠️ Scroll failed")
                        break
                else:
                    break

        log("\n" + "=" * 60)
        log(f"Progression Claims Complete: {claimed}")
        log("=" * 60)
        driver.save_screenshot(f"progression_final_{player_id}.png")

    except Exception as e:
        log(f"❌ Progression error: {e}")
        try:
            driver.save_screenshot(f"progression_error_{player_id}.png")
        except Exception:
            pass
    return claimed


# =========================
# PROCESS ONE PLAYER
# =========================

def process_player(player_id):
    """Process single player - ALL reward pages IN CORRECT ORDER"""
    driver = None
    stats = {
        "id": player_id,
        "daily": 0,
        "store": 0,
        "progression": 0,
        "status": "Failed",
    }
    try:
        log("\n" + "=" * 60)
        log(f"🚀 {player_id}")
        log("=" * 60)

        driver = create_driver()
        log("✅ Driver ready")

        if not login_to_hub(driver, player_id):
            stats["status"] = "Login Failed"
            return stats

        stats["daily"] = claim_daily_rewards(driver, player_id)
        stats["store"] = claim_store_rewards(driver, player_id)
        stats["progression"] = claim_progression_program_rewards(driver, player_id)

        total = stats["daily"] + stats["store"] + stats["progression"]
        if total > 0:
            stats["status"] = "Success"
            log(
                f"🎉 Total: {total} (D:{stats['daily']} "
                f"S:{stats['store']} P:{stats['progression']})"
            )
        else:
            stats["status"] = "No Rewards"
            log("⚠️ None claimed")

    except Exception as e:
        log(f"❌ Error: {e}")
        stats["status"] = "Error"
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
    return stats


# =========================
# HTML EMAIL SUMMARY
# =========================

def build_email_html(results, window_start, next_reset):
    ist_now = get_ist_time()
    now_str = ist_now.strftime("%Y-%m-%d %I:%M %p IST")
    window_start_str = window_start.strftime("%Y-%m-%d %I:%M %p IST")
    next_reset_str = next_reset.strftime("%Y-%m-%d %I:%M %p IST")
    time_until_reset = format_time_until_reset(next_reset)

    total_daily = sum(r["daily"] for r in results)
    total_store = sum(r["store"] for r in results)
    total_prog = sum(r["progression"] for r in results)
    total_all = total_daily + total_store + total_prog

    total_claims = sum(
        (r["daily"] + r["store"] + r["progression"]) > 0 for r in results
    )

    rows_html = ""
    for r in results:
        total = r["daily"] + r["store"] + r["progression"]
        status = r["status"]
        bg = "#ffffff"
        if status.startswith("Success"):
            bg = "#e6ffed"
        elif "Login Failed" in status or status.startswith("Error"):
            bg = "#ffecec"

        rows_html += f"""
      <tr style="background:{bg};">
        <td style="padding:8px 12px;border-bottom:1px solid #eee;font-family:monospace;">{r['id']}</td>
        <td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:center;">{r['daily']}</td>
        <td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:center;">{r['store']}</td>
        <td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:center;">{r['progression']}</td>
        <td style="padding:8px 12px;border-bottom:1px solid #eee;text-align:center;font-weight:600;">{total}</td>
        <td style="padding:8px 12px;border-bottom:1px solid #eee;">{status}</td>
      </tr>
    """

    rows_html += f"""
      <tr style="background:#f9fafb;font-weight:600;">
        <td style="padding:8px 12px;border-top:2px solid #ddd;">TOTAL</td>
        <td style="padding:8px 12px;border-top:2px solid #ddd;text-align:center;">{total_daily}</td>
        <td style="padding:8px 12px;border-top:2px solid #ddd;text-align:center;">{total_store}</td>
        <td style="padding:8px 12px;border-top:2px solid #ddd;text-align:center;">{total_prog}</td>
        <td style="padding:8px 12px;border-top:2px solid #ddd;text-align:center;">{total_all}</td>
        <td style="padding:8px 12px;border-top:2px solid #ddd;"></td>
      </tr>
    """

    html = f"""
  <html>
    <body style="margin:0;padding:0;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
      <table width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6;padding:20px 0;">
        <tr>
          <td align="center">
            <table width="650" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:12px;overflow:hidden;box-shadow:0 10px 30px rgba(15,23,42,0.15);">
              <tr>
                <td style="background:linear-gradient(135deg,#4f46e5,#6366f1);padding:20px 24px;color:#ffffff;">
                  <div style="font-size:24px;font-weight:700;display:flex;align-items:center;">
                    <span style="font-size:26px;margin-right:8px;">🎮</span>
                    Hub Rewards Summary
                  </div>
                  <div style="margin-top:4px;font-size:13px;opacity:0.9;">
                    Run: {now_str}
                  </div>
                </td>
              </tr>

              <tr>
                <td style="padding:20px 24px;background:#f9fafb;border-bottom:1px solid #e5e7eb;">
                  <table width="100%" cellpadding="0" cellspacing="0" style="font-size:13px;">
                    <tr>
                      <td style="width:50%;vertical-align:top;padding-right:10px;">
                        <div style="font-weight:600;margin-bottom:6px;">⏱ Timing Info</div>
                        <table cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;">
                          <tr>
                            <td style="padding:4px 0;color:#6b7280;">Current Time:</td>
                            <td style="padding:4px 0;text-align:right;color:#111827;">{now_str}</td>
                          </tr>
                          <tr>
                            <td style="padding:4px 0;color:#6b7280;">Window Started:</td>
                            <td style="padding:4px 0;text-align:right;color:#111827;">{window_start_str}</td>
                          </tr>
                          <tr>
                            <td style="padding:4px 0;color:#6b7280;">Next Reset:</td>
                            <td style="padding:4px 0;text-align:right;color:#111827;">{next_reset_str}</td>
                          </tr>
                          <tr>
                            <td style="padding:4px 0;color:#6b7280;">Time to Reset:</td>
                            <td style="padding:4px 0;text-align:right;color:#111827;">{time_until_reset}</td>
                          </tr>
                        </table>
                      </td>
                      <td style="width:50%;vertical-align:top;padding-left:10px;border-left:1px solid #e5e7eb;">
                        <div style="font-weight:600;margin-bottom:6px;">📊 Summary</div>
                        <table cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;">
                          <tr>
                            <td style="padding:4px 0;color:#374151;">🏅 Total Daily:</td>
                            <td style="padding:4px 0;text-align:right;font-weight:600;">{total_daily}</td>
                          </tr>
                          <tr>
                            <td style="padding:4px 0;color:#374151;">🏬 Total Store:</td>
                            <td style="padding:4px 0;text-align:right;font-weight:600;">{total_store}</td>
                          </tr>
                          <tr>
                            <td style="padding:4px 0;color:#374151;">📈 Total Progression:</td>
                            <td style="padding:4px 0;text-align:right;font-weight:600;">{total_prog}</td>
                          </tr>
                          <tr>
                            <td style="padding:4px 0;color:#111827;font-weight:600;">🔥 TOTAL ALL:</td>
                            <td style="padding:4px 0;text-align:right;font-weight:700;color:#16a34a;">{total_all} claims</td>
                          </tr>
                          <tr>
                            <td style="padding:4px 0;color:#6b7280;">Players with rewards:</td>
                            <td style="padding:4px 0;text-align:right;color:#111827;">{total_claims}</td>
                          </tr>
                        </table>
                      </td>
                    </tr>
                  </table>
                </td>
              </tr>

              <tr>
                <td style="padding:18px 24px 4px 24px;">
                  <div style="font-weight:600;margin-bottom:8px;">📋 Per-Player Breakdown (This Run)</div>
                  <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;font-size:13px;">
                    <thead>
                      <tr style="background:#f3f4f6;border-bottom:1px solid #e5e7eb;">
                        <th style="padding:8px 12px;text-align:left;font-weight:600;color:#4b5563;">ID</th>
                        <th style="padding:8px 12px;text-align:center;font-weight:600;color:#4b5563;">Daily</th>
                        <th style="padding:8px 12px;text-align:center;font-weight:600;color:#4b5563;">Store</th>
                        <th style="padding:8px 12px;text-align:center;font-weight:600;color:#4b5563;">Progression</th>
                        <th style="padding:8px 12px;text-align:center;font-weight:600;color:#4b5563;">Total</th>
                        <th style="padding:8px 12px;text-align:left;font-weight:600;color:#4b5563;">Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rows_html}
                    </tbody>
                  </table>
                </td>
              </tr>

              <tr>
                <td style="padding:16px 24px 20px 24px;">
                  <div style="font-size:12px;color:#6b7280;border-top:1px dashed #e5e7eb;padding-top:10px;">
                    <div style="margin-bottom:4px;"><strong>Note:</strong></div>
                    <ul style="margin:0 0 6px 16px;padding:0;">
                      <li>Store Rewards: Earns 3 per player per day (resets 24h after last claim).</li>
                      <li>Daily Rewards: One per 24h per player (window-based, not clock-time only).</li>
                      <li>Progression: Unlimited (upgrade envelopes from Store claims).</li>
                    </ul>
                    <div style="margin-top:4px;color:#9ca3af;">
                      Automated run at {now_str}
                    </div>
                  </div>
                </td>
              </tr>
            </table>
          </td>
        </tr>
      </table>
    </body>
  </html>
  """
    subject = f"Hub Rewards - {ist_now.strftime('%d-%b %I:%M %p')} IST ({total_all} claims)"
    return subject, html


def send_email_summary(results, window_start, next_reset):
    if not SENDER_EMAIL or not RECIPIENT_EMAIL or not GMAIL_APP_PASSWORD:
        log("⚠️ Email not sent – SMTP env vars missing.")
        return

    subject, html = build_email_html(results, window_start, next_reset)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SENDER_EMAIL
    msg["To"] = RECIPIENT_EMAIL

    part = MIMEText(html, "html")
    msg.attach(part)

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(SENDER_EMAIL, GMAIL_APP_PASSWORD)
            server.sendmail(SENDER_EMAIL, [RECIPIENT_EMAIL], msg.as_string())
        log("✅ Summary email sent.")
    except Exception as e:
        log(f"❌ Failed to send email: {e}")


# =========================
# MAIN
# =========================

def main():
    log("🚀 Hub claimer run started")
    ist_now = get_ist_time()
    window_start = get_current_daily_window_start()
    next_reset = get_next_daily_reset()
    log(f"🕐 IST: {ist_now.strftime('%Y-%m-%d %I:%M %p')}")
    log(f"⏰ Next Reset: {format_time_until_reset(next_reset)}")

    # Read players (expects header 'id')
    try:
        with open(PLAYER_ID_FILE, "r") as f:
            reader = csv.DictReader(f)
            players = [row["id"].strip() for row in reader if row.get("id", "").strip()]
    except Exception as e:
        log(f"❌ Cannot read {PLAYER_ID_FILE}: {e}")
        return

    num_players = len(players)
    log(f"📋 {num_players} player(s)")
    log(players)

    results = []
    for player_id in players:
        stats = process_player(player_id)
        results.append(stats)
        time.sleep(3)

    log("")
    log("=" * 60)
    log("FINAL SUMMARY")
    log("=" * 60)
    total_d = sum(r["daily"] for r in results)
    total_s = sum(r["store"] for r in results)
    total_p = sum(r["progression"] for r in results)
    log(
        f"Daily: {total_d}, Store: {total_s}/{num_players * EXPECTED_STORE_PER_PLAYER}, "
        f"Progression: {total_p}"
    )
    for r in results:
        total = r["daily"] + r["store"] + r["progression"]
        log(
            f"{r['id']}: D={r['daily']}, S={r['store']}, "
            f"P={r['progression']}, Total={total} → {r['status']}"
        )

    send_email_summary(results, window_start, next_reset)
    log("")
    log("🏁 Done!")


if __name__ == "__main__":
    main()
