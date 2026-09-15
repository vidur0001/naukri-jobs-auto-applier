FROM python:3.10-slim

# Install system dependencies needed for Playwright headless execution
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    libglib2.0-0 \
    libnss3 \
    libatk-1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python requirements
RUN pip install playwright flask jinja2
RUN playwright install chromium

# Copy script files
COPY app.py bot.py /app/

# Port configurations expose for email webhooks
EXPOSE 5000

CMD ["python", "app.py"]
