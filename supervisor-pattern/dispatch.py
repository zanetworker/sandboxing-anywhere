"""
Sandboxed dispatcher: creates an OpenShell sandbox per agent on the cluster,
applies per-agent network policy, runs the agent reasoning inside the sandbox
with vLLM, collects stdout, and tears down the sandbox.

dispatch.py itself runs in the worker pod (NOT inside a sandbox).
It is a sandbox lifecycle manager.

Called by the brain via bash tool call:
    python3 dispatch.py data_analyst "analyze Q3 revenue data"

Set USE_SANDBOX=true to run agents inside OpenShell sandboxes (production).
Set USE_SANDBOX=false to run agents in-process (dev/testing only).
"""

import base64
import os
import subprocess
import sys

VLLM_URL = os.environ.get(
    "VLLM_BASE_URL",
    "https://vllm-api-user-egallen.apps.ocp.cloud.rhai-tmm.dev/v1",
)
VLLM_MODEL = os.environ.get("VLLM_MODEL", "qwen3-8b-fp8")
USE_SANDBOX = os.environ.get("USE_SANDBOX", "false").lower() == "true"
GATEWAY_INSECURE = os.environ.get("GATEWAY_INSECURE", "true").lower() == "true"

VLLM_HOST = VLLM_URL.replace("https://", "").replace("http://", "").split("/")[0]

PYTHON_BINARIES = [
    "/sandbox/.venv/bin/python3",
    "/sandbox/.venv/bin/python3.13",
    "/sandbox/.uv/python/cpython-3.13.12-linux-x86_64-gnu/bin/python3.13",
    "/sandbox/.uv/python/cpython-3.13.12-linux-aarch64-gnu/bin/python3.13",
]

AGENT_CONFIGS = {
    "metrics_agent": {
        "description": "Queries Prometheus/Grafana for service anomalies",
        "system": (
            "You are a metrics analysis agent for incident investigation. "
            "Analyze the provided metrics data. Identify anomalies, correlate "
            "time windows, and classify severity. Report specific numbers: "
            "latency percentiles, error rates, CPU/memory. Be concise."
        ),
        "sample_data": (
            "Prometheus query results for checkout-service (last 30 min): "
            "p99 latency spiked from 120ms to 1450ms at 14:32 UTC. "
            "Error rate jumped from 0.1% to 12.4% at 14:31 UTC. "
            "CPU usage: 89% (normal: 35%). Memory: 4.2GB/8GB (normal: 2.1GB). "
            "Upstream payment-gateway: healthy, latency stable at 45ms. "
            "Database connection pool: 48/50 active (near exhaustion). "
            "Pod restarts: 3 in last 15 minutes (OOMKilled)."
        ),
    },
    "log_agent": {
        "description": "Searches application logs for errors in the time window",
        "system": (
            "You are a log analysis agent for incident investigation. "
            "Search the provided log data for errors, exceptions, and "
            "anomalous patterns. Identify root cause signals. Report "
            "specific error messages, stack traces, and frequency. "
            "Do NOT include any customer PII from request payloads."
        ),
        "sample_data": (
            "Log aggregator results for checkout-service (14:30-15:00 UTC): "
            "ERROR PaymentProcessor.java:142 NullPointerException in processPayment() "
            "- 3,400 occurrences since 14:31. "
            "WARN ConnectionPool.java:89 'Pool exhausted, waiting for connection' "
            "- 12,000 occurrences since 14:30. "
            "ERROR HikariPool.java:251 'Connection is not available, request timed out after 30000ms' "
            "- 890 occurrences. "
            "INFO DeployController: 'Deployment checkout-service-v2.14.3 rolled out at 14:28 UTC'. "
            "DEBUG ConnectionPool: max_pool_size=50, idle_timeout=600s, max_lifetime=1800s."
        ),
    },
    "runbook_agent": {
        "description": "Searches runbooks and past incidents for matching patterns",
        "system": (
            "You are a runbook and incident history agent. Search the provided "
            "knowledge base for past incidents matching the current symptoms. "
            "Identify relevant runbooks, past root causes, and proven "
            "remediation steps. Be specific about which past incident matches "
            "and what fixed it."
        ),
        "sample_data": (
            "Runbook search results: "
            "INC-2847 (2026-03-15): 'Connection pool exhaustion after deploy'. "
            "Root cause: v2.12.1 introduced connection leak in retry logic. "
            "Fix: rollback to v2.12.0, then patch with connection.close() in finally block. "
            "Time to resolve: 22 minutes. Runbook: RB-CHECKOUT-004. "
            "INC-3102 (2026-04-22): 'OOMKilled pods after traffic spike'. "
            "Root cause: memory leak in session cache, not deploy-related. "
            "Fix: increase memory limit to 12GB, add cache eviction policy. "
            "INC-1955 (2025-11-08): 'Payment processing NullPointerException'. "
            "Root cause: upstream API changed response schema without notice. "
            "Fix: add null-safety checks in PaymentProcessor.processPayment()."
        ),
    },
}


def _run(cmd, timeout=120):
    """Run a shell command, return (stdout, stderr, returncode)."""
    result = subprocess.run(
        cmd, shell=True, capture_output=True, text=True, timeout=timeout,
    )
    return result.stdout.strip(), result.stderr.strip(), result.returncode


def _gw_flag():
    """Return --gateway-insecure flag if configured."""
    return "--gateway-insecure" if GATEWAY_INSECURE else ""


