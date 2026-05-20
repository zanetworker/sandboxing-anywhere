"""
Fully self-hosted agent sandbox test.

Runs an AI agent inside an OpenShell sandbox, using a self-hosted vLLM model
on a GPU cluster. No data leaves your infrastructure.

Requires:
  - A running OpenShell gateway
  - A vLLM endpoint (set VLLM_BASE_URL and VLLM_MODEL env vars)
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from openai import AsyncOpenAI

from agents import Agent, Runner
from agents.extensions.sandbox import OpenShellSandboxClient, OpenShellSandboxClientOptions
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from agents.sandbox import Manifest
from agents.sandbox.entries import File


VLLM_BASE_URL = os.environ.get(
    "VLLM_BASE_URL",
    "https://vllm-api-user-egallen.apps.ocp.cloud.rhai-tmm.dev/v1",
)
VLLM_MODEL = os.environ.get("VLLM_MODEL", "qwen3-8b-fp8")


async def main() -> None:
    print("=== Fully Self-Hosted: OpenShell Sandbox + vLLM on GPU Cluster ===")
    print()
    print(f"Model endpoint: {VLLM_BASE_URL}")
    print(f"Model: {VLLM_MODEL}")
    print()

    # Connect to self-hosted vLLM.
    vllm_client = AsyncOpenAI(base_url=VLLM_BASE_URL, api_key="unused")
    model = OpenAIChatCompletionsModel(model=VLLM_MODEL, openai_client=vllm_client)
    print("[1] Connected to self-hosted model")

    # Create sandbox with workspace files.
    manifest = Manifest(
        root="/sandbox",
        entries={
            "report.md": File(
                content=(
                    b"# Q2 Revenue Report\n\n"
                    b"- North America: $4.2M (up 12%)\n"
                    b"- Europe: $2.8M (flat)\n"
                    b"- APAC: $1.9M (up 23%)\n"
                    b"- Total: $8.9M\n"
                ),
            ),
            "risks.txt": File(
                content=(
                    b"Key risks:\n"
                    b"1. APAC growth depends on one large customer (40% of region)\n"
                    b"2. Europe pipeline is thin for Q3\n"
                    b"3. Engineering headcount freeze may delay product launches\n"
                ),
            ),
        },
    )

    client = OpenShellSandboxClient()
    options = OpenShellSandboxClientOptions()

    print("[2] Creating OpenShell sandbox...")
    session = await client.create(manifest=manifest, options=options)

    try:
        await session.start()
        inner = session._inner
        print(f"    sandbox: {inner.state.sandbox_name}")

        # Verify files landed.
        result = await session.exec("ls", "-la", shell=False)
        print("[3] Workspace files:")
        for line in result.stdout.decode().splitlines():
            if "report" in line or "risks" in line:
                print(f"    {line}")

        # Read files from sandbox.
        report = await session.read(Path("report.md"))
        report_text = report.read().decode()
        risks = await session.read(Path("risks.txt"))
        risks_text = risks.read().decode()
        print(f"[4] Read {len(report_text) + len(risks_text)} bytes from sandbox")

        # Send to self-hosted model for analysis.
        print(f"[5] Asking {VLLM_MODEL} to analyze workspace files...")

        agent = Agent(
            name="Analyst",
            model=model,
            instructions=(
                "You are a business analyst. Analyze the provided files and give "
                "a brief assessment in 2-3 sentences. No thinking tags. Be direct."
            ),
        )

        prompt = (
            f"Analyze these workspace files:\n\n"
            f"--- report.md ---\n{report_text}\n"
            f"--- risks.txt ---\n{risks_text}"
        )
        result = await Runner.run(agent, prompt)
        print(f"    Response: {result.final_output}")
        print()
        print("=== SUCCESS: Fully self-hosted stack validated ===")
        print(f"    Model: {VLLM_MODEL} on GPU cluster")
        print(f"    Sandbox: OpenShell ({inner.state.sandbox_name})")
        print("    No data left the infrastructure.")

    finally:
        print()
        print("[6] Cleaning up sandbox...")
        await session.aclose()
        print("    Done.")


if __name__ == "__main__":
    asyncio.run(main())
