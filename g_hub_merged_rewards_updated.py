import os
import csv
import time
from datetime import datetime
from typing import Tuple, List

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
import smtplib
from email.mime.text import MIMEText


# ----------------------------
# Email helpers (uses GitHub Secrets via env)
# ----------------------------
SMTP_SERVER   = os.getenv("SMTP_SERVER", os.getenv("SMTP_HOST", "smtp.gmail.com"))
SMTP_PORT     = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", os.getenv("SMTP_USER"))
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
SMTP_FROM     = os.getenv("SMTP_FROM", SMTP_USERNAME or "no-reply@example.com")
SMTP_TO       = os.getenv("SMTP_TO", SMTP_FROM)

def send_email(subject: str, body: str) -> None:
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = SMTP_TO

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.starttls()
        if SMTP_USERNAME and SMTP_PASSWORD:
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.sendmail(SMTP_FROM, [SMTP_TO], msg.as_string())


# ----------------------------
# Selenium helpers
# ----------------------------
HUB_BASE = "https://hub.vertigogames.co"

def build_driver() -> webdriver.Chrome:
    chrome_opts = Options()
    chrome_opts.add_argument("--headless=new")
    chrome_opts.add_argument("--no-sandbox")
    chrome_opts.add_argument("--disable-dev-shm-usage")
    chrome_opts.add_argument("--window-size=1280,900")
    chrome_opts.add_argument("--disable-gpu")
    chrome_opts.add_argument("--disable-notifications")
    chrome_opts.add_argument("--lang=en-US")
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=chrome_opts)

def wait_clickable(driver, locator, timeout=15):
    return WebDriverWait(driver, timeout).until(EC.element_to_be_clickable(locator))

def safe_click(driver, by, value, timeout=4) -> bool:
    try:
        el = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((by, value)))
        driver.execute_script("arguments[0].click();", el)
        return True
    except Exception:
        return False

def accept_cookies_and_popups(driver) -> None:
    # Common cookie banners / popups
    candidates = [
        (By.XPATH, "//button[contains(.,'Accept') or contains(.,'I Agree') or contains(.,'Got it')]"),
        (By.XPATH, "//button[contains(.,'OK')]"),
        (By.XPATH, "//div[contains(@class,'close') or contains(@class,'Close') or contains(@aria-label,'close')]"),
        (By.XPATH, "//span[text()='×' or text()='✕']/ancestor::button"),
    ]
    end = time.time() + 8
    while time.time() < end:
        clicked_any = False
        for by, xp in candidates:
            if safe_click(driver, by, xp, timeout=1):
                clicked_any = True
                time.sleep(0.3)
        if not clicked_any:
            break

def login_with_player(driver, player_id: str) -> bool:
    """
    Mirrors your manual script:
    - open landing
    - enter player_id (if required)
    - handle cookie/popup
    - end result: authenticated session for that ID
    """
    driver.get(HUB_BASE)
    accept_cookies_and_popups(driver)

    # If your manual flow uses a dedicated login field, adapt here:
    # Try common patterns; if your site already auto-maps ID in a saved cookie,
    # this quickly returns True.
    try:
        # Locate an input for player/UID if present
        input_guess = [
            "//input[@type='text' and (contains(@placeholder,'Player') or contains(@placeholder,'UID') or contains(@name,'player') or contains(@name,'uid'))]",
            "//input[contains(@id,'player') or contains(@id,'uid')]",
        ]
        clicked = False
        for xp in input_guess:
            try:
                inp = WebDriverWait(driver, 3).until(EC.presence_of_element_located((By.XPATH, xp)))
                inp.clear()
                inp.send_keys(player_id)
                clicked = True
                break
            except Exception:
                pass

        # Submit/login button heuristic
        if clicked:
            btn_x = "//button[contains(.,'Login') or contains(.,'Sign in') or contains(.,'Continue')]"
            safe_click(driver, By.XPATH, btn_x, timeout=3)

        # Final wait for any signed-in UI cue
        WebDriverWait(driver, 8).until(
            EC.presence_of_element_located((By.XPATH, "//nav|//aside|//a[contains(@href,'/daily-rewards')]"))
        )
        accept_cookies_and_popups(driver)
        return True
    except Exception:
        # Even if no explicit login UI, you might already be signed-in via cookie
        accept_cookies_and_popups(driver)
        return True


def navigate_daily_rewards(driver) -> None:
    # left menu or direct route
    try:
        if safe_click(driver, By.XPATH, "//a[contains(@href,'/daily-rewards')]", timeout=4):
            pass
        else:
            driver.get(f"{HUB_BASE}/daily-rewards")
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, "//*[contains(.,'Daily Rewards') or contains(.,'DAY')]/ancestor::*[self::main or self::div]"))
        )
        accept_cookies_and_popups(driver)
    except Exception:
        driver.get(f"{HUB_BASE}/daily-rewards")
        time.sleep(2)
        accept_cookies_and_popups(driver)

