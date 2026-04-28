#!/bin/bash

mkdir -p /workspace/invokeai

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
