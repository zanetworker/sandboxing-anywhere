"""
Local worker agent that runs inside an OpenShell sandbox.
Uses vLLM via the OpenAI-compatible API to reason about tasks locally.
The brain dispatches task descriptions. This agent decides HOW to complete them.

Usage:
    python3 local_agent.py "Analyze the Q3 revenue data and flag anomalies"
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from openai import OpenAI

VLLM_URL = os.environ.get(
    "VLLM_BASE_URL",
    "https://vllm-api-user-egallen.apps.ocp.cloud.rhai-tmm.dev/v1",
)
MODEL = os.environ.get("VLLM_MODEL", "qwen3-8b-fp8")
WORKER_NAME = os.environ.get("WORKER_NAME", "worker")
WORKSPACE = Path(os.environ.get("WORKSPACE", "/workspace"))

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Execute a shell command in the sandbox and return stdout/stderr",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to run"}
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write content to a file in the workspace",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to workspace"},
                    "content": {"type": "string", "description": "File content"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file from the workspace",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path relative to workspace"}
                },
                "required": ["path"],
            },
        },
    },
]


def execute_tool(name, args):
    if name == "run_command":
        try:
            result = subprocess.run(
                args["command"], shell=True, capture_output=True, text=True, timeout=30, cwd=str(WORKSPACE)
            )
            output = result.stdout or result.stderr or "(no output)"
            return output[:2000]
        except subprocess.TimeoutExpired:
            return "Command timed out after 30 seconds"
        except Exception as e:
            return f"Error: {e}"

    elif name == "write_file":
        filepath = WORKSPACE / args["path"]
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(args["content"])
        return f"Written to {filepath}"

    elif name == "read_file":
        filepath = WORKSPACE / args["path"]
        if filepath.exists():
            return filepath.read_text()[:2000]
        return f"File not found: {filepath}"

    return f"Unknown tool: {name}"


def run_agent(task_description):
    client = OpenAI(base_url=VLLM_URL, api_key="unused")

    system = f"""You are {WORKER_NAME}, a specialized worker agent in an isolated sandbox.
Your workspace is {WORKSPACE}. You can run commands, read files, and write files.
Complete the task below. Use the tools provided. Be concise.
When done, write your final summary to {WORKSPACE}/result.json with keys: summary, findings, status."""

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": task_description},
    ]

    print(f"[{WORKER_NAME}] Task: {task_description}")
    print(f"[{WORKER_NAME}] Model: {MODEL} at {VLLM_URL}")

    max_turns = 5
    for turn in range(max_turns):
        print(f"[{WORKER_NAME}] Turn {turn + 1}/{max_turns}...")

        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            max_tokens=1024,
            temperature=0.3,
        )

        msg = response.choices[0].message

        if msg.tool_calls:
            messages.append(msg)
            for tc in msg.tool_calls:
                fn_name = tc.function.name
                fn_args = json.loads(tc.function.arguments)
                print(f"[{WORKER_NAME}]   Tool: {fn_name}({json.dumps(fn_args)[:100]})")
                result = execute_tool(fn_name, fn_args)
                print(f"[{WORKER_NAME}]   Result: {result[:100]}")
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })
        else:
            print(f"[{WORKER_NAME}] Agent response: {msg.content[:300]}")
            result_path = WORKSPACE / "result.json"
            if not result_path.exists():
                result_path.write_text(json.dumps({
                    "summary": msg.content[:500],
                    "status": "completed",
                    "worker": WORKER_NAME,
                    "model": MODEL,
                }))
            break

    result_path = WORKSPACE / "result.json"
    if result_path.exists():
        return json.loads(result_path.read_text())
    return {"summary": "Agent did not produce a result", "status": "incomplete", "worker": WORKER_NAME}


if __name__ == "__main__":
    task = sys.argv[1] if len(sys.argv) > 1 else "List the files in the workspace and describe what you find."
    result = run_agent(task)
    print(f"\n[{WORKER_NAME}] Final result:")
    print(json.dumps(result, indent=2))
