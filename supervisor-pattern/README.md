# Supervisor Pattern: One Brain, Many Agents, One Worker

Claude Managed Agent as a centralized brain that decomposes tasks and dispatches them to specialized local agents via `dispatch.py`. Each agent reasons independently using Qwen3-8B on vLLM. One self-hosted worker, one environment, parallel execution.

## Validated Session

- **Brain (Managed Agent):** `agent_01SBbVXmU32VifmfTJpTjGKe`
- **Session:** `sesn_01EwwWcF4pMCBogsuqnJvduD`
- **Toolset:** `agent_toolset_20260401` (bash)
- **Model:** Claude on Anthropic cloud (brain), Qwen3-8B via vLLM (workers)

## Architecture

```
Anthropic Cloud (Brain)              Your Infrastructure (Worker)
┌──────────────────────────┐         ┌──────────────────────────────────────┐
│  Claude Managed Agent    │  bash   │  ant CLI worker (polls queue)       │
│  System prompt lists     │ ──────> │  workdir contains dispatch.py       │
│  3 agent roles           │         │                                     │
│  Uses bash tool to call  │         │  dispatch.py receives:              │
│  dispatch.py in parallel │         │    agent_name + task description    │
│                          │ <────── │                                     │
│  Synthesizes results     │ results │  Runs local agent loop:             │
│  from all 3 agents       │         │    Qwen3-8B via vLLM                │
└──────────────────────────┘         │    Returns summary to stdout        │
                                     └──────────────────────────────────────┘
```

The brain sends parallel bash tool calls like:

```bash
python3 dispatch.py data_analyst "analyze Q3 revenue and flag anomalies"
python3 dispatch.py api_integrator "summarize the pipeline status"
python3 dispatch.py report_writer "generate a compliance status report"
```

Each call runs a local agent loop: dispatch.py picks the agent config (system prompt + sample data), calls vLLM for reasoning, and returns the result on stdout. The brain collects all three results and synthesizes a final answer.

## Setup

### Prerequisites

- `ant` CLI v1.9.1+ (`~/bin/ant`)
- `~/.ant-env` with `ANTHROPIC_API_KEY`, `ANTHROPIC_ENVIRONMENT_ID`, `ANTHROPIC_ENVIRONMENT_KEY`
- vLLM serving Qwen3-8B (or any OpenAI-compatible endpoint)
- Python 3 with `openai` package installed

### Step 1: Create the Environment

```bash
source ~/.ant-env

# Create a self-hosted environment (one time)
~/bin/ant beta:environments create --name supervisor \
    --config '{"type": "self_hosted"}'

# Generate an environment key in the Console:
# https://platform.claude.com/workspaces/default/environments
# Click your environment > Generate environment key
# Add to ~/.ant-env as ANTHROPIC_ENVIRONMENT_KEY
```

### Step 2: Create the Brain Agent

The brain is a Managed Agent with the `bash` tool and a system prompt that lists the three agents and how to call them.

```bash
source ~/.ant-env

# Create the agent with bash tool access
~/bin/ant beta:agents create --name supervisor-brain \
    --model claude-sonnet-4-6 \
    --tool bash
```

Then set the system prompt (from `brain-system-prompt.txt`):

```python
import anthropic

client = anthropic.Anthropic()

# Read the system prompt
with open("brain-system-prompt.txt") as f:
    system_prompt = f.read()

# Update the agent's system prompt
agent = client.beta.agents.update(
    agent_id="agent_01SBbVXmU32VifmfTJpTjGKe",
    system_prompt=system_prompt,
)
```

### Step 3: Start the Worker

The worker polls the environment queue. Its workdir must contain `dispatch.py`.

```bash
source ~/.ant-env

# Ensure dispatch.py is in the workdir
cp dispatch.py /workspace/dispatch.py

# Start polling
~/bin/ant beta:worker poll --workdir /workspace
```

### Step 4: Create a Session and Send a Task

```python
import anthropic

client = anthropic.Anthropic()

# Create a session
session = client.beta.sessions.create(
    agent_id="agent_01SBbVXmU32VifmfTJpTjGKe",
)
print(f"Session: {session.id}")

# Send a user message
client.beta.sessions.messages.create(
    session_id=session.id,
    role="user",
    content="Generate a quarterly business review covering revenue data, pipeline status, and compliance posture.",
)
```

The brain decomposes the request, sends parallel bash calls to dispatch.py, each agent reasons with vLLM, and the brain synthesizes all results into a single response.

## How dispatch.py Works

```
brain sends: python3 dispatch.py data_analyst "analyze Q3 revenue"
                │
                ▼
dispatch.py receives agent_name + task via sys.argv
                │
                ▼
Looks up AGENT_CONFIGS[agent_name]
  - system prompt (agent personality)
  - sample data (simulated context)
                │
                ▼
Calls vLLM (Qwen3-8B) with:
  - system message from config
  - user message with data + task
                │
                ▼
Returns result JSON on stdout
  - agent name, model, task, result text
```

Three agents are configured:

| Agent | Role | Sample Data |
|-------|------|-------------|
| `data_analyst` | Financial data analysis, PII handling, anomaly detection | Q3 revenue by region with flagged anomalies |
| `api_integrator` | CRM/API data summarization | Salesforce pipeline with deal status |
| `report_writer` | Structured compliance report generation | GDPR/SOC2 audit items |

## Files

| File | Purpose |
|------|---------|
| `dispatch.py` | Receives agent name + task, runs local vLLM reasoning loop |
| `brain-system-prompt.txt` | System prompt for the Claude Managed Agent (lists agents, dispatch syntax) |
| `local_agent.py` | Full tool-use agent loop (multi-turn, reads/writes files in sandbox) |
| `supervisor_test.py` | End-to-end test: creates OpenShell sandboxes, seeds data, dispatches tasks |
| `policy-worker.yaml` | Network policy allowing vLLM and PyPI access |

## What Was Validated

1. **Parallel dispatch:** Brain sent three bash tool calls concurrently, one per agent
2. **Local reasoning:** Each dispatch.py call ran its own Qwen3-8B inference, no Anthropic API for worker reasoning
3. **Task specialization:** Different system prompts produced role-appropriate analysis from the same model
4. **Result synthesis:** Brain collected all three agent summaries and produced a unified quarterly review
5. **Single environment:** One worker, one environment, no need for multiple environments per agent role

## Agent Identity

Agent identity is defined **at runtime**, not at deployment time. When the brain calls `python3 dispatch.py data_analyst "..."`, the string `data_analyst` is a key in the `AGENT_CONFIGS` dictionary. It selects a system prompt and context data for the local model. There is no pre-registered agent, no separate process, no dedicated container. The agent exists only for the duration of that one dispatch call.

In the current setup, all agents share the same sandbox. For per-agent data partitioning, see the production path below.

## Production Path

For production, each `dispatch.py` call would:

1. Create an OpenShell sandbox with a per-agent policy (e.g., `policy-data-analyst.yaml` with DB-only egress)
2. Upload the task and agent script into the sandbox
3. Run the agent loop inside the sandbox
4. Collect results and tear down the sandbox

This gives you per-agent data partitioning and network isolation, while keeping the same single-worker architecture.

See `supervisor_test.py` for the full sandbox lifecycle (create, seed, dispatch, verify isolation, cleanup).

## Related

- [Anthropic Self-Hosted Sandboxes](../anthropic-self-hosted/) (Experiment 3)
- [OpenShell Agents SDK](../openshell-agents-sdk/) (Experiment 1)
