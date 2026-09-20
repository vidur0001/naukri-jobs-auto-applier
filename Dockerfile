FROM python:3.10-slim

# Install system dependencies needed for Playwright headless execution
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    xvfb \
    x11vnc \
    novnc \
    websockify \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PYTHONUNBUFFERED=1
ENV TZ=Asia/Kolkata

# Install Python requirements
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install --with-deps chromium

# Copy script files
COPY app.py bot.py mailer.py ai_helper.py config.json profile.json /app/

# Port configurations expose for email webhooks (5000) and the noVNC web
# viewer (6080) used to let a human solve Akamai's verification challenge
# remotely by watching/clicking the bot's live browser session.
EXPOSE 5000 6080

ENV DISPLAY=:99
CMD ["sh", "-c", "Xvfb :99 -screen 0 1280x800x24 -nolisten tcp & sleep 2; x11vnc -display :99 -forever -shared -nopw -rfbport 5900 -bg -o /tmp/x11vnc.log; websockify -D --web=/usr/share/novnc 6080 localhost:5900; exec python app.py"]
