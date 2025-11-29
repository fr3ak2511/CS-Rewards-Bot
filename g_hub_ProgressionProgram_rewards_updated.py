import csv
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
import threading
import os

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
    # Use preinstalled Chrome/Chromedriver on GitHub runner
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(25)
    return driver

def automate_player(player_id, thread_id):
    from selenium.webdriver.common.keys import Keys
    driver = create_driver()
    wait = WebDriverWait(driver, 12)
    result = {
        "player_id": player_id,
        "login_successful": False,
        "monthly_rewards": 0,
        "status": "error",
    }
    try:
        driver.get("https://hub.vertigogames.co/progression-program")
        login_btns = driver.find_elements(By.XPATH, "//button[contains(text(),'Login') or contains(text(),'Log in')]")
        if not login_btns:
            thread_safe_print(f"[{player_id}] No login button")
            return result

        login_btns[0].click()
        inp = wait.until(EC.presence_of_element_located((By.XPATH, "//input[@type='text']")))
        inp.send_keys(player_id)
        inp.send_keys(Keys.ENTER)
        time.sleep(2)
        result["login_successful"] = True

        claim_btns = driver.find_elements(By.XPATH, "//button[contains(text(),'Claim')]")
        claimed = 0
        for btn in claim_btns:
            try:
                if btn.is_displayed() and btn.is_enabled():
                    btn.click()
                    claimed += 1
                    time.sleep(1)
            except Exception:
                continue
        result["monthly_rewards"] = claimed
        result["status"] = "success" if claimed > 0 else "no_claims"
    except Exception as e:
        thread_safe_print(f"[{player_id}] Error: {e}")
    finally:
        driver.quit()
    return result

def process_batch(players, batch_num):
    results = []
    with ThreadPoolExecutor(max_workers=len(players)) as exe:
        future_to_id = {exe.submit(automate_player, pid, batch_num): pid for pid in players}
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

    thread_safe_print(f"Loaded {len(players)} players")

    start = time.time()
    batch_size = 2
    all_results = []
    for i in range(0, len(players), batch_size):
        batch = players[i:i+batch_size]
        all_results.extend(process_batch(batch, i//batch_size + 1))
        time.sleep(0.5)

    total_time = time.time() - start
    successes = sum(1 for r in all_results if r["login_successful"])
    total_rewards = sum(r["monthly_rewards"] for r in all_results)

    summary = (
        f"\n{'='*60}\nPROGRESSION PROGRAM SUMMARY\n{'='*60}\n"
        f"Total Players: {len(players)}\n"
        f"Successful Logins: {successes}\n"
        f"Total Rewards Claimed: {total_rewards}\n"
        f"Execution Time: {total_time:.1f}s ({total_time/60:.1f} min)\n"
        f"{'='*60}\n"
    )
    thread_safe_print(summary)

    with open("workflow_summary.log", "w", encoding="utf-8") as f:
        f.write(summary)

if __name__ == "__main__":
    main()
