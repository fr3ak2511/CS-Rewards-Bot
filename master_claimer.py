import csv
import time
import os
import json
import smtplib
import re
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains

PLAYER_ID_FILE = "players.csv"
HEADLESS = True

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

# =========================
# 🔥 DRIVER FIX (CRITICAL)
# =========================
def create_driver():
    for attempt in range(3):
        try:
            options = uc.ChromeOptions()

            if HEADLESS:
                options.add_argument("--headless=new")

            options.add_argument("--window-size=1920,1080")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-gpu")
            options.add_argument("--disable-notifications")
            options.add_argument("--disable-popup-blocking")
            options.add_argument("--remote-debugging-port=0")

            # ✅ FIX: AUTO driver version (removed version_main)
            driver = uc.Chrome(options=options, use_subprocess=True)

            driver.set_page_load_timeout(30)
            driver.set_script_timeout(30)

            log("✅ Driver initialized (auto)")
            return driver

        except Exception as e:
            log(f"⚠️ Driver init attempt {attempt+1} failed: {str(e)[:100]}")
            time.sleep(2)

            if attempt == 2:
                log("🔥 CRITICAL: Driver failed → aborting job")
                raise RuntimeError("Driver init failed")

# =========================
# LOGIN (same behavior)
# =========================
def login_to_hub(driver, player_id):
    try:
        driver.get("https://hub.vertigogames.co/daily-rewards")
        time.sleep(2)

        # Click login
        for btn in driver.find_elements(By.TAG_NAME, "button"):
            if "login" in btn.text.lower():
                btn.click()
                break

        time.sleep(2)

        # Enter ID
        inputs = driver.find_elements(By.TAG_NAME, "input")
        for i in inputs:
            i.send_keys(player_id)
            break

        time.sleep(1)

        # Submit
        for btn in driver.find_elements(By.TAG_NAME, "button"):
            if "login" in btn.text.lower() or "submit" in btn.text.lower():
                btn.click()
                break

        time.sleep(3)
        log("✅ Login success")
        return True

    except Exception as e:
        log(f"❌ Login failed: {e}")
        return False

# =========================
# DAILY (UNCHANGED STYLE)
# =========================
def claim_daily_rewards(driver, player_id):
    log("🎁 Daily check")

    driver.get("https://hub.vertigogames.co/daily-rewards")
    time.sleep(2)

    claimed = 0

    result = driver.execute_script("""
        let btns = document.querySelectorAll('button');
        for (let b of btns){
            if(b.innerText.trim().toLowerCase()==='claim' && !b.disabled){
                b.click();
                return true;
            }
        }
        return false;
    """)

    if result:
        claimed = 1
        log("✅ Daily claimed")

    return claimed

# =========================
# STORE (UNCHANGED STYLE)
# =========================
def claim_store_rewards(driver, player_id):
    log("🏪 Store check")

    driver.get("https://hub.vertigogames.co/store")
    time.sleep(2)

    claimed = 0

    for _ in range(3):
        result = driver.execute_script("""
            let btns = document.querySelectorAll('button');
            for (let b of btns){
                if(b.innerText.trim().toLowerCase()==='free' && !b.disabled){
                    b.click();
                    return true;
                }
            }
            return false;
        """)

        if result:
            claimed += 1
            log("✅ Store claimed")
            time.sleep(2)

    return claimed

# =========================
# PROGRESSION (UNCHANGED STYLE)
# =========================
def claim_progression_program_rewards(driver, player_id):
    log("🎯 Progression check")

    driver.get("https://hub.vertigogames.co/progression-program")
    time.sleep(2)

    claimed = 0

    for _ in range(5):
        result = driver.execute_script("""
            let btns = document.querySelectorAll('button');
            for (let b of btns){
                if(b.innerText.trim().toLowerCase()==='claim' && !b.disabled){
                    b.click();
                    return true;
                }
            }
            return false;
        """)

        if result:
            claimed += 1
            log("✅ Progression claimed")
            time.sleep(2)

    return claimed

# =========================
# 🆕 LOYALTY (NEW)
# =========================
def claim_loyalty_rewards(driver, player_id):
    log("🏆 Loyalty check")

    driver.get("https://hub.vertigogames.co/loyalty-program")
    time.sleep(2)

    claimed = 0

    for _ in range(6):
        result = driver.execute_script("""
            let btns = document.querySelectorAll('button');
            for (let b of btns){
                let txt = b.innerText.trim().toLowerCase();
                if(txt==='claim' && !b.disabled){
                    let p = b.parentElement.innerText.toLowerCase();
                    if(p.includes('lock')) continue;
                    b.click();
                    return true;
                }
            }
            return false;
        """)

        if result:
            claimed += 1
            log("✅ Loyalty claimed")
            time.sleep(2)

    if claimed == 0:
        log("ℹ️ No loyalty rewards")

    return claimed

# =========================
# PROCESS PLAYER (PATCHED)
# =========================
def process_player(player_id):

    stats = {
        "player_id": player_id,
        "daily": 0,
        "store": 0,
        "progression": 0,
        "loyalty": 0
    }

    driver = None

    try:
        log(f"\n🚀 {player_id}")

        # 🔥 HARD FAIL
        driver = create_driver()

        if not login_to_hub(driver, player_id):
            return stats

        stats["daily"] = claim_daily_rewards(driver, player_id)
        stats["store"] = claim_store_rewards(driver, player_id)
        stats["progression"] = claim_progression_program_rewards(driver, player_id)
        stats["loyalty"] = claim_loyalty_rewards(driver, player_id)

        total = sum(stats.values()) - 1  # exclude player_id

        log(f"🎉 Total claimed: {total}")

    except Exception as e:
        log(f"❌ CRITICAL ERROR: {e}")
        raise  # stop job

    finally:
        if driver:
            driver.quit()

    return stats

# =========================
# MAIN
# =========================
def main():
    with open(PLAYER_ID_FILE) as f:
        reader = csv.DictReader(f)
        players = [row["player_id"] for row in reader]

    results = []

    for p in players:
        results.append(process_player(p))
        time.sleep(2)

    total_loyalty = sum(r["loyalty"] for r in results)
    log(f"📊 Loyalty total: {total_loyalty}")

if __name__ == "__main__":
    main()
