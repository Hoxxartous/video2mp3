#!/bin/bash
echo "🔧 Starting Cloudflare WARP..."

warp-svc &
sleep 3

warp-cli registration new 2>/dev/null || true
sleep 1

warp-cli mode proxy 2>/dev/null || true
sleep 1

warp-cli connect 2>/dev/null || true
sleep 2

echo "🌐 WARP Status:"
warp-cli status 2>/dev/null || echo "WARP not ready yet (will retry)"

echo "🎵 Starting Video2MP3 Pro..."
python -u app.py
