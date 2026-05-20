"""
Agent-level OpenShell sandbox test.

Runs a SandboxAgent with a WorkspaceShellCapability inside an OpenShell sandbox.
The agent inspects workspace files and answers questions.

Requires:
  - A running OpenShell gateway
  - OPENAI_API_KEY environment variable
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from agents import ModelSettings, Runner
from agents.extensions.sandbox import OpenShellSandboxClient, OpenShellSandboxClientOptions
from agents.run import RunConfig
from agents.sandbox import Manifest, SandboxAgent, SandboxRunConfig
from agents.sandbox.entries import File
from agents.tool import ShellCallOutcome, ShellCommandOutput, ShellCommandRequest, ShellResult, ShellTool, Tool
from agents.sandbox import Capability, Manifest
from agents.sandbox.session.base_sandbox_session import BaseSandboxSession


class WorkspaceShellCapability(Capability):
    """Expose one shell tool for inspecting the active sandbox workspace."""

    def __init__(self) -> None:
        super().__init__(type="workspace_shell")
        self._session: BaseSandboxSession | None = None

    def bind(self, session: BaseSandboxSession) -> None:
        self._session = session

    def tools(self) -> list[Tool]:
        return [ShellTool(executor=self._execute_shell)]

    async def instructions(self, manifest: Manifest) -> str | None:
        return (
            "Use the `shell` tool to inspect the sandbox workspace before answering. "
            "The workspace root is the current working directory, so prefer relative paths "
            "with commands like `pwd`, `find .`, and `cat`. Only cite files you actually read."
        )

    async def _execute_shell(self, request: ShellCommandRequest) -> ShellResult:
        if self._session is None:
            raise RuntimeError("Workspace shell is not bound to a sandbox session.")

        timeout_s = request.timeout if request.timeout and request.timeout > 0 else 30
        joined = " ".join(request.command)
        result = await self._session.exec(joined, timeout=timeout_s)

        return ShellResult(
            call_id=request.call_id,
            outcome=ShellCallOutcome(
                output=[
                    ShellCommandOutput(
                        command=joined,
                        output=result.stdout.decode("utf-8", errors="replace")
                        + result.stderr.decode("utf-8", errors="replace"),
                        exit_code=result.exit_code,
                    )
                ]
            ),
        )


async def main() -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not set. Skipping agent test.")
        sys.exit(1)

    print("=== OpenShell Agent-Level Test ===\n")

    manifest = Manifest(
        root="/sandbox",
        entries={
            "README.md": File(
                content=b"# Project Status\n\nThis workspace contains a sample project status report.\n",
            ),
            "status.md": File(
                content=(
                    b"# Sprint 42 Status\n\n"
                    b"- Auth service: on track, shipping Tuesday.\n"
                    b"- Search reindex: blocked on infra ticket INFRA-1234.\n"
                    b"- Dashboard v2: 80% complete, needs UX review.\n"
                ),
            ),
        },
    )

    agent = SandboxAgent(
        name="OpenShell Sandbox Assistant",
        model="gpt-5.5",
        instructions=(
            "Answer questions about the sandbox workspace. Inspect the files before answering "
            "and keep the response concise. "
            "Do not invent files or statuses that are not present in the workspace. Cite the "
            "file names you inspected."
        ),
        default_manifest=manifest,
        capabilities=[WorkspaceShellCapability()],
        model_settings=ModelSettings(tool_choice="required"),
    )

    run_config = RunConfig(
        sandbox=SandboxRunConfig(
            client=OpenShellSandboxClient(),
            options=OpenShellSandboxClientOptions(),
        ),
        workflow_name="OpenShell sandbox agent example",
    )

    question = "Summarize the project status from the workspace files."
    print(f"Question: {question}\n")

    result = await Runner.run(agent, question, run_config=run_config)
    print(f"assistant> {result.final_output}")


if __name__ == "__main__":
    asyncio.run(main())
