#!/usr/bin/env bash
# setup-dcgm.sh
# Installs the NVIDIA DCGM Exporter on a GPU node so Prometheus can
# scrape GPU utilization, memory, and power metrics on port 9400.
#
# Assumes:
#   - Ubuntu 22.04 or 24.04
#   - NVIDIA drivers already installed (nvidia-smi works)
#   - Run as ubuntu (sudo available)
#
# Called by deploy-monitor.zsh on surf-endpoint.
# Run manually on any additional GPU node (e.g. surf-baseline replicas):
#   scp -i surf-crusoe.pem setup-dcgm.sh ubuntu@<gpu-ip>:/tmp/
#   ssh -i surf-crusoe.pem ubuntu@<gpu-ip> "bash /tmp/setup-dcgm.sh"
#
# Exposes metrics at http://<gpu-ip>:9400/metrics

set -euo pipefail
log() { echo "[$(date '+%H:%M:%S')] $*"; }

DCGM_EXPORTER_VERSION="3.3.6-3.4.2"

# ── 1. Disk space check ───────────────────────────────────────────────────────
# The DCGM exporter Docker image is ~5 GB. The root disk on GPU nodes is often
# only 8 GB. If /data is mounted (NVMe SSD via setup-baseline.sh), we redirect
# Docker's data-root there before pulling the image.
ROOT_FREE_GB=$(df -BG / | awk 'NR==2{gsub("G",""); print $4}')
log "Root disk free: ${ROOT_FREE_GB} GB"

if mountpoint -q /data 2>/dev/null; then
    log "/data is mounted — redirecting Docker data-root to /data/docker"
    sudo mkdir -p /data/docker
    sudo mkdir -p /etc/docker
    echo '{"data-root": "/data/docker"}' | sudo tee /etc/docker/daemon.json > /dev/null
    DOCKER_CONFIGURED=1
elif (( ROOT_FREE_GB < 8 )); then
    echo "ERROR: Root disk has only ${ROOT_FREE_GB} GB free and /data is not mounted."
    echo "       The DCGM image requires ~5 GB. Options:"
    echo "         1. Mount a larger volume to /data first (see setup-baseline.sh)"
    echo "         2. Expand the root EBS volume in the AWS console"
    exit 1
else
    log "Using default Docker data-root (root disk, ${ROOT_FREE_GB} GB free)."
    DOCKER_CONFIGURED=0
fi

# ── 2. Verify GPU is present ──────────────────────────────────────────────────
if ! nvidia-smi &>/dev/null; then
    # Try loading the kernel module (works on fresh instances without a reboot)
    sudo modprobe nvidia 2>/dev/null || true
    nvidia-smi &>/dev/null || { echo "ERROR: nvidia-smi not found. Install CUDA drivers first."; exit 1; }
fi
log "GPU detected: $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader)"

# ── 2. Install Docker ─────────────────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
    log "Installing Docker..."
    curl -fsSL https://get.docker.com | sudo sh
    sudo usermod -aG docker ubuntu
    log "  Docker installed."
else
    log "  Docker already installed."
fi

# Apply data-root config (written before Docker was started, or restart now)
if [[ ${DOCKER_CONFIGURED:-0} == 1 ]]; then
    log "  Restarting Docker to apply /data/docker data-root..."
    sudo systemctl restart docker
fi

# ── 3. Install NVIDIA Container Toolkit ───────────────────────────────────────
# Required so Docker containers can access the GPU.
if ! dpkg -l | grep -q nvidia-container-toolkit; then
    log "Installing nvidia-container-toolkit..."
    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
        | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
    curl -sL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
        | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
        | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list > /dev/null
    sudo apt-get update -qq
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y nvidia-container-toolkit 2>&1 | tail -3
    sudo nvidia-ctk runtime configure --runtime=docker
    sudo systemctl restart docker
    log "  nvidia-container-toolkit installed."
else
    log "  nvidia-container-toolkit already installed."
fi

# ── 4. Pull and start DCGM Exporter container ─────────────────────────────────
DCGM_IMAGE="nvcr.io/nvidia/k8s/dcgm-exporter:${DCGM_EXPORTER_VERSION}-ubuntu22.04"

log "Pulling DCGM Exporter image (${DCGM_EXPORTER_VERSION})..."
sudo docker pull "$DCGM_IMAGE"

# Stop existing container if present
sudo docker rm -f dcgm-exporter 2>/dev/null && log "  Removed existing dcgm-exporter container." || true

log "Starting DCGM Exporter on port 9400..."
sudo docker run -d \
    --restart unless-stopped \
    --name dcgm-exporter \
    --gpus all \
    --cap-add SYS_ADMIN \
    -p 9400:9400 \
    "$DCGM_IMAGE"

# ── 5. Smoke test ─────────────────────────────────────────────────────────────
log "Waiting for DCGM Exporter to start (30s)..."
sleep 30
if curl -sf http://localhost:9400/metrics | grep -q "DCGM_FI_DEV_GPU_UTIL"; then
    log "  DCGM Exporter healthy — GPU metrics are being exported."
else
    log "  WARNING: DCGM Exporter may not be ready yet. Check:"
    log "    sudo docker logs dcgm-exporter"
fi

log ""
log "Key metrics available at http://localhost:9400/metrics:"
log "  DCGM_FI_DEV_GPU_UTIL        — GPU utilization (%)"
log "  DCGM_FI_DEV_FB_USED         — GPU memory used (MiB)"
log "  DCGM_FI_DEV_FB_FREE         — GPU memory free (MiB)"
log "  DCGM_FI_DEV_POWER_USAGE     — GPU power draw (W)"
log "  DCGM_FI_PROF_PIPE_TENSOR_ACTIVE — Tensor core utilization"
log ""
log "DCGM Exporter setup complete."
