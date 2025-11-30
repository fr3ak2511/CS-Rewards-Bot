#!/usr/bin/env python3
# ==============================================================
# GitHub Actions runner: Progression Program (Monthly) rewards
# Mirrors the manual "2_hub_ProgressionProgram_rewards_updated.py"
# ==============================================================

import os
import sys
import csv
import time
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager


# ----------------------- Logging ---------------------------------
print_lock = threading.Lock()
def log(msg: str):
    with print_lock:
        ts = datetime.utcnow().strftime("%H:%M:%S")
        print(f"[{ts}] {msg}", flush=True)


# ----------------------- Driver ----------------------------------
def create_driver():
    try:
        log("Creating Chrome driver...")

        opts = Options()
        headless = os.getenv("HEADLESS", "false").lower() == "true"
        if headless:
            opts.add_argument("--headless=new")

        # Align to your manual driver posture
        opts.add_argument("--incognito")
        opts.add_argument("--start-maximized")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_argument("--disable-infobars")
        opts.add_argument("--disable-extensions")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-logging")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--disable-web-security")
        opts.add_argument("--disable-popup-blocking")
        opts.add_argument("--disable-notifications")
        opts.add_argument("--disable-default-apps")
        opts.add_argument("--disable-features=VizDisplayCompositor")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)

        ua = os.getenv("BROWSER_UA")
        if ua:
            opts.add_argument(f"--user-agent={ua}")

        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=opts)
        driver.set_page_load_timeout(25)
        driver.set_script_timeout(25)

        try:
            driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
            )
        except Exception:
            pass

        log("✓ Chrome driver created")
        return driver
    except Exception as e:
        log(f"✗ DRIVER CREATION ERROR: {e}")
        return None


# ----------------------- Helpers ---------------------------------
def accept_cookies(driver):
    try:
        btn = WebDriverWait(driver, 2).until(
            EC.element_to_be_clickable(
                (By.XPATH, "//button[normalize-space()='Accept All' or contains(text(),'Accept') or contains(text(),'Allow') or contains(text(),'Consent')]")
            )
        )
        btn.click()
        time.sleep(0.3)
    except Exception:
        pass

