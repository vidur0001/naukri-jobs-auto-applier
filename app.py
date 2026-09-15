import os
import json
import subprocess
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from flask import Flask, request, jsonify, render_template_string

app = Flask(__name__)

# System Configurations
CONFIG_PATH = "/app/config.json"
DEFAULT_PROFILE = {
    "target_roles": ["Software Engineer", "Associate Software Engineer", "Backend Developer", "SRE Engineer", "DevOps Engineer"],
    "filters": {
        "experience_years": "0",
        "locations": ["bangalore", "pune", "hyderabad", "mumbai", "gurgaon", "noida", "delhi", "chandigarh", "chennai"]
    },
    "questionnaire_answers": {
        "current_ctc": "7,50,000 INR",
        "expected_ctc": "6,00,000 INR",
        "notice_period": "Immediate",
        "skills": "Python, Java, Rust, C, SQL, REST APIs, Docker, AWS (EC2/S3), Git, Log Aggregation, Telemetry, Lustre Filesystem, HPC, Linux Admin, Diagnostics, Databases, PostgreSQL, MongoDB"
    },
    "blacklist_companies": ["Generic Placement Agency", "Third Party Consultants"],
    "email_target": "vidursharma8035@gmail.com"
}

# Ensure persistent configurations state
if not os.path.exists(CONFIG_PATH):
    with open(CONFIG_PATH, "w") as f:
        json.dump(DEFAULT_PROFILE, f, indent=4)

def load_config():
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)

def save_config(data):
    with open(CONFIG_PATH, "w") as f:
        json.dump(data, f, indent=4)

# Webhook configuration for approval emails
EMAIL_HTML_TEMPLATE = """
<html>
<body style="font-family: Arial, sans-serif; color: #333; line-height: 1.6;">
    <h2 style="color: #4CAF50;">📋 Naukri Auto-Apply Monitoring Check</h2>
    <p>Hello Vidur, your monitoring system is prepared to run automatic applications with the following criteria:</p>
    <table style="border-collapse: collapse; width: 100%; max-width: 600px;">
        <tr style="background-color: #f2f2f2;"><th style="padding: 8px; border: 1px solid #ddd; text-align: left;">Detail Name</th><th style="padding: 8px; border: 1px solid #ddd; text-align: left;">Value</th></tr>
        <tr><td style="padding: 8px; border: 1px solid #ddd;"><b>Target Roles</b></td><td style="padding: 8px; border: 1px solid #ddd;">{{ roles }}</td></tr>
        <tr><td style="padding: 8px; border: 1px solid #ddd;"><b>Locations</b></td><td style="padding: 8px; border: 1px solid #ddd;">{{ locations }}</td></tr>
        <tr><td style="padding: 8px; border: 1px solid #ddd;"><b>Current / Expected CTC</b></td><td style="padding: 8px; border: 1px solid #ddd;">{{ current_ctc }} / {{ expected_ctc }}</td></tr>
        <tr><td style="padding: 8px; border: 1px solid #ddd;"><b>Core Technical Stack</b></td><td style="padding: 8px; border: 1px solid #ddd;">{{ skills }}</td></tr>
    </table>
    <br/>
    <div style="margin-top: 15px;">
        <a href="http://localhost:5000/approve" style="background-color: #4CAF50; color: white; padding: 10px 20px; text-decoration: none; border-radius: 4px; font-weight: bold; margin-right: 10px;">🚀 Approve & Run Now</a>
        <a href="http://localhost:5000/edit" style="background-color: #2196F3; color: white; padding: 10px 20px; text-decoration: none; border-radius: 4px; font-weight: bold;">📝 Edit Configuration Details</a>
    </div>
</body>
</html>
"""

def send_approval_email(config):
    # Setup SMTP configurations using your server options (Environment Variables recommended)
    smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", 587))
    sender_email = os.getenv("SENDER_EMAIL", "your-bot-email@gmail.com")
    sender_password = os.getenv("SENDER_PASSWORD", "your-app-password")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "🚀 Action Required: Approve Auto-Apply Batch Execution"
    msg["From"] = sender_email
    msg["To"] = config["email_target"]

    # Render template string manually for formatting efficiency
    from jinja2 import Template
    html_body = Template(EMAIL_HTML_TEMPLATE).render(
        roles=", ".join(config["target_roles"]),
        locations=", ".join(config["filters"]["locations"]),
        current_ctc=config["questionnaire_answers"]["current_ctc"],
        expected_ctc=config["questionnaire_answers"]["expected_ctc"],
        skills=config["questionnaire_answers"]["skills"]
    )
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, config["email_target"], msg.as_string())
        print("[+] Monitoring email sent successfully to Vidur.")
    except Exception as e:
        print(f"[X] Failed to send email update notification: {e}")

@app.route("/")
def index():
    config = load_config()
    send_approval_email(config)
    return "<h3>Email notification sent to vidursharma8035@gmail.com. Monitor active.</h3>"

@app.route("/approve")
def approve_and_run():
    print("[+] Approval webhook received. Initializing Playwright pipeline workflow...")
    # Fire off bot.py execution async or via subprocess safely
    subprocess.Popen(["python", "/app/bot.py"])
    return "<h2>Application runner approved! Background container processing launched successfully.</h2>"

@app.route("/edit", methods=["GET", "POST"])
def edit_config():
    config = load_config()
    if request.method == "POST":
        config["questionnaire_answers"]["expected_ctc"] = request.form.get("expected_ctc")
        config["questionnaire_answers"]["skills"] = request.form.get("skills")
        save_config(config)
        return "<h2>Configuration updated! Close this window and return to your application workflow.</h2>"
    
    # Quick inline form rendering for manual data updating overrides
    return render_template_string("""
        <h3>Edit Form-Filling Criteria</h3>
        <form method="POST">
            Expected CTC: <input type="text" name="expected_ctc" value="{{ ctc }}"><br><br>
            Skills Stack: <textarea name="skills" rows="4" cols="50">{{ skills }}</textarea><br><br>
            <input type="submit" value="Save Changes & Update Batch">
        </form>
    """, ctc=config["questionnaire_answers"]["expected_ctc"], skills=config["questionnaire_answers"]["skills"])

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
