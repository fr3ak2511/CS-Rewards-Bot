import os
import sys
import subprocess
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


def run_script_and_capture_output(script_path: str):
    """Run the target script and capture stdout/stderr as text."""
    result = subprocess.run(
        [sys.executable, script_path],
        capture_output=True,
        text=True,
    )
    stdout = result.stdout or ""
    stderr = result.stderr or ""

    full_output = stdout
    if stderr.strip():
        full_output += "\n\n--- STDERR ---\n" + stderr

    return result.returncode, full_output


def send_email(subject: str, body: str):
    smtp_host = os.environ["SMTP_HOST"]
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ["SMTP_USER"]
    smtp_pass = os.environ["SMTP_PASS"]
    email_from = os.environ["EMAIL_FROM"]
    email_to = os.environ["EMAIL_TO"]

    msg = MIMEMultipart()
    msg["From"] = email_from
    msg["To"] = email_to
    msg["Subject"] = subject

    msg.attach(MIMEText(body, "plain"))

    context = ssl.create_default_context()
    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls(context=context)
        server.login(smtp_user, smtp_pass)
        server.send_message(msg)


def main():
    if len(sys.argv) < 3:
        print("Usage: python send_email_with_log.py <script_name.py> <job_label>")
        sys.exit(1)

    script_name = sys.argv[1]
    job_label = sys.argv[2]

    return_code, output = run_script_and_capture_output(script_name)

    status = "SUCCESS" if return_code == 0 else "FAILED"
    subject = f"[{job_label}] {status}"
    body = (
        f"Job: {job_label}\n"
        f"Exit code: {return_code}\n\n"
        f"--- OUTPUT START ---\n{output}\n--- OUTPUT END ---"
    )

    # Print to GitHub Actions log
    print(body)

    # Email the log
    try:
        send_email(subject, body)
        print("Email sent")
    except Exception as e:
        print(f"Failed to send email: {e}")

    sys.exit(return_code)


if __name__ == "__main__":
    main()
