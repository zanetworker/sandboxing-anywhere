# Sandboxing Anywhere: Architecture and Reproduction Guide

## What this repo validates

OpenShell works as a sandbox backend across every major agent platform and every sandboxing mode. This doc explains the architecture end-to-end so you can reproduce each experiment.

## Prerequisites

```bash
# OpenShell CLI
openshell --version  # 0.0.0+

# Anthropic ant CLI
~/bin/ant --version  # 1.9.1

# Anthropic credentials (scoped, not in shell profile)
cat ~/.ant-env
# ANTHROPIC_API_KEY=sk-ant-api03-...
# ANTHROPIC_ENVIRONMENT_ID=env_...
# ANTHROPIC_ENVIRONMENT_KEY=sk-ant-oat01-...

# OpenShell gateway (at least one)
openshell gateway list
# local-docker (local, podman/docker)
# rhai-gw (remote, RHOAI cluster)

# vLLM models (on RHOAI cluster)
curl -sk https://vllm-api-user-egallen.apps.ocp.cloud.rhai-tmm.dev/v1/models
# qwen3-8b-fp8
```

## Experiment 1: OpenAI Agents SDK + OpenShell (Mode 3)

**Dir:** `openshell-agents-sdk/`

**What it does:** The OpenAI Agents SDK sandbox extensions let the developer plug in a sandbox provider. We built an `OpenShellSandboxClient` that drops in alongside Docker and Unix local backends. The agent logic runs unsandboxed. The code it generates executes inside an OpenShell sandbox.

**Architecture:**
```
Your machine (unsandboxed)          OpenShell sandbox
├── Agent loop (OpenAI SDK)         ├── Code execution
├── Full credentials                ├── File I/O
├── Full API access                 ├── Landlock + seccomp
└── Calls SandboxRunConfig ────────>└── Network policy enforced
```

**How to run:**
```bash
cd openshell-agents-sdk
pip install openai-agents[openshell] openshell
python examples/session_test.py   # No LLM, tests sandbox lifecycle
python examples/agent_test.py     # Runs SandboxAgent with shell tools
```

**Key code:**
```python
from agents.extensions.sandbox.openshell import (
    OpenShellSandboxClient, OpenShellSandboxClientOptions,
)

result = await Runner.run(
    agent,
    "Fix the bug and run the tests.",
    run_config=RunConfig(
        sandbox=SandboxRunConfig(
            client=OpenShellSandboxClient(),
            options=OpenShellSandboxClientOptions(cluster="my-cluster"),
        ),
    ),
)
```

