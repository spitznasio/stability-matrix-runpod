FROM runpod/pytorch:1.0.3-cu1300-torch291-ubuntu2404

# InvokeAI + tooling dependencies for Ubuntu 24.04
# libgl1 + libglib2.0-0: required by InvokeAI (verified from official Dockerfile)
# libegl1 + libglx0: GPU rendering paths
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglx0 \
    libegl1 \
    libglib2.0-0 \
    aria2 \
    wget \
    curl \
    git \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Install code-server
RUN curl -fsSL https://code-server.dev/install.sh | sh

# Install AWS CLI v2
RUN curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip \
    && unzip /tmp/awscliv2.zip -d /tmp/aws-install \
    && /tmp/aws-install/aws/install \
    && rm -rf /tmp/awscliv2.zip /tmp/aws-install

# Install HuggingFace CLI
RUN curl -LsSf https://hf.co/cli/install.sh | bash

# CivitAI Manager web app dependencies — installed before InvokeAI so its
# resolver pass for invokeai==${INVOKEAI_VERSION} below is unaffected by these.
RUN pip install --no-cache-dir \
    fastapi \
    "uvicorn[standard]" \
    jinja2 \
    httpx \
    python-multipart \
    itsdangerous

# Pin InvokeAI so builds are reproducible unless this arg is intentionally bumped.
ARG INVOKEAI_VERSION=6.13.0

# Install InvokeAI first so its dependency resolver picks a compatible torch version,
# then force-reinstall cu130 wheels to ensure Blackwell (sm_120) GPU support.
RUN pip install --no-cache-dir "invokeai==${INVOKEAI_VERSION}"

RUN pip install --no-cache-dir --force-reinstall \
    "torch==2.9.1+cu130" \
    "torchvision==0.24.1+cu130" \
    "torchaudio==2.9.1+cu130" \
    --extra-index-url https://download.pytorch.org/whl/cu130

RUN mkdir -p /workspace

COPY download_from_civitai.py /workspace/download_from_civitai.py
COPY download_from_s3_skip_existing.py /workspace/download_from_s3_skip_existing.py
COPY upload_images_to_s3.py /workspace/upload_images_to_s3.py
COPY upload_models_to_s3.py /workspace/upload_models_to_s3.py
COPY restart_invokeai.sh /workspace/restart_invokeai.sh
RUN chmod +x /workspace/restart_invokeai.sh
# Installed outside /workspace because /workspace is overlaid by the RunPod
# volume disk at runtime, which would hide app code baked in here.
COPY civitai_manager /opt/civitai_manager

ENV INVOKEAI_ROOT=/workspace/invokeai
ENV INVOKEAI_HOST=0.0.0.0
ENV INVOKEAI_PORT=9090
ENV PYTORCH_CUDA_ALLOC_CONF=backend:cudaMallocAsync
ENV CUDA_CACHE_MAXSIZE=4294967296
ENV SD_USE_FP4=1
ENV CUDA_MODULE_LOADING=LAZY
ENV HF_HUB_ENABLE_HF_TRANSFER=1
ENV PATH="/root/.local/bin:${PATH}"

EXPOSE 8080 8000 9090

COPY start.sh /start.sh
RUN chmod +x /start.sh

ENTRYPOINT ["/start.sh"]
