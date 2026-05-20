# OpenShell + Self-Hosted vLLM (Fully Air-Gapped)

Run AI agents inside [NVIDIA OpenShell](https://github.com/NVIDIA/OpenShell) sandboxes with a self-hosted vLLM model on your GPU cluster. No data leaves your infrastructure.

## What this demonstrates

A fully self-hosted agent stack:
- **Model:** qwen3-8b-fp8 served by vLLM on a RHOAI (Red Hat OpenShift AI) GPU cluster
- **Sandbox:** OpenShell container with policy-governed network access
- **Framework:** OpenAI Agents Python SDK with `OpenAIChatCompletionsModel` pointing at the vLLM endpoint

The agent reads workspace files inside the sandbox, sends them to the self-hosted model for analysis, and returns the result. No calls to OpenAI, Anthropic, or any external API.

## Prerequisites

- An OpenShell gateway running (local or on-cluster)
- A vLLM model endpoint (any OpenAI-compatible inference server works)
- Python 3.10+

## Setup

```bash
cd openshell-agents-sdk-self-hosted
uv sync
```

## Run

```bash
# Set your vLLM endpoint (change to match your cluster)
export VLLM_BASE_URL="https://vllm-api-user-egallen.apps.ocp.cloud.rhai-tmm.dev/v1"
export VLLM_MODEL="qwen3-8b-fp8"

uv run python examples/self_hosted_test.py
```

## How it works

```python
from openai import AsyncOpenAI
from agents import Agent, Runner
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from agents.extensions.sandbox import OpenShellSandboxClient, OpenShellSandboxClientOptions

# Point at your self-hosted vLLM endpoint.
vllm_client = AsyncOpenAI(base_url=VLLM_BASE_URL, api_key="unused")
model = OpenAIChatCompletionsModel(model=VLLM_MODEL, openai_client=vllm_client)

# Agent runs inside OpenShell sandbox.
agent = Agent(name="Analyst", model=model, instructions="...")

# Sandbox client manages the OpenShell lifecycle.
run_config = RunConfig(
    sandbox=SandboxRunConfig(
        client=OpenShellSandboxClient(),
        options=OpenShellSandboxClientOptions(),
    ),
)

result = await Runner.run(agent, "Analyze the workspace files", run_config=run_config)
```

## Tested with

- OpenShell gateway: local-docker (Docker driver on macOS/Podman)
- Model: `qwen3-8b-fp8` on RHOAI cluster (`apps.ocp.cloud.rhai-tmm.dev`)
- Agent SDK: openai-agents-python with [OpenShell extension PR #3469](https://github.com/openai/openai-agents-python/pull/3469)
