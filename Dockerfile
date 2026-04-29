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

# Install InvokeAI alongside explicit cu130 torch pins in a single resolver pass.
# Listing torch/torchvision/torchaudio with the +cu130 local version label forces
# pip to pull from the PyTorch wheel index rather than PyPI — PyPI never carries
# local-label builds. Because it's one solve, InvokeAI's torch dependency is
# satisfied by the cu130 wheels directly and they are never replaced, avoiding the
# extra ~3 GB layer that a post-hoc --force-reinstall would create.
RUN pip install --no-cache-dir \
    "torch==2.9.1+cu130" \
    "torchvision==0.24.1+cu130" \
    "torchaudio==2.9.1+cu130" \
    invokeai \
    --extra-index-url https://download.pytorch.org/whl/cu130

RUN mkdir -p /workspace

ENV INVOKEAI_ROOT=/workspace/invokeai
ENV INVOKEAI_HOST=0.0.0.0
ENV INVOKEAI_PORT=9090
ENV PYTORCH_CUDA_ALLOC_CONF=backend:cudaMallocAsync
ENV CUDA_CACHE_MAXSIZE=4294967296
ENV SD_USE_FP4=1
ENV CUDA_MODULE_LOADING=LAZY
ENV HF_HUB_ENABLE_HF_TRANSFER=1
ENV PATH="/root/.local/bin:${PATH}"

EXPOSE 8080 9090

COPY start.sh /start.sh
RUN chmod +x /start.sh

ENTRYPOINT ["/start.sh"]
