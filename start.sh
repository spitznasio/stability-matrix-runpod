#!/bin/bash

mkdir -p /workspace

# Virtual framebuffer — Stability Matrix (Avalonia/.NET desktop app) requires a display
Xvfb :1 -screen 0 1280x800x24 -nolisten tcp &
sleep 2  # wait for Xvfb to be ready
export DISPLAY=:1

# VNC server pointed at the virtual display (no password, localhost only)
x11vnc -display :1 -nopw -listen localhost -xkb -forever -quiet &

# noVNC WebSocket proxy on port 6006
# Exposes the Stability Matrix desktop GUI as a browser-accessible URL:
#   https://<pod-id>-6006.proxy.runpod.net/vnc.html
websockify --web /usr/share/novnc 6006 localhost:5900 &

# code-server on port 8080
# --auth none: RunPod network isolation handles access control
# To activate a Stability Matrix-managed Python venv in the terminal:
#   source /workspace/StabilityMatrix/Data/venv/<env-name>/bin/activate
code-server \
    --bind-addr 0.0.0.0:8080 \
    --auth none \
    --disable-telemetry \
    /workspace &

# Stability Matrix in portable mode
# --appimage-extract-and-run: avoids FUSE entirely, works as root in Docker
cd /workspace
/opt/StabilityMatrix --appimage-extract-and-run &

echo "Services started:"
echo "  code-server   : http://0.0.0.0:8080"
echo "  Stability Matrix (noVNC) : http://0.0.0.0:6006/vnc.html"
echo "  ComfyUI       : http://0.0.0.0:8188  (after install in SM)"
echo "  A1111/Forge   : http://0.0.0.0:7860  (after install in SM)"

# Keep container alive regardless of subprocess exit codes
sleep infinity
