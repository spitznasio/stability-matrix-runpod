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

# code-server on port 8080
# --auth none: RunPod network isolation handles access control
code-server \
    --bind-addr 0.0.0.0:8080 \
    --auth none \
    --disable-telemetry \
    /workspace &

# InvokeAI web server on port 9090
# INVOKEAI_ROOT env var (set in image) directs models/images to the volume disk
invokeai-web &

echo "Services started:"
echo "  code-server : http://0.0.0.0:8080"
echo "  InvokeAI    : http://0.0.0.0:9090"

# Keep container alive regardless of subprocess exit codes
sleep infinity
