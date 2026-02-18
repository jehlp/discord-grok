FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ make \
    default-jdk-headless \
    nodejs npm \
    zip unzip tar \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY grok/ ./grok/

CMD ["python", "-m", "grok"]
