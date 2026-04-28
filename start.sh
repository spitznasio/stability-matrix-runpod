#!/bin/bash

mkdir -p /workspace

# Virtual framebuffer — Stability Matrix (Avalonia UI) requires a display
Xvfb :1 -screen 0 1280x800x24 -nolisten tcp &
sleep 2  # wait for Xvfb to be ready before any X11 clients connect
export DISPLAY=:1

# code-server on port 8080
# --no-sandbox: required when running as root in Docker (IMPORTANT.md §4)
# --auth none: RunPod network isolation handles access control
# To activate a Stability Matrix-managed Python venv in the terminal:
#   source /workspace/StabilityMatrix/Data/venv/<env-name>/bin/activate
code-server \
    --bind-addr 0.0.0.0:8080 \
    --auth none \
    --disable-telemetry \
    /workspace &

# Stability Matrix in portable mode
# --appimage-extract-and-run: extracts AppImage to a temp dir and runs directly,
# avoiding FUSE entirely — works as root in Docker without sandbox flags
cd /workspace
/opt/StabilityMatrix --appimage-extract-and-run &

echo "Services started:"
echo "  code-server : http://0.0.0.0:8080"
echo "  Stability Matrix mgmt : http://0.0.0.0:6006"
echo "  ComfyUI     : http://0.0.0.0:8188  (after install)"
echo "  A1111/Forge : http://0.0.0.0:7860  (after install)"

# Keep container alive regardless of subprocess exit codes.
# Do NOT use 'set -e' or 'wait' here — if SM crashes, code-server must
# remain accessible for debugging.
sleep infinity
