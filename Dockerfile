FROM python:3.11-slim

# Install system packages + SSL certificates
RUN apt-get update && \
    apt-get install -y ffmpeg wget curl ca-certificates openssl && \
    update-ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Install latest yt-dlp
RUN wget https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -O /usr/local/bin/yt-dlp && \
    chmod a+rx /usr/local/bin/yt-dlp

# Install Python SSL packages
RUN pip install --no-cache-dir certifi urllib3 requests

WORKDIR /app
RUN mkdir -p uploads converted templates static/css static/js

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod -R 777 uploads converted

ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

EXPOSE 7860

CMD ["python", "app.py"]
