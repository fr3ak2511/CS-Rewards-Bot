# g_hub_ProgressionProgram_rewards_updated.py
# Runs on GitHub Actions (Linux). No hardcoded Windows paths.
# Prints a ready-to-email plain-text summary.

import os
import csv
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

# Use webdriver-manager so the runner always has a compatible ChromeDriver
try:
    from webdriver_manager.chrome import ChromeDriverManager
    _USE_WDM = True
except Exception:
    _USE_WDM = False  # Fallback to system chromedriver if preinstalled

# ----------------- thread-safe logging -----------------
print_lock = threading.Lock()
def log(msg: str):
    with print_lock:
        print(msg)

# ----------------- browser factory -----------------
def create_driver():
    opts = Options()
    # deterministic headless
    opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1920,1080")
    # stability/perf
    opts.add_argument("--incognito")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--disable-features=VizDisplayCompositor")
    opts.add_argument("--disable-popup-blocking")
    opts.add_argument("--disable-notifications")
    opts.add_argument("--disable-renderer-backgrounding")
    opts.add_argument("--disable-backgrounding-occluded-windows")
    opts.add_argument("--disable-background-timer-throttling")
    # reduce page weight
    opts.add_experimental_option("prefs", {
        "profile.default_content_setting_values": {
            "images": 2,
            "notifications": 2,
            "popups": 2
        }
    })
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)

    caps = DesiredCapabilities.CHROME.copy()
    caps["pageLoadStrategy"] = "eager"
    for k, v in caps.items():
        opts.set_capability(k, v)

    if _USE_WDM:
        service = Service(ChromeDriverManager().install())
    else:
        service = Service()  # rely on runner's chromedriver in PATH

    driver = webdriver.Chrome(service=service, options=opts)
    driver.set_page_load_timeout(25)
    driver.set_script_timeout(25)
    return driver

# ----------------- helpers -----------------
def accept_cookies(driver, wait, timeout=2):
    try:
        btn = WebDriverWait(driver, timeout).until(
            EC.element_to_be_clickable((By.XPATH,
                "//button[normalize-space()='Accept All' or contains(.,'Accept') or contains(.,'Allow') or contains(.,'Consent')]"))
        )
        btn.click()
        time.sleep(0.2)
    except Exception:
        pass

def wait_for_login_complete(driver, max_wait=15):
    t0 = time.time()
    while time.time() - t0 < max_wait:
        try:
            url = driver.current_url.lower()
            if "user" in url or "dashboard" in url or "progression-program" in url:
                return True
            if driver.find_elements(By.XPATH, "//button[contains(.,'Logout') or contains(.,'Profile')]"):
                return True
        except Exception:
            pass
        time.sleep(0.25)
    return True  # do not block run

