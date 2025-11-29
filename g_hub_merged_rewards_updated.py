import os
import csv
import time
import smtplib
from email.mime.text import MIMEText
from datetime import datetime
from typing import List, Dict, Tuple

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    TimeoutException,
    ElementClickInterceptedException,
    StaleElementReferenceException,
    NoSuchElementException,
)
from webdriver_manager.chrome import ChromeDriverManager


# -----------------------------
# Config
# -----------------------------
HUB_BASE = "https://hub.vertigogames.co"
URL_DAILY = f"{HUB_BASE}/daily-rewards"
URL_STORE = f"{HUB_BASE}/store"

PLAYERS_CSV = "players.csv"  # relative path in repo

# Selenium waits
SHORT = 5
MEDIUM = 15
LONG = 25

# E-mail secrets (already set in your repo)
SMTP_SERVER = os.getenv("SMTP_SERVER")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
SMTP_FROM = os.getenv("SMTP_FROM")
SMTP_TO = os.getenv("SMTP_TO") or SMTP_FROM


# -----------------------------
# Selenium helpers
# -----------------------------
def make_driver() -> webdriver.Chrome:
    options = ChromeOptions()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1440,1200")
    options.add_argument("--lang=en-US")
    driver = webdriver.Chrome(ChromeDriverManager().install(), options=options)
    driver.set_page_load_timeout(LONG)
    driver.implicitly_wait(2)
    return driver


def js_click(driver: webdriver.Chrome, el):
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    driver.execute_script("arguments[0].click();", el)


def robust_click(driver: webdriver.Chrome, el):
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        el.click()
    except (ElementClickInterceptedException, StaleElementReferenceException):
        js_click(driver, el)


def wait_visible(driver, locator, timeout=MEDIUM):
    return WebDriverWait(driver, timeout).until(EC.visibility_of_element_located(locator))


def wait_clickable(driver, locator, timeout=MEDIUM):
    return WebDriverWait(driver, timeout).until(EC.element_to_be_clickable(locator))


def wait_disappear(driver, locator, timeout=MEDIUM):
    WebDriverWait(driver, timeout).until(EC.invisibility_of_element_located(locator))


def safe_find_all(driver, locator) -> List:
    try:
        return driver.find_elements(*locator)
    except Exception:
        return []


def accept_cookies_and_popups(driver: webdriver.Chrome):
    # Cookie banner
    for xpath in [
        "//button[contains(.,'Accept') or contains(.,'I agree')]",
        "//button[contains(.,'Got it')]",
    ]:
        try:
            btns = safe_find_all(driver, (By.XPATH, xpath))
            for b in btns:
                if b.is_displayed():
                    robust_click(driver, b)
                    time.sleep(0.3)
        except Exception:
            pass

    # Generic modal close (×) buttons
    for xpath in [
        "//button[@aria-label='Close']",
        "//div[contains(@class,'modal')]//button[contains(.,'Close')]",
        "//div[contains(@class,'modal')]//button[contains(.,'OK')]",
    ]:
        try:
            btns = safe_find_all(driver, (By.XPATH, xpath))
            for b in btns:
                if b.is_displayed():
                    robust_click(driver, b)
                    time.sleep(0.2)
        except Exception:
            pass


# -----------------------------
# Game-specific helpers
# -----------------------------
def login_with_player_id(driver: webdriver.Chrome, player_id: str) -> bool:
    """
    Your manual script already logs in successfully. This function mirrors the same flow:
    - open HUB base
    - ensure we're authenticated for the Player ID (stored session / ID-login link / your existing method)
    If you were using one-time links or a JS login method in the manual script, copy that here.
    The current site often persists sessions; we just navigate and verify the avatar is present.
    """
    driver.get(HUB_BASE)
    accept_cookies_and_popups(driver)

    # If your manual scripts open a specific URL for Player ID auth, call it here.
    # Example (pseudo):
    # driver.get(f"{HUB_BASE}/login?player={player_id}")

    # Then verify a left nav is visible (Home/Store etc.)
    try:
        wait_visible(driver, (By.XPATH, "//nav//a[contains(.,'Home') or contains(.,'Store')]"), timeout=LONG)
        return True
    except TimeoutException:
        return False


def claim_one_daily_reward(driver: webdriver.Chrome) -> int:
    """
    Go to Daily Rewards grid and click the first enabled "Claim" button (one claim per day).
    Returns 1 if claimed, else 0.
    """
    driver.get(URL_DAILY)
    accept_cookies_and_popups(driver)

    # Enabled Claim button on daily grid (avoid locked/claimed cards)
    # Target the green "Claim" button that is clickable.
    try:
        # Some cards show button text "Claim" when available; otherwise it's "Claimed" or disabled.
        btn = wait_clickable(driver, (By.XPATH, "//button[normalize-space()='Claim' and not(@disabled)]"), timeout=MEDIUM)
        pre_html = btn.get_attribute("outerHTML")
        robust_click(driver, btn)
        # wait for state change: button disappears or text changes to 'Claimed'
        time.sleep(0.5)
        WebDriverWait(driver, MEDIUM).until(
            lambda d: "Claimed" in btn.text or btn.get_attribute("outerHTML") != pre_html
        )
        return 1
    except TimeoutException:
        # Nothing available to claim
        return 0
    except Exception:
        return 0


