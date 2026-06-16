FROM python:3.11-slim

RUN apt-get update && \
    apt-get install -y ffmpeg wget curl ca-certificates openssl && \
    update-ca-certificates && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade yt-dlp

WORKDIR /app
RUN mkdir -p uploads converted templates static/css static/js

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod -R 777 uploads converted

ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

EXPOSE 10000
CMD ["python", "app.py"]