def close_initial_popup(driver):
    # Close button
    for xp in [
        "//button[normalize-space()='Close']",
        "//*[name()='svg']/parent::button[contains(@class,'close')]",
        "//button[contains(@aria-label,'Close')]",
    ]:
        try:
            el = driver.find_element(By.XPATH, xp)
            if el.is_displayed():
                el.click()
                time.sleep(0.4)
                return
        except Exception:
            pass
    # Safe click once if needed
    try:
        size = driver.get_window_size()
        x = int(size["width"] * 0.9)
        y = int(size["height"] * 0.5)
        ActionChains(driver).move_by_offset(x - size["width"]//2, y - size["height"]//2).click().perform()
        ActionChains(driver).move_by_offset(-(x - size["width"]//2), -(y - size["height"]//2)).perform()
        time.sleep(0.4)
    except Exception:
        pass

def claim_buttons(driver):
    """
    Find and click one visible 'Claim' button at a time; refresh list each loop.
    Returns number of claims.
    """
    total = 0
    for attempt in range(1, 9):
        # JS query limited to content area (x > 400), exclude 'Delivered'
        try:
            elems = driver.execute_script("""
const out=[];
document.querySelectorAll('button').forEach(b=>{
  const t=(b.innerText||'').trim();
  if(t==='Claim'){
    const r=b.getBoundingClientRect(); 
    if(r.left>400){
      const p=b.closest('div'); 
      const txt=p ? p.innerText : '';
      if(!/Delivered/i.test(txt)) out.push(b);
    }
  }
});
return out;
""")
        except Exception:
            elems = []

        if not elems:
            break

        btn = elems[0]
        try:
            driver.execute_script("arguments[0].scrollIntoView({behavior:'instant',block:'center'});", btn)
            time.sleep(0.2)
            driver.execute_script("arguments[0].click();", btn)
            total += 1
            time.sleep(1.0)
            # close ‘Close’ dialog if it appears
            try:
                c = driver.find_element(By.XPATH, "//button[normalize-space()='Close']")
                if c.is_displayed():
                    c.click()
                    time.sleep(0.4)
            except Exception:
                pass
        except Exception:
            continue
    return total

# ----------------- per-player automation -----------------
def automate_player(player_id, idx):
    log(f"\n[#{idx}] Player: {player_id}")
    d = create_driver()
    w = WebDriverWait(d, 12)
    login_ok = False
    claimed = 0
    try:
        d.get("https://hub.vertigogames.co/progression-program")
        time.sleep(0.4)
        accept_cookies(d, w)
        # open login
        clicked = False
        for sel in [
            "//button[contains(.,'Login') or contains(.,'Log in') or contains(.,'Sign in')]",
            "//a[contains(.,'Login') or contains(.,'Log in') or contains(.,'Sign in')]",
            "//button[contains(translate(.,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'login')]",
        ]:
            try:
                for el in d.find_elements(By.XPATH, sel):
                    if el.is_displayed() and el.is_enabled():
                        el.click(); clicked=True; break
            except Exception:
                pass
            if clicked: break
        if not clicked:
            return {"player_id": player_id, "monthly_rewards": 0, "login_successful": False, "status": "login_button_not_found"}

        # input id
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
            except Exception:
                pass
        if not box:
            return {"player_id": player_id, "monthly_rewards": 0, "login_successful": False, "status": "input_field_not_found"}
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
            except Exception:
                pass
        if not submitted:
            try: box.send_keys(Keys.ENTER); submitted=True
            except Exception: pass
        if not submitted:
            return {"player_id": player_id, "monthly_rewards": 0, "login_successful": False, "status": "login_cta_not_found"}

        wait_for_login_complete(d, 15)
        time.sleep(1.0)
        login_ok = True
        close_initial_popup(d)
        # ensure cards
        try:
            d.execute_script("window.scrollTo(0,0)")
        except Exception:
            pass
        claimed = claim_buttons(d)

        return {"player_id": player_id, "monthly_rewards": claimed, "login_successful": login_ok, "status": ("success" if claimed>0 else "no_claims")}
    except Exception as e:
        log(f"[#{idx}] ERROR: {e}")
        return {"player_id": player_id, "monthly_rewards": 0, "login_successful": login_ok, "status": "error"}
    finally:
        try: d.quit()
        except Exception: pass

# ----------------- orchestrator -----------------
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

    all_res, t0 = [], time.time()
    for bnum, batch in enumerate(batches, 1):
        with ThreadPoolExecutor(max_workers=len(batch)) as ex:
            futs = {ex.submit(automate_player, pid, f"{bnum}-{i+1}"): pid for i, pid in enumerate(batch)}
            for fut in as_completed(futs):
                all_res.append(fut.result())
        if bnum < len(batches): time.sleep(0.7)

    total_time = time.time() - t0
    total_players = len(all_res)
    successful_logins = sum(1 for r in all_res if r["login_successful"])
    total_monthly = sum(r["monthly_rewards"] for r in all_res)
    avg_time_per_id = (total_time/total_players) if total_players else 0.0

    # --------- email-ready summary ----------
    print("\n" + "="*60)
    print("PROGRESSION PROGRAM SUMMARY")
    print("="*60)
    print(f"Total Players: {total_players}")
    print(f"Successful Logins: {successful_logins}")
    print(f"Monthly Rewards Claimed: {total_monthly}")
    print(f"Total Time Taken: {total_time:.1f}s ({total_time/60:.1f} min)")
    print(f"Avg Time per ID: {avg_time_per_id:.1f}s")
    print("="*60)

if __name__ == "__main__":
    main()
