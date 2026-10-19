#!/usr/bin/env zsh
# deploy-monitor.zsh
# Launches a t3.medium EC2 instance running Prometheus + Grafana.
# Also installs the DCGM exporter on the surf-endpoint GPU node so GPU
# metrics flow into Prometheus from day one.
#
# What it does:
#   1. Launches t3.medium in the same VPC/subnet as surf-endpoint
#   2. Creates a dedicated SG: ports 22 / 3000 (Grafana) / 9090 (Prometheus)
#      opened to your current public IP
#   3. Opens port 9400 (DCGM) on the GPU node SG from the monitor's IP
#   4. SCPs setup-monitor.sh + dashboard JSON and runs them on the monitor
#   5. SCPs setup-dcgm.sh and runs it on surf-endpoint
#
# Usage:
#   cd scripts/infra
#   ./deploy-monitor.zsh

set -euo pipefail

REGION="us-west-2"
ENDPOINT_INSTANCE_ID="i-04b7898eca5338aed"   # surf-endpoint (Crusoe stand-in)
ENDPOINT_IP="34.212.46.35"
GPU_SG_ID="sg-9f7ee5fa"                       # existing SG on GPU instances
MONITOR_NAME="surf-monitor"
INSTANCE_TYPE="t3.medium"
KEY_NAME="surf-crusoe"
SCRIPT_DIR="${0:a:h}"
PEM="${SCRIPT_DIR}/../../surf-crusoe.pem"   # bin/surf-crusoe.pem
MONITOR_ID_FILE="${SCRIPT_DIR}/.monitor-instance-id"

log()   { print "[deploy-monitor] $*" }
abort() { print "[deploy-monitor] ERROR: $*" >&2; exit 1 }

[[ -f $PEM ]] || abort "PEM file not found: $PEM"

# ── Resolve AMI + subnet from surf-endpoint ───────────────────────────────────
log "Looking up AMI and subnet from surf-endpoint ${ENDPOINT_INSTANCE_ID}..."
read AMI SUBNET_ID <<< $(aws ec2 describe-instances \
    --region $REGION \
    --instance-ids $ENDPOINT_INSTANCE_ID \
    --query 'Reservations[0].Instances[0].[ImageId, SubnetId]' \
    --output text)
log "  AMI:    $AMI"
log "  Subnet: $SUBNET_ID"

VPC_ID=$(aws ec2 describe-subnets \
    --region $REGION \
    --subnet-ids $SUBNET_ID \
    --query 'Subnets[0].VpcId' --output text)
log "  VPC:    $VPC_ID"

# ── Create (or reuse) monitor security group ──────────────────────────────────
log "Resolving security group for monitor..."
MONITOR_SG_ID=$(aws ec2 describe-security-groups \
    --region $REGION \
    --filters "Name=group-name,Values=surf-monitor-sg" "Name=vpc-id,Values=${VPC_ID}" \
    --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || true)

if [[ $MONITOR_SG_ID == "None" || -z $MONITOR_SG_ID ]]; then
    log "  Creating surf-monitor-sg..."
    MONITOR_SG_ID=$(aws ec2 create-security-group \
        --region $REGION \
        --group-name "surf-monitor-sg" \
        --description "Surf Report: Prometheus + Grafana monitor" \
        --vpc-id $VPC_ID \
        --query 'GroupId' --output text)
    log "  Created: $MONITOR_SG_ID"
else
    log "  Reusing: $MONITOR_SG_ID"
fi

