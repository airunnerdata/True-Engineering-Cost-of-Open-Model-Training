#!/usr/bin/env bash
# setup-monitor.sh
# Installs Prometheus + Grafana on a t3.medium Ubuntu instance.
# Prometheus is pre-configured to scrape vLLM /metrics and DCGM :9400.
# Grafana is provisioned with the Test 1 dashboard and Prometheus datasource.
#
# Environment variables (injected by deploy-monitor.zsh):
#   ENDPOINT_IP  — public IP of the inference endpoint (default: 34.212.46.35)
#
# Can also be re-run manually:
#   ssh -i surf-crusoe.pem ubuntu@<monitor-ip>
#   ENDPOINT_IP=34.212.46.35 bash /tmp/setup-monitor.sh

set -euo pipefail
log() { echo "[$(date '+%H:%M:%S')] $*"; }

ENDPOINT_IP="${ENDPOINT_IP:-34.212.46.35}"
GRAFANA_ADMIN_PASSWORD="surfadmin"
PROM_VERSION="2.51.2"

# ── 1. System packages ────────────────────────────────────────────────────────
log "Updating packages..."
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    wget curl gnupg apt-transport-https software-properties-common 2>&1 | tail -3

# ── 2. Prometheus ─────────────────────────────────────────────────────────────
log "Installing Prometheus ${PROM_VERSION}..."
cd /tmp
wget -q "https://github.com/prometheus/prometheus/releases/download/v${PROM_VERSION}/prometheus-${PROM_VERSION}.linux-amd64.tar.gz" \
    -O prometheus.tar.gz
tar -xzf prometheus.tar.gz
sudo mv "prometheus-${PROM_VERSION}.linux-amd64/prometheus" /usr/local/bin/
sudo mv "prometheus-${PROM_VERSION}.linux-amd64/promtool"   /usr/local/bin/
rm -rf "prometheus-${PROM_VERSION}.linux-amd64" prometheus.tar.gz
sudo mkdir -p /etc/prometheus /var/lib/prometheus

sudo tee /etc/prometheus/prometheus.yml > /dev/null << PROM_EOF
global:
  scrape_interval:     15s
  evaluation_interval: 15s

scrape_configs:

  # ── vLLM inference endpoints ────────────────────────────────────────────────
  # "stack" label lets Grafana panels split by crusoe vs baseline.

  - job_name: vllm_endpoint
    metrics_path: /metrics
    static_configs:
      - targets: ['${ENDPOINT_IP}:8000']
        labels:
          stack: crusoe

  # Update BASELINE_IP_PLACEHOLDER after running deploy-baseline.zsh
  - job_name: vllm_baseline
    metrics_path: /metrics
    static_configs:
      - targets: ['BASELINE_IP_PLACEHOLDER:8000']
        labels:
          stack: baseline

  # ── DCGM GPU exporters (port 9400) ──────────────────────────────────────────

  - job_name: dcgm_endpoint
    static_configs:
      - targets: ['${ENDPOINT_IP}:9400']
        labels:
          stack: crusoe

  - job_name: dcgm_baseline
    static_configs:
      - targets: ['BASELINE_IP_PLACEHOLDER:9400']
        labels:
          stack: baseline

  # ── Prometheus self-monitoring ───────────────────────────────────────────────
  - job_name: prometheus
    static_configs:
      - targets: ['localhost:9090']
PROM_EOF

sudo tee /etc/systemd/system/prometheus.service > /dev/null << 'SVC_EOF'
[Unit]
Description=Prometheus
After=network-online.target
Wants=network-online.target

[Service]
User=ubuntu
ExecStart=/usr/local/bin/prometheus \
    --config.file=/etc/prometheus/prometheus.yml \
    --storage.tsdb.path=/var/lib/prometheus \
    --storage.tsdb.retention.time=30d \
    --web.listen-address=0.0.0.0:9090 \
    --web.enable-lifecycle
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
SVC_EOF

sudo chown -R ubuntu:ubuntu /var/lib/prometheus /etc/prometheus
sudo systemctl daemon-reload
sudo systemctl enable --now prometheus
log "  Prometheus running on :9090"

# ── 3. Grafana ────────────────────────────────────────────────────────────────
log "Installing Grafana (OSS)..."
wget -q -O - https://apt.grafana.com/gpg.key \
    | sudo gpg --dearmor -o /usr/share/keyrings/grafana.gpg
echo "deb [signed-by=/usr/share/keyrings/grafana.gpg] https://apt.grafana.com stable main" \
    | sudo tee /etc/apt/sources.list.d/grafana.list > /dev/null
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y grafana 2>&1 | tail -3

# ── 4. Grafana datasource provisioning ───────────────────────────────────────
sudo mkdir -p /etc/grafana/provisioning/datasources \
              /etc/grafana/provisioning/dashboards \
              /var/lib/grafana/dashboards

sudo tee /etc/grafana/provisioning/datasources/prometheus.yml > /dev/null << 'DS_EOF'
apiVersion: 1
datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://localhost:9090
    isDefault: true
    editable: false
    jsonData:
      timeInterval: "15s"
DS_EOF

# ── 5. Grafana dashboard provisioning ─────────────────────────────────────────
sudo tee /etc/grafana/provisioning/dashboards/surf.yml > /dev/null << 'DB_EOF'
apiVersion: 1
providers:
  - name: surf-report
    type: file
    disableDeletion: false
    updateIntervalSeconds: 30
    options:
      path: /var/lib/grafana/dashboards
DB_EOF

# Copy dashboard JSON (uploaded by deploy-monitor.zsh)
if [[ -f /tmp/dashboard_test01.json ]]; then
    sudo cp /tmp/dashboard_test01.json /var/lib/grafana/dashboards/test01.json
    sudo chown grafana:grafana /var/lib/grafana/dashboards/test01.json
    log "  Dashboard JSON installed."
else
    log "  WARNING: /tmp/dashboard_test01.json not found — upload it manually."
fi

# ── 6. Grafana admin password ─────────────────────────────────────────────────
# grafana.ini uses semicolons for commented defaults; handle both forms.
sudo sed -i \
    "s/^;*\s*admin_password\s*=.*/admin_password = ${GRAFANA_ADMIN_PASSWORD}/" \
    /etc/grafana/grafana.ini

sudo systemctl enable --now grafana-server
log "  Grafana running on :3000  (admin / ${GRAFANA_ADMIN_PASSWORD})"

# ── 7. Smoke test ─────────────────────────────────────────────────────────────
sleep 5
if curl -sf http://localhost:9090/-/healthy > /dev/null; then
    log "  Prometheus healthy."
fi
if curl -sf http://localhost:3000/api/health > /dev/null; then
    log "  Grafana healthy."
fi

log "Setup complete."
log ""
log "  Grafana    → http://$(curl -sf https://checkip.amazonaws.com):3000  (admin / ${GRAFANA_ADMIN_PASSWORD})"
log "  Prometheus → http://$(curl -sf https://checkip.amazonaws.com):9090"
log ""
log "  After deploying surf-baseline, edit /etc/prometheus/prometheus.yml"
log "  and replace BASELINE_IP_PLACEHOLDER, then: sudo systemctl reload prometheus"
