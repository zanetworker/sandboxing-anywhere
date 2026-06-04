"""
Supervisor pattern test: dispatch tasks to specialized OpenShell sandboxes.
Each sandbox runs its own local agent loop with vLLM.

Tests:
1. File system isolation between workers
2. Independent agent reasoning per sandbox
3. Task-level data partitioning
"""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
GATEWAY = os.environ.get("OPENSHELL_GATEWAY", "local-docker")
VLLM_URL = os.environ.get("VLLM_BASE_URL", "https://vllm-api-user-egallen.apps.ocp.cloud.rhai-tmm.dev/v1")
VLLM_MODEL = os.environ.get("VLLM_MODEL", "qwen3-8b-fp8")


def run_cmd(cmd, timeout=60):
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return result.stdout.strip(), result.stderr.strip(), result.returncode


def create_sandbox(name):
    print(f"[supervisor] Creating sandbox: {name}")
    run_cmd(f"openshell sandbox delete {name} 2>/dev/null")
    out, err, code = run_cmd(f"openshell sandbox create --name {name} --gateway {GATEWAY}")
    if code != 0 and "already exists" not in err:
        print(f"  Warning: {err[:200]}")
    out, _, _ = run_cmd(f"openshell sandbox list --gateway {GATEWAY}")
    print(f"  {out}")


def exec_in_sandbox(name, command, timeout=60):
    out, err, code = run_cmd(f'openshell sandbox exec --name {name} -- {command}', timeout=timeout)
    return out or err, code


def upload_to_sandbox(name, local_path, dest_path):
    import base64
    with open(local_path) as f:
        content = f.read()
    b64 = base64.b64encode(content.encode()).decode()
    chunk_size = 4000
    chunks = [b64[i:i + chunk_size] for i in range(0, len(b64), chunk_size)]
    exec_in_sandbox(name, f'sh -c "rm -f {dest_path}"')
    for chunk in chunks:
        exec_in_sandbox(name, f'sh -c "printf \'%s\' \'{chunk}\' >> {dest_path}.b64"')
    exec_in_sandbox(name, f'sh -c "base64 -d < {dest_path}.b64 > {dest_path} && rm {dest_path}.b64"')


def main():
    print("=" * 60)
    print("Supervisor Pattern: One Brain, Many Sandboxes")
    print("=" * 60)

    # Step 1: Create sandboxes
    create_sandbox("data-analyst")
    create_sandbox("report-writer")

    # Step 2: Verify isolation
    print("\n[supervisor] Testing sandbox isolation...")
    exec_in_sandbox("data-analyst", 'sh -c "echo SECRET_PII_DATA > /tmp/pii.txt"')
    out, code = exec_in_sandbox("report-writer", "cat /tmp/pii.txt")
    if "No such file" in out or code != 0:
        print("  PASS: report-writer cannot see data-analyst files")
    else:
        print(f"  FAIL: report-writer saw: {out}")

    # Step 3: Seed each sandbox with different data
    print("\n[supervisor] Seeding sandbox data...")
    exec_in_sandbox("data-analyst",
        'sh -c "echo \'Q3 Revenue: EMEA 4.2M, APAC 3.1M, Americas 5.1M. Anomalies: EMEA-034 (duplicate), APAC-112 (negative), EMEA-089 (date mismatch)\' > /sandbox/q3_data.txt"')
    exec_in_sandbox("report-writer",
        'sh -c "echo \'Compliance framework: GDPR Art.5, SOC2 CC6.1. Last audit: 2026-03-15. Open items: 2 medium, 1 high.\' > /sandbox/compliance_data.txt"')

    # Step 4: Install openai in both sandboxes
    print("\n[supervisor] Installing dependencies...")
    for name in ["data-analyst", "report-writer"]:
        out, code = exec_in_sandbox(name, "pip3 install openai", timeout=120)
        if code == 0:
            print(f"  {name}: openai installed")
        else:
            print(f"  {name}: install issue (may work without it): {out[:100]}")

    # Step 5: Upload local agent to both
    print("\n[supervisor] Uploading local agent...")
    agent_path = SCRIPT_DIR / "local_agent.py"
    for name in ["data-analyst", "report-writer"]:
        upload_to_sandbox(name, str(agent_path), "/sandbox/local_agent.py")
        out, _ = exec_in_sandbox(name, "head -1 /sandbox/local_agent.py")
        print(f"  {name}: agent uploaded (first line: {out[:60]})")

    # Step 6: Dispatch specialized tasks
    print("\n[supervisor] Dispatching tasks to specialized workers...")
    print("  (Each worker runs its own agent loop with vLLM)")

    tasks = {
        "data-analyst": "Read /sandbox/q3_data.txt and analyze the financial data. Identify the 3 anomalies, classify their severity, and write a summary. Do not include raw revenue figures in your output, only percentages and anomaly classifications.",
        "report-writer": "Read /sandbox/compliance_data.txt and generate a structured compliance status report with sections: Executive Summary, Open Items, Recommendations. Keep it under 300 words.",
    }

    results = {}
    for name, task in tasks.items():
        print(f"\n  [{name}] Starting agent loop...")
        safe_task = task.replace("'", "'\\''")
        env_vars = f"WORKER_NAME={name} VLLM_BASE_URL={VLLM_URL} VLLM_MODEL={VLLM_MODEL} WORKSPACE=/sandbox DISABLE_THINKING=true"
        out, code = exec_in_sandbox(
            name,
            f"sh -c '{env_vars} python3 /sandbox/local_agent.py \"{safe_task}\"'",
            timeout=120,
        )
        print(f"  [{name}] Output: {out[:500]}")
        results[name] = {"output": out[:1000], "exit_code": code}

    # Step 7: Collect and compare results
    print("\n" + "=" * 60)
    print("Results Summary")
    print("=" * 60)
    for name, result in results.items():
        print(f"\n[{name}] Exit code: {result['exit_code']}")
        # Try to read the result.json
        out, _ = exec_in_sandbox(name, "cat /sandbox/result.json")
        if out and not out.startswith("cat:"):
            try:
                r = json.loads(out)
                print(f"  Summary: {r.get('summary', 'N/A')[:200]}")
                print(f"  Status: {r.get('status', 'N/A')}")
            except json.JSONDecodeError:
                print(f"  Raw: {out[:200]}")
        else:
            print(f"  No result.json (agent may have timed out or failed)")

    # Step 8: Final isolation check
    print("\n[supervisor] Final isolation verification...")
    out, _ = exec_in_sandbox("report-writer", "cat /sandbox/q3_data.txt")
    if "No such file" in out:
        print("  PASS: report-writer still cannot see data-analyst data")
    else:
        print(f"  Note: {out[:100]}")

    # Cleanup
    print("\n[supervisor] Cleaning up...")
    run_cmd("openshell sandbox delete data-analyst 2>/dev/null")
    run_cmd("openshell sandbox delete report-writer 2>/dev/null")
    print("[supervisor] Done.")


if __name__ == "__main__":
    main()
