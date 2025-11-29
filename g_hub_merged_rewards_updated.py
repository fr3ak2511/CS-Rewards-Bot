import os
import csv
import time
import smtplib
from email.mime.text import MIMEText
from datetime import datetime
from typing import List, Dict

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    ElementClickInterceptedException,
    StaleElementReferenceException,
)
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager


# -----------------------------
# Config
# -----------------------------
HUB_BASE = "https://hub.vertigogames.co"
URL_DAILY = f"{HUB_BASE}/daily-rewards"
URL_STORE = f"{HUB_BASE}/store"

PLAYERS_CSV = "players.csv"

SHORT = 5
MEDIUM = 15
LONG = 25

SMTP_SERVER = os.getenv("SMTP_SERVER")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
SMTP_FROM = os.getenv("SMTP_FROM")
SMTP_TO = os.getenv("SMTP_TO") or SMTP_FROM


# -----------------------------
# Selenium setup
# -----------------------------
def make_driver() -> webdriver.Chrome:
    options = ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1440,1200")
    options.add_argument("--lang=en-US")

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(LONG)
    driver.implicitly_wait(2)
    return driver


def js_click(driver, el):
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    driver.execute_script("arguments[0].click();", el)


def robust_click(driver, el):
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        el.click()
    except (ElementClickInterceptedException, StaleElementReferenceException):
        js_click(driver, el)


def wait_visible(driver, locator, timeout=MEDIUM):
    return WebDriverWait(driver, timeout).until(EC.visibility_of_element_located(locator))


def wait_clickable(driver, locator, timeout=MEDIUM):
    return WebDriverWait(driver, timeout).until(EC.element_to_be_clickable(locator))


def safe_find_all(driver, locator):
    try:
        return driver.find_elements(*locator)
    except Exception:
        return []


def accept_cookies_and_popups(driver):
    selectors = [
        "//button[contains(.,'Accept')]",
        "//button[contains(.,'I Agree')]",
        "//button[contains(.,'Got it')]",
        "//button[contains(.,'OK')]",
        "//button[@aria-label='Close']",
        "//div[contains(@class,'modal')]//button[contains(.,'Close')]",
    ]
    for xp in selectors:
        try:
            for el in safe_find_all(driver, (By.XPATH, xp)):
                if el.is_displayed():
                    robust_click(driver, el)
                    time.sleep(0.3)
        except Exception:
            pass


# -----------------------------
# Game-specific automation
# -----------------------------
def login_with_player(driver, player_id: str) -> bool:
    driver.get(HUB_BASE)
    accept_cookies_and_popups(driver)

    try:
        wait_visible(driver, (By.XPATH, "//nav//a[contains(.,'Home') or contains(.,'Store')]"), timeout=LONG)
        return True
    except TimeoutException:
        return False


def claim_one_daily_reward(driver) -> int:
    driver.get(URL_DAILY)
    accept_cookies_and_popups(driver)

    try:
        btn = wait_clickable(driver, (By.XPATH, "//button[normalize-space()='Claim' and not(@disabled)]"), timeout=MEDIUM)
        before_html = btn.get_attribute("outerHTML")
        robust_click(driver, btn)
        time.sleep(0.5)
        WebDriverWait(driver, MEDIUM).until(
            lambda d: "Claimed" in btn.text or btn.get_attribute("outerHTML") != before_html
        )
        return 1
    except Exception:
        return 0


def switch_to_store_daily_rewards_tab(driver):
    driver.get(URL_STORE)
    accept_cookies_and_popups(driver)

    for xp in [
        "//button[contains(.,'Daily Rewards')]",
        "//div[contains(@class,'tabs')]//button[contains(.,'Daily Rewards')]",
        "//a[contains(.,'Daily Rewards')]",
    ]:
        try:
            el = wait_clickable(driver, (By.XPATH, xp), timeout=SHORT)
            robust_click(driver, el)
            time.sleep(0.4)
            break
        except TimeoutException:
            continue


