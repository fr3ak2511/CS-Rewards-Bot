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

PLAYER_ID_FILE = "players.csv"
HEADLESS = True

# Daily tracking constants
DAILY_RESET_HOUR_IST = 5   # 5:30 AM IST
DAILY_RESET_MINUTE_IST = 30
EXPECTED_STORE_PER_PLAYER = 3


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ========= IST timezone helper functions =========

def get_ist_time():
    """Get current time in IST (UTC+5:30)."""
    utcnow = datetime.utcnow()
    ist_offset = timedelta(hours=5, minutes=30)
    return utcnow + ist_offset


def get_current_daily_window_start():
    """Get the start of current daily window (5:30 AM IST)."""
    ist_now = get_ist_time()
    if (ist_now.hour < DAILY_RESET_HOUR_IST or
        (ist_now.hour == DAILY_RESET_HOUR_IST and ist_now.minute < DAILY_RESET_MINUTE_IST)):
        window_start = ist_now.replace(hour=DAILY_RESET_HOUR_IST,
                                       minute=DAILY_RESET_MINUTE_IST,
                                       second=0, microsecond=0) - timedelta(days=1)
    else:
        window_start = ist_now.replace(hour=DAILY_RESET_HOUR_IST,
                                       minute=DAILY_RESET_MINUTE_IST,
                                       second=0, microsecond=0)
    return window_start


def get_next_daily_reset():
    """Get next daily reset time (5:30 AM IST)."""
    ist_now = get_ist_time()
    if (ist_now.hour < DAILY_RESET_HOUR_IST or
        (ist_now.hour == DAILY_RESET_HOUR_IST and ist_now.minute < DAILY_RESET_MINUTE_IST)):
        next_reset = ist_now.replace(hour=DAILY_RESET_HOUR_IST,
                                     minute=DAILY_RESET_MINUTE_IST,
                                     second=0, microsecond=0)
    else:
        next_reset = ist_now.replace(hour=DAILY_RESET_HOUR_IST,
                                     minute=DAILY_RESET_MINUTE_IST,
                                     second=0, microsecond=0) + timedelta(days=1)
    return next_reset


