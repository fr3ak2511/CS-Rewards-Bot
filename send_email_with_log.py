import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
import os

def send_email_with_log():
    smtp_server = os.environ["SMTP_SERVER"]
    smtp_port = int(os.environ.get("SMTP_PORT", 587))
    username = os.environ["SMTP_USERNAME"]
    password = os.environ["SMTP_PASSWORD"]
    sender = os.environ["SMTP_FROM"]
    recipient = os.environ["SMTP_TO"]

    subject = "Hub Rewards Execution Log"
    body = "Attached is the log file from the latest GitHub Action run."

    message = MIMEMultipart()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message.attach(MIMEText(body, "plain"))

    # Attach log file if present
    if os.path.exists("action.log"):
        with open("action.log", "rb") as f:
            part = MIMEApplication(f.read(), Name="action.log")
            part['Content-Disposition'] = 'attachment; filename="action.log"'
            message.attach(part)

    # Secure connection
    context = ssl.create_default_context()
    with smtplib.SMTP(smtp_server, smtp_port) as server:
        server.ehlo()
        server.starttls(context=context)
        server.ehlo()
        server.login(username, password)
        server.send_message(message)

if __name__ == "__main__":
    send_email_with_log()
