import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication

def send_email_with_log():
    smtp_server = os.getenv("SMTP_SERVER")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_username = os.getenv("SMTP_USERNAME")
    smtp_password = os.getenv("SMTP_PASSWORD")
    sender = os.getenv("SMTP_FROM")
    recipient = os.getenv("SMTP_TO")

    subject = "Hub Rewards Execution Log"

    # --- Load the latest workflow log or summary file if exists ---
    log_file_path = "workflow_summary.log"  # You can change this to match your log filename

    # Case 1: log file exists → attach it and read for email body
    if os.path.exists(log_file_path):
        with open(log_file_path, "r", encoding="utf-8") as f:
            log_content = f.read()
        body_text = f"Attached is the log file from the latest GitHub Action run.\n\n---\n{log_content[-3000:]}"  # last 3k chars
        attach_log = True
    else:
        # Fallback if no file found
        body_text = "GitHub Action completed successfully, but no log file was found."
        attach_log = False

    # --- Compose Email ---
    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = subject

    msg.attach(MIMEText(body_text, "plain"))

    # --- Attach Log File (if present) ---
    if attach_log:
        with open(log_file_path, "rb") as f:
            attachment = MIMEApplication(f.read(), Name=os.path.basename(log_file_path))
        attachment["Content-Disposition"] = f'attachment; filename="{os.path.basename(log_file_path)}"'
        msg.attach(attachment)

    # --- Send Email via Gmail ---
    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.sendmail(sender, recipient, msg.as_string())
        print("Email sent successfully with log attachment.")
    except Exception as e:
        print(f"Error sending email: {e}")

if __name__ == "__main__":
    send_email_with_log()
