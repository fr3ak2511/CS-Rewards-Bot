import csv
import time
import os
import smtplib
from datetime import datetime, timedelta  # NEW: Added timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager

PLAYER_ID_FILE = "players.csv"
HEADLESS = True

# NEW: Daily tracking constants
DAILY_RESET_HOUR_IST = 5
DAILY_RESET_MINUTE_IST = 30
EXPECTED_STORE_PER_PLAYER = 3

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

# NEW: IST timezone helper functions
def get_ist_time():
    """Get current time in IST (UTC+5:30)"""
    utc_now = datetime.utcnow()
    ist_offset = timedelta(hours=5, minutes=30)
    return utc_now + ist_offset

def get_current_daily_window_start():
    """Get the start of current daily window (5:30 AM IST)"""
    ist_now = get_ist_time()
    if ist_now.hour < DAILY_RESET_HOUR_IST or (ist_now.hour == DAILY_RESET_HOUR_IST and ist_now.minute < DAILY_RESET_MINUTE_IST):
        window_start = ist_now.replace(hour=DAILY_RESET_HOUR_IST, minute=DAILY_RESET_MINUTE_IST, second=0, microsecond=0) - timedelta(days=1)
    else:
        window_start = ist_now.replace(hour=DAILY_RESET_HOUR_IST, minute=DAILY_RESET_MINUTE_IST, second=0, microsecond=0)
    return window_start

def get_next_daily_reset():
    """Get next daily reset time (5:30 AM IST)"""
    ist_now = get_ist_time()
    if ist_now.hour < DAILY_RESET_HOUR_IST or (ist_now.hour == DAILY_RESET_HOUR_IST and ist_now.minute < DAILY_RESET_MINUTE_IST):
        next_reset = ist_now.replace(hour=DAILY_RESET_HOUR_IST, minute=DAILY_RESET_MINUTE_IST, second=0, microsecond=0)
    else:
        next_reset = ist_now.replace(hour=DAILY_RESET_HOUR_IST, minute=DAILY_RESET_MINUTE_IST, second=0, microsecond=0) + timedelta(days=1)
    return next_reset

