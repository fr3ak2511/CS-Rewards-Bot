#!/usr/bin/env python3
# ==============================================================
# GitHub Actions runner: Merged (Daily + Store) rewards
# Mirrors the manual "hub_merged_rewards.py" flow end-to-end.
# ==============================================================

import os
import sys
import csv
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager


# ----------------------- Logging ---------------------------------
print_lock = threading.Lock()
def log(msg: str):
    with print_lock:
        ts = datetime.utcnow().strftime("%H:%M:%S")
        print(f"[{ts}] {msg}", flush=True)

def screenshot(driver, name: str):
    try:
        os.makedirs("screenshots", exist_ok=True)
        path = os.path.join("screenshots", name)
        driver.save_screenshot(path)
        log(f"📸 Saved screenshot: {path}")
    except Exception:
        pass


# ----------------------- Driver ----------------------------------
def create_driver():
    try:
        log("Creating Chrome driver...")

        opts = Options()
        # Critical: run headful unless HEADLESS=true
        headless = os.getenv("HEADLESS", "false").lower() == "true"
        if headless:
            opts.add_argument("--headless=new")

        # Match your manual session “feel” (fingerprint & stability)
        opts.add_argument("--incognito")
        opts.add_argument("--start-maximized")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_argument("--disable-infobars")
        opts.add_argument("--disable-extensions")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-logging")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--disable-web-security")
        opts.add_argument("--disable-popup-blocking")
        opts.add_argument("--disable-notifications")
        opts.add_argument("--disable-default-apps")
        opts.add_argument("--disable-features=VizDisplayCompositor")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)

        # Optional UA override (secret)
        ua = os.getenv("BROWSER_UA")
        if ua:
            opts.add_argument(f"--user-agent={ua}")

        # Do NOT block images in CI (site visuals matter)
        # (Keep cookies/popups suppressed via logic below)

        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=opts)
        driver.set_page_load_timeout(25)
        driver.set_script_timeout(25)

        # Slight anti-bot tweak
        try:
            driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
            )
        except Exception:
            pass

        log("✓ Chrome driver created")
        return driver
    except Exception as e:
        log(f"✗ DRIVER CREATION ERROR: {e}")
        return None


# ------------------ Shared helpers (mirrors manual) --------------
def accept_cookies(driver, wait):
    try:
        btn = WebDriverWait(driver, 2).until(
            EC.element_to_be_clickable(
                (By.XPATH, "//button[normalize-space()='Accept All' or contains(text(),'Accept') or contains(text(),'Allow') or contains(text(),'Consent')]")
            )
        )
        btn.click()
        time.sleep(0.5)
        log("Cookies accepted")
    except Exception:
        pass

def click_element_or_coords(driver, wait, locator, fallback_coords=None, timeout=8):
    try:
        elm = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable(locator))
        elm.click()
        time.sleep(0.3)
        return True
    except Exception:
        if fallback_coords:
            try:
                actions = ActionChains(driver)
                actions.move_by_offset(fallback_coords[0], fallback_coords[1]).click().perform()
                actions.move_by_offset(-fallback_coords[0], -fallback_coords[1]).perform()
                time.sleep(0.3)
                return True
            except Exception:
                pass
        return False

def wait_for_login_complete(driver, max_wait=12):
    start = time.time()
    while time.time() - start < max_wait:
        try:
            url = driver.current_url.lower()
            if any(k in url for k in ["user", "dashboard", "daily-rewards", "store"]):
                return True
            if driver.find_elements(By.XPATH, "//button[contains(text(),'Logout') or contains(text(),'Profile') or contains(@class,'user')]"):
                return True
            time.sleep(0.25)
        except Exception:
            time.sleep(0.25)
    return True  # best-effort, align with manual behavior

def ensure_daily_rewards_page(driver):
    if "daily-rewards" not in driver.current_url.lower():
        log(f"WARNING: Not on daily-rewards; navigating…")
        driver.get("https://hub.vertigogames.co/daily-rewards")
        time.sleep(2)
        return True
    return False

def ensure_store_page(driver):
    if "store" not in driver.current_url.lower():
        log(f"WARNING: Not on store; navigating…")
        driver.get("https://hub.vertigogames.co/store")
        time.sleep(2)
        return True
    return False

