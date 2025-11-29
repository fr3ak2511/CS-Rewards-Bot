import csv
import time
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys

print_lock = threading.Lock()

def thread_safe_print(msg):
    with print_lock:
        print(msg)

def create_driver():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(25)
    return driver

def automate_player(player_id, thread_id):
    driver = create_driver()
    wait = WebDriverWait(driver, 20)
    result = {
        "player_id": player_id,
        "login_successful": False,
        "daily_claims": 0,
        "store_claims": 0,
        "status": "error",
    }

    try:
        driver.get("https://hub.vertigogames.co/daily-rewards")
        thread_safe_print(f"[{player_id}] Opened daily rewards page.")

        # Login
        login_btn = None
        for xp in [
            "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'),'login')]",
            "//a[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'),'login')]",
        ]:
            try:
                login_btn = wait.until(EC.element_to_be_clickable((By.XPATH, xp)))
                break
            except Exception:
                continue

        if not login_btn:
            thread_safe_print(f"[{player_id}] No login button found.")
            return result

        driver.execute_script("arguments[0].click();", login_btn)
        inp = wait.until(EC.presence_of_element_located((By.XPATH, "//input[@type='text' or @name='username']")))
        inp.send_keys(player_id)
        inp.send_keys(Keys.ENTER)
        time.sleep(3)
        result["login_successful"] = True
        thread_safe_print(f"[{player_id}] Login successful.")

        # Daily reward claims
        claimed = 0
        claim_btns = driver.find_elements(By.XPATH, "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'),'claim')]")
        for btn in claim_btns:
            try:
                driver.execute_script("arguments[0].click();", btn)
                claimed += 1
                time.sleep(1)
            except Exception:
                continue
        result["daily_claims"] = claimed
        thread_safe_print(f"[{player_id}] Daily claims done: {claimed}")

        # Store reward claims
        driver.get("https://hub.vertigogames.co/store")
        time.sleep(2)
        store_claimed = 0
        store_btns = driver.find_elements(By.XPATH, "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'),'claim')]")
        for btn in store_btns:
            try:
                driver.execute_script("arguments[0].click();", btn)
                store_claimed += 1
                time.sleep(1)
            except Exception:
                continue
        result["store_claims"] = store_claimed
        thread_safe_print(f"[{player_id}] Store claims done: {store_claimed}")

        result["status"] = "success" if (claimed + store_claimed) > 0 else "no_claims"

    except Exception as e:
        thread_safe_print(f"[{player_id}] Error: {e}")
    finally:
        driver.quit()
    return result

def process_batch(players, batch_num):
    results = []
    with ThreadPoolExecutor(max_workers=min(3, len(players))) as executor:
        future_to_id = {executor.submit(automate_player, pid, batch_num): pid for pid in players}
        for fut in as_completed(future_to_id):
            results.append(fut.result())
    return results

def main():
    csv_path = os.path.join(os.path.dirname(__file__), "players.csv")
    players = []
    with open(csv_path, newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            pid = row[0].strip()
            if pid:
                players.append(pid)

    thread_safe_print(f"Loaded {len(players)} players.")
    start_time = time.time()

    all_results = []
    batch_size = 2
    for i in range(0, len(players), batch_size):
        batch = players[i:i + batch_size]
        thread_safe_print(f"Processing batch {i//batch_size + 1}")
        all_results.extend(process_batch(batch, i//batch_size + 1))
        time.sleep(0.5)

    total_time = time.time() - start_time
    total_players = len(players)
    successful_logins = sum(1 for r in all_results if r["login_successful"])
    total_daily = sum(r["daily_claims"] for r in all_results)
    total_store = sum(r["store_claims"] for r in all_results)
    avg_time_per_id = total_time / total_players if total_players > 0 else 0

    summary = (
        f"\n{'='*70}\nMERGED REWARDS SUMMARY\n{'='*70}\n"
        f"Total Players: {total_players}\n"
        f"Successful Logins: {successful_logins}\n"
        f"Daily Rewards Claimed: {total_daily}\n"
        f"Store Rewards Claimed: {total_store}\n"
        f"Total Rewards Claimed: {total_daily + total_store}\n"
        f"Total Time Taken: {total_time:.1f}s ({total_time/60:.1f} min)\n"
        f"Avg Time per ID: {avg_time_per_id:.1f}s\n"
        f"{'='*70}\n"
    )

    thread_safe_print(summary)
    with open("workflow_summary.log", "w", encoding="utf-8") as f:
        f.write(summary)

if __name__ == "__main__":
    main()
