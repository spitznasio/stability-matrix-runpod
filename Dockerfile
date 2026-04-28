FROM runpod/pytorch:1.0.3-cu1300-torch291-ubuntu2404

# Install OS-level dependencies for Stability Matrix AppImage + GUI on Ubuntu 24.04
# libfuse2t64: Ubuntu 24.04 renamed libfuse2 (64-bit time_t transition) — required for AppImage
# libgl1 + libglx0: replaces dummy libgl1-mesa-glx on 24.04
# libdecor-0-0 + libatk-bridge2.0-0 + libglib2.0-0: required for Avalonia UI framework
RUN apt-get update && apt-get install -y --no-install-recommends \
    libfuse2t64 \
    libgl1 \
    libglx0 \
    libegl1 \
    libx11-6 \
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libasound2t64 \
    libglib2.0-0 \
    libdecor-0-0 \
    aria2 \
    wget \
    curl \
    git \
    xvfb \
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

# Download and install Stability Matrix
RUN wget -q -O /tmp/SM.zip \
    "https://github.com/LykosAI/StabilityMatrix/releases/latest/download/StabilityMatrix-linux-x64.zip" \
    && unzip /tmp/SM.zip -d /tmp/SM_extract \
    && cp /tmp/SM_extract/StabilityMatrix-linux-x64 /opt/StabilityMatrix \
    && chmod +x /opt/StabilityMatrix \
    && rm -rf /tmp/SM.zip /tmp/SM_extract

# Workspace directory for portable mode (populated by Volume Disk at runtime)
RUN mkdir -p /workspace

# Blackwell / VRAM / portable-mode environment
ENV SM_PORTABLE=1
ENV PYTORCH_CUDA_ALLOC_CONF=backend:cudaMallocAsync
ENV CUDA_CACHE_MAXSIZE=4294967296
ENV SD_USE_FP4=1
ENV CUDA_MODULE_LOADING=LAZY
ENV HF_HUB_ENABLE_HF_TRANSFER=1
# HF CLI installs to ~/.local/bin; ensure it is on PATH
ENV PATH="/root/.local/bin:${PATH}"

EXPOSE 6006 7860 8080 8188

COPY start.sh /start.sh
RUN chmod +x /start.sh

ENTRYPOINT ["/start.sh"]
