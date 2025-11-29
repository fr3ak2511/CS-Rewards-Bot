import csv
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.desired_capabilities import DesiredCapabilities
import re

# Thread-safe printing
print_lock = threading.Lock()


def thread_safe_print(message):
    with print_lock:
        print(message)


def create_driver():
    options = Options()

    # Existing flags
    options.add_argument("--incognito")
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-logging")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-web-security")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-default-apps")
    options.add_argument("--disable-features=VizDisplayCompositor")

    # Headless + reduced background throttling
    options.add_argument("--headless=new")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-background-timer-throttling")
    options.add_argument("--disable-renderer-backgrounding")
    options.add_argument("--disable-backgrounding-occluded-windows")

    # Reduce page weight (disable images as well)
    prefs = {
        "profile.default_content_setting_values": {
            "images": 2,         # block images
            "notifications": 2,
            "popups": 2,
        },
        "profile.default_content_settings.popups": 0,
        "profile.managed_default_content_settings.popups": 0,
    }
    options.add_experimental_option("prefs", prefs)

    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    # Faster page load strategy (Selenium 4 way – set on options)
    caps = DesiredCapabilities.CHROME.copy()
    caps["pageLoadStrategy"] = "eager"
    for k, v in caps.items():
        options.set_capability(k, v)

    from webdriver_manager.chrome import ChromeDriverManager
service = Service(ChromeDriverManager().install())
driver = webdriver.Chrome(service=service, options=options)

    driver.set_page_load_timeout(20)
    driver.set_script_timeout(20)
    return driver


def accept_cookies(driver, wait):
    try:
        btn = WebDriverWait(driver, 2).until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "//button[normalize-space()='Accept All' or contains(text(), 'Accept') or "
                    "contains(text(), 'Allow') or contains(text(), 'Consent')]",
                )
            )
        )
        btn.click()
        time.sleep(0.3)
        thread_safe_print("Cookies accepted")
    except TimeoutException:
        thread_safe_print("No cookies popup found - continuing")
        pass


def click_element_or_coords(driver, wait, locator, fallback_coords=None, description="", timeout=8):
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


def wait_for_login_complete(driver, wait, max_wait=8):
    start_time = time.time()
    while time.time() - start_time < max_wait:
        try:
            current_url = driver.current_url
            if (
                "user" in current_url.lower()
                or "dashboard" in current_url.lower()
                or "daily-rewards" in current_url.lower()
            ):
                return True
            user_elements = driver.find_elements(
                By.XPATH,
                "//button[contains(text(),'Logout') or contains(text(),'Profile') or contains(@class,'user')]",
            )
            if user_elements:
                return True
            time.sleep(0.2)
        except Exception:
            time.sleep(0.2)
    return True


def ensure_daily_rewards_page(driver):
    current_url = driver.current_url
    if "daily-rewards" not in current_url.lower():
        thread_safe_print(f"WARNING: Not on daily-rewards page ({current_url}), navigating back...")
        driver.get("https://hub.vertigogames.co/daily-rewards")
        time.sleep(0.7)
        return True
    return False


def ensure_store_page(driver):
    current_url = driver.current_url
    if "store" not in current_url.lower():
        thread_safe_print(f"WARNING: Not on store page ({current_url}), navigating back...")
        driver.get("https://hub.vertigogames.co/store")
        time.sleep(0.7)
        return True
    return False


