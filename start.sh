#!/bin/bash
set -e

echo "=========================================="
echo "  🎵 Video2MP3 Pro - Starting up..."
echo "=========================================="

# Step 1: Start D-Bus (WARP NEEDS this - was missing before!)
echo "[1/5] Starting D-Bus..."
mkdir -p /run/dbus
rm -f /run/dbus/pid
dbus-daemon --system --fork 2>/dev/null || dbus-daemon --config-file=/usr/share/dbus-1/system.conf --fork 2>/dev/null || echo "D-Bus failed"
sleep 1

# Step 2: Start WARP service
echo "[2/5] Starting WARP service..."
warp-svc --accept-tos &
sleep 4

# Step 3: Register
echo "[3/5] Registering WARP..."
warp-cli --accept-tos registration new 2>/dev/null || echo "Already registered"
sleep 1

# Step 4: Set proxy mode
echo "[4/5] Setting proxy mode..."
warp-cli --accept-tos mode proxy 2>/dev/null || true
sleep 1

# Step 5: Connect
echo "[5/5] Connecting WARP..."
warp-cli --accept-tos connect 2>/dev/null || true
sleep 3

echo ""
echo "WARP Status:"
warp-cli --accept-tos status 2>/dev/null || echo "Status check failed"
echo ""

echo "🎵 Starting server..."
python -u app.py
