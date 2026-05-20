# Sandboxing Anywhere

Agent sandboxing experiments across providers and entrypoints. Each directory is a self-contained integration example with its own dependencies and instructions.

> **OpenShell + OpenAI Agents SDK** is the first working integration. The upstream PR adding OpenShell as a sandbox provider to the OpenAI Agents Python SDK: [openai/openai-agents-python#3469](https://github.com/openai/openai-agents-python/pull/3469)

## Experiments

| Directory | What | Sandbox Provider | Agent Framework |
|-----------|------|-----------------|-----------------|
| `openshell-agents-sdk/` | OpenShell + OpenAI Agents SDK | [NVIDIA OpenShell](https://github.com/NVIDIA/OpenShell) | [OpenAI Agents Python SDK](https://github.com/openai/openai-agents-python) |
| `openshell-ogx/` | OpenShell + OGX (Llama Stack) | [NVIDIA OpenShell](https://github.com/NVIDIA/OpenShell) | [OGX](https://github.com/ogx-ai/ogx) Containers API + Responses API |

## Planned

- `openai-containers-api/` -- OpenAI Responses API with Containers API (hosted sandboxes)
- `anthropic-self-hosted/` -- Anthropic Claude with self-hosted sandbox execution
- `openshell-mcp/` -- OpenShell sandboxes exposed as MCP tools
- `openshell-llama-stack/` -- OpenShell + Llama Stack agents (native API, not OpenAI-compat)

## Structure

Each experiment directory contains:

```
<experiment>/
  README.md          # Setup instructions and usage
  pyproject.toml     # Dependencies (or requirements.txt)
  src/               # Source code
  examples/          # Runnable examples
```

## Prerequisites

All experiments assume you have the relevant sandbox provider running. See each experiment's README for specific setup instructions.
