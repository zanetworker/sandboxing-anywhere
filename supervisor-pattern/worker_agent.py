"""
Worker agent that runs inside an OpenShell sandbox.
Uses a local vLLM model (not Anthropic) to reason about and complete tasks.
The brain dispatches task descriptions; this agent decides HOW to complete them.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

try:
    from openai import AsyncOpenAI
except ImportError:
    print("pip install openai")
    sys.exit(1)


VLLM_BASE_URL = os.environ.get(
    "VLLM_BASE_URL",
    "https://vllm-api-user-egallen.apps.ocp.cloud.rhai-tmm.dev/v1",
)
VLLM_MODEL = os.environ.get("VLLM_MODEL", "qwen3-8b-fp8")
WORKSPACE = Path(os.environ.get("WORKSPACE", "/workspace"))


async def run_agent(task_description: str, worker_name: str) -> dict:
    """Run a local agent loop that reasons about and completes a task."""

    client = AsyncOpenAI(base_url=VLLM_BASE_URL, api_key="unused")

    system_prompt = f"""You are {worker_name}, a specialized worker agent running inside an isolated sandbox.
You have access to the local file system at {WORKSPACE}.
You can read files, analyze data, and produce results.
You do NOT have internet access except to the model API.
Complete the task described below. Be concise in your reasoning.
Write your final output to {WORKSPACE}/result.txt."""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task_description},
    ]

    print(f"[{worker_name}] Received task: {task_description}")
    print(f"[{worker_name}] Reasoning with {VLLM_MODEL}...")

    response = await client.chat.completions.create(
        model=VLLM_MODEL,
        messages=messages,
        max_tokens=1024,
        temperature=0.7,
    )

    reasoning = response.choices[0].message.content
    print(f"[{worker_name}] Agent reasoning:\n{reasoning[:500]}")

    result_path = WORKSPACE / "result.txt"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(reasoning)

    return {
        "worker": worker_name,
        "task": task_description,
        "model": VLLM_MODEL,
        "result_summary": reasoning[:200],
        "result_file": str(result_path),
    }


async def main():
    worker_name = os.environ.get("WORKER_NAME", "worker-default")
    task = os.environ.get("TASK", "Analyze the workspace and report what you find.")

    result = await run_agent(task, worker_name)
    print(f"\n[{worker_name}] Task complete.")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
