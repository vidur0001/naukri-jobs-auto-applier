"""Shared SMTP email helper used by app.py and bot.py."""
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


def send_email(to_addr, subject, html_body):
    smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", 587))
    sender_email = os.getenv("SENDER_EMAIL", "your-bot-email@gmail.com")
    sender_password = os.getenv("SENDER_PASSWORD", "your-app-password")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender_email
    msg["To"] = to_addr
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(smtp_server, smtp_port, timeout=15) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, to_addr, msg.as_string())
        print(f"[+] Email sent: {subject}")
        return True
    except Exception as e:
        print(f"[X] Failed to send email '{subject}': {e}")
        return False