def close_popups_safe(driver):
    try:
        popup_selectors = [
            "//div[contains(@class,'modal') and not(contains(@style,'display: none'))]",
            "//div[contains(@class,'popup') and not(contains(@style,'display: none'))]",
            "//div[@data-testid='item-popup-content']",
            "//div[contains(@class,'dialog') and not(contains(@style,'display: none'))]",
        ]
        # Try “Close/×”
        close_selectors = [
            "//button[contains(@class,'close') or contains(@aria-label,'Close')]",
            "//button[text()='×' or text()='X' or text()='✕']",
            "//*[@data-testid='close-button']",
            "//*[contains(@class,'icon-close')]",
        ]
        # If any popup present, click its close
        visible = False
        for s in popup_selectors:
            try:
                els = driver.find_elements(By.XPATH, s)
                if any(e.is_displayed() for e in els):
                    visible = True
                    break
            except Exception:
                pass
        if not visible:
            return False

        for s in close_selectors:
            try:
                el = driver.find_element(By.XPATH, s)
                if el.is_displayed():
                    try:
                        el.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", el)
                    time.sleep(1)
                    return True
            except Exception:
                continue

        # Safe area clicks (outside)
        try:
            size = driver.get_window_size()
            w, h = size["width"], size["height"]
            safe_pts = [(30,30),(w-50,30),(30,h-50),(w-50,h-50),(w//4,30),(3*w//4,30)]
            for (x,y) in safe_pts:
                actions = ActionChains(driver)
                actions.move_by_offset(x - w//2, y - h//2).click().perform()
                actions.move_by_offset(-(x - w//2), -(y - h//2)).perform()
                time.sleep(0.8)
                still = False
                for s in popup_selectors:
                    try:
                        els = driver.find_elements(By.XPATH, s)
                        if any(e.is_displayed() for e in els):
                            still = True
                            break
                    except Exception:
                        pass
                if not still:
                    return True
        except Exception:
            pass

        # ESC fallback
        try:
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            time.sleep(0.5)
            return True
        except Exception:
            pass
    except Exception:
        pass
    return False


# ------------------ DAILY page claim logic -----------------------
def get_claim_buttons_daily(driver):
    log("Scanning Daily Rewards page for claim buttons…")
    buttons = []
    try:
        # Broad scan, then filter like manual
        all_btns = driver.find_elements(By.TAG_NAME, "button")
        for b in all_btns:
            try:
                t = (b.text or "").strip()
                if t and "claim" in t.lower():
                    if b.is_displayed() and b.is_enabled():
                        if any(w in t.lower() for w in ["buy", "purchase", "payment", "pay", "$"]):
                            continue
                        buttons.append(b)
            except Exception:
                continue
    except Exception:
        pass

    if buttons:
        return buttons

    # XPath fallback
    xpaths = [
        "//button[normalize-space()='Claim']",
        "//button[contains(text(), 'Claim') and not(contains(text(), 'Buy'))]",
        "//*[contains(text(),'Claim') and (self::button or self::a)]",
    ]
    for xp in xpaths:
        try:
            for b in driver.find_elements(By.XPATH, xp):
                if b.is_displayed() and b.is_enabled() and b not in buttons:
                    buttons.append(b)
        except Exception:
            continue
    return buttons

def claim_daily_rewards_page(driver):
    claimed = 0
    time.sleep(1.5)
    ensure_daily_rewards_page(driver)
    close_popups_safe(driver)
    time.sleep(1)

    btns = get_claim_buttons_daily(driver)
    if not btns:
        log("No Daily claim buttons found; double-checking…")
        time.sleep(1.5)
        ensure_daily_rewards_page(driver)
        close_popups_safe(driver)
        btns = get_claim_buttons_daily(driver)
        if not btns:
            log("Daily page: no claimable rewards.")
            return 0

    log(f"Daily page: found {len(btns)} claimable buttons")
    for idx, b in enumerate(btns):
        try:
            if ensure_daily_rewards_page(driver):
                btns2 = get_claim_buttons_daily(driver)
                if idx < len(btns2):
                    b = btns2[idx]
                else:
                    continue

            close_popups_safe(driver)
            driver.execute_script("arguments[0].scrollIntoView({behavior:'smooth', block:'center'});", b)
            time.sleep(0.5)

            clicked = False
            try:
                b.click(); clicked=True; log(f"Clicked Daily #{idx+1} (native)")
            except Exception:
                try:
                    driver.execute_script("arguments[0].click();", b); clicked=True; log(f"Clicked Daily #{idx+1} (JS)")
                except Exception:
                    try:
                        ActionChains(driver).move_to_element(b).click().perform(); clicked=True; log(f"Clicked Daily #{idx+1} (Actions)")
                    except Exception:
                        pass

            if clicked:
                claimed += 1
                log(f"DAILY REWARD {claimed} CLAIMED")
                time.sleep(2)
                ensure_daily_rewards_page(driver)
                close_popups_safe(driver)
            else:
                log(f"Daily button {idx+1}: all click methods failed")
        except Exception as e:
            log(f"Daily button {idx+1}: error {e}")
            continue

    log(f"Daily page: claimed {claimed}")
    return claimed


# ------------------ STORE page claim logic -----------------------
def click_daily_rewards_tab(driver):
    selectors = [
        "//div[contains(@class,'tab')]//span[contains(text(),'Daily Rewards')]",
        "//button[contains(@class,'tab')][contains(text(),'Daily Rewards')]",
        "//*[text()='Daily Rewards' and (contains(@class,'tab') or parent::*[contains(@class,'tab')])]",
        "//div[contains(@class,'Tab')]//div[contains(text(),'Daily Rewards')]",
        "//a[contains(@class,'tab')][contains(text(),'Daily Rewards')]",
    ]
    for xp in selectors:
        try:
            els = driver.find_elements(By.XPATH, xp)
            for el in els:
                if el.is_displayed():
                    try:
                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                        time.sleep(0.5)
                        el.click(); time.sleep(1.5); return True
                    except Exception:
                        try:
                            driver.execute_script("arguments[0].click();", el)
                            time.sleep(1.5); return True
                        except Exception:
                            continue
        except Exception:
            continue
    return False

def navigate_to_daily_rewards_section_store(driver):
    log("Navigating to Store → Daily Rewards section…")
    ensure_store_page(driver)
    close_popups_safe(driver)
    time.sleep(0.5)

    if click_daily_rewards_tab(driver):
        time.sleep(1.5)
        return True

    # Scroll fallback
    try:
        driver.execute_script("window.scrollTo(0, 0);"); time.sleep(0.5)
        for _ in range(4):
            driver.execute_script("window.scrollBy(0, 400);"); time.sleep(1)
            els = driver.find_elements(By.XPATH, "//*[contains(text(),'Daily Reward') and not(self::a) and not(self::button)]")
            if els:
                for e in els:
                    try:
                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", e)
                        time.sleep(1.5)
                        return True
                    except Exception:
                        continue
    except Exception:
        pass
    return False

def get_claim_buttons_store(driver):
    buttons = []
    xps = [
        "//button[normalize-space()='Claim']",
        "//button[contains(text(),'Claim') and not(contains(text(),'Buy')) and not(contains(text(),'Purchase'))]",
        "//div[contains(@class,'reward')]//button[contains(text(),'Claim')]",
    ]
    for xp in xps:
        try:
            for b in driver.find_elements(By.XPATH, xp):
                if b.is_displayed() and b not in buttons:
                    t = (b.text or "").strip().lower()
                    if any(w in t for w in ["buy","purchase","payment","pay","$"]):
                        continue
                    buttons.append(b)
        except Exception:
            continue
    return buttons

def close_store_popup_after_claim(driver):
    try:
        time.sleep(1)
        popup_selectors = [
            "//div[contains(@class,'modal') and not(contains(@style,'display: none'))]",
            "//div[contains(@class,'popup') and not(contains(@style,'display: none'))]",
            "//div[@data-testid='item-popup-content']",
            "//div[contains(@class,'dialog') and not(contains(@style,'display: none'))]",
        ]
        # Continue button
        cont_xps = [
            "//button[normalize-space()='Continue']",
            "//button[contains(text(),'Continue')]",
            "//button[contains(@class,'continue')]",
            "//*[contains(text(),'Continue') and (self::button or self::a)]",
        ]
        # If popup present
        visible = False
        for s in popup_selectors:
            try:
                if any(e.is_displayed() for e in driver.find_elements(By.XPATH, s)):
                    visible = True; break
            except Exception:
                pass
        if not visible:
            return True

        for xp in cont_xps:
            try:
                btn = driver.find_element(By.XPATH, xp)
                if btn.is_displayed() and btn.is_enabled():
                    try:
                        btn.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", btn)
                    time.sleep(1)
                    # verify closed
                    still = False
                    for s in popup_selectors:
                        try:
                            if any(e.is_displayed() for e in driver.find_elements(By.XPATH, s)):
                                still = True; break
                        except Exception:
                            pass
                    if not still:
                        return True
                    break
            except Exception:
                continue

        # Fallback to generic close
        if close_popups_safe(driver):
            return True
    except Exception:
        pass
    return False

def claim_store_daily_rewards(driver):
    claimed = 0
    max_rounds = 5

    if not navigate_to_daily_rewards_section_store(driver):
        log("Store: failed to reach Daily Rewards section.")
        return 0

    for round_i in range(max_rounds):
        log(f"Store claim round {round_i+1}…")
        ensure_store_page(driver)
        close_popups_safe(driver)

        if round_i > 0:
            if not navigate_to_daily_rewards_section_store(driver):
                log("Store: re-navigation failed; continuing.")
                continue

        time.sleep(1.5)
        btns = get_claim_buttons_store(driver)
        if not btns:
            log("Store: no claim buttons; double-checking…")
            time.sleep(1.5)
            btns = get_claim_buttons_store(driver)
            if not btns:
                log("Store: no more claims.")
                break

        claimed_this_round = False
        for idx, b in enumerate(btns):
            try:
                t = (b.text or "").strip()
                log(f"Attempting Store claim btn {idx+1}: '{t}'")

                if b.is_enabled():
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", b)
                    time.sleep(0.5)
                    close_popups_safe(driver)

                    clicked = False
                    try:
                        b.click(); clicked=True; log("Store clicked (native)")
                    except Exception:
                        try:
                            driver.execute_script("arguments[0].click();", b); clicked=True; log("Store clicked (JS)")
                        except Exception:
                            pass

                    if clicked:
                        claimed += 1
                        claimed_this_round = True
                        log(f"STORE REWARD {claimed} CLAIMED")
                        time.sleep(1.3)

                        close_store_popup_after_claim(driver)
                        ensure_store_page(driver)
                        close_popups_safe(driver)
                        break
                    else:
                        log("Store claim: all click methods failed")
                else:
                    log("Store claim: button not enabled")
            except Exception as e:
                log(f"Store claim error: {e}")
                continue

        if not claimed_this_round:
            log("No buttons claimed in this round.")
            break

        if claimed >= 3:
            log("All 3 Store daily rewards claimed.")
            break

    log(f"Store: total claimed {claimed}")
    return claimed


# ------------------ Player flow ----------------------------------
def login_with_player(driver, player_id, thread_id):
    try:
        log(f"[Thread-{thread_id}] Logging in with ID {player_id}")
        driver.get("https://hub.vertigogames.co/daily-rewards")
        time.sleep(1.5)
        accept_cookies(driver, WebDriverWait(driver, 10))

        login_selectors = [
            "//button[contains(text(),'Login') or contains(text(),'Log in') or contains(text(),'Sign in')]",
            "//a[contains(text(),'Login') or contains(text(),'Log in') or contains(text(),'Sign in')]",
            "//button[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'login')]",
            "//button[contains(text(),'claim')]",
            "//div[contains(text(),'Daily Rewards') or contains(text(),'daily')]//button",
            "//button[contains(@class,'btn') or contains(@class,'button')]",
            "//*[contains(text(),'Login') or contains(text(),'login')][@onclick or @href or self::button or self::a]",
        ]

        clicked = False
        for sel in login_selectors:
            try:
                els = driver.find_elements(By.XPATH, sel)
                for el in els:
                    if el.is_displayed() and el.is_enabled():
                        el.click()
                        clicked = True
                        break
                if clicked:
                    break
            except Exception:
                continue

        if not clicked:
            log(f"[Thread-{thread_id}] No login trigger found.")
            screenshot(driver, f"{player_id}_no_login_button.png")
            return False

        # input
        input_selectors = [
            "#user-id-input",
            "//input[contains(@placeholder,'ID') or contains(@placeholder,'User') or contains(@name,'user') or contains(@placeholder,'id')]",
            "//input[@type='text']",
            "//input[contains(@class,'input')]",
            "//div[contains(@class,'modal') or contains(@class,'dialog')]//input[@type='text']",
        ]
        box = None
        for s in input_selectors:
            try:
                if s.startswith("#"):
                    box = WebDriverWait(driver, 3).until(EC.visibility_of_element_located((By.ID, s[1:])))
                else:
                    box = WebDriverWait(driver, 3).until(EC.visibility_of_element_located((By.XPATH, s)))
                box.clear(); box.send_keys(player_id); time.sleep(0.1)
                break
            except Exception:
                continue

        if not box:
            log(f"[Thread-{thread_id}] No input field for login.")
            screenshot(driver, f"{player_id}_no_input.png")
            return False

        # submit
        ctas = [
            "//button[contains(text(),'Login') or contains(text(),'Log in') or contains(text(),'Sign in')]",
            "//button[@type='submit']",
            "//div[contains(@class,'modal') or contains(@class,'dialog')]//button[not(contains(text(),'Cancel')) and not(contains(text(),'Close'))]",
            "//button[contains(@class,'primary') or contains(@class,'submit')]",
        ]
        submitted = False
        for xp in ctas:
            try:
                if click_element_or_coords(driver, WebDriverWait(driver, 10), (By.XPATH, xp), None, timeout=2):
                    submitted = True
                    break
            except Exception:
                continue
        if not submitted:
            try:
                box.send_keys(Keys.ENTER); time.sleep(0.3); submitted = True
            except Exception:
                pass

        wait_for_login_complete(driver, max_wait=12)
        time.sleep(2)
        log(f"[Thread-{thread_id}] Login completed")
        return True
    except TimeoutException:
        log(f"[Thread-{thread_id}] Login timeout")
        return False
    except Exception as e:
        log(f"[Thread-{thread_id}] Login error: {e}")
        return False

def process_player(player_id, thread_id):
    driver = create_driver()
    if not driver:
        return {"player_id": player_id, "daily": 0, "store": 0, "status": "driver_failed", "login_successful": False}

    login_successful = False
    try:
        if not login_with_player(driver, player_id, thread_id):
            return {"player_id": player_id, "daily": 0, "store": 0, "status": "login_failed", "login_successful": False}

        login_successful = True

        # Step 1: Daily page
        ensure_daily_rewards_page(driver)
        close_popups_safe(driver)
        time.sleep(1.5)
        daily_claimed = claim_daily_rewards_page(driver)

        # Step 2: Store → Daily Rewards section
        driver.get("https://hub.vertigogames.co/store")
        time.sleep(2)
        ensure_store_page(driver)
        close_popups_safe(driver)
        time.sleep(1.5)
        store_claimed = claim_store_daily_rewards(driver)

        total = daily_claimed + store_claimed
        status = "success" if total > 0 else "no_claims"
        return {"player_id": player_id, "daily": daily_claimed, "store": store_claimed, "status": status, "login_successful": True}
    finally:
        try:
            driver.quit()
        except Exception:
            pass


# ------------------ Main (players.csv from repo root) ------------
def main():
    csv_path = "players.csv"
    if not os.path.exists(csv_path):
        log("✗ players.csv missing at repo root.")
        sys.exit(1)

    with open(csv_path, newline="") as f:
        players = [row.strip() for row in f if row.strip()]

    log(f"Loaded {len(players)} player IDs")

    results = []
    start = time.time()
    # Keep max_workers small to avoid site throttling; mirrors manual batching
    with ThreadPoolExecutor(max_workers=2) as ex:
        futs = {ex.submit(process_player, pid, i+1): pid for i, pid in enumerate(players)}
        for fut in as_completed(futs):
            res = fut.result()
            results.append(res)
            log(str(res))

    total_time = time.time() - start
    successful_logins = sum(1 for r in results if r.get("login_successful"))
    total_daily = sum(r.get("daily", 0) for r in results)
    total_store = sum(r.get("store", 0) for r in results)
    total_claims = total_daily + total_store

    # Final summary (matches your manual format)
    log("\n" + "="*70)
    log("MERGED MODULE - FINAL SUMMARY")
    log("="*70)
    log(f"Total players processed: {len(results)}")
    log(f"Successful Logins: {successful_logins}")
    log(f"Daily Rewards Claimed: {total_daily}")
    log(f"Store Rewards Claimed: {total_store}")
    log(f"Total Rewards Claimed: {total_claims}")
    log(f"Total Time: {total_time:.1f}s")
    log("="*70)

if __name__ == "__main__":
    main()
