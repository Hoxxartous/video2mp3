FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    ffmpeg \
    ca-certificates \
    curl \
    gnupg \
    lsb-release \
    dbus \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg | gpg --yes --dearmor -o /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] https://pkg.cloudflareclient.com/ bookworm main" > /etc/apt/sources.list.d/cloudflare-client.list \
    && apt-get update \
    && apt-get install -y cloudflare-warp \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /run/dbus

WORKDIR /app
RUN mkdir -p uploads converted templates

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod -R 777 uploads converted
RUN chmod +x start.sh

EXPOSE 10000
ENV PYTHONUNBUFFERED=1

CMD ["bash", "start.sh"]
