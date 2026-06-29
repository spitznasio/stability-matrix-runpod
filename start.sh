#!/bin/bash

mkdir -p /workspace/invokeai

# Inject CivitAI API token into InvokeAI config before the server starts.
# Set CIVITAI_API_TOKEN as a RunPod environment variable — never hardcode it.
if [ -n "$CIVITAI_API_TOKEN" ]; then
    python3 - <<PYEOF
import yaml, os, sys

config_path = "/workspace/invokeai/invokeai.yaml"
token = os.environ["CIVITAI_API_TOKEN"]

try:
    with open(config_path) as f:
        config = yaml.safe_load(f) or {}
except FileNotFoundError:
    config = {}

schema_version = config.pop("schema_version", "4.0.2")
config["remote_api_tokens"] = [
    {"url_regex": "civitai.com", "token": token},
    {"url_regex": "civitai.red", "token": token},
]

with open(config_path, "w") as f:
    f.write("# Internal metadata - do not edit:\n")
    f.write(f"schema_version: {schema_version}\n\n")
    f.write("# Put user settings here - see https://invoke-ai.github.io/InvokeAI/configuration/:\n")
    yaml.dump(config, f, default_flow_style=False, sort_keys=False)
PYEOF
fi

# code-server (8080), InvokeAI (9090), and CivitAI Manager (8000) are started
# and supervised by the Server Admin app's process supervisor, so they get
# PID tracking, log capture, and start/stop/restart control from its UI.
# --auth none for code-server: RunPod network isolation handles access control.
python3 -m server_admin.supervisor start code-server
python3 -m server_admin.supervisor start invokeai
python3 -m server_admin.supervisor start civitai-manager

# Server Admin web app on port 8001
# Set SERVER_ADMIN_USERNAME / SERVER_ADMIN_PASSWORD as RunPod env vars to
# require login (a real login page + session cookie); if either is unset, the
# UI is unprotected. Unlike CivitAI Manager, this app can stop/start
# services, so leaving it open is higher risk.
uvicorn server_admin.main:app \
    --app-dir /opt \
    --host 0.0.0.0 \
    --port 8001 &

# OneDrive Sync Manager web app on port 8002.
# Local auth is mandatory for this service. Set these RunPod env vars:
# - ONEDRIVE_MANAGER_USERNAME
# - ONEDRIVE_MANAGER_PASSWORD_HASH
# - ONEDRIVE_MANAGER_SESSION_SECRET (recommended, otherwise generated on boot)
uvicorn onedrive_sync_manager.main:app \
    --app-dir /opt \
    --host 0.0.0.0 \
    --port 8002 &

echo "Services started:"
echo "  code-server     : http://0.0.0.0:8080"
echo "  CivitAI Manager : http://0.0.0.0:8000"
echo "  InvokeAI        : http://0.0.0.0:9090"
echo "  Server Admin    : http://0.0.0.0:8001"
echo "  OneDrive Sync   : http://0.0.0.0:8002"

# Keep container alive regardless of subprocess exit codes
sleep infinity