def close_popups_safe(driver):
    """Enhanced popup closing for Daily Rewards promotional popups"""
    try:
        popup_selectors = [
            "//div[contains(@class, 'modal') and not(contains(@style, 'display: none'))]",
            "//div[contains(@class, 'popup') and not(contains(@style, 'display: none'))]",
            "//div[@data-testid='item-popup-content']",
            "//div[contains(@class, 'dialog') and not(contains(@style, 'display: none'))]",
        ]

        popup_found = False
        for selector in popup_selectors:
            try:
                popup_elements = driver.find_elements(By.XPATH, selector)
                visible_popups = [elem for elem in popup_elements if elem.is_displayed()]
                if visible_popups:
                    popup_found = True
                    thread_safe_print("Promotional popup detected, attempting to close...")
                    break
            except Exception:
                continue

        if popup_found:
            # Try clicking close button first
            close_selectors = [
                "//button[contains(@class, 'close')]",
                "//button[contains(@aria-label, 'Close')]",
                "//*[contains(@class, 'close') and (self::button or self::span or self::div[@role='button'])]",
                "//button[text()='×' or text()='X' or text()='✕']",
                "//*[@data-testid='close-button']",
                "//*[contains(@class, 'icon-close')]",
            ]

            close_clicked = False
            for selector in close_selectors:
                try:
                    close_btn = driver.find_element(By.XPATH, selector)
                    if close_btn.is_displayed():
                        try:
                            close_btn.click()
                            thread_safe_print("Close button clicked successfully")
                            time.sleep(0.5)
                            close_clicked = True
                            break
                        except Exception:
                            driver.execute_script("arguments[0].click();", close_btn)
                            thread_safe_print("Close button clicked via JavaScript")
                            time.sleep(0.5)
                            close_clicked = True
                            break
                except Exception:
                    continue

            # Check if popup closed
            popup_still_visible = False
            for selector in popup_selectors:
                try:
                    popup_elements = driver.find_elements(By.XPATH, selector)
                    if any(elem.is_displayed() for elem in popup_elements):
                        popup_still_visible = True
                        break
                except Exception:
                    continue

            if popup_still_visible:
                thread_safe_print("Popup still visible, trying safe area clicks...")
                window_size = driver.get_window_size()
                width = window_size["width"]
                height = window_size["height"]
                # Click safe areas OUTSIDE the popup (corners and edges)
                safe_areas = [
                    (30, 30),  # Top-left corner
                    (width - 50, 30),  # Top-right corner
                    (30, height - 50),  # Bottom-left corner
                    (width - 50, height - 50),  # Bottom-right corner
                    (50, 50),
                    (width - 100, 50),
                ]

                for i, (x, y) in enumerate(safe_areas):
                    try:
                        actions = ActionChains(driver)
                        actions.move_by_offset(x - width // 2, y - height // 2).click().perform()
                        actions.move_by_offset(-(x - width // 2), -(y - height //2)).perform()
                        time.sleep(0.5)
                        # Check if popup closed
                        popup_still_visible = False
                        for selector in popup_selectors:
                            try:
                                popup_elements = driver.find_elements(By.XPATH, selector)
                                if any(elem.is_displayed() for elem in popup_elements):
                                    popup_still_visible = True
                                    break
                            except Exception:
                                continue
                        if not popup_still_visible:
                            thread_safe_print(f"Popup closed by safe area click {i+1}")
                            return True
                    except Exception:
                        continue
            else:
                thread_safe_print("Popup closed successfully")
                return True

        # Final attempt: ESC key
        try:
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            time.sleep(0.3)
            thread_safe_print("ESC key pressed")
            return True
        except Exception:
            pass

    except Exception:
        pass
    return False


def close_store_popup_after_claim(driver):
    try:
        thread_safe_print("Checking for popup after Store claim...")
        time.sleep(0.5)
        popup_selectors = [
            "//div[contains(@class, 'modal') and not(contains(@style, 'display: none'))]",
            "//div[contains(@class, 'popup') and not(contains(@style, 'display: none'))]",
            "//div[@data-testid='item-popup-content']",
            "//div[contains(@class, 'dialog') and not(contains(@style, 'display: none'))]",
        ]

        popup_found = False
        for selector in popup_selectors:
            try:
                popup_elements = driver.find_elements(By.XPATH, selector)
                visible_popups = [elem for elem in popup_elements if elem.is_displayed()]
                if visible_popups:
                    popup_found = True
                    thread_safe_print(f"Popup detected with selector: {selector}")
                    break
            except Exception:
                continue

        if not popup_found:
            thread_safe_print("No popup detected after claim")
            return True

        # Method 1: Try clicking Continue button
        thread_safe_print("Method 1: Attempting to click 'Continue' button...")
        continue_selectors = [
            "//button[normalize-space()='Continue']",
            "//button[contains(text(), 'Continue')]",
            "//button[contains(@class, 'continue')]",
            "//*[contains(text(), 'Continue') and (self::button or self::a)]",
        ]

        for selector in continue_selectors:
            try:
                continue_btn = driver.find_element(By.XPATH, selector)
                if continue_btn.is_displayed() and continue_btn.is_enabled():
                    try:
                        continue_btn.click()
                        thread_safe_print("'Continue' button clicked successfully")
                        time.sleep(0.5)
                    except Exception:
                        driver.execute_script("arguments[0].click();", continue_btn)
                        thread_safe_print("'Continue' button clicked via JavaScript")
                        time.sleep(0.5)
                    popup_still_visible = False
                    for ps in popup_selectors:
                        try:
                            popup_elements = driver.find_elements(By.XPATH, ps)
                            if any(elem.is_displayed() for elem in popup_elements):
                                popup_still_visible = True
                                break
                        except Exception:
                            continue
                    if not popup_still_visible:
                        thread_safe_print("Popup closed successfully via Continue button")
                        return True
                    else:
                        thread_safe_print("Popup still visible after Continue button")
                        break
            except Exception:
                continue

        # Method 2: Try clicking close/cross button
        thread_safe_print("Method 2: Attempting to click close/cross button...")
        close_selectors = [
            "//button[contains(@class, 'close')]",
            "//button[contains(@aria-label, 'Close')]",
            "//*[contains(@class, 'close') and (self::button or self::span or self::div[@role='button'])]",
            "//button[text()='×' or text()='X' or text()='✕']",
            "//*[@data-testid='close-button']",
            "//button[contains(@class, 'modal')]//span[contains(@class, 'close')]",
            "//*[contains(@class, 'icon-close')]",
        ]

        for selector in close_selectors:
            try:
                close_btn = driver.find_element(By.XPATH, selector)
                if close_btn.is_displayed():
                    try:
                        close_btn.click()
                        thread_safe_print("Close button clicked successfully")
                        time.sleep(0.5)
                    except Exception:
                        driver.execute_script("arguments[0].click();", close_btn)
                        thread_safe_print("Close button clicked via JavaScript")
                        time.sleep(0.5)
                    popup_still_visible = False
                    for ps in popup_selectors:
                        try:
                            popup_elements = driver.find_elements(By.XPATH, ps)
                            if any(elem.is_displayed() for elem in popup_elements):
                                popup_still_visible = True
                                break
                        except Exception:
                            continue
                    if not popup_still_visible:
                        thread_safe_print("Popup closed successfully via close button")
                        return True
                    else:
                        thread_safe_print("Popup still visible after close button")
                        break
            except Exception:
                continue

        # Method 3: Safe click anywhere on page to dismiss
        thread_safe_print("Method 3: Attempting safe-click to dismiss popup...")
        window_size = driver.get_window_size()
        width = window_size["width"]
        height = window_size["height"]
        safe_click_areas = [
            (30, 30),
            (width - 50, 30),
            (30, height - 50),
            (width - 50, height - 50),
            (width // 4, 30),
            (3 * width // 4, 30),
        ]

        for i, (x, y) in enumerate(safe_click_areas):
            try:
                actions = ActionChains(driver)
                actions.move_by_offset(x - width // 2, y - height // 2).click().perform()
                actions.move_by_offset(-(x - width // 2), -(y - height // 2)).perform()
                thread_safe_print(f"Safe-clicked area {i+1} at coordinates ({x}, {y})")
                time.sleep(0.5)
                popup_still_visible = False
                for ps in popup_selectors:
                    try:
                        popup_elements = driver.find_elements(By.XPATH, ps)
                        if any(elem.is_displayed() for elem in popup_elements):
                            popup_still_visible = True
                            break
                    except Exception:
                        continue
                if not popup_still_visible:
                    thread_safe_print(f"Popup closed successfully via safe-click at area {i+1}")
                    return True
            except Exception:
                continue

        # Final attempt: ESC key
        thread_safe_print("Final attempt: Pressing ESC key...")
        try:
            driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            time.sleep(0.5)
            thread_safe_print("ESC key pressed")
            return True
        except Exception:
            pass

        thread_safe_print("WARNING: Could not close popup with any method")
        return False

    except Exception as e:
        thread_safe_print(f"Exception in close_store_popup_after_claim: {e}")
        return False


def get_store_claim_timers(driver):
    """Captures ONLY 3rd CTA timer, and only if <= 1 hour"""
    timers = []
    try:
        thread_safe_print("Capturing timer information from Store CTAs (3rd CTA only)...")
        # Find all reward containers
        all_claim_sections = []
        try:
            reward_containers = driver.find_elements(
                By.XPATH,
                "//div[contains(@class, 'reward') or contains(@class, 'card') or contains(@class, 'item')]",
            )
            for container in reward_containers:
                try:
                    if container.is_displayed():
                        container_text = container.text
                        if (
                            "Claim" in container_text
                            or "Daily" in container_text
                            or "Store Bonus" in container_text
                        ):
                            all_claim_sections.append(container)
                except Exception:
                    continue
        except Exception:
            pass

        # Extract timer from each section - looking for valid time patterns ONLY
        for section in all_claim_sections:
            try:
                section_text = section.text
                # Strict pattern: only capture "XXh XXm" format (numbers only)
                time_pattern = r"(?:Next in\s+)?(\d+h\s+\d+m)"
                match = re.search(time_pattern, section_text)
                if match:
                    time_str = match.group(1).strip()
                    if time_str not in timers:
                        timers.append(time_str)
            except Exception:
                continue

        # Ensure we have exactly 3 entries
        while len(timers) < 3:
            timers.append("Available")
        if len(timers) > 3:
            timers = timers[:3]

        thread_safe_print(f"Captured all timers: {', '.join(timers)}")

        # Now check ONLY the 3rd CTA timer
        third_cta_timer = timers[2] if len(timers) >= 3 else "N/A"

        # Check if 3rd CTA is <= 1 hour
        if third_cta_timer != "Available" and third_cta_timer != "N/A":
            # Parse the time
            time_match = re.match(r"(\d+)h\s+(\d+)m", third_cta_timer)
            if time_match:
                hours = int(time_match.group(1))
                minutes = int(time_match.group(2))
                # Check if <= 1 hour (60 minutes)
                total_minutes = (hours * 60) + minutes
                if total_minutes <= 60:
                    thread_safe_print(f"3rd CTA timer is <= 1 hour: {third_cta_timer}")
                    return third_cta_timer
                else:
                    thread_safe_print(
                        f"3rd CTA timer is > 1 hour: {third_cta_timer} - Not reporting"
                    )
                    return None
            else:
                thread_safe_print(f"3rd CTA timer format invalid: {third_cta_timer}")
                return None
        else:
            thread_safe_print("3rd CTA is Available or N/A")
            return None

    except Exception as e:
        thread_safe_print(f"Error capturing store timers: {e}")
        return None


def get_claim_buttons_daily(driver):
    thread_safe_print("Searching for claim buttons on daily rewards page...")
    claim_buttons = []
    try:
        all_buttons = driver.find_elements(By.TAG_NAME, "button")
        thread_safe_print(f"Found {len(all_buttons)} total buttons on page")
        for btn in all_buttons:
            try:
                btn_text = btn.text.strip()
                if btn_text and "claim" in btn_text.lower():
                    if btn.is_displayed() and btn.is_enabled():
                        if any(
                            word in btn_text.lower()
                            for word in ["buy", "purchase", "payment", "pay", "$"]
                        ):
                            continue
                        claim_buttons.append(btn)
                        thread_safe_print(
                            f"Found claim button: '{btn_text}' - Enabled: {btn.is_enabled()}"
                        )
            except Exception:
                continue
    except Exception as e:
        thread_safe_print(f"Error getting all buttons: {e}")

    if not claim_buttons:
        thread_safe_print("No claim buttons found manually, trying XPath selectors...")
        xpath_selectors = [
            "//button[normalize-space()='Claim']",
            "//button[contains(text(), 'Claim') and not(contains(text(), 'Buy'))]",
            "//*[contains(text(), 'Claim') and (self::button or self::a)]",
        ]
        for selector in xpath_selectors:
            try:
                found_buttons = driver.find_elements(By.XPATH, selector)
                for btn in found_buttons:
                    if btn.is_displayed() and btn.is_enabled() and btn not in claim_buttons:
                        btn_text = btn.text.strip()
                        if any(
                            word in btn_text.lower()
                            for word in ["buy", "purchase", "payment", "pay", "$"]
                        ):
                            continue
                        claim_buttons.append(btn)
                        thread_safe_print(f"Added claim button via XPath: '{btn_text}'")
            except Exception:
                continue

    return claim_buttons


def claim_daily_rewards_page(driver, wait):
    claimed = 0
    try:
        time.sleep(0.5)
        thread_safe_print("Processing Daily Rewards page...")
        ensure_daily_rewards_page(driver)
        close_popups_safe(driver)
        time.sleep(0.3)

        claim_buttons = get_claim_buttons_daily(driver)
        if not claim_buttons:
            thread_safe_print("No claim buttons found - performing double check...")
            time.sleep(0.5)
            ensure_daily_rewards_page(driver)
            close_popups_safe(driver)
            claim_buttons = get_claim_buttons_daily(driver)
            if not claim_buttons:
                thread_safe_print("Double check confirmed: No claimable rewards available")
                return 0

        thread_safe_print(f"Found {len(claim_buttons)} claimable rewards")

        for idx, btn in enumerate(claim_buttons):
            try:
                thread_safe_print(
                    f"Processing claim button {idx + 1} of {len(claim_buttons)}"
                )
                if ensure_daily_rewards_page(driver):
                    thread_safe_print(
                        "Had to navigate back to daily-rewards page before claim"
                    )
                    updated_buttons = get_claim_buttons_daily(driver)
                    if idx < len(updated_buttons):
                        btn = updated_buttons[idx]
                    else:
                        thread_safe_print(
                            f"Button {idx + 1} no longer available after navigation"
                        )
                        continue

                close_popups_safe(driver)
                btn_text = btn.text.strip()
                thread_safe_print(
                    f"Attempting to claim: '{btn_text}' - Enabled: {btn.is_enabled()}"
                )

                if btn.is_displayed() and btn.is_enabled():
                    driver.execute_script(
                        "arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});",
                        btn,
                    )
                    time.sleep(0.3)
                    clicked = False
                    try:
                        btn.click()
                        clicked = True
                        thread_safe_print(
                            f"Successfully clicked claim button {idx + 1} (regular click)"
                        )
                    except Exception:
                        try:
                            driver.execute_script("arguments[0].click();", btn)
                            clicked = True
                            thread_safe_print(
                                f"Successfully clicked claim button {idx + 1} (JavaScript click)"
                            )
                        except Exception:
                            try:
                                actions = ActionChains(driver)
                                actions.move_to_element(btn).click().perform()
                                clicked = True
                                thread_safe_print(
                                    f"Successfully clicked claim button {idx + 1} (ActionChains click)"
                                )
                            except Exception:
                                pass

                    if clicked:
                        claimed += 1
                        thread_safe_print(
                            f"DAILY REWARD {claimed} CLAIMED SUCCESSFULLY!"
                        )
                        time.sleep(1.2)
                        if ensure_daily_rewards_page(driver):
                            thread_safe_print(
                                "Had to navigate back to daily-rewards page after claim"
                            )
                            close_popups_safe(driver)
                    else:
                        thread_safe_print(
                            f"All click methods failed for reward {idx + 1}"
                        )
                else:
                    thread_safe_print(f"Claim button {idx + 1} not clickable")
            except Exception as e:
                thread_safe_print(
                    f"Error processing claim button {idx + 1}: {e}"
                )
                continue

        thread_safe_print(f"Daily rewards page: claimed {claimed} rewards")

    except Exception as e:
        thread_safe_print(f"Error on daily rewards page: {e}")

    return claimed


def navigate_to_daily_rewards_section_store(driver):
    thread_safe_print("Navigating to Daily Rewards section in Store...")
    ensure_store_page(driver)
    close_popups_safe(driver)
    time.sleep(0.3)

    tab_clicked = click_daily_rewards_tab(driver)
    if tab_clicked:
        thread_safe_print("Successfully navigated to Daily Rewards section via tab")
        time.sleep(0.7)
        return True
    else:
        thread_safe_print("Tab click failed, trying scroll method...")
        try:
            driver.execute_script("window.scrollTo(0, 0);")
            time.sleep(0.3)
            max_scrolls = 4
            for scroll_attempt in range(max_scrolls):
                thread_safe_print(
                    f"Scroll attempt {scroll_attempt + 1}/{max_scrolls}..."
                )
                driver.execute_script("window.scrollBy(0, 400);")
                time.sleep(0.8)
                daily_text_elements = driver.find_elements(
                    By.XPATH,
                    "//*[contains(text(), 'Daily Reward') and not(self::a) and not(self::button)]",
                )
                if daily_text_elements:
                    for element in daily_text_elements:
                        try:
                            element_text = element.text.strip()
                            thread_safe_print(f"Found Daily Rewards text: '{element_text}'")
                            driver.execute_script(
                                "arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});",
                                element,
                            )
                            time.sleep(1.0)
                            thread_safe_print(
                                "Successfully navigated to Daily Rewards section via scroll"
                            )
                            return True
                        except Exception:
                            continue
        except Exception as e:
            thread_safe_print(f"Scroll navigation failed: {e}")

    thread_safe_print("Failed to navigate to Daily Rewards section")
    return False


def click_daily_rewards_tab(driver):
    tab_selectors = [
        "//div[contains(@class, 'tab')]//span[contains(text(), 'Daily Rewards')]",
        "//button[contains(@class, 'tab')][contains(text(), 'Daily Rewards')]",
        "//*[text()='Daily Rewards' and (contains(@class, 'tab') or parent::*[contains(@class, 'tab')])]",
        "//div[contains(@class, 'Tab')]//div[contains(text(), 'Daily Rewards')]",
        "//a[contains(@class, 'tab')][contains(text(), 'Daily Rewards')]",
    ]

    for i, selector in enumerate(tab_selectors):
        try:
            tab_elements = driver.find_elements(By.XPATH, selector)
            for j, tab in enumerate(tab_elements):
                try:
                    if tab.is_displayed():
                        tab_text = tab.text.strip()
                        try:
                            parent = tab.find_element(By.XPATH, "./..")
                            parent_classes = parent.get_attribute("class") or ""
                            if any(
                                word in parent_classes.lower()
                                for word in ["sidebar", "menu", "nav", "side"]
                            ):
                                continue
                        except Exception:
                            pass

                        try:
                            driver.execute_script(
                                "arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});",
                                tab,
                            )
                            time.sleep(0.3)
                            tab.click()
                            thread_safe_print(
                                "Successfully clicked Daily Rewards tab (regular click)"
                            )
                            time.sleep(0.7)
                            return True
                        except Exception:
                            try:
                                driver.execute_script("arguments[0].click();", tab)
                                thread_safe_print(
                                    "Successfully clicked Daily Rewards tab (JS click)"
                                )
                                time.sleep(0.7)
                                return True
                            except Exception:
                                continue
                except Exception:
                    continue
        except Exception:
            continue

    return False


def get_claim_buttons_store(driver):
    claim_buttons = []
    specific_claim_selectors = [
        "//button[normalize-space()='Claim']",
        "//button[contains(text(), 'Claim') and not(contains(text(), 'Buy')) and not(contains(text(), 'Purchase'))]",
        "//div[contains(@class, 'reward')]//button[contains(text(), 'Claim')]",
    ]

    for selector in specific_claim_selectors:
        try:
            found_buttons = driver.find_elements(By.XPATH, selector)
            for btn in found_buttons:
                if btn.is_displayed() and btn not in claim_buttons:
                    btn_text = btn.text.strip()
                    if any(
                        word in btn_text.lower()
                        for word in ["buy", "purchase", "payment", "pay", "$"]
                    ):
                        continue
                    claim_buttons.append(btn)
                    thread_safe_print(
                        f"Found claim button: '{btn_text}' - Enabled: {btn.is_enabled()}"
                    )
        except Exception:
            continue

    return claim_buttons


def claim_store_daily_rewards(driver, wait):
    claimed = 0
    store_timer = None
    max_claim_attempts = 5
    try:
        time.sleep(0.5)
        thread_safe_print("Processing Store page Daily Rewards section...")

        if not navigate_to_daily_rewards_section_store(driver):
            thread_safe_print("Failed to navigate to Daily Rewards section initially")
            return 0, None

        for claim_round in range(max_claim_attempts):
            thread_safe_print(f"\n--- CLAIM ROUND {claim_round + 1} ---")
            ensure_store_page(driver)
            close_popups_safe(driver)

            if claim_round > 0:
                thread_safe_print(
                    "Re-navigating to Daily Rewards section before next claim..."
                )
                if not navigate_to_daily_rewards_section_store(driver):
                    thread_safe_print(
                        f"Failed to re-navigate to Daily Rewards section in round {claim_round + 1}"
                    )
                    continue
            time.sleep(0.7)

            claim_buttons = get_claim_buttons_store(driver)
            if not claim_buttons:
                thread_safe_print(
                    f"No claim buttons found in round {claim_round + 1}"
                )
                thread_safe_print("Performing double check for claim buttons...")
                time.sleep(0.7)
                claim_buttons = get_claim_buttons_store(driver)
                if not claim_buttons:
                    thread_safe_print(
                        "Double check confirmed: No more claim buttons available"
                    )
                    break

            button_claimed_this_round = False
            for idx, btn in enumerate(claim_buttons):
                try:
                    thread_safe_print(
                        f"Attempting to claim button {idx + 1} in round {claim_round + 1}"
                    )
                    btn_text = btn.text.strip()
                    thread_safe_print(
                        f"Button text: '{btn_text}' - Enabled: {btn.is_enabled()}"
                    )

                    if btn.is_enabled():
                        driver.execute_script(
                            "arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});",
                            btn,
                        )
                        time.sleep(0.3)
                        close_popups_safe(driver)

                        clicked = False
                        try:
                            btn.click()
                            clicked = True
                            thread_safe_print(
                                f"Successfully clicked claim button {idx + 1} (regular click)"
                            )
                        except Exception:
                            try:
                                driver.execute_script("arguments[0].click();", btn)
                                clicked = True
                                thread_safe_print(
                                    f"Successfully clicked claim button {idx + 1} (JavaScript click)"
                                )
                            except Exception:
                                pass

                        if clicked:
                            claimed += 1
                            button_claimed_this_round = True
                            thread_safe_print(
                                f"STORE REWARD {claimed} CLAIMED SUCCESSFULLY!"
                            )
                            time.sleep(1.2)
                            close_store_popup_after_claim(driver)
                            if ensure_store_page(driver):
                                thread_safe_print(
                                    "Had to navigate back to store page after claim"
                                )
                                close_popups_safe(driver)
                            break
                        else:
                            thread_safe_print(
                                f"All click methods failed for button {idx + 1}"
                            )
                    else:
                        thread_safe_print(f"Button {idx + 1} not enabled")
                except Exception as e:
                    thread_safe_print(
                        f"Error processing claim button {idx + 1}: {e}"
                    )
                    continue

            if not button_claimed_this_round:
                thread_safe_print(
                    f"No buttons claimed in round {claim_round + 1} - all available claims completed"
                )
                break

            thread_safe_print(
                f"Round {claim_round + 1} completed. Total claimed so far: {claimed}"
            )
            if claimed >= 3:
                thread_safe_print("All 3 daily rewards claimed successfully!")
                break

        thread_safe_print(f"\nFINAL RESULT: Claimed {claimed} store rewards")

        # Capture timer information for 3rd CTA only if <= 1 hour
        try:
            thread_safe_print("Capturing 3rd CTA timer status...")
            ensure_store_page(driver)
            if navigate_to_daily_rewards_section_store(driver):
                time.sleep(0.7)
                store_timer = get_store_claim_timers(driver)
            else:
                store_timer = None
        except Exception as timer_error:
            thread_safe_print(f"Error capturing timer: {timer_error}")
            store_timer = None

    except Exception as e:
        thread_safe_print(f"Error in store daily rewards: {e}")
        store_timer = None

    return claimed, store_timer


def automate_player(player_id, thread_id, is_retry=False):
    retry_text = " (RETRY)" if is_retry else ""
    thread_safe_print(f"[Thread-{thread_id}] Processing player{retry_text}: {player_id}")
    driver = create_driver()
    wait = WebDriverWait(driver, 10)
    login_successful = False

    try:
        driver.get("https://hub.vertigogames.co/daily-rewards")
        time.sleep(0.4)
        accept_cookies(driver, wait)

        login_selectors = [
            "//button[contains(text(),'Login') or contains(text(),'Log in') or contains(text(), 'Sign in')]",
            "//a[contains(text(),'Login') or contains(text(),'Log in') or contains(text(), 'Sign in')]",
            "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'login')]",
            "//button[contains(text(), 'claim')]",
            "//div[contains(text(), 'Daily Rewards') or contains(text(), 'daily')]//button",
            "//button[contains(@class, 'btn') or contains(@class, 'button')]",
            "//*[contains(text(), 'Login') or contains(text(), 'login')][@onclick or @href or self::button or self::a]",
        ]

        login_clicked = False
        for i, selector in enumerate(login_selectors):
            try:
                elements = driver.find_elements(By.XPATH, selector)
                if elements:
                    for j, element in enumerate(elements):
                        try:
                            element_text = element.text.strip()
                            if element.is_displayed() and element.is_enabled():
                                element.click()
                                login_clicked = True
                                thread_safe_print(
                                    f"[Thread-{thread_id}] Successfully clicked login element"
                                )
                                break
                        except Exception:
                            continue
                if login_clicked:
                    break
            except Exception:
                continue

        if not login_clicked:
            thread_safe_print(
                f"[Thread-{thread_id}] No login button found for {player_id}"
            )
            return {
                "player_id": player_id,
                "daily_page": 0,
                "store_daily": 0,
                "store_timer": None,
                "status": "login_button_not_found",
                "login_successful": False,
            }

        try:
            time.sleep(0.3)
            input_selectors = [
                "#user-id-input",
                "//input[contains(@placeholder, 'ID') or contains(@placeholder, 'User') or contains(@name, 'user') or contains(@placeholder, 'id')]",
                "//input[@type='text']",
                "//input[contains(@class, 'input')]",
                "//div[contains(@class, 'modal') or contains(@class, 'dialog')]//input[@type='text']",
            ]

            input_found = False
            input_box = None
            for i, selector in enumerate(input_selectors):
                try:
                    if selector.startswith("#"):
                        input_box = WebDriverWait(driver, 2).until(
                            EC.visibility_of_element_located((By.ID, selector[1:]))
                        )
                    else:
                        input_box = WebDriverWait(driver, 2).until(
                            EC.visibility_of_element_located((By.XPATH, selector))
                        )
                    thread_safe_print(f"[Thread-{thread_id}] Input field found")
                    input_box.clear()
                    input_box.send_keys(player_id)
                    time.sleep(0.1)
                    input_found = True
                    break
                except Exception:
                    continue

            if not input_found:
                thread_safe_print(
                    f"[Thread-{thread_id}] No input field found for {player_id}"
                )
                return {
                    "player_id": player_id,
                    "daily_page": 0,
                    "store_daily": 0,
                    "store_timer": None,
                    "status": "input_field_not_found",
                    "login_successful": False,
                }

            login_cta_selectors = [
                "//button[contains(text(), 'Login') or contains(text(), 'Log in') or contains(text(), 'Sign in')]",
                "//button[@type='submit']",
                "//div[contains(@class, 'modal') or contains(@class, 'dialog')]//button[not(contains(text(), 'Cancel')) and not(contains(text(), 'Close'))]",
                "//button[contains(@class, 'primary') or contains(@class, 'submit')]",
            ]

            login_cta_clicked = False
            for i, selector in enumerate(login_cta_selectors):
                try:
                    if click_element_or_coords(
                        driver, wait, (By.XPATH, selector), None, f"Login CTA {i+1}", timeout=2
                    ):
                        login_cta_clicked = True
                        thread_safe_print(
                            f"[Thread-{thread_id}] Login CTA clicked successfully"
                        )
                        break
                except Exception:
                    continue

            if not login_cta_clicked:
                try:
                    input_box.send_keys(Keys.ENTER)
                    time.sleep(0.3)
                    thread_safe_print(
                        f"[Thread-{thread_id}] Enter key pressed successfully"
                    )
                except Exception:
                    thread_safe_print(
                        f"[Thread-{thread_id}] Login CTA not found for {player_id}"
                    )
                    return {
                        "player_id": player_id,
                        "daily_page": 0,
                        "store_daily": 0,
                        "store_timer": None,
                        "status": "login_cta_not_found",
                        "login_successful": False,
                    }

            thread_safe_print(f"[Thread-{thread_id}] Waiting for login to complete...")
            wait_for_login_complete(driver, wait, max_wait=12)
            time.sleep(0.7)
            thread_safe_print(f"[Thread-{thread_id}] Login completed successfully")
            login_successful = True

        except TimeoutException:
            thread_safe_print(
                f"[Thread-{thread_id}] Login timeout for {player_id}"
            )
            return {
                "player_id": player_id,
                "daily_page": 0,
                "store_daily": 0,
                "store_timer": None,
                "status": "login_timeout",
                "login_successful": False,
            }

        thread_safe_print(
            f"[Thread-{thread_id}] === STEP 1: DAILY REWARDS PAGE ==="
        )
        ensure_daily_rewards_page(driver)
        close_popups_safe(driver)
        time.sleep(0.7)
        daily_page_claimed = claim_daily_rewards_page(driver, wait)
        thread_safe_print(
            f"[Thread-{thread_id}] Daily Page Claims: {daily_page_claimed}"
        )

        thread_safe_print(
            f"[Thread-{thread_id}] === STEP 2: NAVIGATING TO STORE PAGE ==="
        )
        driver.get("https://hub.vertigogames.co/store")
        time.sleep(0.7)
        ensure_store_page(driver)
        close_popups_safe(driver)
        time.sleep(0.5)
        store_claimed, store_timer = claim_store_daily_rewards(driver, wait)
        thread_safe_print(
            f"[Thread-{thread_id}] Store Page Claims: {store_claimed}"
        )

        if store_timer:
            thread_safe_print(
                f"[Thread-{thread_id}] 3rd CTA Timer (<=1h): {store_timer}"
            )
        else:
            thread_safe_print(
                f"[Thread-{thread_id}] 3rd CTA Timer: Not applicable or > 1 hour"
            )

        total_claimed = daily_page_claimed + store_claimed
        if total_claimed > 0:
            status = "success"
        else:
            status = "no_claims"

        thread_safe_print(f"[Thread-{thread_id}] === COMPLETED: {player_id} ===")
        thread_safe_print(
            f"[Thread-{thread_id}] Daily: {daily_page_claimed}, Store: {store_claimed}, Total: {total_claimed}"
        )

        return {
            "player_id": player_id,
            "daily_page": daily_page_claimed,
            "store_daily": store_claimed,
            "store_timer": store_timer,
            "status": status,
            "login_successful": True,
        }

    except Exception as e:
        thread_safe_print(
            f"[Thread-{thread_id}] Exception for player {player_id}: {e}"
        )
        return {
            "player_id": player_id,
            "daily_page": 0,
            "store_daily": 0,
            "store_timer": None,
            "status": "error",
            "login_successful": login_successful,
        }

    finally:
        try:
            driver.quit()
            thread_safe_print(
                f"[Thread-{thread_id}] Driver closed for player {player_id}"
            )
        except Exception:
            pass


def process_batch(player_batch, batch_number, is_retry=False):
    retry_text = " (RETRY)" if is_retry else ""
    thread_safe_print(
        f"Starting Batch {batch_number}{retry_text} with {len(player_batch)} players"
    )
    results = []
    with ThreadPoolExecutor(max_workers=len(player_batch)) as executor:
        future_to_player = {
            executor.submit(
                automate_player, player_id, f"{batch_number}-{idx+1}", is_retry
            ): player_id
            for idx, player_id in enumerate(player_batch)
        }

        for future in as_completed(future_to_player):
            player_id = future_to_player[future]
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                thread_safe_print(
                    f"Batch {batch_number}{retry_text} - Player {player_id} failed: {e}"
                )
                results.append(
                    {
                        "player_id": player_id,
                        "daily_page": 0,
                        "store_daily": 0,
                        "store_timer": None,
                        "status": "failed",
                        "login_successful": False,
                    }
                )

    thread_safe_print(f"Batch {batch_number}{retry_text} completed")
    return results


def main():
    players = []
    with open("players.csv", newline="") as csvfile:
        reader = csv.reader(csvfile)
        for row in reader:
            pid = row[0].strip()
            if pid:
                players.append(pid)

    thread_safe_print(f"Loaded {len(players)} player IDs")

    BATCH_SIZE = 2
    batches = [players[i : i + BATCH_SIZE] for i in range(0, len(players), BATCH_SIZE)]
    thread_safe_print(
        f"Processing {len(batches)} batches of up to {BATCH_SIZE} players each"
    )

    all_results = []
    start_time = time.time()

    thread_safe_print("\n" + "=" * 60)
    thread_safe_print("STARTING INITIAL RUN FOR ALL PLAYERS")
    thread_safe_print("=" * 60)

    for batch_num, batch in enumerate(batches, 1):
        batch_start = time.time()
        batch_results = process_batch(batch, batch_num, is_retry=False)
        all_results.extend(batch_results)
        batch_time = time.time() - batch_start
        thread_safe_print(f"Batch {batch_num} took {batch_time:.1f} seconds")
        if batch_num < len(batches):
            time.sleep(0.7)

    failed_players = []
    for result in all_results:
        if result["status"] in [
            "error",
            "login_button_not_found",
            "input_field_not_found",
            "login_cta_not_found",
            "login_timeout",
            "failed",
        ]:
            failed_players.append(result["player_id"])

    if failed_players:
        thread_safe_print("\n" + "=" * 60)
        thread_safe_print(
            f"STARTING RETRY RUN FOR {len(failed_players)} FAILED PLAYERS"
        )
        thread_safe_print("=" * 60)
        thread_safe_print(f"Failed Player IDs: {', '.join(failed_players)}")
        thread_safe_print("=" * 60)

        retry_batches = [
            failed_players[i : i + BATCH_SIZE]
            for i in range(0, len(failed_players), BATCH_SIZE)
        ]
        retry_results = []

        for batch_num, batch in enumerate(retry_batches, 1):
            batch_start = time.time()
            batch_results = process_batch(batch, f"R{batch_num}", is_retry=True)
            retry_results.extend(batch_results)
            batch_time = time.time() - batch_start
            thread_safe_print(f"Retry Batch R{batch_num} took {batch_time:.1f} seconds")
            if batch_num < len(retry_batches):
                time.sleep(0.7)

        retry_dict = {r["player_id"]: r for r in retry_results}
        for i, result in enumerate(all_results):
            if result["player_id"] in retry_dict:
                all_results[i] = retry_dict[result["player_id"]]

    total_time = time.time() - start_time
    successful_logins = sum(1 for r in all_results if r["login_successful"])
    successful_processes = sum(1 for r in all_results if r["status"] == "success")
    total_daily_page = sum(r["daily_page"] for r in all_results)
    total_store_daily = sum(r["store_daily"] for r in all_results)

    final_failed = [
        r["player_id"]
        for r in all_results
        if r["status"]
        in [
            "error",
            "login_button_not_found",
            "input_field_not_found",
            "login_cta_not_found",
            "login_timeout",
            "failed",
        ]
    ]
    final_no_claims = [
        r["player_id"] for r in all_results if r["status"] == "no_claims"
    ]

    thread_safe_print("\n" + "=" * 60)
    thread_safe_print("MERGED MODULE - FINAL SUMMARY")
    thread_safe_print("=" * 60)
    thread_safe_print(f"Total players processed: {len(all_results)}")
    thread_safe_print(f"Successful logins: {successful_logins}")
    thread_safe_print(f"Successful claim processes: {successful_processes}")
    thread_safe_print(f"Daily Rewards page claims: {total_daily_page}")
    thread_safe_print(f"Store Daily Rewards claims: {total_store_daily}")
    thread_safe_print(
        f"Total claims: {total_daily_page + total_store_daily}"
    )
    thread_safe_print(f"Total script execution time: {total_time:.1f} seconds")
    if players:
        thread_safe_print(
            f"Average time per player: {total_time/len(players):.1f} seconds"
        )

    if failed_players:
        thread_safe_print(
            f"Players that required retry: {len(failed_players)}"
        )

    status_counts = {}
    for result in all_results:
        status = result["status"]
        status_counts[status] = status_counts.get(status, 0) + 1

    thread_safe_print("\nStatus Breakdown:")
    for status, count in status_counts.items():
        thread_safe_print(f" {status}: {count}")

    if final_failed:
        thread_safe_print(
            f"\nFinal Failed Player IDs: {', '.join(final_failed)}"
        )
    if final_no_claims:
        thread_safe_print(
            f"No Claims Available Player IDs: {', '.join(final_no_claims)}"
        )

    # Store Module Timer Details - ONLY 3rd CTA if <= 1 hour
    thread_safe_print("\n" + "=" * 60)
    thread_safe_print("STORE 3RD CTA TIMERS (<= 1 HOUR)")
    thread_safe_print("=" * 60)

    urgent_players = []
    for result in all_results:
        if "store_timer" in result and result["store_timer"] is not None:
            urgent_players.append(
                {
                    "player_id": result["player_id"],
                    "timer": result["store_timer"],
                }
            )

    if urgent_players:
        for player in urgent_players:
            thread_safe_print(f"Player ID: {player['player_id']}")
            thread_safe_print(f"3rd CTA Timer: {player['timer']}")
            thread_safe_print("-" * 40)
        thread_safe_print(
            f"\n⚠️ {len(urgent_players)} player(s) have 3rd CTA available within 1 hour!"
        )
    else:
        thread_safe_print("No players have 3rd CTA available within 1 hour")
    thread_safe_print("=" * 60)


if __name__ == "__main__":
    main()