def format_time_until_reset(next_reset):
    """Format time remaining until next reset"""
    ist_now = get_ist_time()
    delta = next_reset - ist_now
    hours, remainder = divmod(delta.seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    return f"{hours}h {minutes}m"

# [ALL YOUR EXISTING FUNCTIONS REMAIN EXACTLY THE SAME - NOT SHOWING HERE TO SAVE SPACE]
# create_driver, accept_cookies, login_to_hub, close_popup, ensure_store_page,
# click_daily_rewards_tab, navigate_to_daily_rewards_section_store,
# claim_daily_rewards, claim_store_rewards, claim_progression_program_rewards,
# process_player - ALL STAY UNCHANGED

def create_driver():
    """GitHub Actions-compatible driver"""
    options = Options()
    
    if HEADLESS:
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1920,1080")
    
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
    options.add_argument("--disable-background-timer-throttling")
    options.add_argument("--disable-renderer-backgrounding")
    options.add_argument("--disable-backgrounding-occluded-windows")
    
    prefs = {
        "profile.default_content_setting_values": {
            "images": 2,
            "notifications": 2,
            "popups": 2,
        },
        "profile.default_content_settings.popups": 0,
        "profile.managed_default_content_settings.popups": 0,
    }
    options.add_experimental_option("prefs", prefs)
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    
    try:
        driver_path = ChromeDriverManager().install()
    except:
        driver_path = "/usr/bin/chromedriver"
    
    service = Service(driver_path)
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(30)
    driver.set_script_timeout(30)
    
    return driver

# [COPY ALL YOUR OTHER FUNCTIONS HERE - accept_cookies, login_to_hub, close_popup, etc.]
# I'm not repeating them to save space since they DON'T CHANGE

# NEW: Enhanced email function with daily tracking
def send_email_summary(results, num_players):
    """Send email with daily tracking stats"""
    try:
        sender = os.environ.get("SENDER_EMAIL")
        recipient = os.environ.get("RECIPIENT_EMAIL")
        password = os.environ.get("GMAIL_APP_PASSWORD")
        
        if not all([sender, recipient, password]):
            log("⚠️  Email env vars missing")
            return
        
        # Calculate totals
        total_d = sum(r['daily'] for r in results)
        total_s = sum(r['store'] for r in results)
        total_p = sum(r['progression'] for r in results)
        total_all = total_d + total_s + total_p
        
        success_count = sum(1 for r in results if r['status'] == 'Success')
        
        # NEW: Daily tracking calculations
        expected_store_total = num_players * EXPECTED_STORE_PER_PLAYER
        store_progress_pct = int((total_s / expected_store_total) * 100) if expected_store_total > 0 else 0
        
        # NEW: Time calculations
        ist_now = get_ist_time()
        window_start = get_current_daily_window_start()
        next_reset = get_next_daily_reset()
        time_until_reset = format_time_until_reset(next_reset)
        hours_since_reset = int((ist_now - window_start).total_seconds() // 3600)
        
        # NEW: Build enhanced HTML email
        html = f"""
        <html>
        <body style="font-family: Arial, sans-serif;">
        <h2>🎮 Hub Rewards Summary</h2>
        
        <div style="background-color: #f0f8ff; padding: 15px; border-radius: 8px; margin-bottom: 20px;">
            <h3 style="margin-top: 0;">📊 Daily Window Tracking (5:30 AM IST Reset)</h3>
            <table style="width: 100%; border-collapse: collapse;">
                <tr>
                    <td style="padding: 5px;"><strong>Current Time:</strong></td>
                    <td>{ist_now.strftime('%Y-%m-%d %I:%M %p IST')}</td>
                </tr>
                <tr>
                    <td style="padding: 5px;"><strong>Window Started:</strong></td>
                    <td>{window_start.strftime('%Y-%m-%d %I:%M %p IST')} ({hours_since_reset}h ago)</td>
                </tr>
                <tr>
                    <td style="padding: 5px;"><strong>Next Reset:</strong></td>
                    <td>{next_reset.strftime('%Y-%m-%d %I:%M %p IST')} (in {time_until_reset})</td>
                </tr>
            </table>
        </div>
        
        <div style="background-color: #fff3cd; padding: 15px; border-radius: 8px; margin-bottom: 20px;">
            <h3 style="margin-top: 0;">📈 Today's Cumulative Stats (Since 5:30 AM)</h3>
            <table style="width: 100%; border-collapse: collapse;">
                <tr>
                    <td style="padding: 5px;"><strong>💰 Total Daily:</strong></td>
                    <td><strong>{total_d}</strong> (varies per player)</td>
                </tr>
                <tr style="background-color: {'#d4edda' if total_s == expected_store_total else '#fff3cd'};">
                    <td style="padding: 5px;"><strong>🏪 Total Store:</strong></td>
                    <td><strong>{total_s} / {expected_store_total}</strong> ({store_progress_pct}%) {'✅ COMPLETE' if total_s == expected_store_total else f'⚠️ {expected_store_total - total_s} remaining'}</td>
                </tr>
                <tr>
                    <td style="padding: 5px;"><strong>🎯 Total Progression:</strong></td>
                    <td><strong>{total_p}</strong> (grenade-dependent)</td>
                </tr>
                <tr style="background-color: #e7f3ff;">
                    <td style="padding: 5px;"><strong>🎁 TOTAL ALL:</strong></td>
                    <td><strong style="font-size: 1.2em;">{total_all}</strong> claims</td>
                </tr>
            </table>
        </div>
        
        <h3>👥 Per-Player Breakdown (This Run)</h3>
        <table border="1" cellpadding="5" cellspacing="0" style="border-collapse: collapse; width: 100%;">
        <tr style="background-color: #f0f0f0;">
            <th>ID</th><th>Daily</th><th>Store</th><th>Progression</th><th>Total</th><th>Status</th>
        </tr>
        """
        
        for r in results:
            total_player = r['daily'] + r['store'] + r['progression']
            
            if r['status'] == 'Success':
                status_color = "#90EE90"
            elif r['status'] == 'No Rewards':
                status_color = "#FFE4B5"
            else:
                status_color = "#FFB6C1"
            
            row_html = f"""<tr>
                <td>{r['player_id']}</td>
                <td>{r['daily']}</td>
                <td>{r['store']}{' ✅' if r['store'] == EXPECTED_STORE_PER_PLAYER else ''}</td>
                <td>{r['progression']}</td>
                <td><strong>{total_player}</strong></td>
                <td style="background-color: {status_color};">{r['status']}</td>
            </tr>"""
            html += row_html
        
        html += f"""
        <tr style="background-color: #e0e0e0; font-weight: bold;">
            <td>TOTAL</td>
            <td>{total_d}</td>
            <td>{total_s}</td>
            <td>{total_p}</td>
            <td>{total_all}</td>
            <td>{success_count}/{len(results)}</td>
        </tr>
        </table>
        
        <div style="margin-top: 20px; padding: 10px; background-color: #f9f9f9; border-left: 4px solid #4CAF50;">
            <p style="margin: 5px 0;"><strong>💡 Note:</strong></p>
            <ul style="margin: 5px 0;">
                <li><strong>Store Rewards:</strong> Exactly 3 per player per day (resets at 5:30 AM IST)</li>
                <li><strong>Daily Rewards:</strong> Variable (~1 per hour, player-dependent)</li>
                <li><strong>Progression:</strong> Unlimited (requires Grenades from Store claims)</li>
            </ul>
        </div>
        
        <p style="margin-top: 20px; color: #666; font-size: 0.9em;">
            🤖 Automated run at {ist_now.strftime('%Y-%m-%d %I:%M %p IST')}
        </p>
        </body>
        </html>
        """
        
        msg = MIMEMultipart('alternative')
        msg['Subject'] = f"Hub Rewards - {ist_now.strftime('%d-%b %I:%M %p')} IST ({total_all} claims)"
        msg['From'] = sender
        msg['To'] = recipient
        
        msg.attach(MIMEText(html, 'html'))
        
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
            server.login(sender, password)
            server.send_message(msg)
        
        log("✅ Email sent")
        
    except Exception as e:
        log(f"❌ Email error: {e}")

def main():
    """Main orchestrator"""
    log("="*60)
    log("CS HUB AUTO-CLAIMER v2.1 (Daily Tracking)")
    log("="*60)
    
    # NEW: Show IST tracking info
    ist_now = get_ist_time()
    window_start = get_current_daily_window_start()
    next_reset = get_next_daily_reset()
    
    log(f"🕐 IST: {ist_now.strftime('%Y-%m-%d %I:%M %p')}")
    log(f"⏰ Next Reset: {format_time_until_reset(next_reset)}")
    log("")
    
    # Read players
    players = []
    try:
        with open(PLAYER_ID_FILE, 'r') as f:
            reader = csv.DictReader(f)
            players = [row['player_id'].strip() for row in reader if row['player_id'].strip()]
    except Exception as e:
        log(f"❌ Cannot read {PLAYER_ID_FILE}: {e}")
        return
    
    num_players = len(players)
    log(f"📋 {num_players} player(s)")
    log("")
    
    results = []
    
    # Process each player
    for player_id in players:
        stats = process_player(player_id)
        results.append(stats)
        time.sleep(3)
    
    # Final summary
    log("")
    log("="*60)
    log("FINAL SUMMARY")
    log("="*60)
    
    total_d = sum(r['daily'] for r in results)
    total_s = sum(r['store'] for r in results)
    total_p = sum(r['progression'] for r in results)
    
    log(f"Daily: {total_d}, Store: {total_s}/{num_players * EXPECTED_STORE_PER_PLAYER}, Progression: {total_p}")
    
    for r in results:
        total = r['daily'] + r['store'] + r['progression']
        log(f"{r['player_id']}: D={r['daily']}, S={r['store']}, P={r['progression']}, Total={total} → {r['status']}")
    
    # NEW: Pass num_players to email function
    send_email_summary(results, num_players)
    
    log("")
    log("🏁 Done!")

if __name__ == "__main__":
    main()
