import time
import csv
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ============================================================
# CONFIG
# ============================================================
PLAYERS_FILE = "players.csv"
STORE_URL = "https://hub.vertigogames.co/store"
DAILY_URL = "https://hub.vertigogames.co/daily-rewards"
LOG_FILE = "workflow_summary.log"

# ============================================================
# CHROME SETUP
# ============================================================
def create_driver():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    service = Service()  # auto-handled on GitHub Actions
    driver = webdriver.Chrome(service=service, options=options)
    return driver

# ============================================================
# LOGIN FUNCTION
# ============================================================
def login(driver, player_id):
    driver.get(DAILY_URL)
    wait = WebDriverWait(driver, 20)

    try:
        login_button = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'Login')]")))
        driver.execute_script("arguments[0].click();", login_button)
        time.sleep(2)

        input_box = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[placeholder*='Enter']")))
        input_box.clear()
        input_box.send_keys(player_id)
        time.sleep(1)

        submit_button = driver.find_element(By.XPATH, "//button[contains(.,'Login')]")
        driver.execute_script("arguments[0].click();", submit_button)
        time.sleep(3)

        if "daily-rewards" in driver.current_url or "Claim" in driver.page_source:
            return True
        else:
            return False
    except Exception as e:
        print(f"[{player_id}] Login failed: {e}")
        return False

# ============================================================
# CLAIM FUNCTION (DAILY + STORE)
# ============================================================
def claim_rewards(driver, player_id):
    wait = WebDriverWait(driver, 10)
    total_claims = 0

    # Step 1: Daily Rewards
    try:
        driver.get(DAILY_URL)
        time.sleep(3)
        daily_buttons = driver.find_elements(By.XPATH, "//button[contains(translate(., 'CLAIM', 'claim'),'claim')]")
        for btn in daily_buttons:
            try:
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
                time.sleep(0.5)
                driver.execute_script("arguments[0].click();", btn)
                time.sleep(1.5)
                state = btn.text.strip().lower()
                if "claim" not in state:
                    total_claims += 1
                    print(f"[{player_id}] Daily reward claimed.")
            except Exception as e:
                print(f"[{player_id}] Error claiming daily reward: {e}")
    except Exception as e:
        print(f"[{player_id}] Daily reward section failed: {e}")

    # Step 2: Store Rewards
    try:
        driver.get(STORE_URL)
        time.sleep(3)
        store_buttons = driver.find_elements(By.XPATH, "//button[contains(translate(., 'CLAIM', 'claim'),'claim')]")
        for btn in store_buttons:
            try:
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
                time.sleep(0.5)
                driver.execute_script("arguments[0].click();", btn)
                time.sleep(1.5)
                state = btn.text.strip().lower()
                if "claim" not in state:
                    total_claims += 1
                    print(f"[{player_id}] Store reward claimed.")
            except Exception as e:
                print(f"[{player_id}] Error claiming store reward: {e}")
    except Exception as e:
        print(f"[{player_id}] Store reward section failed: {e}")

    return total_claims

# ============================================================
# MAIN PROCESS
# ============================================================
def main():
    with open(PLAYERS_FILE, "r") as f:
        players = [line.strip() for line in f.readlines() if line.strip()]

    total_players = len(players)
    successful_logins = 0
    total_claims = 0
    start_time = time.time()

    for pid in players:
        driver = create_driver()
        print(f"Processing Player ID: {pid}")

        if login(driver, pid):
            successful_logins += 1
            claims = claim_rewards(driver, pid)
            total_claims += claims
        else:
            print(f"[{pid}] Login unsuccessful.")

        driver.quit()
        time.sleep(1)

    end_time = time.time()
    total_time = end_time - start_time
    avg_time = total_time / total_players if total_players else 0

    with open(LOG_FILE, "w") as log:
        log.write("====================================================\n")
        log.write("MERGED REWARDS SUMMARY\n")
        log.write("====================================================\n")
        log.write(f"Total Players: {total_players}\n")
        log.write(f"Successful Logins: {successful_logins}\n")
        log.write(f"Total Rewards Claimed: {total_claims}\n")
        log.write(f"Total Time Taken: {total_time:.1f}s ({total_time/60:.1f} min)\n")
        log.write(f"Avg Time per ID: {avg_time:.1f}s\n")
        log.write("====================================================\n")

if __name__ == "__main__":
    main()
