FROM python:3.11-slim

RUN apt-get update && \
    apt-get install -y ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN mkdir -p uploads converted templates static/css static/js

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod -R 777 uploads converted

EXPOSE 7860

CMD ["python", "app.py"]
