#!/usr/bin/env python3
# ==============================================================
# GitHub Actions: Fixed Progression Rewards Script
# ==============================================================

import os
import sys
import time
import csv
import traceback
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager


def log(msg):
    ts = datetime.utcnow().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def create_driver():
    try:
        log("Creating Chrome driver...")
        opts = Options()
        headless = os.getenv("HEADLESS", "true").lower() == "true"
        if headless:
            opts.add_argument("--headless=new")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--window-size=1920,1080")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)

        ua = os.getenv(
            "BROWSER_UA",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        opts.add_argument(f"--user-agent={ua}")

        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=opts)
        driver.set_page_load_timeout(25)
        driver.set_script_timeout(25)
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
        )
        log("✓ Chrome driver ready")
        return driver
    except Exception as e:
        log(f"✗ DRIVER ERROR: {e}")
        return None


def close_popups_safe(driver):
    try:
        driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
    except:
        pass


def login_with_player(driver, pid):
    try:
        driver.get("https://hub.vertigogames.co/daily-rewards")
        time.sleep(2)
        close_popups_safe(driver)

        triggers = [
            "//button[contains(text(),'Login')]",
            "//a[contains(text(),'Login')]",
            "//button[contains(text(),'Sign in')]",
            "//button[contains(text(),'Claim')]",
        ]
        for t in triggers:
            try:
                btns = driver.find_elements(By.XPATH, t)
                for b in btns:
                    if b.is_displayed():
                        driver.execute_script("arguments[0].click();", b)
                        time.sleep(2)
                        break
            except:
                continue

        inputs = [
            "//input[contains(@placeholder,'Player')]",
            "//input[@type='text']",
        ]
        for i in inputs:
            try:
                box = WebDriverWait(driver, 5).until(EC.visibility_of_element_located((By.XPATH, i)))
                box.clear()
                box.send_keys(pid)
                time.sleep(0.5)
                box.send_keys(Keys.ENTER)
                break
            except:
                continue

        time.sleep(3)
        return True
    except Exception as e:
        log(f"Login failed: {e}")
        return False


def claim_progression(driver):
    try:
        driver.get("https://hub.vertigogames.co/progression-program")
        time.sleep(2)
        close_popups_safe(driver)
        driver.refresh()
        time.sleep(2)
        total = 0
        for attempt in range(1, 10):
            claimables = driver.execute_script(
                """
                const btns = Array.from(document.querySelectorAll('button')).filter(b=>{
                    const txt=(b.innerText||'').trim();
                    if(txt!=='Claim')return false;
                    const rect=b.getBoundingClientRect();
                    if(rect.left<=400)return false;
                    const p=b.closest('div');
                    const pt=p?p.innerText:'';
                    if(pt.includes('Delivered'))return false;
                    return true;
                });
                return btns;
                """
            )
            if not claimables:
                break
            try:
                btn = claimables[0]
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                time.sleep(0.3)
                driver.execute_script("arguments[0].click();", btn)
                total += 1
                log(f"✓ Claimed progression #{total}")
                time.sleep(1)
                close_popups_safe(driver)
            except Exception as e:
                log(f"Claim click failed: {e}")
                time.sleep(0.5)
                continue
        return total
    except Exception as e:
        log(f"Progression claim error: {e}")
        return 0


def main():
    if not os.path.exists("players.csv"):
        log("players.csv missing")
        sys.exit(1)

    with open("players.csv") as f:
        players = [r.strip() for r in f if r.strip()]

    driver = create_driver()
    if not driver:
        log("Driver setup failed")
        sys.exit(1)

    total_claimed = 0
    for pid in players:
        log(f"Processing {pid}")
        if login_with_player(driver, pid):
            claimed = claim_progression(driver)
            total_claimed += claimed
            log(f"ID {pid} done | Claimed={claimed}")
            driver.delete_all_cookies()
        else:
            log(f"Login failed for {pid}")

    log("=" * 50)
    log(f"Total Players: {len(players)} | Total Claimed: {total_claimed}")
    log("=" * 50)
    driver.quit()


if __name__ == "__main__":
    main()
