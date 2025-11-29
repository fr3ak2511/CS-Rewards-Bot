# g_hub_merged_rewards_updated.py
# Daily Rewards page + Store "Daily Rewards" section.
# Prints an email-ready summary with both buckets and accurate totals.

import os
import csv
import re
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.desired_capabilities import DesiredCapabilities

# webdriver-manager for runners
try:
    from webdriver_manager.chrome import ChromeDriverManager
    _USE_WDM = True
except Exception:
    _USE_WDM = False

# -------- logging --------
print_lock = threading.Lock()
def log(msg: str):
    with print_lock:
        print(msg)

# -------- driver --------
def create_driver():
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--incognito")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--disable-popup-blocking")
    opts.add_argument("--disable-notifications")
    opts.add_argument("--disable-renderer-backgrounding")
    opts.add_argument("--disable-backgrounding-occluded-windows")
    opts.add_argument("--disable-background-timer-throttling")
    opts.add_experimental_option("prefs", {
        "profile.default_content_setting_values": {
            "images": 2, "notifications": 2, "popups": 2
        }
    })
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)

    caps = DesiredCapabilities.CHROME.copy()
    caps["pageLoadStrategy"] = "eager"
    for k, v in caps.items():
        opts.set_capability(k, v)

    service = Service(ChromeDriverManager().install()) if _USE_WDM else Service()
    d = webdriver.Chrome(service=service, options=opts)
    d.set_page_load_timeout(25)
    d.set_script_timeout(25)
    return d

# -------- common helpers --------
def accept_cookies(driver, timeout=2):
    try:
        btn = WebDriverWait(driver, timeout).until(
            EC.element_to_be_clickable((By.XPATH,
                "//button[normalize-space()='Accept All' or contains(.,'Accept') or contains(.,'Allow') or contains(.,'Consent')]"))
        )
        btn.click(); time.sleep(0.2)
    except Exception:
        pass

