# OpenShell + OGX (Llama Stack)

Run OGX Containers API and Responses API shell tool with [NVIDIA OpenShell](https://github.com/NVIDIA/OpenShell) as the sandbox backend. Commands execute inside security-hardened OpenShell sandboxes with per-binary network policies and credential isolation.

> **Status:** requires the `remote::openshell` provider from [ogx-ai/ogx#5853](https://github.com/ogx-ai/ogx/pull/5853) (not yet merged).

| Component | Version / Ref |
|-----------|--------------|
| OGX (Llama Stack) | [`feat/containers-api`](https://github.com/ogx-ai/ogx/pull/5853) branch (`ed0abeaf3`) |
| OpenShell gateway | `0.x` dev (commit `f819f7dc`) |
| OpenShell Python SDK | `0.0.0` (installed from source) |
| OpenAI Python SDK | `>=1.80.0` (containers client support) |
| Python | `>=3.12` |

## What this demonstrates

1. **Containers API** (no LLM needed): Create sandboxes, execute commands, list/retrieve/delete through the OpenAI-compatible `/v1/containers` endpoints backed by OpenShell.

2. **Responses API with shell tool** (requires inference provider): Send a prompt with `tools: [{type: "shell"}]`, the model proposes commands, OGX executes them in an OpenShell sandbox, feeds output back.

## Prerequisites

- An OpenShell gateway running
- OGX server built from the `feat/containers-api` branch (PR #5853)
- Python 3.12+
- `openshell` and `grpcio` packages installed in the OGX venv

### Install OpenShell

```bash
curl -LsSf https://raw.githubusercontent.com/NVIDIA/OpenShell/main/install.sh | sh
```

### Start a local gateway

```bash
openshell sandbox create -- echo "gateway is ready"
```

On first run, OpenShell bootstraps a local Docker gateway automatically. Verify with:

```bash
openshell gateway list
```

Note the endpoint (e.g., `127.0.0.1:18080`) and update `examples/ogx-openshell-config.yaml` if it differs.

## Setup

```bash
cd openshell-ogx
uv sync
```

Install the OpenShell SDK in the OGX venv:

```bash
cd /path/to/llama-stack
uv pip install -e /path/to/openshell
```

## Run

### Start the OGX server

```bash
cd /path/to/llama-stack
uv run ogx run /path/to/sandboxing-anywhere/openshell-ogx/examples/ogx-openshell-config.yaml
```

Wait for `Application startup complete.`

### Containers CRUD test (no LLM, validates the provider works)

```bash
uv run python examples/containers_crud.py
```

Creates an OpenShell sandbox via the OGX Containers API, runs commands, verifies output, and cleans up.

### Responses API with shell tool (requires OPENAI_API_KEY)

```bash
export OPENAI_API_KEY=sk-...
uv run python examples/responses_shell_tool.py
```

Sends a Responses API request with a shell tool. The model writes and runs a Python script inside the OpenShell sandbox.

## How it works

OGX's `remote::openshell` provider translates Containers API calls to OpenShell gRPC:

| OGX endpoint | OpenShell gRPC |
|---|---|
| `POST /v1/containers` | `CreateSandbox` + `wait_ready` |
| `GET /v1/containers` | Read from local metadata |
| `GET /v1/containers/{id}` | Read from local metadata |
| `POST /v1/containers/{id}/exec` | `ExecSandbox` (streaming) |
| `DELETE /v1/containers/{id}` | `DeleteSandbox` |

The connection supports three modes (highest priority first):

1. `gateway_endpoint` in config (explicit `host:port`)
2. `cluster_name` in config (resolved via `~/.config/openshell/gateways/<name>/`)
3. Active cluster fallback (`~/.config/openshell/active_gateway`)

### Security model

Unlike the Docker provider (which uses basic `network_mode: none|bridge`), OpenShell provides:

- Per-binary network policies (L4 + L7)
- Credential isolation (agent never sees real tokens)
- Landlock filesystem confinement
- Seccomp syscall filtering

Network policy mapping from OGX's simple allowlist model to OpenShell's rich policy is planned for a follow-up. Currently, sandboxes use OpenShell's default policy.
