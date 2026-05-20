"""
Session-level OpenShell sandbox test. No LLM needed.

Validates the full sandbox lifecycle: create, exec, read, write, persist, shutdown.
Requires a running OpenShell gateway.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path

from agents.extensions.sandbox import OpenShellSandboxClient, OpenShellSandboxClientOptions
from agents.sandbox import Manifest
from agents.sandbox.entries import File


async def main() -> None:
    print("=== OpenShell Session-Level Test ===\n")

    manifest = Manifest(
        root="/sandbox",
        entries={
            "hello.txt": File(content=b"Hello from OpenShell sandbox!\n"),
            "data/numbers.csv": File(content=b"a,b,c\n1,2,3\n4,5,6\n"),
        },
    )

    client = OpenShellSandboxClient()
    options = OpenShellSandboxClientOptions()

    print("1. Creating sandbox...")
    session = await client.create(manifest=manifest, options=options)

    try:
        print("2. Starting session (materializing workspace)...")
        await session.start()

        print("3. Running 'ls -la' in workspace...")
        result = await session.exec("ls", "-la", shell=False)
        print(f"   exit_code={result.exit_code}")
        print(f"   stdout:\n{result.stdout.decode()}")

        print("4. Reading hello.txt...")
        content = await session.read(Path("hello.txt"))
        text = content.read()
        if isinstance(text, bytes):
            text = text.decode("utf-8")
        print(f"   content: {text.strip()!r}")
        assert "Hello from OpenShell sandbox!" in text

        print("5. Writing a new file...")
        await session.write(
            Path("output.txt"),
            io.BytesIO(b"Written by the OpenAI Agents SDK via OpenShell.\n"),
        )

        print("6. Verifying the written file...")
        result = await session.exec("cat", "output.txt", shell=False)
        assert result.exit_code == 0
        print(f"   content: {result.stdout.decode().strip()!r}")

        print("7. Running a multi-step shell command...")
        result = await session.exec("wc -l data/numbers.csv && echo 'done'")
        print(f"   output: {result.stdout.decode().strip()}")

        print("8. Checking sandbox is running...")
        is_running = await session.running()
        print(f"   running: {is_running}")
        assert is_running

        print("9. Running Python inside the sandbox...")
        result = await session.exec("python3", "-c", "print(40 + 2)", shell=False)
        print(f"   result: {result.stdout.decode().strip()}")

        print("10. Persisting workspace (tar snapshot)...")
        snapshot = await session.persist_workspace()
        snapshot_bytes = snapshot.read()
        print(f"    snapshot size: {len(snapshot_bytes)} bytes")
        assert len(snapshot_bytes) > 0

        print("\nAll checks passed.")

    finally:
        print("\n11. Shutting down sandbox...")
        await session.aclose()
        print("    Done.")


if __name__ == "__main__":
    asyncio.run(main())
