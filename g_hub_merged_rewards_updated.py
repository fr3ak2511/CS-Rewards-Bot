#!/usr/bin/env python3
# ==============================================================
# GitHub Actions: Fixed Merged Rewards Script
# Handles Daily + Store Rewards robustly
# ==============================================================

import os
import sys
import time
import csv
import traceback
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager


# ------------------------------------------------------------------
# Logging utilities
# ------------------------------------------------------------------
def log(msg: str):
    ts = datetime.utcnow().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)
    sys.stderr.flush()


def take_screenshot(driver, name: str):
    try:
        path = os.path.join("screenshots", name)
        os.makedirs("screenshots", exist_ok=True)
        driver.save_screenshot(path)
        log(f"📸 Saved screenshot: {path}")
    except Exception as e:
        log(f"⚠️ Screenshot failed: {e}")


# ------------------------------------------------------------------
# Chrome driver
# ------------------------------------------------------------------
def create_driver():
    try:
        log("Creating Chrome driver...")
        options = Options()

        headless = os.getenv("HEADLESS", "true").lower() == "true"
        if headless:
            options.add_argument("--headless=new")

        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-gpu")
        options.add_argument("--lang=en-US")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)

        ua = os.getenv(
            "BROWSER_UA",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        options.add_argument(f"--user-agent={ua}")

        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(25)
        driver.set_script_timeout(25)

        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
        )

        log("✓ Chrome driver created")
        return driver
    except Exception as e:
        log(f"✗ DRIVER CREATION ERROR: {e}")
        return None


# ------------------------------------------------------------------
# Cookie & Popup Handling
# ------------------------------------------------------------------
def close_popups_safe(driver):
    try:
        popup_selectors = [
            "//div[contains(@class,'modal') and not(contains(@style,'display:none'))]",
            "//div[contains(@class,'popup') and not(contains(@style,'display:none'))]",
            "//div[@data-testid='item-popup-content']",
        ]
        for sel in popup_selectors:
            try:
                elems = driver.find_elements(By.XPATH, sel)
                visible = [e for e in elems if e.is_displayed()]
                for v in visible:
                    close_btns = v.find_elements(By.XPATH, ".//button[contains(text(),'×') or contains(text(),'Close') or contains(@aria-label,'close')]")
                    if close_btns:
                        driver.execute_script("arguments[0].click();", close_btns[0])
                        time.sleep(0.5)
                        log("Popup closed")
                        return True
            except:
                continue
        driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
    except:
        pass
    return False


# ------------------------------------------------------------------
# Login Routine
# ------------------------------------------------------------------
def login_with_player(driver, player_id, thread):
    try:
        log(f"[Thread-{thread}] Logging in with ID {player_id}")
        driver.get("https://hub.vertigogames.co/daily-rewards")
        time.sleep(2)
        close_popups_safe(driver)

        # Try multiple login triggers
        triggers = [
            "//button[contains(text(),'Login')]",
            "//a[contains(text(),'Login')]",
            "//button[contains(text(),'Sign in')]",
            "//button[contains(text(),'Log in')]",
            "//button[contains(text(),'Claim')]",
        ]

        clicked = False
        for t in triggers:
            try:
                btns = driver.find_elements(By.XPATH, t)
                for b in btns:
                    if b.is_displayed():
                        driver.execute_script("arguments[0].click();", b)
                        clicked = True
                        time.sleep(2)
                        break
                if clicked:
                    break
            except:
                continue

        if not clicked:
            log(f"[Thread-{thread}] No login button found.")
            take_screenshot(driver, f"{player_id}_no_login_button.png")
            return False

        # Input field
        field_selectors = [
            "//input[contains(@placeholder,'Player')]",
            "//input[@type='text']",
            "//input[contains(@class,'form-control')]",
        ]
        found = None
        for f in field_selectors:
            try:
                found = WebDriverWait(driver, 5).until(EC.visibility_of_element_located((By.XPATH, f)))
                found.clear()
                found.send_keys(player_id)
                time.sleep(0.5)
                found.send_keys(Keys.ENTER)
                break
            except:
                continue

        if not found:
            log(f"[Thread-{thread}] No input field.")
            take_screenshot(driver, f"{player_id}_no_input.png")
            return False

        time.sleep(3)
        url = driver.current_url.lower()
        if "daily-rewards" in url or "dashboard" in url or "store" in url:
            log(f"[Thread-{thread}] ✓ Login successful")
            return True

        log(f"[Thread-{thread}] Login may have failed (stuck page).")
        return True
    except Exception as e:
        log(f"[Thread-{thread}] Login error: {e}")
        take_screenshot(driver, f"{player_id}_login_error.png")
        return False


# ------------------------------------------------------------------
# Claim Routines
# ------------------------------------------------------------------
def claim_rewards(driver, section, thread, player_id):
    try:
        log(f"[Thread-{thread}] Opening {section} section")
        driver.get(f"https://hub.vertigogames.co/{section}")
        time.sleep(3)
        close_popups_safe(driver)

        claim_buttons = driver.find_elements(By.XPATH, "//button[contains(text(),'Claim')]")
        claimed = 0
        for b in claim_buttons:
            try:
                if b.is_displayed() and b.is_enabled():
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", b)
                    time.sleep(0.3)
                    driver.execute_script("arguments[0].click();", b)
                    claimed += 1
                    log(f"[Thread-{thread}] Claimed reward #{claimed}")
                    time.sleep(1)
                    close_popups_safe(driver)
            except:
                continue
        return claimed
    except Exception as e:
        log(f"[Thread-{thread}] Claim routine error: {e}")
        take_screenshot(driver, f"{player_id}_claim_error.png")
        return 0


# ------------------------------------------------------------------
# Worker per Player
# ------------------------------------------------------------------
def process_player(pid, thread):
    driver = create_driver()
    if not driver:
        return {"player_id": pid, "login_success": False, "daily": 0, "store": 0}

    try:
        ok = login_with_player(driver, pid, thread)
        if not ok:
            return {"player_id": pid, "login_success": False, "daily": 0, "store": 0}

        daily = claim_rewards(driver, "daily-rewards", thread, pid)
        store = claim_rewards(driver, "store", thread, pid)
        total = daily + store
        status = "success" if total else "no_claims"

        return {"player_id": pid, "login_success": True, "daily": daily, "store": store, "status": status}
    finally:
        try:
            driver.quit()
        except:
            pass


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------
def main():
    csv_path = "players.csv"
    if not os.path.exists(csv_path):
        log("✗ players.csv missing")
        sys.exit(1)

    with open(csv_path) as f:
        players = [r.strip() for r in f if r.strip()]

    log(f"Loaded {len(players)} player IDs")

    results = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        for i, pid in enumerate(players):
            res = pool.submit(process_player, pid, i + 1)
            results.append(res)

        output = []
        total_daily, total_store = 0, 0
        for r in results:
            result = r.result()
            total_daily += result.get("daily", 0)
            total_store += result.get("store", 0)
            output.append(result)
            log(str(result))

    log("=" * 60)
    log("FINAL SUMMARY")
    log(f"Total Players: {len(players)}")
    log(f"Daily Rewards Claimed: {total_daily}")
    log(f"Store Rewards Claimed: {total_store}")
    log("=" * 60)


if __name__ == "__main__":
    main()