def _build_agent_script(system, sample_data, task):
    """Build a self-contained Python script for execution inside the sandbox."""
    script = (
        'import sys\n'
        'from openai import OpenAI\n'
        f'c = OpenAI(base_url="{VLLM_URL}", api_key="unused")\n'
        f'system = """{system}"""\n'
        f'data = """{sample_data}"""\n'
        f'task = """{task}"""\n'
        'prompt = "Context data:\\n" + data + "\\n\\nTask: " + task\n'
        'r = c.chat.completions.create(\n'
        f'    model="{VLLM_MODEL}",\n'
        '    messages=[{"role": "system", "content": system},\n'
        '             {"role": "user", "content": prompt}],\n'
        '    max_tokens=512, temperature=0.3,\n'
        '    extra_body={"chat_template_kwargs": {"enable_thinking": False}},\n'
        ')\n'
        'print(r.choices[0].message.content)\n'
    )
    return script


def run_in_sandbox(agent_name, task, config):
    """Create an OpenShell sandbox, run the agent inside it, tear it down."""
    sandbox_name = f"agent-{agent_name.replace('_', '-')}-{os.getpid()}"
    gw = _gw_flag()

    try:
        # 1. Create sandbox on the cluster
        print(f"[dispatch] Creating sandbox: {sandbox_name}")
        out, err, code = _run(f"openshell sandbox create --name {sandbox_name} {gw}")
        benign_errors = ["already exists", "connect_path is empty", "ssh exited with status", "UnknownIssuer"]
        if code != 0 and not any(b in err for b in benign_errors):
            return f"Failed to create sandbox: {err[:300]}"
        # Wait for sandbox to be ready
        import time
        time.sleep(2)

        # 2. Install openai (default policy allows PyPI)
        print(f"[dispatch] Installing dependencies in sandbox")
        _run(f"openshell sandbox exec --name {sandbox_name} {gw} -- pip3 install -q openai")

        # 3. Add vLLM endpoint with binary scoping (live policy update)
        print(f"[dispatch] Adding vLLM policy to sandbox")
        binary_flags = " ".join(f'--binary "{b}"' for b in PYTHON_BINARIES)
        _run(
            f"openshell policy update {sandbox_name} "
            f'--add-endpoint "{VLLM_HOST}:443:full:rest:enforce" '
            f"{binary_flags} --wait {gw}"
        )

        # 4. Build and upload agent script via base64
        script = _build_agent_script(config["system"], config["sample_data"], task)
        b64 = base64.b64encode(script.encode()).decode()
        _run(
            f"openshell sandbox exec --name {sandbox_name} {gw} -- "
            f"sh -c \"echo {b64} | base64 -d > /sandbox/agent.py\""
        )

        # 5. Run the agent INSIDE the sandbox
        print(f"[dispatch] Running agent inside sandbox with {VLLM_MODEL}")
        out, err, code = _run(
            f"openshell sandbox exec --name {sandbox_name} {gw} -- "
            f"python3 /sandbox/agent.py",
            timeout=60,
        )

        if code != 0:
            return f"Agent error (exit {code}): {err[:500]}"

        # Filter out TLS warnings from openshell
        lines = [l for l in out.split("\n") if "WARN" not in l and "TLS" not in l]
        return "\n".join(lines).strip()

    except subprocess.TimeoutExpired:
        return "Agent timed out"

    finally:
        # 6. Always tear down
        print(f"[dispatch] Deleting sandbox: {sandbox_name}")
        _run(f"openshell sandbox delete {sandbox_name} {gw}")


def run_local(agent_name, task, config):
    """Run directly in the worker process (no sandbox). For dev/testing only."""
    try:
        from openai import OpenAI
    except ImportError:
        return "Error: openai not installed. Run: pip install openai"

    client = OpenAI(base_url=VLLM_URL, api_key="unused")

    messages = [
        {"role": "system", "content": config["system"]},
        {"role": "user", "content": f"Context data:\n{config['sample_data']}\n\nTask: {task}"},
    ]

    kwargs = {}
    if os.environ.get("DISABLE_THINKING", "").lower() in ("1", "true"):
        kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}

    try:
        response = client.chat.completions.create(
            model=VLLM_MODEL,
            messages=messages,
            max_tokens=512,
            temperature=0.3,
            **kwargs,
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"Agent error: {e}"


def main():
    if len(sys.argv) < 3:
        agents = ", ".join(AGENT_CONFIGS.keys())
        print(f"Usage: python3 dispatch.py <agent_name> <task>")
        print(f"Available agents: {agents}")
        sys.exit(1)

    agent_name = sys.argv[1]
    task = " ".join(sys.argv[2:])

    if agent_name not in AGENT_CONFIGS:
        print(f"Unknown agent: {agent_name}")
        print(f"Available: {', '.join(AGENT_CONFIGS.keys())}")
        sys.exit(1)

    config = AGENT_CONFIGS[agent_name]
    mode = "sandboxed" if USE_SANDBOX else "local (no sandbox)"
    print(f"[dispatch] Agent: {agent_name} ({mode})")
    print(f"[dispatch] Task: {task}")
    print(f"[dispatch] Model: {VLLM_MODEL}")

    if USE_SANDBOX:
        result = run_in_sandbox(agent_name, task, config)
    else:
        result = run_local(agent_name, task, config)

    print(f"\n[dispatch] Result from {agent_name}:")
    print(result)


if __name__ == "__main__":
    main()
