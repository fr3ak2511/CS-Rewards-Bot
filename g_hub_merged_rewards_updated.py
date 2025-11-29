import time
import csv
import os
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
from datetime import datetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


# =====================================================
# Utility: Make Chrome Driver
# =====================================================
def make_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    driver.set_page_load_timeout(20)
    return driver


# =====================================================
# Utility: Login Function
# =====================================================
def login_and_claim_rewards(player_id):
    driver = make_driver()
    start_time = time.time()

    daily_claimed = 0
    store_claimed = 0
    login_success = False

    try:
        driver.get(f"https://hub.vertigogames.co/?inviteCode={player_id}")
        time.sleep(5)

        # Handle cookie popup
        try:
            cookie_btn = driver.find_element(By.XPATH, "//button[contains(text(),'Accept')]")
            cookie_btn.click()
            time.sleep(1)
        except:
            pass

        # Try clicking login or continuing to login page
        try:
            login_button = driver.find_element(By.XPATH, "//a[contains(text(),'Login')]")
            login_button.click()
            time.sleep(5)
        except:
            pass

        # Verify login success by checking profile element
        try:
            driver.find_element(By.XPATH, "//div[contains(@class,'profile-avatar')]")
            login_success = True
        except:
            login_success = False

        # Navigate to Daily Rewards
        if login_success:
            driver.get("https://hub.vertigogames.co/daily-rewards")
            time.sleep(4)
            try:
                claim_buttons = driver.find_elements(By.XPATH, "//button[contains(text(),'Claim')]")
                for btn in claim_buttons:
                    btn.click()
                    time.sleep(2)
                    daily_claimed += 1
            except:
                pass

            # Navigate to Store Rewards
            driver.get("https://hub.vertigogames.co/store")
            time.sleep(5)
            try:
                claim_buttons = driver.find_elements(By.XPATH, "//button[contains(text(),'Claim')]")
                for btn in claim_buttons[:3]:  # claim first 3
                    btn.click()
                    time.sleep(2)
                    store_claimed += 1
            except:
                pass

    except Exception as e:
        print(f"[ERROR] {player_id}: {str(e)}")
    finally:
        driver.quit()

    elapsed_time = round(time.time() - start_time, 1)
    return login_success, daily_claimed, store_claimed, elapsed_time


# =====================================================
# Utility: Send Email Summary
# =====================================================
def send_email(summary):
    sender_email = "saurabh.mendiratta7@gmail.com"
    receiver_email = "saurabh.mendiratta7@gmail.com"
    password = os.getenv("EMAIL_APP_PASSWORD")  # use GitHub Secret for this

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Hub Merged Rewards Summary"
    msg["From"] = sender_email
    msg["To"] = receiver_email

    body = f"Hello,\n\nHere is the latest automated Hub Merged Rewards execution summary.\n\n{summary}\n\nRegards,\nGitHub Automation Bot"
    msg.attach(MIMEText(body, "plain"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender_email, password)
        server.send_message(msg)


# =====================================================
# MAIN SCRIPT EXECUTION
# =====================================================
def main():
    start_time = time.time()
    print(f"[INFO] Run started at {datetime.utcnow()} UTC")

    # Load player IDs (repo-relative)
    csv_path = os.path.join(os.getcwd(), "players.csv")
    with open(csv_path, newline="") as f:
        reader = csv.reader(f)
        player_ids = [row[0].strip() for row in reader if row]

    results = []
    total_daily = total_store = total_success = 0

    for pid in player_ids:
        success, daily, store, duration = login_and_claim_rewards(pid)
        results.append((pid, success, daily, store, duration))

        if success:
            total_success += 1
        total_daily += daily
        total_store += store

    total_rewards = total_daily + total_store
    total_time = round(time.time() - start_time, 1)
    avg_time = round(total_time / len(player_ids), 1)

    summary_lines = [
        "===================================================",
        "HUB MERGED REWARDS SUMMARY",
        "===================================================",
        f"Total Players: {len(player_ids)}",
        f"Successful Logins: {total_success}",
        f"Daily Rewards Claimed: {total_daily}",
        f"Store Rewards Claimed: {total_store}",
        f"Total Rewards Claimed: {total_rewards}",
        f"Total Time Taken: {total_time}s ({round(total_time/60,1)} min)",
        f"Avg Time per ID: {avg_time}s",
        "---------------------------------------------------",
        "-",
        "Per-ID details:"
    ]

    for pid, success, daily, store, duration in results:
        summary_lines.append(
            f"ID: {pid} | Login: {'Yes' if success else 'No'} | Daily: {daily} | Store: {store} | Time: {duration}s"
        )

    summary_lines.append("===================================================")
    summary = "\n".join(summary_lines)

    print(summary)
    send_email(summary)


if __name__ == "__main__":
    main()