def close_any_popup(driver):
    # Try close/continue first
    for xp in [
        "//button[normalize-space()='Continue']",
        "//button[normalize-space()='Close']",
        "//button[contains(@aria-label,'Close')]",
        "//*[text()='×' or text()='X' or text()='✕']/ancestor::button"
    ]:
        try:
            el = driver.find_element(By.XPATH, xp)
            if el.is_displayed():
                try: el.click()
                except Exception: driver.execute_script("arguments[0].click()", el)
                time.sleep(0.4); return True
        except Exception: pass
    # Safe area clicks (corners)
    try:
        size = driver.get_window_size()
        coords = [(30,30), (size["width"]-50,30), (30,size["height"]-50), (size["width"]-50,size["height"]-50)]
        for x,y in coords:
            ActionChains(driver).move_by_offset(x - size["width"]//2, y - size["height"]//2).click().perform()
            ActionChains(driver).move_by_offset(-(x - size["width"]//2), -(y - size["height"]//2)).perform()
            time.sleep(0.2)
        return True
    except Exception:
        return False

def wait_login(driver, max_wait=12):
    t0 = time.time()
    while time.time() - t0 < max_wait:
        try:
            u = driver.current_url.lower()
            if any(k in u for k in ["daily-rewards", "store", "dashboard", "user"]):
                return True
            if driver.find_elements(By.XPATH, "//button[contains(.,'Logout') or contains(.,'Profile')]"):
                return True
        except Exception:
            pass
        time.sleep(0.25)
    return True

# -------- Daily Rewards page --------
def ensure_daily_rewards(driver):
    if "daily-rewards" not in driver.current_url.lower():
        driver.get("https://hub.vertigogames.co/daily-rewards")
        time.sleep(0.6)

def get_claim_buttons_daily(driver):
    out = []
    try:
        # Prefer exact buttons
        for sel in [
            "//button[normalize-space()='Claim']",
            "//*[contains(.,'Claim') and (self::button or self::a)]"
        ]:
            for b in driver.find_elements(By.XPATH, sel):
                try:
                    if b.is_displayed() and b.is_enabled():
                        t=(b.text or "").strip().lower()
                        if not any(w in t for w in ["buy","purchase","payment","pay","$"]):
                            out.append(b)
                except Exception:
                    pass
    except Exception:
        pass
    return out

def claim_daily_rewards(driver):
    ensure_daily_rewards(driver)
    close_any_popup(driver)
    time.sleep(0.3)

    claimed = 0
    for round_ in range(1,6):
        btns = get_claim_buttons_daily(driver)
        if not btns: break
        b = btns[0]
        try:
            driver.execute_script("arguments[0].scrollIntoView({behavior:'instant',block:'center'})", b)
            time.sleep(0.2)
            try: b.click()
            except Exception: driver.execute_script("arguments[0].click()", b)
            claimed += 1
            time.sleep(1.0)
            close_any_popup(driver)
            ensure_daily_rewards(driver)
        except Exception:
            continue
    return claimed

# -------- Store page (Daily Rewards section) --------
def ensure_store(driver):
    if "/store" not in driver.current_url.lower():
        driver.get("https://hub.vertigogames.co/store")
        time.sleep(0.6)

def goto_store_daily_rewards(driver):
    ensure_store(driver); close_any_popup(driver)
    # Try tab/text
    for sel in [
        "//div[contains(@class,'tab')]//*[contains(text(),'Daily Rewards')]",
        "//button[contains(@class,'tab')][contains(.,'Daily Rewards')]",
        "//*[text()='Daily Rewards' and (self::div or self::span or self::button or self::a)]"
    ]:
        try:
            for el in driver.find_elements(By.XPATH, sel):
                if el.is_displayed():
                    try:
                        driver.execute_script("arguments[0].scrollIntoView({behavior:'instant',block:'center'})", el)
                        el.click()
                    except Exception:
                        driver.execute_script("arguments[0].click()", el)
                    time.sleep(0.6)
                    return True
        except Exception:
            pass
    # Fallback: scroll and find section text
    try:
        for _ in range(5):
            driver.execute_script("window.scrollBy(0, 450)"); time.sleep(0.5)
            if driver.find_elements(By.XPATH, "//*[contains(text(),'Daily Reward') and not(self::a) and not(self::button)]"):
                return True
    except Exception:
        pass
    return False

def get_claim_buttons_store(driver):
    out = []
    for sel in [
        "//button[normalize-space()='Claim']",
        "//div[contains(@class,'reward')]//button[contains(.,'Claim')]"
    ]:
        try:
            for b in driver.find_elements(By.XPATH, sel):
                if b.is_displayed() and b.is_enabled():
                    t=(b.text or "").strip().lower()
                    if not any(w in t for w in ["buy","purchase","payment","pay","$"]):
                        out.append(b)
        except Exception:
            pass
    return out

def capture_3rd_cta_timer(driver):
    # Collect visible time strings and extract the "3rd" CTA if <= 1 hour
    try:
        texts = " ".join(el.text for el in driver.find_elements(By.XPATH, "//*[contains(.,'Next in') or contains(.,'h') or contains(.,'m')]"))
        ms = re.findall(r"(\d+)h\s+(\d+)m", texts)
        timers = [f"{h}h {m}m" for h,m in ms][:3]  # take first 3 patterns seen
        while len(timers) < 3:
            timers.append("Available")
        third = timers[2]
        if third in ("Available",):
            return None
        m = re.match(r"(\d+)h\s+(\d+)m", third)
        if not m:
            return None
        hours = int(m.group(1)); mins = int(m.group(2))
        return third if (hours*60 + mins) <= 60 else None
    except Exception:
        return None

def claim_store_daily(driver):
    if not goto_store_daily_rewards(driver):
        return 0, None
    total = 0
    for round_ in range(1,6):
        ensure_store(driver); close_any_popup(driver)
        if round_ > 1:
            if not goto_store_daily_rewards(driver):
                break
        btns = get_claim_buttons_store(driver)
        if not btns: break
        b = btns[0]
        try:
            driver.execute_script("arguments[0].scrollIntoView({behavior:'instant',block:'center'})", b)
            time.sleep(0.2)
            try: b.click()
            except Exception: driver.execute_script("arguments[0].click()", b)
            total += 1
            time.sleep(1.0)
            close_any_popup(driver)
            ensure_store(driver)
        except Exception:
            continue
        if total >= 3:  # practical cap
            break

    # Timer for 3rd CTA if within 1 hour
    ensure_store(driver)
    if goto_store_daily_rewards(driver):
        time.sleep(0.6)
        timer = capture_3rd_cta_timer(driver)
    else:
        timer = None
    return total, timer

# -------- per-player flow --------
def automate_player(player_id, idx):
    log(f"\n[#{idx}] Player: {player_id}")
    d = create_driver()
    login_ok = False
    daily_claims = 0
    store_claims = 0
    store_timer = None
    try:
        d.get("https://hub.vertigogames.co/daily-rewards")
        time.sleep(0.4)
        accept_cookies(d)

        # login button
        clicked = False
        for sel in [
            "//button[contains(.,'Login') or contains(.,'Log in') or contains(.,'Sign in')]",
            "//a[contains(.,'Login') or contains(.,'Log in') or contains(.,'Sign in')]",
            "//button[contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'login')]"
        ]:
            try:
                for el in d.find_elements(By.XPATH, sel):
                    if el.is_displayed() and el.is_enabled():
                        el.click(); clicked=True; break
            except Exception: pass
            if clicked: break
        if not clicked:
            return {"player_id": player_id, "daily_page": 0, "store_daily": 0, "store_timer": None, "login_successful": False, "status": "login_button_not_found"}

        # id field
        box = None
        for sel in [
            "//*[@id='user-id-input']",
            "//input[contains(@placeholder,'ID') or contains(@placeholder,'User') or contains(@name,'user') or contains(@placeholder,'id')]",
            "//div[contains(@class,'modal') or contains(@class,'dialog')]//input[@type='text']",
            "//input[@type='text']"
        ]:
            try:
                box = WebDriverWait(d, 3).until(EC.visibility_of_element_located((By.XPATH, sel)))
                break
            except Exception: pass
        if not box:
            return {"player_id": player_id, "daily_page": 0, "store_daily": 0, "store_timer": None, "login_successful": False, "status": "input_field_not_found"}

        box.clear(); box.send_keys(player_id); time.sleep(0.1)

        # submit
        submitted = False
        for sel in [
            "//button[contains(.,'Login') or contains(.,'Log in') or contains(.,'Sign in')]",
            "//button[@type='submit']",
            "//div[contains(@class,'modal') or contains(@class,'dialog')]//button[not(contains(.,'Cancel')) and not(contains(.,'Close'))]"
        ]:
            try:
                b = d.find_element(By.XPATH, sel)
                if b.is_displayed():
                    b.click(); submitted=True; break
            except Exception: pass
        if not submitted:
            try: box.send_keys(Keys.ENTER); submitted=True
            except Exception: pass
        if not submitted:
            return {"player_id": player_id, "daily_page": 0, "store_daily": 0, "store_timer": None, "login_successful": False, "status": "login_cta_not_found"}

        wait_login(d, 12); time.sleep(0.7)
        login_ok = True

        # Step 1: Daily page
        daily_claims = claim_daily_rewards(d)

        # Step 2: Store page > Daily Rewards section
        d.get("https://hub.vertigogames.co/store"); time.sleep(0.6)
        store_claims, store_timer = claim_store_daily(d)

        status = "success" if (daily_claims + store_claims) > 0 else "no_claims"
        return {
            "player_id": player_id,
            "daily_page": daily_claims,
            "store_daily": store_claims,
            "store_timer": store_timer,
            "login_successful": login_ok,
            "status": status,
        }
    except Exception as e:
        log(f"[#{idx}] ERROR: {e}")
        return {"player_id": player_id, "daily_page": 0, "store_daily": 0, "store_timer": None, "login_successful": login_ok, "status": "error"}
    finally:
        try: d.quit()
        except Exception: pass

# -------- runner --------
def read_players():
    repo_dir = os.path.dirname(os.path.abspath(__file__))
    csv_path = os.path.join(repo_dir, "players.csv")
    players = []
    with open(csv_path, newline="") as f:
        for row in csv.reader(f):
            pid = (row[0] if row else "").strip()
            if pid:
                players.append(pid)
    return players

def main():
    players = read_players()
    log(f"Loaded {len(players)} player IDs")

    BATCH = 2
    batches = [players[i:i+BATCH] for i in range(0, len(players), BATCH)]

    all_res = []
    t0 = time.time()
    for bnum, batch in enumerate(batches, 1):
        with ThreadPoolExecutor(max_workers=len(batch)) as ex:
            futs = {ex.submit(automate_player, pid, f"{bnum}-{i+1}"): pid for i, pid in enumerate(batch)}
            for fut in as_completed(futs):
                all_res.append(fut.result())
        if bnum < len(batches): time.sleep(0.7)

    total_time = time.time() - t0
    total_players = len(all_res)
    successful_logins = sum(1 for r in all_res if r["login_successful"])
    daily_total = sum(r["daily_page"] for r in all_res)
    store_total = sum(r["store_daily"] for r in all_res)
    total_claims = daily_total + store_total
    avg_time = (total_time/total_players) if total_players else 0.0

    # players with <=1h 3rd CTA
    urgent = [{"player_id": r["player_id"], "timer": r["store_timer"]} for r in all_res if r.get("store_timer")]

    # -------- email-ready summary ----------
    print("\n" + "="*60)
    print("MERGED REWARDS SUMMARY")
    print("="*60)
    print(f"Total Players: {total_players}")
    print(f"Successful Logins: {successful_logins}")
    print(f"Daily Rewards (page): {daily_total}")
    print(f"Store Daily Rewards: {store_total}")
    print(f"Total Rewards Claimed: {total_claims}")
    print(f"Total Time Taken: {total_time:.1f}s ({total_time/60:.1f} min)")
    print(f"Avg Time per ID: {avg_time:.1f}s")
    print("-"*60)
    if urgent:
        print("STORE 3rd CTA ≤ 1 hour:")
        for p in urgent:
            print(f" • {p['player_id']} — {p['timer']}")
    else:
        print("STORE 3rd CTA ≤ 1 hour: None")
    print("="*60)

if __name__ == "__main__":
    main()
