# OpenShell + OpenAI Agents SDK

Run OpenAI Agents SDK sandbox agents inside [NVIDIA OpenShell](https://github.com/NVIDIA/OpenShell) sandboxes. Self-hosted, policy-governed, no data leaves your infrastructure.

## What this demonstrates

1. **Session-level integration** (no LLM needed): Create sandboxes, execute commands, read/write files, persist workspace state through the SDK's `BaseSandboxClient` / `BaseSandboxSession` interface.

2. **Agent-level integration** (requires `OPENAI_API_KEY`): Run a `SandboxAgent` with a shell capability inside an OpenShell sandbox. The agent inspects workspace files, runs commands, and responds.

## Prerequisites

- An OpenShell gateway running (local, remote, or cloud)
- Python 3.10+
- `OPENAI_API_KEY` environment variable (for agent-level tests only)

### Install OpenShell

```bash
curl -LsSf https://raw.githubusercontent.com/NVIDIA/OpenShell/main/install.sh | sh
```

Or via PyPI:

```bash
uv tool install -U openshell
```

### Start a local gateway

```bash
openshell sandbox create -- echo "gateway is ready"
```

On first run, OpenShell bootstraps a local gateway automatically.

## Setup

```bash
cd openshell-agents-sdk
uv sync
```

## Run

### Session-level test (no LLM, just validates the extension works)

```bash
uv run python examples/session_test.py
```

This creates an OpenShell sandbox, writes files into it, reads them back, executes commands, persists the workspace as a tar snapshot, and shuts down. All through the SDK's sandbox interface.

### Agent-level test (requires OPENAI_API_KEY)

```bash
uv run python examples/agent_test.py
```

This runs a `SandboxAgent` with a `WorkspaceShellCapability` inside an OpenShell sandbox. The agent uses the shell tool to inspect workspace files and answer questions.

## How it works

The integration uses the `OpenShellSandboxClient` and `OpenShellSandboxSession` classes from the [openai-agents-python OpenShell extension](https://github.com/openai/openai-agents-python/pull/3469):

```python
from agents.extensions.sandbox import OpenShellSandboxClient, OpenShellSandboxClientOptions
from agents.sandbox import Manifest, SandboxAgent, SandboxRunConfig
from agents.run import RunConfig

client = OpenShellSandboxClient()
options = OpenShellSandboxClientOptions(
    image="ghcr.io/nvidia/openshell-community/sandboxes/base:latest",
    gpu=False,
)

run_config = RunConfig(
    sandbox=SandboxRunConfig(client=client, options=options),
)

result = await Runner.run(agent, "Summarize the workspace", run_config=run_config)
```

### Gateway discovery

The extension resolves the OpenShell gateway automatically:

1. `OpenShellSandboxClientOptions(cluster="my-gateway")` -- explicit cluster name
2. `OPENSHELL_GATEWAY` environment variable
3. `~/.config/openshell/active_gateway` file (set by `openshell gateway select`)

### What happens under the hood

1. `OpenShellSandboxClient.create()` calls `SandboxClient.create(spec=...)` via gRPC, then `wait_ready()` until `SANDBOX_PHASE_READY`
2. `session.start()` materializes the manifest (writes files into the sandbox via exec + base64)
3. `session.exec()` calls `SandboxClient.exec(sandbox_id, command_list)` via gRPC
4. `session.read()` / `session.write()` use exec + base64 encoding (OpenShell has no native file API)
5. `session.persist_workspace()` tars the workspace and base64-decodes the output
6. `session.aclose()` deletes the sandbox via gRPC and closes the channel
