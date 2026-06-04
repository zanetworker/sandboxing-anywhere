# Sandboxing Anywhere

Agent sandboxing experiments across providers and entrypoints. Each directory is a self-contained integration example with its own dependencies and instructions.

> **OpenShell + OpenAI Agents SDK** is the first working integration. The upstream PR adding OpenShell as a sandbox provider to the OpenAI Agents Python SDK: [openai/openai-agents-python#3469](https://github.com/openai/openai-agents-python/pull/3469)

## Experiments

| Directory | What | Sandbox Provider | Agent Framework |
|-----------|------|-----------------|-----------------|
| `openshell-agents-sdk/` | OpenShell + OpenAI Agents SDK | [NVIDIA OpenShell](https://github.com/NVIDIA/OpenShell) | [OpenAI Agents Python SDK](https://github.com/openai/openai-agents-python) |
| `openshell-agents-sdk-self-hosted/` | OpenShell + self-hosted vLLM (fully air-gapped) | [NVIDIA OpenShell](https://github.com/NVIDIA/OpenShell) | OpenAI Agents SDK + vLLM on GPU cluster |
| `openshell-ogx/` | OpenShell + OGX (Llama Stack) | [NVIDIA OpenShell](https://github.com/NVIDIA/OpenShell) | [OGX](https://github.com/ogx-ai/ogx) Containers API + Responses API (**PR**) |
| `anthropic-self-hosted/` | OpenShell + Anthropic Self-Hosted Sandboxes | [NVIDIA OpenShell](https://github.com/NVIDIA/OpenShell) | [Anthropic `ant` CLI](https://github.com/anthropics/anthropic-cli) (Managed Agents, self-hosted) |
| `supervisor-pattern/` | Multi-agent supervisor: brain decomposes, each agent reasons with vLLM inside its own OpenShell sandbox with per-agent network policy | [NVIDIA OpenShell](https://github.com/NVIDIA/OpenShell) | Claude Managed Agents (brain) + vLLM/Qwen3-8B (sandboxed local agents) |

## Related PRs and Issues

| Link | Status | Description |
|------|--------|-------------|
| [ogx-ai/ogx#5853](https://github.com/ogx-ai/ogx/pull/5853) | Open PR | Containers API + Docker provider + `remote::openshell` provider |
| [ogx-ai/ogx#5852](https://github.com/ogx-ai/ogx/issues/5852) | Open Issue | Containers API feature request |
| [openai/openai-agents-python#3468](https://github.com/openai/openai-agents-python/issues/3468) | Open Issue | Feature request: add OpenShell sandbox provider |
| [openai/openai-agents-python#3469](https://github.com/openai/openai-agents-python/pull/3469) | Open PR | OpenShell sandbox extension for the OpenAI Agents SDK |
| [RHAIRFE-1538](https://redhat.atlassian.net/browse/RHAIRFE-1538) | Stakeholder Review | Sandboxed Containers API and Shell Tool for Agent Code Execution |

## Planned

- `openai-containers-api/` -- OpenAI Responses API with Containers API (hosted sandboxes)
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
