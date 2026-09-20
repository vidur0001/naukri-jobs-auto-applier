"""Shared SMTP email helper used by app.py and bot.py."""
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication


def send_email(to_addr, subject, html_body, reply_to=None, attachment_path=None):
    """Sends an HTML email. Optionally sets a Reply-To header (so replies from
    a cold-outreach recipient land in the candidate's personal inbox instead
    of the bot's sender account) and/or attaches a single file (e.g. resume)."""
    smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", 587))
    sender_email = os.getenv("SENDER_EMAIL", "your-bot-email@gmail.com")
    sender_password = os.getenv("SENDER_PASSWORD", "your-app-password")

    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = sender_email
    msg["To"] = to_addr
    if reply_to:
        msg["Reply-To"] = reply_to

    body_part = MIMEMultipart("alternative")
    body_part.attach(MIMEText(html_body, "html"))
    msg.attach(body_part)

    if attachment_path and os.path.exists(attachment_path):
        try:
            with open(attachment_path, "rb") as f:
                part = MIMEApplication(f.read(), Name=os.path.basename(attachment_path))
            part["Content-Disposition"] = f'attachment; filename="{os.path.basename(attachment_path)}"'
            msg.attach(part)
        except Exception as e:
            print(f"[-] Could not attach {attachment_path}: {e}")

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
