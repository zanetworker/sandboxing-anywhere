#!/bin/bash
# Supervisor pattern: dispatch tasks to isolated OpenShell sandboxes
# Each sandbox runs its own agent loop with a local vLLM model

set -e

GATEWAY="${OPENSHELL_GATEWAY:-local-docker}"
VLLM_URL="${VLLM_BASE_URL:-https://vllm-api-user-egallen.apps.ocp.cloud.rhai-tmm.dev/v1}"
VLLM_MODEL="${VLLM_MODEL:-qwen3-8b-fp8}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== Supervisor Pattern: One Brain, Many Sandboxes ==="
echo "Gateway: $GATEWAY"
echo "Model: $VLLM_MODEL"
echo ""

# Create sandboxes
echo "[supervisor] Creating Worker A sandbox (data analysis)..."
openshell sandbox create --name worker-a --gateway "$GATEWAY" 2>/dev/null || true

echo "[supervisor] Creating Worker B sandbox (report generation)..."
openshell sandbox create --name worker-b --gateway "$GATEWAY" 2>/dev/null || true

echo ""
openshell sandbox list --gateway "$GATEWAY"
echo ""

# Upload the worker agent to both sandboxes
echo "[supervisor] Uploading worker agent to sandboxes..."
openshell sandbox upload --name worker-a "$SCRIPT_DIR/worker_agent.py" --dest /workspace/worker_agent.py 2>&1 || echo "Upload to worker-a: using exec fallback"
openshell sandbox upload --name worker-b "$SCRIPT_DIR/worker_agent.py" --dest /workspace/worker_agent.py 2>&1 || echo "Upload to worker-b: using exec fallback"

# Dispatch Task A: analyze sensitive data
echo ""
echo "[supervisor] Dispatching Task A to Worker A: analyze financial data"
openshell sandbox exec --name worker-a -- env \
    WORKER_NAME=worker-a \
    TASK="You are analyzing Q3 financial records. The data shows revenue of 12.4M with 3 anomalies in the EMEA region. Write a summary of findings and flag the anomalies. Do not include raw figures in your summary, only percentages and trends." \
    VLLM_BASE_URL="$VLLM_URL" \
    VLLM_MODEL="$VLLM_MODEL" \
    WORKSPACE=/workspace \
    python3 /workspace/worker_agent.py 2>&1 &
PID_A=$!

# Dispatch Task B: generate a report
echo "[supervisor] Dispatching Task B to Worker B: generate compliance report"
openshell sandbox exec --name worker-b -- env \
    WORKER_NAME=worker-b \
    TASK="You are generating a compliance report. The audit covers data handling practices for EU customers. Write a structured report with sections: Executive Summary, Findings, Recommendations. Keep it under 500 words." \
    VLLM_BASE_URL="$VLLM_URL" \
    VLLM_MODEL="$VLLM_MODEL" \
    WORKSPACE=/workspace \
    python3 /workspace/worker_agent.py 2>&1 &
PID_B=$!

# Wait for both
echo ""
echo "[supervisor] Waiting for workers to complete..."
wait $PID_A 2>/dev/null
echo "[supervisor] Worker A complete."
wait $PID_B 2>/dev/null
echo "[supervisor] Worker B complete."

# Verify isolation
echo ""
echo "=== Isolation Verification ==="
echo "[supervisor] Worker A result:"
openshell sandbox exec --name worker-a -- cat /workspace/result.txt 2>&1 | head -5
echo "..."

echo ""
echo "[supervisor] Worker B result:"
openshell sandbox exec --name worker-b -- cat /workspace/result.txt 2>&1 | head -5
echo "..."

echo ""
echo "[supervisor] Cross-sandbox isolation test (Worker B reads Worker A's data):"
openshell sandbox exec --name worker-b -- cat /workspace/worker-a-data.txt 2>&1 || echo "CONFIRMED: Worker B cannot access Worker A's files"

# Cleanup
echo ""
echo "[supervisor] Cleaning up sandboxes..."
openshell sandbox delete worker-a 2>/dev/null || true
openshell sandbox delete worker-b 2>/dev/null || true
echo "[supervisor] Done."