def format_time_until_reset(next_reset):
    """Format time remaining until next reset."""
    ist_now = get_ist_time()
    delta = next_reset - ist_now
    hours, remainder = divmod(delta.seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    return f"{hours}h {minutes}m"


# ========= Driver setup =========

def create_driver():
    """GitHub Actions-compatible Chrome driver."""
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
        "profile.default_content_setting_values.images": 2,
        "profile.default_content_setting_values.notifications": 2,
        "profile.default_content_setting_values.popups": 2,
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


# ========= Common helpers =========

def accept_cookies(driver):
    """Accept cookie banner if visible."""
    try:
        btn = WebDriverWait(driver, 3).until(
            EC.element_to_be_clickable(
                (By.XPATH,
                 "//button[normalize-space()='Accept All' or contains(text(),'Accept') "
                 "or contains(text(),'Allow') or contains(text(),'Consent')]")
            )
        )
        btn.click()
        time.sleep(0.3)
        log("Cookies accepted")
    except TimeoutException:
        log("No cookie banner")


def login_to_hub(driver, player_id):
    """Login flow using resilient selectors."""
    log(f"Logging in {player_id}")
    try:
        driver.get("https://hub.vertigogames.co/daily-rewards")
        time.sleep(0.4)
        driver.save_screenshot(f"01_page_loaded_{player_id}.png")
        accept_cookies(driver)

        # 1) Login button
        login_selectors = [
            "//button[contains(text(),'Login') or contains(text(),'Log in') or contains(text(),'Sign in')]",
            "//a[contains(text(),'Login') or contains(text(),'Log in') or contains(text(),'Sign in')]",
            "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'login')]",
            "//button[contains(text(),'claim') or contains(text(),'Claim')]",
            "//div[contains(text(),'Daily Rewards') or contains(text(),'daily')]//button",
            "//button[contains(@class,'btn') or contains(@class,'button')][contains(text(),'Login') or contains(text(),'login')]",
        ]

        login_clicked = False
        for i, selector in enumerate(login_selectors, start=1):
            try:
                elements = driver.find_elements(By.XPATH, selector)
                if not elements:
                    continue
                for element in elements:
                    try:
                        if element.is_displayed() and element.is_enabled():
                            element.click()
                            login_clicked = True
                            log(f"Login button clicked (selector {i})")
                            break
                    except Exception:
                        continue
                if login_clicked:
                    break
            except Exception:
                continue

        if not login_clicked:
            log("No login button found")
            driver.save_screenshot(f"02_login_not_found_{player_id}.png")
            return False

        time.sleep(0.5)
        driver.save_screenshot(f"02_login_clicked_{player_id}.png")

        # 2) Input field
        input_selectors = [
            "user-id-input",
            "//input[contains(@placeholder,'ID') or contains(@placeholder,'User') "
            "or contains(@name,'user') or contains(@placeholder,'id')]",
            "//input[@type='text']",
            "//div[contains(@class,'modal') or contains(@class,'dialog')]//input[@type='text']",
        ]
        input_found = False
        input_box = None

        for selector in input_selectors:
            try:
                if selector == "user-id-input":
                    input_box = WebDriverWait(driver, 3).until(
                        EC.visibility_of_element_located((By.ID, "user-id-input"))
                    )
                else:
                    input_box = WebDriverWait(driver, 3).until(
                        EC.visibility_of_element_located((By.XPATH, selector))
                    )
                log("Input field found")
                input_box.clear()
                input_box.send_keys(player_id)
                time.sleep(0.1)
                input_found = True
                break
            except Exception:
                continue

        if not input_found:
            log("No input field found")
            driver.save_screenshot(f"03_input_not_found_{player_id}.png")
            return False

        driver.save_screenshot(f"03_input_entered_{player_id}.png")

        # 3) Login CTA
        login_cta_selectors = [
            "//button[contains(text(),'Login') or contains(text(),'Log in') or contains(text(),'Sign in')]",
            "//button[@type='submit']",
            "//div[contains(@class,'modal') or contains(@class,'dialog')]//button[not(contains(text(),'Cancel')) and not(contains(text(),'Close'))]",
            "//button[contains(@class,'primary') or contains(@class,'submit')]",
        ]
        login_cta_clicked = False
        for selector in login_cta_selectors:
            try:
                btn = WebDriverWait(driver, 2).until(
                    EC.element_to_be_clickable((By.XPATH, selector))
                )
                btn.click()
                login_cta_clicked = True
                log("Login CTA clicked")
                break
            except Exception:
                continue

        if not login_cta_clicked:
            try:
                input_box.send_keys(Keys.ENTER)
                log("Enter key pressed")
            except Exception:
                log("Login CTA not found")
                driver.save_screenshot(f"04_cta_not_found_{player_id}.png")
                return False

        time.sleep(1)
        driver.save_screenshot(f"04_submitted_{player_id}.png")

        # 4) Wait for login complete
        log("Waiting for login...")
        start_time = time.time()
        max_wait = 12
        while time.time() - start_time < max_wait:
            try:
                current_url = driver.current_url.lower()
                if ("user" in current_url or
                        "dashboard" in current_url or
                        "daily-rewards" in current_url):
                    log("Login verified by URL")
                    driver.save_screenshot(f"05_login_success_{player_id}.png")
                    return True
                user_elems = driver.find_elements(
                    By.XPATH,
                    "//button[contains(text(),'Logout') or contains(text(),'Profile')]"
                )
                if user_elems:
                    log("Login verified by user button")
                    driver.save_screenshot(f"05_login_success_{player_id}.png")
                    return True
                time.sleep(0.3)
            except Exception:
                time.sleep(0.3)

        log("Login verification timeout")
        driver.save_screenshot(f"05_login_timeout_{player_id}.png")
        return False

    except Exception as e:
        log(f"Login exception: {e}")
        try:
            driver.save_screenshot(f"99_exception_{player_id}.png")
        except Exception:
            pass
        return False


def close_popup(driver):
    """Best-effort popup closing (Continue, Close, ESC)."""
    try:
        log("Checking for popup...")
        time.sleep(0.8)
        popup_selectors = [
            "//div[contains(@class,'modal') and not(contains(@style,'display: none'))]",
            "//div[contains(@class,'popup') and not(contains(@style,'display: none'))]",
            "//div[@data-testid='item-popup-content']",
            "//div[contains(@class,'dialog') and not(contains(@style,'display: none'))]",
        ]
        popup_found = False
        for selector in popup_selectors:
            try:
                elems = driver.find_elements(By.XPATH, selector)
                visible = [e for e in elems if e.is_displayed()]
                if visible:
                    popup_found = True
                    log("Popup detected...")
                    break
            except Exception:
                continue
        if not popup_found:
            log("No popup detected")
            return True

        # 1) Continue button
        continue_selectors = [
            "//button[normalize-space()='Continue']",
            "//button[contains(text(),'Continue')]",
            "//button[contains(@class,'continue')]",
            "(//button[contains(text(),'Continue')])[1]",
        ]
        for selector in continue_selectors:
            try:
                btn = driver.find_element(By.XPATH, selector)
                if btn.is_displayed() and btn.is_enabled():
                    try:
                        btn.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", btn)
                    log("Continue clicked")
                    time.sleep(0.8)
                    popup_still = False
                    for ps in popup_selectors:
                        try:
                            elems = driver.find_elements(By.XPATH, ps)
                            if any(e.is_displayed() for e in elems):
                                popup_still = True
                                break
                        except Exception:
                            continue
                    if not popup_still:
                        log("Popup closed via Continue")
                        return True
                    break
            except Exception:
                continue

        # 2) Close button
        close_selectors = [
            "//button[normalize-space()='Close']",
            "//button[contains(@class,'close')]",
            "//button[contains(@aria-label,'Close')]",
            "//button[.='X' or .='x']",
        ]
        for selector in close_selectors:
            try:
                btn = driver.find_element(By.XPATH, selector)
                if btn.is_displayed():
                    try:
                        btn.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", btn)
                    log("Close clicked")
                    time.sleep(0.8)
                    popup_still = False
                    for ps in popup_selectors:
                        try:
                            elems = driver.find_elements(By.XPATH, ps)
                            if any(e.is_displayed() for e in elems):
                                popup_still = True
                                break
                        except Exception:
                            continue
                    if not popup_still:
                        log("Popup closed via Close button")
                        return True
                    break
            except Exception:
                continue

        # 3) ESC key
        try:
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            time.sleep(0.5)
            log("ESC pressed")
            return True
        except Exception:
            log("Popup may still be visible")
            return False

    except Exception as e:
        log(f"Popup close error: {e}")
        return False


def ensure_store_page(driver):
    """Ensure we are on Store page."""
    try:
        current_url = driver.current_url.lower()
        if "store" in current_url:
            log("On Store page")
            return True
        log("Not on Store, navigating...")
        driver.get("https://hub.vertigogames.co/store")
        time.sleep(0.7)
        if "store" in driver.current_url.lower():
            log("Back on Store")
            return True
        log("Failed to reach Store")
        return False
    except Exception as e:
        log(f"Error ensuring Store: {e}")
        return False


def click_daily_rewards_tab(driver):
    """Click Daily Rewards tab in Store (top horizontal tabs)."""
    log("Clicking Daily Rewards tab...")
    try:
        result = driver.execute_script("""
            let all = document.querySelectorAll('*');
            for (let elem of all) {
                if (!elem.innerText) continue;
                if (!elem.innerText.includes('Daily Rewards')) continue;
                let className = (elem.className || '').toString().toLowerCase();
                let parent = elem.parentElement;
                let parentClass = parent ? (parent.className || '').toString().toLowerCase() : '';
                if (!className.includes('tab') && !parentClass.includes('tab')) continue;
                // Skip left sidebar type menus
                if (parentClass.includes('sidebar') || parentClass.includes('menu')) continue;
                elem.scrollIntoView({behavior:'smooth', block:'nearest', inline:'center'});
                setTimeout(() => elem.click(), 800);
                return true;
            }
            return false;
        """)
        if result:
            log("Daily Rewards tab clicked")
            time.sleep(1.0)
            return True
        log("Daily Rewards tab not found")
        return False
    except Exception as e:
        log(f"Tab click failed: {e}")
        return False


def navigate_to_daily_rewards_section_store(driver):
    """Navigate to Store > Daily Rewards section."""
    log("Navigating to Daily Rewards section...")
    if not ensure_store_page(driver):
        return False
    close_popup(driver)
    time.sleep(0.3)
    if click_daily_rewards_tab(driver):
        log("In Daily Rewards section...")
        time.sleep(0.7)
        return True
    log("Tab navigation failed")
    return False


# ========= Claimers =========

def claim_daily_rewards(driver, player_id):
    log("Claiming Daily Rewards...")
    claimed = 0
    try:
        driver.get("https://hub.vertigogames.co/daily-rewards")
        time.sleep(1.5)
        for _ in range(2):
            close_popup(driver)
        for attempt in range(10):
            result = driver.execute_script("""
                let buttons = document.querySelectorAll('button');
                for (let btn of buttons) {
                    let text = (btn.innerText || '').trim().toLowerCase();
                    if (!text.includes('claim')) continue;
                    if (!btn.offsetParent) continue;
                    if (text.includes('buy') || text.includes('purchase')) continue;
                    btn.click();
                    return true;
                }
                return false;
            """)
            if result:
                log(f"Daily claimed (#{attempt+1})")
                claimed += 1
                time.sleep(1.5)
                close_popup(driver)
            else:
                log("No more daily rewards")
                break
        driver.save_screenshot(f"daily_final_{player_id}.png")
    except Exception as e:
        log(f"Daily error: {e}")
    return claimed


def claim_store_rewards(driver, player_id):
    """
    Claim Store Daily Rewards (Gold, Cash, Luckyloon) by DOM inspection only.
    Always open Store & Daily Rewards and find green Claim buttons with NO timer text.
    """
    log("Claiming Store...")
    claimed = 0
    max_claims = EXPECTED_STORE_PER_PLAYER
    try:
        driver.get("https://hub.vertigogames.co/store")
        time.sleep(2)
        for _ in range(2):
            close_popup(driver)

        if not ensure_store_page(driver):
            log("Cannot access Store")
            return 0
        if not navigate_to_daily_rewards_section_store(driver):
            log("Navigation failed")
            time.sleep(0.5)
            driver.save_screenshot(f"store_final_{player_id}.png")
            return 0

        driver.save_screenshot(f"store_01_ready_{player_id}.png")

        for attempt in range(max_claims):
            log(f"--- Store Claim Attempt {attempt+1}/{max_claims} ---")
            if attempt > 0:
                log("Re-navigating to Daily Rewards section...")
                if not navigate_to_daily_rewards_section_store(driver):
                    log("Re-navigation failed")
                    break
                time.sleep(0.5)

            result = driver.execute_script("""
                // Find cards that show 'Store Bonus' and 1/1 and specific Daily items
                let allDivs = document.querySelectorAll('div');
                let storeBonusCards = [];
                for (let div of allDivs) {
                    let text = (div.innerText || '');
                    if (!text.includes('Store Bonus') || !text.includes('1/1')) continue;
                    let parent = div.parentElement;
                    let attempts = 0;
                    while (parent && attempts < 5) {
                        let pt = (parent.innerText || '');
                        if (pt.includes('Gold (Daily)') ||
                            pt.includes('Cash (Daily)') ||
                            pt.includes('Luckyloon (Daily)')) {
                            storeBonusCards.push(parent);
                            break;
                        }
                        parent = parent.parentElement;
                        attempts++;
                    }
                }

                // Within those cards, only click Claim buttons that don't show timers
                for (let card of storeBonusCards) {
                    let cardText = (card.innerText || '');
                    if (cardText.includes('Next in') || cardText.match(/\\d+h \\d+m/)) {
                        continue; // timer card – skip
                    }
                    let buttons = card.querySelectorAll('button');
                    for (let btn of buttons) {
                        let btnText = (btn.innerText || '').trim().toLowerCase();
                        if (!btnText.includes('claim')) continue;
                        if (!btn.offsetParent || btn.disabled) continue;

                        // Extra guard: ignore anything that also has timer in same button
                        if (btnText.includes('next in')) continue;

                        btn.scrollIntoView({behavior:'smooth', block:'center', inline:'center'});
                        setTimeout(function () {
                            btn.click();
                            console.log('Clicked GREEN Claim button');
                        }, 500);
                        return true;
                    }
                }
                console.log('No more available claim buttons found');
                return false;
            """)

            if result:
                log(f"Store Claim #{attempt+1} SUCCESS")
                claimed += 1
                time.sleep(1.5)
                log("Handling post-claim popup...")
                close_popup(driver)
                time.sleep(0.5)
                if not ensure_store_page(driver):
                    log("Lost Store page, stopping")
                    break
                time.sleep(0.3)
            else:
                log(f"No more available claims (attempt {attempt+1})")
                break

        log(f"Store Claims Complete: {claimed}/{max_claims}")
        driver.save_screenshot(f"store_final_{player_id}.png")

    except Exception as e:
        log(f"Store error: {e}")
        try:
            driver.save_screenshot(f"store_error_{player_id}.png")
        except Exception:
            pass

    return claimed


def claim_progression_program_rewards(driver, player_id):
    log("Claiming Progression Program...")
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
            log(f"--- Progression Claim Attempt {attempt+1}/{max_attempts} ---")
            result = driver.execute_script("""
                let allButtons = document.querySelectorAll('button');
                let claimButtons = [];
                for (let btn of allButtons) {
                    let t = (btn.innerText || '').trim().toLowerCase();
                    if (!t.includes('claim')) continue;
                    if (!btn.offsetParent || btn.disabled) continue;
                    let parentText = (btn.parentElement ? btn.parentElement.innerText : '') || '';
                    if (parentText.includes('Delivered')) continue;
                    claimButtons.push(btn);
                }
                console.log('Found', claimButtons.length, 'claim buttons');
                if (claimButtons.length === 0) return false;
                let btn = claimButtons[0];
                btn.scrollIntoView({behavior:'smooth', block:'center', inline:'center'});
                setTimeout(function () {
                    btn.click();
                    console.log('Clicked Progression Claim button');
                }, 600);
                return true;
            """)

            if result:
                log(f"Progression Claim #{attempt+1} SUCCESS")
                claimed += 1
                time.sleep(1.5)
                log("Handling post-claim popup...")
                close_popup(driver)
                time.sleep(0.5)
            else:
                log(f"No more claim buttons (attempt {attempt+1})")
                if attempt < max_attempts - 1:
                    log("Scrolling horizontally...")
                    try:
                        driver.execute_script("""
                            let containers = document.querySelectorAll('div');
                            for (let c of containers) {
                                if (c.scrollWidth > c.clientWidth) {
                                    c.scrollLeft += 400;
                                    console.log('Scrolled right');
                                    break;
                                }
                            }
                        """)
                        log("Scrolled right")
                        time.sleep(1)
                    except Exception:
                        log("Scroll failed")
                        break
                else:
                    break

        log(f"Progression Claims Complete: {claimed}")
        driver.save_screenshot(f"progression_final_{player_id}.png")

    except Exception as e:
        log(f"Progression error: {e}")
        try:
            driver.save_screenshot(f"progression_error_{player_id}.png")
        except Exception:
            pass

    return claimed


# ========= Per-player processing & email =========

def process_player(player_id):
    """Process single player across Daily, Store, Progression (in that order)."""
    driver = None
    stats = {
        "playerid": player_id,
        "daily": 0,
        "store": 0,
        "progression": 0,
        "status": "Failed",
    }
    try:
        log("=" * 60)
        log(player_id)
        log("=" * 60)
        driver = create_driver()
        log("Driver ready")

        if not login_to_hub(driver, player_id):
            stats["status"] = "Login Failed"
            return stats

        stats["daily"] = claim_daily_rewards(driver, player_id)
        stats["store"] = claim_store_rewards(driver, player_id)
        stats["progression"] = claim_progression_program_rewards(driver, player_id)

        total = stats["daily"] + stats["store"] + stats["progression"]
        if total > 0:
            stats["status"] = "Success"
            log(f"Total {total} (D{stats['daily']} S{stats['store']} P{stats['progression']})")
        else:
            stats["status"] = "No Rewards"
            log("None claimed")
    except Exception as e:
        log(f"Error processing {player_id}: {e}")
        stats["status"] = "Error"
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
    return stats


def send_email_summary(results, num_players):
    """Send HTML email with summary table."""
    try:
        sender = os.environ.get("SENDEREMAIL")
        recipient = os.environ.get("RECIPIENTEMAIL")
        password = os.environ.get("GMAILAPPPASSWORD")
        if not all([sender, recipient, password]):
            log("Email env vars missing")
            return

        total_d = sum(r["daily"] for r in results)
        total_s = sum(r["store"] for r in results)
        total_p = sum(r["progression"] for r in results)
        total_all = total_d + total_s + total_p
        success_count = sum(1 for r in results if r["status"] == "Success")

        expected_store_total = num_players * EXPECTED_STORE_PER_PLAYER
        store_progress_pct = int(total_s / expected_store_total * 100) if expected_store_total else 0

        ist_now = get_ist_time()
        window_start = get_current_daily_window_start()
        next_reset = get_next_daily_reset()
        time_until_reset = format_time_until_reset(next_reset)
        hours_since_reset = int((ist_now - window_start).total_seconds() / 3600)

        html = f"""
<html>
  <body style="font-family: Arial, sans-serif;">
    <h2>Hub Rewards Summary</h2>

    <div style="background-color:#f0f8ff;padding:15px;border-radius:8px;margin-bottom:20px;">
      <h3 style="margin-top:0;">Daily Window Tracking (5:30 AM IST Reset)</h3>
      <table style="width:100%;border-collapse:collapse;">
        <tr><td style="padding:5px;"><strong>Current Time</strong></td>
            <td>{ist_now.strftime('%Y-%m-%d %I:%M %p')} IST</td></tr>
        <tr><td style="padding:5px;"><strong>Window Started</strong></td>
            <td>{window_start.strftime('%Y-%m-%d %I:%M %p')} IST ({hours_since_reset}h ago)</td></tr>
        <tr><td style="padding:5px;"><strong>Next Reset</strong></td>
            <td>{next_reset.strftime('%Y-%m-%d %I:%M %p')} IST (in {time_until_reset})</td></tr>
      </table>
    </div>

    <div style="background-color:#fff3cd;padding:15px;border-radius:8px;margin-bottom:20px;">
      <h3 style="margin-top:0;">Today's Cumulative Stats (since 5:30 AM)</h3>
      <table style="width:100%;border-collapse:collapse;">
        <tr><td style="padding:5px;"><strong>Total Daily</strong></td>
            <td><strong>{total_d}</strong> (varies per player)</td></tr>
        <tr style="background-color:{'#d4edda' if total_s == expected_store_total else '#fff3cd'};">
          <td style="padding:5px;"><strong>Total Store</strong></td>
          <td><strong>{total_s}/{expected_store_total}</strong> ({store_progress_pct}% COMPLETE)</td>
        </tr>
        <tr><td style="padding:5px;"><strong>Total Progression</strong></td>
            <td><strong>{total_p}</strong> (grenade-dependent)</td></tr>
        <tr style="background-color:#e7f3ff;">
          <td style="padding:5px;"><strong>TOTAL ALL</strong></td>
          <td><strong style="font-size:1.2em;">{total_all}</strong> claims</td>
        </tr>
      </table>
    </div>

    <h3>Per-Player Breakdown (This Run)</h3>
    <table border="1" cellpadding="5" cellspacing="0" style="border-collapse:collapse;width:100%;">
      <tr style="background-color:#f0f0f0;">
        <th>ID</th><th>Daily</th><th>Store</th><th>Progression</th><th>Total</th><th>Status</th>
      </tr>
"""

        for r in results:
            total_player = r["daily"] + r["store"] + r["progression"]
            if r["status"] == "Success":
                status_color = "#90EE90"
            elif r["status"] == "No Rewards":
                status_color = "#FFE4B5"
            else:
                status_color = "#FFB6C1"
            row_html = f"""
      <tr>
        <td>{r['playerid']}</td>
        <td>{r['daily']}</td>
        <td>{r['store']}</td>
        <td>{r['progression']}</td>
        <td><strong>{total_player}</strong></td>
        <td style="background-color:{status_color};">{r['status']}</td>
      </tr>
"""
            html += row_html

        html += f"""
      <tr style="background-color:#e0e0e0;font-weight:bold;">
        <td>TOTAL</td>
        <td>{total_d}</td>
        <td>{total_s}</td>
        <td>{total_p}</td>
        <td>{total_all}</td>
        <td>{success_count}/{len(results)} OK</td>
      </tr>
    </table>

    <div style="margin-top:20px;padding:10px;background-color:#f9f9f9;border-left:4px solid #4CAF50;">
      <p style="margin:5px 0;"><strong>Notes</strong></p>
      <ul style="margin:5px 0;">
        <li><strong>Store Rewards</strong> Exactly 3 per player per day, resets at 5:30 AM IST.</li>
        <li><strong>Daily Rewards</strong> Variable, roughly 1 per hour per player.</li>
        <li><strong>Progression</strong> Uses grenades obtained from Store claims.</li>
      </ul>
    </div>

    <p style="margin-top:20px;color:#666;font-size:0.9em;">
      Automated run at {ist_now.strftime('%Y-%m-%d %I:%M %p')} IST
    </p>
  </body>
</html>
"""

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"Hub Rewards - {ist_now.strftime('%d-%b %I:%M %p IST')} ({total_all} claims)"
        msg["From"] = sender
        msg["To"] = recipient
        msg.attach(MIMEText(html, "html"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(sender, password)
            server.send_message(msg)
        log("Email sent")
    except Exception as e:
        log(f"Email error: {e}")


def main():
    log("=" * 60)
    log("CS HUB AUTO-CLAIMER v2.1 - Daily Tracking")
    log("=" * 60)

    ist_now = get_ist_time()
    window_start = get_current_daily_window_start()
    next_reset = get_next_daily_reset()

    log(f"IST: {ist_now.strftime('%Y-%m-%d %I:%M %p')}")
    log(f"Next Reset: {format_time_until_reset(next_reset)}")
    log("")

    try:
        with open(PLAYER_ID_FILE, "r") as f:
            reader = csv.DictReader(f)
            players = [row["playerid"].strip() for row in reader if row["playerid"].strip()]
    except Exception as e:
        log(f"Cannot read {PLAYER_ID_FILE}: {e}")
        return

    num_players = len(players)
    log(f"{num_players} players...")
    log(", ".join(players))
    log("")

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
    log(f"Daily: {total_d}, Store: {total_s}/{num_players * EXPECTED_STORE_PER_PLAYER}, Progression: {total_p}")
    for r in results:
        total = r["daily"] + r["store"] + r["progression"]
        log(f"{r['playerid']} D{r['daily']}, S{r['store']}, P{r['progression']}, Total{total} {r['status']}")

    send_email_summary(results, num_players)
    log("")
    log("Done!")


if __name__ == "__main__":
    main()
