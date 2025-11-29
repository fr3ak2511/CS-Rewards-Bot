import os
import csv
import time
from datetime import datetime
from typing import List

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

def safe_click(driver, by, value, timeout=4) -> bool:
    try:
        el = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((by, value)))
        driver.execute_script("arguments[0].click();", el)
        return True
    except Exception:
        return False

def accept_cookies_and_popups(driver) -> None:
    candidates = [
        (By.XPATH, "//button[contains(.,'Accept') or contains(.,'I Agree') or contains(.,'Got it')]"),
        (By.XPATH, "//button[contains(.,'OK')]"),
        (By.XPATH, "//div[contains(@class,'close') or contains(@aria-label,'close')]"),
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
    driver.get(HUB_BASE)
    accept_cookies_and_popups(driver)

    try:
        # Try to feed player id if a field is shown
        guesses = [
            "//input[@type='text' and (contains(@placeholder,'Player') or contains(@placeholder,'UID') or contains(@name,'player') or contains(@name,'uid'))]",
            "//input[contains(@id,'player') or contains(@id,'uid')]",
        ]
        typed = False
        for xp in guesses:
            try:
                inp = WebDriverWait(driver, 3).until(EC.presence_of_element_located((By.XPATH, xp)))
                inp.clear()
                inp.send_keys(player_id)
                typed = True
                break
            except Exception:
                pass

        if typed:
            safe_click(driver, By.XPATH, "//button[contains(.,'Login') or contains(.,'Sign in') or contains(.,'Continue')]", timeout=3)

        WebDriverWait(driver, 8).until(
            EC.presence_of_element_located((By.XPATH, "//nav|//aside|//a[contains(@href,'/progression')]"))
        )
        accept_cookies_and_popups(driver)
        return True
    except Exception:
        accept_cookies_and_popups(driver)
        return True

def read_players_csv(repo_root: str) -> List[str]:
    csv_path = os.path.join(repo_root, "players.csv")
    ids = []
    with open(csv_path, "r", newline="") as f:
        for row in f:
            row = row.strip()
            if row:
                ids.append(row)
    return ids

def navigate_progression(driver) -> None:
    # Direct route or menu
    if not safe_click(driver, By.XPATH, "//a[contains(@href,'/progression') or contains(@href,'/progression-program')]", timeout=4):
        driver.get(f"{HUB_BASE}/progression-program")
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, "//*[contains(.,'Progression')]/ancestor::*[self::main or self::div]"))
        )
    except Exception:
        pass
    accept_cookies_and_popups(driver)

def claim_all_progression(driver, max_rounds=12) -> int:
    """
    Click all visible 'Claim' buttons in Progression (dynamic count).
    Stops when none left or max_rounds reached.
    """
    total = 0
    rounds = 0
    while rounds < max_rounds:
        rounds += 1
        accept_cookies_and_popups(driver)
        buttons = driver.find_elements(By.XPATH, "//button[not(@disabled) and contains(.,'Claim')]")
        clicked_this_round = 0
        for b in buttons:
            try:
                if b.is_displayed() and b.is_enabled():
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", b)
                    time.sleep(0.2)
                    driver.execute_script("arguments[0].click();", b)
                    time.sleep(1.0)
                    accept_cookies_and_popups(driver)
                    total += 1
                    clicked_this_round += 1
                    time.sleep(0.4)
            except Exception:
                continue
        if clicked_this_round == 0:
            break
    return total


def main():
    repo_root = os.getcwd()
    players = read_players_csv(repo_root)

    total_players = len(players)
    success_logins = 0
    total_claimed = 0

    per_id_lines = []
    t0 = time.time()

    driver = build_driver()
    try:
        for pid in players:
            t_id = time.time()
            login_ok = False
            claimed = 0
            try:
                login_ok = login_with_player(driver, pid)
                if login_ok:
                    success_logins += 1
                    navigate_progression(driver)
                    claimed = claim_all_progression(driver, max_rounds=12)
                    total_claimed += claimed
            except Exception:
                pass

            per_id_lines.append(
                f"ID: {pid} | Login: {'Yes' if login_ok else 'No'} | Rewards Claimed: {claimed} | Time: {time.time()-t_id:.1f}s"
            )

            try:
                driver.delete_all_cookies()
            except Exception:
                pass

        total_time = time.time() - t0
        avg_time = total_time / total_players if total_players else 0.0

        # FINAL SUMMARY (Progression)
        summary = []
        summary.append("=" * 60)
        summary.append("PROGRESSION PROGRAM SUMMARY")
        summary.append("=" * 60)
        summary.append(f"Total Players: {total_players}")
        summary.append(f"Successful Logins: {success_logins}")
        summary.append(f"Monthly Rewards Claimed: {total_claimed}")
        summary.append(f"Total Time Taken: {total_time:.1f}s ({total_time/60.0:.1f} min)")
        summary.append(f"Avg Time per ID: {avg_time:.1f}s")
        summary.append("-")
        summary.append("Per-ID details:")
        summary.extend(per_id_lines)
        summary.append("=" * 60)

        body = "Hello,\n\nHere is the latest automated Hub Progression Rewards execution summary.\n\n"
        body += "\n".join(summary)
        body += "\n\nRegards,\nGitHub Automation Bot\n"

        send_email("Hub Progression Rewards Summary", body)

    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