**Upstream:** [PR #3469](https://github.com/openai/openai-agents-python/pull/3469), [Issue #3468](https://github.com/openai/openai-agents-python/issues/3468)

## Experiment 2: OpenAI Agents SDK + OpenShell + Self-Hosted Models (Mode 3)

**Dir:** `openshell-agents-sdk-self-hosted/`

**What it does:** Same as Experiment 1, but the model runs on your RHOAI cluster (vLLM) instead of OpenAI's API. The entire stack is self-hosted: vLLM serves the model, OpenShell sandboxes the execution.

**Architecture:**
```
Your machine                        OpenShell sandbox       RHOAI cluster
├── Agent loop (OpenAI SDK)         ├── Code execution      ├── vLLM
├── Points to vLLM endpoint         ├── File I/O            ├── qwen3-8b-fp8
└── No OpenAI API calls ───────────>└── Policy enforced     └── On-cluster inference
```

**How to run:**
```bash
cd openshell-agents-sdk-self-hosted
pip install openai-agents[openshell] openshell

# Point SDK at vLLM instead of OpenAI
export VLLM_BASE_URL=https://vllm-api-user-egallen.apps.ocp.cloud.rhai-tmm.dev/v1
python examples/self_hosted_test.py
```

**Key code:**
```python
from openai import AsyncOpenAI
from agents import Agent, Runner, OpenAIChatCompletionsModel

vllm_client = AsyncOpenAI(
    base_url="https://vllm-api-user-egallen.apps.ocp.cloud.rhai-tmm.dev/v1",
    api_key="unused",
)

agent = SandboxAgent(
    name="Self-Hosted Agent",
    model=OpenAIChatCompletionsModel(model="qwen3-8b-fp8", openai_client=vllm_client),
    ...
)
```

**What was validated:** Qwen3-8B on the RHOAI GPU cluster analyzed files inside an OpenShell sandbox. No OpenAI API calls. Model inference on your GPUs, code execution in your sandbox.

## Experiment 3: Anthropic Self-Hosted Sandboxes + OpenShell (Mode 2)

**Dir:** `anthropic-self-hosted/`

**What it does:** Anthropic runs the Claude brain in their cloud. Your worker polls their work queue, claims sessions, and executes tool calls inside OpenShell sandboxes. The brain sends code. Your sandbox runs it. Only summarized results go back.

**Architecture:**
```
Anthropic Cloud                     Your infrastructure
├── Claude model                    ├── ant worker (polls queue)
├── Orchestration                   ├── OpenShell sandbox (per session)
├── Tool routing                    ├── Code execution
├── Retry logic                     ├── File system isolated
└── Sends tool calls ──────────────>├── Network: deny-all default
    <── results posted back ────────├── Credentials: not in sandbox
                                    └── Torn down when session completes
```

**Setup:**
```bash
# 1. Create ant-env (one time)
cat > ~/.ant-env << 'EOF'
export ANTHROPIC_API_KEY=sk-ant-api03-...
export ANTHROPIC_ENVIRONMENT_ID=env_01BJ21Cz8SLSviu7wfH6XFW3
export ANTHROPIC_ENVIRONMENT_KEY=sk-ant-oat01-...
EOF

# 2. Create environment and agent (one time)
source ~/.ant-env
~/bin/ant beta:environments create --name self-hosted \
    --config '{"type": "self_hosted"}'
~/bin/ant beta:agents create --name secure-agent \
    --model claude-sonnet-4-6

# 3. Generate environment key in Console:
# https://platform.claude.com/workspaces/default/environments
# Click your environment > Generate environment key
# Add to ~/.ant-env as ANTHROPIC_ENVIRONMENT_KEY

# 4. Run the worker
source ~/.ant-env
~/bin/ant beta:worker poll --workdir /workspace
```

**Tested on:**
- **Podman driver:** Rootless container, LSM enablement (Landlock, SELinux). No Kubernetes needed.
- **OpenShift driver:** Kubernetes pod on Red Hat OpenShift with full policy enforcement.

**Key finding:** The integration required no changes to the Anthropic worker model. OpenShell replaces the container in Anthropic's container-per-session pattern.

## Experiment 4: Responses API + Containers API + OpenShell (Mode 2)

**Dir:** `openshell-ogx/`

**What it does:** The Responses API (implemented in the open by OGX) creates sandboxes, executes commands, and feeds output back to the model. The Containers API is the CRUD layer for managing those sandboxes. We built an OpenShell provider for the Containers API.

**Architecture:**
```
Client (one API call)               OGX Server              OpenShell
├── responses.create()              ├── Responses API        ├── Sandbox created
├── tools: shell + container_auto   ├── Containers API       ├── Commands executed
├── model: Llama-4-Maverick         ├── vLLM inference       ├── Files confined
└── input: "analyze data" ─────────>└── Routes to provider ─>├── Torn down when done
                                                             └── Credentials: proxy-injected
```

**Key code:**
```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8321/v1")

response = client.responses.create(
    model="meta-llama/Llama-4-Maverick-17B-128E",
    tools=[{
        "type": "shell",
        "environment": {"type": "container_auto"},
    }],
    input="Analyze the CSV and plot the results.",
)
```

**Status:** Responses API in OGX is mature and OpenResponses-compliant. Containers API with OpenShell provider is in active development.

**Upstream:** [OGX PR #5853](https://github.com/ogx-ai/ogx/pull/5853)

## Experiment 5: Supervisor Pattern (Multi-Agent, Sandboxed)

**Dir:** `supervisor-pattern/`

**What it does:** A Claude Managed Agent (brain) decomposes tasks and sends parallel bash tool calls to `dispatch.py`. For each call, `dispatch.py` creates an OpenShell sandbox on the cluster, applies a per-agent network policy via live `openshell policy update`, runs the agent reasoning with Qwen3-8B via vLLM inside the sandbox, collects the summary, and tears down the sandbox.

**Validated sessions:** `sesn_01EwwWcF4pMCBogsuqnJvduD`, `sesn_01DZ67bzmQ34zPHP8uh4UrpC`. Agent `agent_01SBbVXmU32VifmfTJpTjGKe`, toolset `agent_toolset_20260401`.

**Architecture:**
```
    Anthropic Cloud (brain)
         ^                      |
         | posts results        | dispatches bash tool calls
         | (outbound HTTPS)     | (via work queue)
         |                      v
┌──────────────────────────────────────────────────────────┐
│  Your OpenShift Cluster                                  │
│                                                          │
│  Worker Pod (polls Anthropic queue)                      │
│  ├── dispatch.py (sandbox lifecycle manager)             │
│  │                                                       │
│  │   ┌─ Sandbox: data_analyst ──────────────────────┐   │
│  │   │  Landlock + seccomp enforced                  │   │
│  │   │  Network: vLLM only (binary-scoped)           │   │
│  │   │  Agent reasons with Qwen3-8B via vLLM  ─────────> vLLM Pod
│  │   └──────────────────────────────────────────────┘   │
│  │                                                       │
│  │   ┌─ Sandbox: api_integrator ────────────────────┐   │
│  │   │  Network: vLLM + scoped CRM API               │   │
│  │   │  Agent reasons with Qwen3-8B via vLLM  ─────────> vLLM Pod
│  │   └──────────────────────────────────────────────┘   │
│  │                                                       │
│  │   ┌─ Sandbox: report_writer ─────────────────────┐   │
│  │   │  Network: vLLM only, read-only FS             │   │
│  │   │  Agent reasons with Qwen3-8B via vLLM  ─────────> vLLM Pod
│  │   └──────────────────────────────────────────────┘   │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

**Sandbox lifecycle per agent:**
```bash
# 1. Create sandbox (default policy: PyPI allowed, everything else denied)
openshell sandbox create --name agent-data-analyst

# 2. Install dependencies (default policy allows PyPI)
openshell sandbox exec --name agent-data-analyst -- pip3 install -q openai

# 3. Apply per-agent network policy (live update, binary-scoped)
openshell policy update agent-data-analyst \
    --add-endpoint "vllm-svc:443:full:rest:enforce" \
    --binary "/sandbox/.venv/bin/python3" --wait

# 4. Upload agent script (base64, gRPC rejects newlines)
openshell sandbox exec --name agent-data-analyst -- \
    sh -c "echo <b64> | base64 -d > /sandbox/agent.py"

# 5. Run agent INSIDE sandbox (vLLM call policy-enforced)
openshell sandbox exec --name agent-data-analyst -- python3 /sandbox/agent.py

# 6. Tear down
openshell sandbox delete agent-data-analyst
```

**What was validated:**
1. **Per-agent sandboxes:** Three sandboxes created, used, and destroyed per dispatch. Each with its own kernel-enforced policy boundary.
2. **Live policy update:** `openshell policy update --add-endpoint --binary --wait` adds vLLM access scoped to python3. Without the update, sandbox returns 403 for vLLM. After, it succeeds.
3. **Parallel dispatch:** Brain sent three bash calls concurrently, one per agent.
4. **Sandboxed reasoning:** Qwen3-8B inference ran inside each sandbox via vLLM (cluster-internal, policy-enforced). Google.com blocked at all times.
5. **File system isolation:** Agent A's `/sandbox/pii.txt` not visible from Agent B.
6. **Result synthesis:** Brain collected all summaries and produced a unified QBR.
7. **Single environment:** One worker, one Anthropic environment, three sandboxes.

**Key learnings:**
- Static policy YAML at create time does not work for multiple endpoints (only the last `network_policies` entry is recognized). Use `openshell policy update` instead.
- The `--binary` flag is required on `--add-endpoint`. Without it, the endpoint is added but no binary is authorized to use it.
- OpenShift gateway is required for vLLM access. Local Docker gateway blocks all outbound HTTPS by default (correct behavior).

**Files:**
- `dispatch.py` — Sandbox lifecycle manager: create, policy update, exec, delete per agent
- `brain-system-prompt.txt` — System prompt for the Claude Managed Agent
- `local_agent.py` — Full tool-use agent loop (multi-turn, reads/writes files in sandbox)
- `supervisor_test.py` — End-to-end test with sandbox creation, data seeding, isolation verification
- `policy-worker.yaml` — Reference network policy (vLLM + PyPI)
- `TUTORIAL.md` — Step-by-step reproduction guide

## OpenShell Concepts

### Gateways

A gateway manages sandbox lifecycle. You can have multiple.

```bash
openshell gateway list
# local-docker    http://127.0.0.1:18080   local
# rhai-gw         http://...rhai-tmm.dev    remote
```

### Sandboxes

Each sandbox is an isolated execution environment with its own file system, network namespace, and policy.

```bash
openshell sandbox create --name my-sandbox --gateway local-docker
openshell sandbox exec --name my-sandbox -- whoami
openshell sandbox exec --name my-sandbox -- ls /sandbox
openshell sandbox delete my-sandbox
```

**Default working directory:** `/sandbox` (not `/workspace`)

### Policy YAML

Controls what the sandbox can access. See `examples/local-inference/sandbox-policy.yaml` in the OpenShell repo for the canonical format.

```yaml
version: 1

filesystem_policy:
  include_workdir: true
  read_only: [/usr, /lib, /etc]
  read_write: [/sandbox, /tmp]

landlock:
  compatibility: best_effort

process:
  run_as_user: sandbox
  run_as_group: sandbox

network_policies:
  vllm:
    name: vLLM
    endpoints:
      - host: vllm-service.namespace.svc.cluster.local
        port: 8000
    binaries:
      - path: /usr/bin/python3.12
```

**Without a policy file:** Default policy allows pip (PyPI) but blocks arbitrary outbound connections. Runs as default user with venv PATH intact.

**With `process.run_as_user: sandbox`:** Changes user context, which loses the venv PATH. Use full paths (`/sandbox/.venv/bin/python3`) or skip the process section.

### Drivers

| Driver | Where it runs | Use case |
|--------|--------------|----------|
| local-docker | Docker/Podman on your machine | Development |
| podman | Rootless Podman with LSM | Development + security |
| openshift | Kubernetes pod on OpenShift | Production |
| libkrun | microVM on Apple Silicon | Hardware isolation |

### CLI Gotchas

```bash
# Sandbox name is positional for create/delete, --name for exec
openshell sandbox create --name my-sandbox    # WRONG
openshell sandbox create my-sandbox           # WRONG (no --name needed)
openshell sandbox exec --name my-sandbox -- cmd  # RIGHT
openshell sandbox exec my-sandbox -- cmd      # WRONG (treats name as command)

# Newlines in exec commands are rejected by gRPC
openshell sandbox exec --name x -- python3 -c 'import os\nprint(os.getcwd())'  # FAILS
# Write to file first, then execute the file

# Default working directory is /sandbox, not /workspace
openshell sandbox exec --name x -- pwd  # /sandbox

# base64 upload for large files can hang; use small chunks or --workdir mount
```

## Anthropic Concepts

### Environments

A self-hosted environment is a work queue. Your worker polls it. Each environment has its own ID and key.

```bash
source ~/.ant-env
~/bin/ant beta:environments list   # List all
~/bin/ant beta:environments create --name my-env --config '{"type": "self_hosted"}'
```

**Environment key:** Generated in the Console (platform.claude.com). Not the same as your API key. The worker uses the environment key, never your API key.

### Agents

An agent is a configuration (model, system prompt, skills). Sessions run against agents.

```bash
~/bin/ant beta:agents create --name my-agent --model claude-sonnet-4-6
~/bin/ant beta:agents list
```

### Sessions

A session is a conversation between a user and an agent. When a session targets a self-hosted environment, Anthropic enqueues it and your worker picks it up.

```bash
~/bin/ant beta:sessions create --agent my-agent --environment-id env_...
~/bin/ant beta:sessions list
```

### Worker

The worker polls the environment's work queue, claims sessions, executes tool calls, and posts results back.

```bash
# In-process (tools run in current process)
~/bin/ant beta:worker poll --workdir /workspace

# Container per session (tools run in a fresh container)
~/bin/ant beta:worker poll --on-work ./spawn.sh
```

**spawn.sh** is where OpenShell plugs in. Instead of `docker run`, the script calls `openshell sandbox create` and runs the worker inside it.

### ant CLI location

Installed at `~/bin/ant` (not in homebrew PATH). Version 1.9.1 (latest as of May 2026).

```bash
~/bin/ant --version
```

### ant-env

Credentials are scoped to the ant CLI only. Do not set `ANTHROPIC_API_KEY` in your shell profile or Claude Code will try to use direct Anthropic API instead of Vertex.

```bash
source ~/.ant-env  # Only when running ant commands
```

## Cluster Resources

### RHOAI Cluster (apps.ocp.cloud.rhai-tmm.dev)

```bash
# Models available
curl -sk https://vllm-api-user-egallen.apps.ocp.cloud.rhai-tmm.dev/v1/models
# qwen3-8b-fp8

# OpenShell gateway
openshell gateway list | grep rhai
# rhai-gw  http://openshell-user-azaalouk.apps.ocp.cloud.rhai-tmm.dev
```

### Sandbox Cluster (apps.cluster-nrpwk...)

```bash
# Models available
curl -sk https://llama3-2-8b-test.apps.cluster-nrpwk.nrpwk.sandbox2474.opentlc.com/v1/models
# llama3-2-8b

# OpenShell gateway
openshell gateway list | grep openshift
# openshift-gw  https://openshell-openshell.apps.cluster-nrpwk...
```

## Reproducing Each Experiment

### Quickstart: test sandbox isolation (2 minutes)

```bash
openshell sandbox create --name test-a --gateway local-docker
openshell sandbox create --name test-b --gateway local-docker

# Write secret data to sandbox A
openshell sandbox exec --name test-a -- sh -c "echo SECRET > /tmp/secret.txt"

# Try to read it from sandbox B
openshell sandbox exec --name test-b -- cat /tmp/secret.txt
# No such file or directory

# Clean up
openshell sandbox delete test-a
openshell sandbox delete test-b
```

### Quickstart: test Anthropic self-hosted (5 minutes)

```bash
source ~/.ant-env
~/bin/ant beta:worker poll --workdir /sandbox &

# In another terminal, create a session
~/bin/ant beta:sessions create --agent secure-agent
# Send a message through the Console UI
# Watch the worker execute tool calls in the terminal
```

### Quickstart: test deny-all network (1 minute)

```bash
openshell sandbox create --name net-test --gateway local-docker
openshell sandbox exec --name net-test -- curl -s -m 5 https://example.com
# Connection refused or timeout (deny-all working)

openshell sandbox exec --name net-test -- pip3 install requests
# Works (pip is allowed by default policy)

openshell sandbox delete net-test
```
