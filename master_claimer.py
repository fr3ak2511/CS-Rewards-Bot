import csv
import time
from datetime import datetime
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By

PLAYER_ID_FILE = "players.csv"
HEADLESS = True

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

# =========================
# 🔥 BULLETPROOF DRIVER FIX
# =========================
def create_driver():
    for attempt in range(3):
        try:
            options = uc.ChromeOptions()

            if HEADLESS:
                options.add_argument("--headless=new")

            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-gpu")
            options.add_argument("--remote-debugging-port=9222")

            # ✅ CRITICAL FIX: Force correct chrome binary
            options.binary_location = "/usr/bin/google-chrome"

            driver = uc.Chrome(
                options=options,
                browser_executable_path="/usr/bin/google-chrome",
                use_subprocess=True
            )

            driver.set_page_load_timeout(30)

            log("✅ Driver initialized SUCCESS")
            return driver

        except Exception as e:
            log(f"⚠️ Driver init failed {attempt+1}: {e}")
            time.sleep(2)

            if attempt == 2:
                raise RuntimeError("Driver init failed")

# =========================
# LOGIN
# =========================
def login_to_hub(driver, player_id):
    driver.get("https://hub.vertigogames.co/daily-rewards")
    time.sleep(3)

    try:
        for btn in driver.find_elements(By.TAG_NAME, "button"):
            if "login" in btn.text.lower():
                btn.click()
                break

        time.sleep(2)

        inputs = driver.find_elements(By.TAG_NAME, "input")
        inputs[0].send_keys(player_id)

        time.sleep(1)

        for btn in driver.find_elements(By.TAG_NAME, "button"):
            if "login" in btn.text.lower() or "submit" in btn.text.lower():
                btn.click()
                break

        time.sleep(3)
        return True

    except:
        return False

# =========================
# DAILY
# =========================
def claim_daily_rewards(driver, player_id):
    driver.get("https://hub.vertigogames.co/daily-rewards")
    time.sleep(2)

    return driver.execute_script("""
        let btns=document.querySelectorAll('button');
        for(let b of btns){
            if(b.innerText.toLowerCase().includes('claim') && !b.disabled){
                b.click(); return 1;
            }
        }
        return 0;
    """)

# =========================
# STORE
# =========================
def claim_store_rewards(driver, player_id):
    driver.get("https://hub.vertigogames.co/store")
    time.sleep(2)

    claimed = 0

    for _ in range(3):
        res = driver.execute_script("""
            let btns=document.querySelectorAll('button');
            for(let b of btns){
                if(b.innerText.toLowerCase().includes('free') && !b.disabled){
                    b.click(); return 1;
                }
            }
            return 0;
        """)

        if res:
            claimed += 1
            time.sleep(2)

    return claimed

# =========================
# PROGRESSION
# =========================
def claim_progression_program_rewards(driver, player_id):
    driver.get("https://hub.vertigogames.co/progression-program")
    time.sleep(2)

    claimed = 0

    for _ in range(5):
        res = driver.execute_script("""
            let btns=document.querySelectorAll('button');
            for(let b of btns){
                if(b.innerText.toLowerCase().includes('claim') && !b.disabled){
                    b.click(); return 1;
                }
            }
            return 0;
        """)

        if res:
            claimed += 1
            time.sleep(2)

    return claimed

# =========================
# 🆕 LOYALTY
# =========================
def claim_loyalty_rewards(driver, player_id):
    driver.get("https://hub.vertigogames.co/loyalty-program")
    time.sleep(2)

    claimed = 0

    for _ in range(6):
        res = driver.execute_script("""
            let btns=document.querySelectorAll('button');
            for(let b of btns){
                let txt=b.innerText.toLowerCase();
                if(txt.includes('claim') && !b.disabled){
                    let p=b.parentElement.innerText.toLowerCase();
                    if(p.includes('lock')) continue;
                    b.click(); return 1;
                }
            }
            return 0;
        """)

        if res:
            claimed += 1
            time.sleep(2)

    return claimed

# =========================
# PROCESS
# =========================
def process_player(player_id):
    driver = create_driver()

    if not login_to_hub(driver, player_id):
        raise RuntimeError("Login failed")

    d = claim_daily_rewards(driver, player_id)
    s = claim_store_rewards(driver, player_id)
    p = claim_progression_program_rewards(driver, player_id)
    l = claim_loyalty_rewards(driver, player_id)

    total = d + s + p + l

    log(f"{player_id} → {total} claimed")

    driver.quit()

# =========================
# MAIN
# =========================
def main():
    with open(PLAYER_ID_FILE) as f:
        players = [row["player_id"] for row in csv.DictReader(f)]

    for p in players:
        process_player(p)
        time.sleep(2)

if __name__ == "__main__":
    main()