def claim_one_daily(driver) -> bool:
    """
    Claims the single daily reward if available.
    Returns True if actually claimed now; False otherwise.
    """
    accept_cookies_and_popups(driver)
    # Green "Claim" button for the current day
    # Heuristic: visible button containing 'Claim'
    try:
        claim_btns = driver.find_elements(By.XPATH, "//button[not(@disabled) and contains(.,'Claim')]")
        for b in claim_btns:
            try:
                if b.is_displayed() and b.is_enabled():
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", b)
                    time.sleep(0.2)
                    driver.execute_script("arguments[0].click();", b)
                    time.sleep(1.0)
                    accept_cookies_and_popups(driver)
                    # If new badge/text "Claimed" appears, consider success
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False

def navigate_store_rewards(driver) -> None:
    # left menu or direct route to store
    try:
        if safe_click(driver, By.XPATH, "//a[contains(@href,'/store')]", timeout=4):
            pass
        else:
            driver.get(f"{HUB_BASE}/store")
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, "//*[contains(.,'Store') or contains(.,'Rewards')]/ancestor::*[self::main or self::div]"))
        )
        accept_cookies_and_popups(driver)
    except Exception:
        driver.get(f"{HUB_BASE}/store")
        time.sleep(2)
        accept_cookies_and_popups(driver)

def claim_store_rewards(driver, max_claims=3) -> int:
    """
    Claims up to 3 store rewards for current ID.
    Returns the count actually claimed now.
    """
    accept_cookies_and_popups(driver)
    claimed = 0
    end = time.time() + 20  # avoid infinite loop

    while claimed < max_claims and time.time() < end:
        # Try to locate a claim button for store items
        try:
            # prioritize visible claim buttons
            targets = driver.find_elements(By.XPATH, "//button[not(@disabled) and contains(.,'Claim')]")
            clicked_any = False
            for btn in targets:
                if claimed >= max_claims:
                    break
                try:
                    if btn.is_displayed() and btn.is_enabled():
                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                        time.sleep(0.2)
                        driver.execute_script("arguments[0].click();", btn)
                        time.sleep(1.2)
                        accept_cookies_and_popups(driver)
                        claimed += 1
                        clicked_any = True
                        # brief pause before next attempt
                        time.sleep(0.6)
                except Exception:
                    continue

            if not clicked_any:
                break
        except Exception:
            break

    return claimed

def read_players_csv(repo_root: str) -> List[str]:
    csv_path = os.path.join(repo_root, "players.csv")
    ids = []
    with open(csv_path, "r", newline="") as f:
        for row in f:
            row = row.strip()
            if row:
                ids.append(row)
    return ids


# ----------------------------
# Main
# ----------------------------
def main():
    repo_root = os.getcwd()
    players = read_players_csv(repo_root)

    grand_total = len(players)
    total_success_logins = 0

    total_daily_claims = 0
    total_store_claims = 0

    t0 = time.time()
    per_id_lines = []

    driver = build_driver()
    try:
        for idx, pid in enumerate(players, start=1):
            t_id_start = time.time()
            login_ok = False
            daily_ok = False
            store_count = 0

            try:
                login_ok = login_with_player(driver, pid)
                if login_ok:
                    total_success_logins += 1

                    # DAILY
                    navigate_daily_rewards(driver)
                    daily_ok = claim_one_daily(driver)
                    if daily_ok:
                        total_daily_claims += 1

                    # STORE (up to 3)
                    navigate_store_rewards(driver)
                    store_count = claim_store_rewards(driver, max_claims=3)
                    total_store_claims += store_count

            except Exception:
                # ignore per-ID failures; continue
                pass

            elapsed = time.time() - t_id_start
            per_id_lines.append(
                f"ID: {pid} | Login: {'Yes' if login_ok else 'No'} | Daily: {'1' if daily_ok else '0'} | Store: {store_count} | Time: {elapsed:.1f}s"
            )

            # Hard refresh between IDs to reduce cross-state issues
            try:
                driver.delete_all_cookies()
            except Exception:
                pass

        total_time = time.time() - t0
        avg_time = total_time / grand_total if grand_total else 0.0

        # ----------------------------
        # FINAL SUMMARY (Merged)
        # ----------------------------
        summary_lines = []
        summary_lines.append("=" * 60)
        summary_lines.append("HUB MERGED REWARDS SUMMARY")
        summary_lines.append("=" * 60)
        summary_lines.append(f"Total Players: {grand_total}")
        summary_lines.append(f"Successful Logins: {total_success_logins}")
        summary_lines.append(f"Daily Rewards Claimed: {total_daily_claims}")
        summary_lines.append(f"Store Rewards Claimed: {total_store_claims}")
        summary_lines.append(f"Total Rewards Claimed: {total_daily_claims + total_store_claims}")
        summary_lines.append(f"Total Time Taken: {total_time:.1f}s ({total_time/60.0:.1f} min)")
        summary_lines.append(f"Avg Time per ID: {avg_time:.1f}s")
        summary_lines.append("-")
        summary_lines.append("Per-ID details:")
        summary_lines.extend(per_id_lines)
        summary_lines.append("=" * 60)

        body = "Hello,\n\nHere is the latest automated Hub Merged Rewards execution summary.\n\n"
        body += "\n".join(summary_lines)
        body += "\n\nRegards,\nGitHub Automation Bot\n"

        subject = "Hub Merged Rewards Summary"
        send_email(subject, body)

    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
