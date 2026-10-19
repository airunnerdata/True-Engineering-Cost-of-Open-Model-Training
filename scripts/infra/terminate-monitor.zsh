#!/usr/bin/env zsh
# terminate-monitor.zsh
# Terminates the surf-monitor instance and cleans up associated resources:
#   - Removes the monitor SG ingress rules from the GPU node SG (port 9400)
#   - Optionally deletes the surf-monitor-sg (skipped if baseline still uses it)
#
# Usage:
#   cd scripts/infra
#   ./terminate-monitor.zsh

set -euo pipefail

REGION="us-west-2"
GPU_SG_ID="sg-9f7ee5fa"      # SG on GPU instances (port 9400 was opened for monitor)
SCRIPT_DIR="${0:a:h}"
MONITOR_ID_FILE="${SCRIPT_DIR}/.monitor-instance-id"

log()   { print "[terminate-monitor] $*" }
abort() { print "[terminate-monitor] ERROR: $*" >&2; exit 1 }

# ── Resolve instance ID ───────────────────────────────────────────────────────
if [[ -f $MONITOR_ID_FILE ]]; then
    MONITOR_ID=$(cat $MONITOR_ID_FILE)
    log "Using saved instance ID: $MONITOR_ID"
else
    log "No saved ID — looking up by Name tag 'surf-monitor'..."
    MONITOR_ID=$(aws ec2 describe-instances \
        --region $REGION \
        --filters \
            "Name=tag:Name,Values=surf-monitor" \
            "Name=instance-state-name,Values=running,stopped,stopping" \
        --query 'Reservations[0].Instances[0].InstanceId' \
        --output text)
    [[ $MONITOR_ID == "None" || -z $MONITOR_ID ]] \
        && abort "No running surf-monitor instance found."
fi

# Get public IP before termination (needed for SG cleanup)
MONITOR_IP=$(aws ec2 describe-instances \
    --region $REGION \
    --instance-ids $MONITOR_ID \
    --query 'Reservations[0].Instances[0].PublicIpAddress' \
    --output text)
log "Terminating $MONITOR_ID ($MONITOR_IP)..."

# ── Terminate ─────────────────────────────────────────────────────────────────
aws ec2 terminate-instances --region $REGION --instance-ids $MONITOR_ID > /dev/null
log "Termination request sent. Waiting..."
aws ec2 wait instance-terminated --region $REGION --instance-ids $MONITOR_ID
log "Instance terminated."

# ── Revoke DCGM port 9400 from GPU node SG ───────────────────────────────────
if [[ $MONITOR_IP != "None" && -n $MONITOR_IP ]]; then
    log "Revoking port 9400 rule for ${MONITOR_IP} from GPU SG ${GPU_SG_ID}..."
    aws ec2 revoke-security-group-ingress \
        --region $REGION \
        --group-id $GPU_SG_ID \
        --protocol tcp --port 9400 \
        --cidr "${MONITOR_IP}/32" 2>/dev/null \
        && log "  Rule revoked." \
        || log "  Rule not found — already removed."
fi

# ── Clean up saved ID ─────────────────────────────────────────────────────────
rm -f $MONITOR_ID_FILE

print ""
print "════════════════════════════════════════════════════════════"
print "  surf-monitor ($MONITOR_ID) terminated."
print "  surf-monitor-sg left in place (reuse on next deploy)."
print "════════════════════════════════════════════════════════════"
