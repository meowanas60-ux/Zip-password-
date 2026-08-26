FROM python:3.11-slim

# System dependencies (john, zip, unzip) install
RUN apt-get update && apt-get install -y \
    john \
    zip \
    unzip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Requirements install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Run bot
CMD ["python", "bot.py"]
