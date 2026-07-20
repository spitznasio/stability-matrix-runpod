#!/bin/bash

mkdir -p /workspace/invokeai
mkdir -p /workspace/civitai-downloads

# Full SSH (public IP, key auth, SCP/SFTP-capable) per RunPod's docs:
# https://docs.runpod.io/pods/configuration/use-ssh#full-ssh-via-public-ip-with-key-authentication
# RunPod injects the account's SSH public key(s) into $PUBLIC_KEY — sshd
# itself isn't started by our custom ENTRYPOINT (it replaces whatever the
# base image's own entrypoint would have done), so it has to be started here.
# No-op if $PUBLIC_KEY is unset (e.g. local/non-RunPod runs).
if [ -n "$PUBLIC_KEY" ]; then
    mkdir -p ~/.ssh
    chmod 700 ~/.ssh
    echo "$PUBLIC_KEY" >> ~/.ssh/authorized_keys
    chmod 600 ~/.ssh/authorized_keys
    # sshd refuses to start without this ("Missing privilege separation
    # directory") — /var/run is often not persisted into the built image layer.
    mkdir -p /var/run/sshd
    service ssh start
fi

# Secret shared between the aria2 RPC daemon and CivitAI Manager (the only
# client that talks to it). Generated per boot if not set as a RunPod env
# var; exported before any supervised process starts so both the aria2c
# process and civitai-manager inherit the same value via os.environ.
export ARIA2_RPC_SECRET="${ARIA2_RPC_SECRET:-$(python3 -c 'import secrets; print(secrets.token_hex(32))')}"

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

# code-server (8080), InvokeAI (9090), CivitAI Manager (8000), and OneDrive
# Sync Manager (8002) are started and supervised by the Server Admin app's
# process supervisor, so they get PID tracking, log capture, and
# start/stop/restart control from its UI.
# --auth none for code-server: RunPod network isolation handles access control.
python3 -m server_admin.supervisor start code-server
python3 -m server_admin.supervisor start invokeai
python3 -m server_admin.supervisor start aria2-rpc
python3 -m server_admin.supervisor start civitai-manager
python3 -m server_admin.supervisor start onedrive-sync

# Server Admin web app on port 8001
# Set SERVER_ADMIN_USERNAME / SERVER_ADMIN_PASSWORD as RunPod env vars to
# require login (a real login page + session cookie); if either is unset, the
# UI is unprotected. Unlike CivitAI Manager, this app can stop/start
# services, so leaving it open is higher risk.
uvicorn server_admin.main:app \
    --app-dir /opt \
    --host 0.0.0.0 \
    --port 8001 &

# OneDrive Sync Manager web app on port 8002, started above via the Server
# Admin supervisor (key: onedrive-sync) for PID tracking, log capture, and
# start/stop/restart control from its UI.
# Local auth is mandatory for this service. Set these RunPod env vars:
# - ONEDRIVE_MANAGER_USERNAME
# - ONEDRIVE_MANAGER_PASSWORD_HASH
# - ONEDRIVE_MANAGER_SESSION_SECRET (recommended, otherwise generated on boot)

echo "Services started:"
echo "  code-server     : http://0.0.0.0:8080"
echo "  CivitAI Manager : http://0.0.0.0:8000"
echo "  InvokeAI        : http://0.0.0.0:9090"
echo "  Server Admin    : http://0.0.0.0:8001"
echo "  OneDrive Sync   : http://0.0.0.0:8002"

# Keep container alive regardless of subprocess exit codes
sleep infinity