def close_initial_popups_only(driver):
    # Your manual “once” popup close
    try:
        # Close button
        try:
            close_btn = driver.find_element(By.XPATH, "//button[normalize-space(text())='Close']")
            if close_btn.is_displayed():
                close_btn.click()
                time.sleep(1)
                return
        except Exception:
            pass
        # X button
        try:
            x_buttons = driver.find_elements(By.XPATH, "//*[name()='svg']/parent::button")
            for x in x_buttons:
                if x.is_displayed():
                    x.click()
                    time.sleep(1)
                    return
        except Exception:
            pass
        # Safe click
        try:
            size = driver.get_window_size()
            w,h = size["width"], size["height"]
            safe_x, safe_y = int(w*0.90), int(h*0.50)
            actions = ActionChains(driver)
            actions.move_by_offset(safe_x - w//2, safe_y - h//2).click().perform()
            actions.move_by_offset(-(safe_x - w//2), -(safe_y - h//2)).perform()
            time.sleep(1)
        except Exception:
            pass
    except Exception:
        pass

def close_popups_safe(driver):
    try:
        # Generic close
        close_initial_popups_only(driver)
        # ESC fallback
        try:
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            time.sleep(0.3)
        except Exception:
            pass
    except Exception:
        pass


# ----------------------- Login -----------------------------------
def login(driver, player_id, thread_id):
    try:
        driver.get("https://hub.vertigogames.co/progression-program")
        time.sleep(0.4)
        accept_cookies(driver)

        selectors = [
            "//button[contains(text(),'Login') or contains(text(),'Log in') or contains(text(),'Sign in')]",
            "//a[contains(text(),'Login') or contains(text(),'Log in') or contains(text(),'Sign in')]",
            "//button[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'login')]",
            "//button[contains(text(),'claim')]",
            "//div[contains(text(),'Store') or contains(text(),'store')]//button",
            "//button[contains(@class,'btn') or contains(@class,'button')]",
            "//*[contains(text(),'Login') or contains(text(),'login')][@onclick or @href or self::button or self::a]",
        ]
        clicked = False
        for xp in selectors:
            try:
                els = driver.find_elements(By.XPATH, xp)
                for el in els:
                    if el.is_displayed() and el.is_enabled():
                        el.click()
                        clicked = True
                        break
                if clicked:
                    break
            except Exception:
                continue

        if not clicked:
            return False

        # Input
        input_selectors = [
            "#user-id-input",
            "//input[contains(@placeholder,'ID') or contains(@placeholder,'User') or contains(@name,'user') or contains(@placeholder,'id')]",
            "//input[@type='text']",
            "//input[contains(@class,'input')]",
            "//div[contains(@class,'modal') or contains(@class,'dialog')]//input[@type='text']",
        ]
        box = None
        for s in input_selectors:
            try:
                if s.startswith("#"):
                    box = WebDriverWait(driver, 3).until(EC.visibility_of_element_located((By.ID, s[1:])))
                else:
                    box = WebDriverWait(driver, 3).until(EC.visibility_of_element_located((By.XPATH, s)))
                box.clear(); box.send_keys(player_id); time.sleep(0.1)
                break
            except Exception:
                continue
        if not box:
            return False

        # Submit
        ctas = [
            "//button[contains(text(),'Login') or contains(text(),'Log in') or contains(text(),'Sign in')]",
            "//button[@type='submit']",
            "//div[contains(@class,'modal') or contains(@class,'dialog')]//button[not(contains(text(),'Cancel')) and not(contains(text(),'Close'))]",
            "//button[contains(@class,'primary') or contains(@class,'submit')]",
        ]
        submitted = False
        for xp in ctas:
            try:
                btn = WebDriverWait(driver, 2).until(EC.element_to_be_clickable((By.XPATH, xp)))
                btn.click(); submitted=True; break
            except Exception:
                continue
        if not submitted:
            try:
                box.send_keys(Keys.ENTER); time.sleep(0.3); submitted = True
            except Exception:
                pass

        # Wait for login completion (manual behavior)
        start = time.time()
        while time.time() - start < 15:
            try:
                url = driver.current_url.lower()
                if any(k in url for k in ["user", "dashboard", "progression-program"]):
                    break
            except Exception:
                pass
            time.sleep(0.2)

        time.sleep(1.5)
        log(f"[Thread-{thread_id}] ✓ Login successful")
        return True
    except Exception:
        return False


# --------------- Progression claim loop (manual JS pattern) ------
def claim_progression_rewards(driver, thread_id):
    try:
        log(f"[Thread-{thread_id}] Claiming Progression rewards …")
        driver.get("https://hub.vertigogames.co/progression-program")
        time.sleep(2)
        close_popups_safe(driver)

        driver.refresh()
        time.sleep(2)

        total = 0
        for attempt in range(1, 9):
            # JS filter: only visible Claim buttons in content area; no “Delivered”
            script = """
const btns = Array.from(document.querySelectorAll('button')).filter(b => {
  const txt = (b.innerText || '').trim();
  if (txt !== 'Claim') return false;
  const rect = b.getBoundingClientRect();
  if (rect.left <= 400) return false;           // ignore left menu/sidebar
  const p = b.closest('div');
  const pt = p ? p.innerText : '';
  if (pt.includes('Delivered')) return false;   // ignore delivered items
  return true;
});
return btns;
"""
            try:
                claimables = driver.execute_script(script)
            except Exception:
                claimables = []
            log(f"[Thread-{thread_id}] Found {len(claimables)} Claim buttons (attempt {attempt})")

            if not claimables:
                break

            btn = claimables[0]
            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                time.sleep(0.3)
                driver.execute_script("arguments[0].click();", btn)
                total += 1
                log(f"[Thread-{thread_id}] ✓ Claimed progression reward #{total}")
                time.sleep(1.2)
                close_popups_safe(driver)
            except Exception as e:
                log(f"[Thread-{thread_id}] Progression click failed: {e}")
                time.sleep(0.5)
                continue

        return total
    except Exception as e:
        log(f"[Thread-{thread_id}] PROGRESSION ERROR: {e}")
        return 0


# ----------------------- Player worker ---------------------------
def process_player(player_id, thread_id):
    driver = create_driver()
    if not driver:
        return {"player_id": player_id, "monthly_rewards": 0, "status": "driver_failed", "login_successful": False}

    login_successful = False
    try:
        if not login(driver, player_id, thread_id):
            return {"player_id": player_id, "monthly_rewards": 0, "status": "login_failed", "login_successful": False}

        login_successful = True
        claimed = claim_progression_rewards(driver, thread_id)
        status = "success" if claimed > 0 else "no_claims"
        return {"player_id": player_id, "monthly_rewards": claimed, "status": status, "login_successful": True}
    finally:
        try:
            driver.quit()
        except Exception:
            pass


# ----------------------- Main -----------------------------------
def main():
    csv_path = "players.csv"
    if not os.path.exists(csv_path):
        log("✗ players.csv missing at repo root.")
        sys.exit(1)

    with open(csv_path, newline="") as f:
        players = [row.strip() for row in f if row.strip()]

    log(f"Loaded {len(players)} player IDs")

    results = []
    start = time.time()
    with ThreadPoolExecutor(max_workers=2) as ex:
        futs = {ex.submit(process_player, pid, i+1): pid for i, pid in enumerate(players)}
        for fut in as_completed(futs):
            res = fut.result()
            results.append(res)
            log(str(res))

    total_time = time.time() - start
    total_players = len(results)
    successful_logins = sum(1 for r in results if r.get("login_successful"))
    total_monthly = sum(r.get("monthly_rewards", 0) for r in results)

    log("\n" + "="*70)
    log("PROGRESSION PROGRAM - FINAL SUMMARY")
    log("="*70)
    log(f"Total Players: {total_players}")
    log(f"Successful Logins: {successful_logins}")
    log(f"Total Monthly Rewards Claimed: {total_monthly}")
    log(f"Total Time: {total_time:.1f}s ({total_time/60:.1f} minutes)")
    log("="*70)

if __name__ == "__main__":
    main()
