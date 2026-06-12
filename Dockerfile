FROM python:3.11-slim

# Install system packages
RUN apt-get update && \
    apt-get install -y ffmpeg wget curl && \
    rm -rf /var/lib/apt/lists/*

# Install latest yt-dlp directly from GitHub
RUN wget https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -O /usr/local/bin/yt-dlp && \
    chmod a+rx /usr/local/bin/yt-dlp

WORKDIR /app

RUN mkdir -p uploads converted templates static/css static/js

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod -R 777 uploads converted

EXPOSE 7860

CMD ["python", "app.py"]