def claim_store_rewards(driver, max_claims=3) -> int:
    switch_to_store_daily_rewards_tab(driver)
    accept_cookies_and_popups(driver)

    claimed = 0
    start = time.time()

    while claimed < max_claims and (time.time() - start) < 60:
        buttons = safe_find_all(driver, (By.XPATH, "//button[normalize-space()='Claim' and not(@disabled)]"))
        if not buttons:
            break
        for btn in buttons:
            if claimed >= max_claims:
                break
            try:
                before_html = btn.get_attribute("outerHTML")
                robust_click(driver, btn)
                time.sleep(0.5)
                WebDriverWait(driver, MEDIUM).until(
                    lambda d: ("Claimed" in btn.text)
                    or (btn.get_attribute("outerHTML") != before_html)
                    or (not btn.is_displayed())
                )
                claimed += 1
                accept_cookies_and_popups(driver)
            except Exception:
                continue
    return claimed


# -----------------------------
# Email + Reporting
# -----------------------------
def send_email(subject: str, body: str):
    if not SMTP_SERVER:
        print("SMTP not configured.")
        print(body)
        return

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = SMTP_TO

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.sendmail(SMTP_FROM, [SMTP_TO], msg.as_string())


def load_player_ids(path: str) -> List[str]:
    ids = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row:
                continue
            ids.append(row[0].strip())
    return [x for x in ids if x]


def run_for_player(driver, pid: str) -> Dict[str, object]:
    start = time.time()
    daily = 0
    store = 0
    logged_in = login_with_player(driver, pid)
    if logged_in:
        try:
            daily = claim_one_daily_reward(driver)
        except Exception:
            daily = 0
        try:
            store = claim_store_rewards(driver, max_claims=3)
        except Exception:
            store = 0
    duration = time.time() - start
    return {
        "player_id": pid,
        "login_successful": logged_in,
        "daily_claimed": daily,
        "store_claimed": store,
        "total_time": duration,
    }


def format_summary(results: List[Dict[str, object]]) -> str:
    total_players = len(results)
    logins = sum(1 for r in results if r["login_successful"])
    total_daily = sum(r["daily_claimed"] for r in results)
    total_store = sum(r["store_claimed"] for r in results)
    total_rewards = total_daily + total_store
    total_time = sum(r["total_time"] for r in results)
    avg_time = total_time / total_players if total_players else 0.0

    lines = []
    lines.append("Hello,\nHere is the latest automated Hub Merged Rewards execution summary.\n")
    lines.append("=" * 60)
    lines.append("HUB MERGED REWARDS SUMMARY")
    lines.append("=" * 60)
    lines.append(f"Total Players: {total_players}")
    lines.append(f"Successful Logins: {logins}")
    lines.append(f"Daily Rewards Claimed: {total_daily}")
    lines.append(f"Store Rewards Claimed: {total_store}")
    lines.append(f"Total Rewards Claimed: {total_rewards}")
    lines.append(f"Total Time Taken: {total_time:.1f}s ({total_time/60.0:.1f} min)")
    lines.append(f"Avg Time per ID: {avg_time:.1f}s")
    lines.append("-")
    lines.append("Per-ID details:")
    for r in results:
        lines.append(
            f"ID: {r['player_id']} | Login: {'Yes' if r['login_successful'] else 'No'} | "
            f"Daily: {r['daily_claimed']} | Store: {r['store_claimed']} | Time: {r['total_time']:.1f}s"
        )
    lines.append("\nRegards,\nGitHub Automation Bot")
    return "\n".join(lines)


# -----------------------------
# Main
# -----------------------------
def main():
    print(f"[INFO] Run started at {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    players = load_player_ids(PLAYERS_CSV)
    if not players:
        raise RuntimeError("No player IDs found in players.csv")

    driver = make_driver()
    results = []
    try:
        for pid in players:
            print(f"[INFO] Processing {pid} ...")
            res = run_for_player(driver, pid)
            results.append(res)
            time.sleep(0.8)
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    body = format_summary(results)
    subject = "Hub Merged Rewards Summary"
    print(body)
    send_email(subject, body)


if __name__ == "__main__":
    main()
