"""
surf_train.py — Long-running GPU training workload for Test 1.

Runs ~40 minutes (2400 steps at ~1 step/sec) on a single GPU.
Checkpoints every 100 steps to /tmp/checkpoints/.

Failure injection hooks:
    F1 (OOM Kill):        touch /tmp/surf_oom   → training process allocates RAM
                          until cgroup limit is hit → pod OOMKilled (exit 137)
    F3 (Silent GPU Hang): touch /tmp/surf_pause → training loop stalls
                          rm /tmp/surf_pause    → training resumes
    F2 (Pod Eviction):    inject_f2_evict.sh deletes the pod externally
    F4 (Node Cordon):     inject_f4_cordon.sh cordons the node externally

stdout format (one line per step):
    step=<N> loss=<F> device=<name>
    CHECKPOINT step=<N> loss=<F>
    PAUSED at step=<N> — waiting for /tmp/surf_pause removal
    OOM_HOOK triggered at step=<N> — allocating RAM until OOMKilled
"""

import os
import sys
import time
import torch

PAUSE_FILE   = "/tmp/surf_pause"
OOM_FILE     = "/tmp/surf_oom"
CKPT_DIR     = "/tmp/checkpoints"
TOTAL_STEPS  = 2400   # ~40 min at ~1 step/sec
CKPT_EVERY   = 100
MATRIX_SIZE  = 2048   # large enough to keep GPU busy, small enough to fit on any H100/A100/A10G

os.makedirs(CKPT_DIR, exist_ok=True)

# ── Device setup ───────────────────────────────────────────────────────────────

if not torch.cuda.is_available():
    print("ERROR: no CUDA device found", flush=True)
    sys.exit(1)

device     = torch.device("cuda")
device_name = torch.cuda.get_device_name(0)
print(f"Device: {device} ({device_name})", flush=True)
print(f"Total steps: {TOTAL_STEPS}  checkpoint every: {CKPT_EVERY}", flush=True)
print(f"Pause hook:  touch {PAUSE_FILE} to hang, rm to resume", flush=True)
print(flush=True)

# ── Model (simple matrix multiply loop — no external deps) ────────────────────

torch.manual_seed(42)
x = torch.randn(MATRIX_SIZE, MATRIX_SIZE, device=device)
w = torch.nn.Parameter(torch.randn(MATRIX_SIZE, MATRIX_SIZE, device=device))
optimizer = torch.optim.SGD([w], lr=0.01, momentum=0.9)

# ── Training loop ──────────────────────────────────────────────────────────────

for step in range(TOTAL_STEPS):

    # F1 hook: exhaust container RAM until cgroup OOM killer fires
    if os.path.exists(OOM_FILE):
        print(f"OOM_HOOK triggered at step={step} — allocating RAM until OOMKilled", flush=True)
        sys.stdout.flush()
        chunks = []
        chunk_bytes = 256 * 1024 * 1024  # 256 MiB at a time — slow enough to print, fast enough to OOM
        while True:
            buf = bytearray(chunk_bytes)
            buf[0] = 1; buf[-1] = 1       # touch first+last page to force commit
            chunks.append(buf)
            allocated_gib = len(chunks) * chunk_bytes / 1024**3
            print(f"  OOM_HOOK allocated {allocated_gib:.1f} GiB so far...", flush=True)
            time.sleep(0.1)

    # F3 hook: stall here while pause sentinel exists
    if os.path.exists(PAUSE_FILE):
        print(f"PAUSED at step={step} — waiting for {PAUSE_FILE} removal", flush=True)
        while os.path.exists(PAUSE_FILE):
            time.sleep(5)
        print(f"RESUMED at step={step}", flush=True)

    # Keep GPU busy for ~90% of each 1-second step so utilization stays well
    # above the F3 alarm threshold (5%) during normal operation.
    # A single 2048x2048 matmul completes in <1ms on A10G/H100 — without this
    # loop the GPU is idle >99% of the time and the hang alarm false-fires.
    step_start = time.time()
    optimizer.zero_grad()
    while time.time() - step_start < 0.9:
        out = x @ w          # keep GPU warm
    loss = out.mean() + 1e-4 * w.pow(2).mean()
    loss.backward()
    optimizer.step()
    # sleep any remaining wall-clock time to maintain ~1 step/sec cadence
    elapsed = time.time() - step_start
    if elapsed < 1.0:
        time.sleep(1.0 - elapsed)

    loss_val = loss.item()

    if step % CKPT_EVERY == 0:
        ckpt_path = os.path.join(CKPT_DIR, f"ckpt_{step:04d}.pt")
        torch.save({"step": step, "loss": loss_val, "w": w.data.cpu()}, ckpt_path)
        print(f"CHECKPOINT step={step} loss={loss_val:.4f} device={device_name}", flush=True)
    else:
        print(f"step={step} loss={loss_val:.4f}", flush=True)

    time.sleep(1)

print("Training complete", flush=True)
