import subprocess
import sys
import os
import smtplib
import traceback
from email.mime.text import MIMEText
from datetime import datetime

SMTP_SERVER = os.getenv("SMTP_SERVER")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
SMTP_FROM = os.getenv("SMTP_FROM")
SMTP_TO = os.getenv("SMTP_TO") or SMTP_FROM

def send_email(subject, body):
    if not SMTP_SERVER:
        print("=" * 60, file=sys.stderr)
        print("⚠️  SMTP NOT CONFIGURED - PRINTING LOG BELOW", file=sys.stderr)
        print("=" * 60, file=sys.stderr)
        print(body, file=sys.stderr)
        return
    
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = SMTP_TO
    
    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM, [SMTP_TO], msg.as_string())
        print(f"✓ Email sent at {datetime.utcnow()}", file=sys.stderr)
    except Exception as e:
        print(f"✗ Email failed: {str(e)}", file=sys.stderr)
        print(f"✗ SMTP_FROM: {SMTP_FROM}, SMTP_TO: {SMTP_TO}", file=sys.stderr)
        print("=" * 60, file=sys.stderr)

if __name__ == "__main__":
    try:
        print(f"🚀 Script started at {datetime.utcnow()}", file=sys.stderr)
        
        # Run the main script and capture output
        result = subprocess.run(
            ["python", "g_hub_merged_rewards_updated.py"],
            capture_output=True,
            text=True,
            timeout=600  # 10 minute timeout
        )
        
        output = result.stdout + "\n" + result.stderr
        
        if result.returncode != 0:
            output += f"\n\n⚠️ SCRIPT EXITED WITH CODE {result.returncode}"
        
        if not output.strip():
            output = f"⚠️ NO OUTPUT FROM SCRIPT at {datetime.utcnow()}\n"
            output += f"Return code: {result.returncode}\n"
            output += f"Stdout: '{result.stdout}'\n"
            output += f"Stderr: '{result.stderr}'"
        
        # Always print to console for GitHub Actions logs
        print(output, file=sys.stderr)
        
        # Send email
        send_email("Hub Merged Rewards Summary", output)
        
    except subprocess.TimeoutExpired:
        error_msg = f"✗ SCRIPT TIMED OUT after 600 seconds at {datetime.utcnow()}"
        print(error_msg, file=sys.stderr)
        send_email("Hub Merged Rewards - TIMEOUT ERROR", error_msg)
    except Exception as e:
        error_msg = f"✗ send_email_with_log.py CRASHED at {datetime.utcnow()}:\n{traceback.format_exc()}"
        print(error_msg, file=sys.stderr)
        send_email("Hub Merged Rewards - CRITICAL ERROR", error_msg)