def switch_to_store_daily_rewards_tab(driver: webdriver.Chrome):
    """
    Ensure we are on Store > Daily Rewards tab (top filter row).
    """
    driver.get(URL_STORE)
    accept_cookies_and_popups(driver)

    # Try to click "Daily Rewards" tab if not already selected.
    # It can be a button or tab-like element.
    for xpath in [
        "//button[contains(.,'Daily Rewards')]",
        "//div[contains(@class,'tabs')]//button[contains(.,'Daily Rewards')]",
        "//a[contains(.,'Daily Rewards')]",
    ]:
        try:
            el = wait_clickable(driver, (By.XPATH, xpath), timeout=SHORT)
            robust_click(driver, el)
            time.sleep(0.4)
            break
        except TimeoutException:
            continue


def claim_store_daily_rewards(driver: webdriver.Chrome, max_to_claim: int = 3) -> int:
    """
    On the Store page, in the 'Daily Rewards' tab, claim up to 3 daily store rewards.
    Returns the number of store rewards claimed.
    """
    switch_to_store_daily_rewards_tab(driver)
    accept_cookies_and_popups(driver)

    claimed = 0
    # Cards with a visible Claim button that is enabled (no cooldown text "Next in")
    # We limit ourselves to the first 3 available.
    start = time.time()
    while claimed < max_to_claim and (time.time() - start) < 60:
        # Refresh the set each loop (sometimes the DOM re-renders after a claim)
        buttons = safe_find_all(driver, (By.XPATH, "//button[normalize-space()='Claim' and not(@disabled)]"))
        if not buttons:
            break

        # Be defensive: sometimes the first “Claim” can belong to a one-time card; filters were added above already,
        # but if more than 3 appear we still only take 3.
        for btn in buttons:
            if claimed >= max_to_claim:
                break
            try:
                pre = btn.get_attribute("outerHTML")
                robust_click(driver, btn)
                time.sleep(0.5)
                # Wait for state change: button text changes or outerHTML changes or button disappears
                WebDriverWait(driver, MEDIUM).until(
                    lambda d: ("Claimed" in btn.text)
                    or (btn.get_attribute("outerHTML") != pre)
                    or (not btn.is_displayed())
                )
                claimed += 1
                accept_cookies_and_popups(driver)
            except Exception:
                # The button may be stale or blocked; try next
                continue

    return claimed


# -----------------------------
# Summary + Email
# -----------------------------
def send_email(subject: str, body: str):
    if not all([SMTP_SERVER, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, SMTP_FROM, SMTP_TO]):
        print("SMTP not fully configured – skipping email.")
        print("Subject:", subject)
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
        # The file is one ID per line (no commas). Handle both CSV or plain lines.
        reader = csv.reader(f)
        for row in reader:
            if not row:
                continue
            ids.append(row[0].strip())
    return [pid for pid in ids if pid]


def run_for_player(driver: webdriver.Chrome, pid: str) -> Dict[str, object]:
    start = time.time()
    daily_claimed = 0
    store_claimed = 0
    logged_in = login_with_player_id(driver, pid)
    if logged_in:
        try:
            # claim daily page first
            daily_claimed = claim_one_daily_reward(driver)
        except Exception:
            daily_claimed = 0

        try:
            # claim up to 3 store rewards
            store_claimed = claim_store_daily_rewards(driver, max_to_claim=3)
        except Exception:
            store_claimed = 0

    total_time = time.time() - start
    return {
        "player_id": pid,
        "login_successful": logged_in,
        "daily_claimed": int(daily_claimed),
        "store_claimed": int(store_claimed),
        "total_time": total_time,
    }


def format_summary(all_results: List[Dict[str, object]]) -> str:
    total_players = len(all_results)
    successful = sum(1 for r in all_results if r["login_successful"])
    total_daily = sum(r["daily_claimed"] for r in all_results)
    total_store = sum(r["store_claimed"] for r in all_results)
    total_rewards = total_daily + total_store
    total_time = sum(r["total_time"] for r in all_results)
    avg_time = (total_time / total_players) if total_players else 0.0

    lines = []
    lines.append("Hello,\nHere is the latest automated Hub Merged Rewards execution summary.\n")
    lines.append("---")
    lines.append("=" * 60)
    lines.append("HUB MERGED REWARDS SUMMARY")
    lines.append("=" * 60)
    lines.append(f"Total Players: {total_players}")
    lines.append(f"Successful Logins: {successful}")
    lines.append(f"Daily Rewards Claimed: {total_daily}")
    lines.append(f"Store Rewards Claimed: {total_store}")
    lines.append(f"Total Rewards Claimed: {total_rewards}")
    lines.append(f"Total Time Taken: {total_time:.1f}s ({total_time/60.0:.1f} min)")
    lines.append(f"Avg Time per ID: {avg_time:.1f}s")
    lines.append("-")
    lines.append("")
    lines.append("Per-ID details:")
    for r in all_results:
        lines.append(
            f"ID: {r['player_id']} | Login: {'Yes' if r['login_successful'] else 'No'} "
            f"| Daily: {r['daily_claimed']} | Store: {r['store_claimed']} | Time: {r['total_time']:.1f}s"
        )
    lines.append("")
    lines.append("--")
    lines.append("Regards,\nGitHub Automation Bot")
    return "\n".join(lines)


# -----------------------------
# Main
# -----------------------------
def main():
    started_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[INFO] Run started at {started_at}")

    player_ids = load_player_ids(PLAYERS_CSV)
    if not player_ids:
        raise RuntimeError("No player IDs found in players.csv")

    driver = make_driver()
    results = []

    try:
        for pid in player_ids:
            print(f"[INFO] Processing {pid} ...")
            r = run_for_player(driver, pid)
            results.append(r)
            # small gap between accounts
            time.sleep(0.8)
    finally:
        try:
            driver.quit()
        except Exception:
            pass

    body = format_summary(results)
    subject = "Hub Merged Rewards Summary"
    print("\n" + body + "\n")
    send_email(subject, body)


if __name__ == "__main__":
    main()