# Open SSH + Grafana + Prometheus to current IP
MY_IP=$(curl -sf https://checkip.amazonaws.com)
log "Opening ports 22 / 3000 / 9090 to ${MY_IP}..."
for PORT in 22 3000 9090; do
    aws ec2 authorize-security-group-ingress \
        --region $REGION \
        --group-id $MONITOR_SG_ID \
        --protocol tcp --port $PORT \
        --cidr "${MY_IP}/32" 2>/dev/null && log "  Port $PORT: opened" \
        || log "  Port $PORT: already open"
done

# ── Launch monitor instance ───────────────────────────────────────────────────
log "Launching ${INSTANCE_TYPE} ${MONITOR_NAME}..."
MONITOR_ID=$(aws ec2 run-instances \
    --region $REGION \
    --image-id $AMI \
    --instance-type $INSTANCE_TYPE \
    --key-name $KEY_NAME \
    --subnet-id $SUBNET_ID \
    --security-group-ids $MONITOR_SG_ID \
    --associate-public-ip-address \
    --block-device-mappings 'DeviceName=/dev/sda1,Ebs={VolumeSize=50,VolumeType=gp3,DeleteOnTermination=true}' \
    --tag-specifications \
        "ResourceType=instance,Tags=[{Key=Name,Value=${MONITOR_NAME}},{Key=Project,Value=surf-report}]" \
    --query 'Instances[0].InstanceId' --output text)

print $MONITOR_ID > $MONITOR_ID_FILE
log "Launched: $MONITOR_ID  (saved to $(basename $MONITOR_ID_FILE))"

# ── Wait for public IP ────────────────────────────────────────────────────────
log "Waiting for instance to reach running state..."
aws ec2 wait instance-running --region $REGION --instance-ids $MONITOR_ID
MONITOR_IP=$(aws ec2 describe-instances \
    --region $REGION \
    --instance-ids $MONITOR_ID \
    --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)
log "Monitor IP: $MONITOR_IP"

# ── Open DCGM port on GPU node SG from monitor IP ────────────────────────────
log "Opening DCGM port 9400 on GPU SG ${GPU_SG_ID} for monitor (${MONITOR_IP})..."
aws ec2 authorize-security-group-ingress \
    --region $REGION \
    --group-id $GPU_SG_ID \
    --protocol tcp --port 9400 \
    --cidr "${MONITOR_IP}/32" 2>/dev/null && log "  Port 9400: opened" \
    || log "  Port 9400: already open"

# ── Wait for SSH ──────────────────────────────────────────────────────────────
log "Waiting for SSH (up to 90s)..."
for i in $(seq 1 18); do
    ssh -i $PEM -o ConnectTimeout=5 -o StrictHostKeyChecking=no \
        ubuntu@$MONITOR_IP true 2>/dev/null && break
    sleep 5
done

# ── Upload files to monitor ───────────────────────────────────────────────────
log "Uploading setup files to monitor..."
scp -i $PEM -o StrictHostKeyChecking=no \
    "${SCRIPT_DIR}/setup-monitor.sh" \
    ubuntu@${MONITOR_IP}:/tmp/setup-monitor.sh

# SCP dashboard JSON
scp -i $PEM -o StrictHostKeyChecking=no \
    "${SCRIPT_DIR}/grafana/dashboard_test01.json" \
    ubuntu@${MONITOR_IP}:/tmp/dashboard_test01.json

ssh -i $PEM -o StrictHostKeyChecking=no ubuntu@$MONITOR_IP chmod +x /tmp/setup-monitor.sh

# ── Run setup on monitor ──────────────────────────────────────────────────────
log "Running setup-monitor.sh on ${MONITOR_IP}..."
ssh -i $PEM -o StrictHostKeyChecking=no ubuntu@$MONITOR_IP \
    "ENDPOINT_IP=${ENDPOINT_IP} bash /tmp/setup-monitor.sh"

# ── Done ──────────────────────────────────────────────────────────────────────
print ""
print "════════════════════════════════════════════════════════════"
print "  Monitor stack deployed."
print ""
print "  Grafana    → http://${MONITOR_IP}:3000   (admin / surfadmin)"
print "  Prometheus → http://${MONITOR_IP}:9090"
print "  Instance   : $MONITOR_ID"
print ""
print "  Next — install DCGM exporter on each GPU node:"
print "    scp -i surf-crusoe.pem scripts/infra/setup-dcgm.sh ubuntu@<gpu-ip>:/tmp/"
print "    ssh -i surf-crusoe.pem ubuntu@<gpu-ip> 'bash /tmp/setup-dcgm.sh'"
print "  (GPU node needs /data mounted for Docker storage — see setup-dcgm.sh)"
print ""
print "  When surf-baseline is deployed, update Prometheus targets:"
print "    ssh -i surf-crusoe.pem ubuntu@${MONITOR_IP}"
print "    sudo nano /etc/prometheus/prometheus.yml"
print "    (replace BASELINE_IP_PLACEHOLDER with baseline public IP)"
print "    sudo systemctl reload prometheus"
print "════════════════════════════════════════════════════════════"
