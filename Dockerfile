FROM python:3.10-slim

# Install system dependencies needed for Playwright headless execution
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    xvfb \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PYTHONUNBUFFERED=1
ENV TZ=Asia/Kolkata

# Install Python requirements
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install --with-deps chromium

# Copy script files
COPY app.py bot.py mailer.py /app/

# Port configurations expose for email webhooks
EXPOSE 5000

ENV DISPLAY=:99
CMD ["sh", "-c", "Xvfb :99 -screen 0 1280x800x24 -nolisten tcp & sleep 2; exec python app.py"]
