# Tutorial: Sandboxed Multi-Agent Supervisor with OpenShell

Step-by-step guide to the supervisor pattern: Claude as a brain in Anthropic's cloud dispatches tasks to specialized agents on your OpenShift cluster. Each agent reasons with a local model inside its own OpenShell sandbox with kernel-enforced security policy.

## What You Will Build

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
│  │   │  Network: vLLM + internal DB only             │   │
│  │   │  local_agent.py reasons with Qwen3-8B  ─────────> vLLM Pod
│  │   │  Reads PII, produces summary only             │   │ (cluster-internal)
│  │   └──────────────────────────────────────────────┘   │
│  │                                                       │
│  │   ┌─ Sandbox: api_integrator ────────────────────┐   │
│  │   │  Network: vLLM + scoped CRM API only          │   │
│  │   │  local_agent.py reasons with Qwen3-8B  ─────────> vLLM Pod
│  │   └──────────────────────────────────────────────┘   │
│  │                                                       │
│  │   ┌─ Sandbox: report_writer ─────────────────────┐   │
│  │   │  Filesystem: read-only, Network: vLLM only    │   │
│  │   │  local_agent.py reasons with Qwen3-8B  ─────────> vLLM Pod
│  │   └──────────────────────────────────────────────┘   │
│                                                          │
└──────────────────────────────────────────────────────────┘
```

Everything runs on your cluster. The worker pod polls Anthropic's queue (outbound HTTPS).
Each dispatched agent runs inside its own OpenShell sandbox with kernel-enforced policy.
vLLM calls are cluster-internal. The only thing leaving the cluster is the outbound
connection to Anthropic (task descriptions in, summarized results out).

## Prerequisites

1. **An OpenShift cluster** with OpenShell installed (gateway running as a cluster service)
2. **vLLM** serving a model on the cluster (we use Qwen3-8B; any OpenAI-compatible endpoint works)
3. **ant CLI** (Anthropic's CLI for Managed Agents)
4. **An Anthropic API key** (from console.anthropic.com)
5. **Python 3.12+** with `openai` and `anthropic` packages

## Part 1: Cluster Setup

### Step 1: Verify the OpenShell Gateway on Your Cluster

The OpenShell gateway manages sandbox lifecycle on the cluster. Verify it is running:

```bash
# From a machine with cluster access
openshell gateway list
# NAME          ENDPOINT                                                   TYPE    AUTH
# cluster-gw    https://openshell.apps.your-cluster.example.com            remote  oidc
```

If the gateway is not listed, deploy it following the
[OpenShell installation guide](https://github.com/NVIDIA/OpenShell).

### Step 2: Verify vLLM Is Serving

Confirm your model is accessible over the cluster-internal network:

```bash
# From inside the cluster (or via route)
curl -sk https://vllm-svc.your-namespace.svc.cluster.local:8000/v1/models
# {"data": [{"id": "qwen3-8b-fp8", ...}]}
```

### Step 3: Understand Per-Agent Policy (Live Updates)

Each agent gets its own network policy applied at runtime via `openshell policy update`.
Sandboxes start with a default policy (PyPI allowed for setup, everything else denied).
After `pip install`, dispatch.py narrows the policy to exactly what the agent needs:

```bash
# Data analyst: vLLM + internal database
openshell policy update agent-data-analyst \
    --add-endpoint "vllm-svc:443:full:rest:enforce" \
    --add-endpoint "postgres.data-ns:5432:full" \
    --binary "/sandbox/.venv/bin/python3" --wait

# API integrator: vLLM + scoped CRM API
openshell policy update agent-api-integrator \
    --add-endpoint "vllm-svc:443:full:rest:enforce" \
    --add-endpoint "salesforce-proxy:443:full:rest:enforce" \
    --binary "/sandbox/.venv/bin/python3" --wait

# Report writer: vLLM only
openshell policy update agent-report-writer \
    --add-endpoint "vllm-svc:443:full:rest:enforce" \
    --binary "/sandbox/.venv/bin/python3" --wait
```

The `--binary` flag means only `python3` inside the sandbox can reach the endpoint.
The `--wait` flag blocks until the sandbox proxy confirms the policy is loaded.
If the data analyst gets prompt-injected, it cannot reach the CRM API because that
endpoint was never added to its policy.

### Step 4: Test Sandbox Isolation on the Cluster

Verify that sandboxes running on the cluster actually isolate from each other:

```bash
# Create two sandboxes
openshell sandbox create --name agent-a --gateway cluster-gw
openshell sandbox create --name agent-b --gateway cluster-gw

# Write sensitive data in agent-a
openshell sandbox exec --name agent-a -- \
    sh -c 'echo "CONFIDENTIAL: salary data" > /sandbox/pii.txt'

# Try to read it from agent-b
openshell sandbox exec --name agent-b -- cat /sandbox/pii.txt
# cat: /sandbox/pii.txt: No such file or directory

# Verify network deny-all (without policy, outbound is blocked)
openshell sandbox exec --name agent-a -- \
    sh -c 'curl -s -m 5 https://example.com || echo "BLOCKED"'
# BLOCKED

# Clean up
openshell sandbox delete agent-a
openshell sandbox delete agent-b
```

This is not just container isolation. OpenShell adds:
- **Landlock** (kernel filesystem access control): the sandbox cannot read paths outside its policy
- **seccomp** (syscall filtering): blocks dangerous syscalls like `ptrace`, `mount`
- **L7 network policy**: per-binary, per-URL-path HTTP inspection via the sandbox proxy
- **Sandbox proxy**: every outbound connection is logged, denied connections are deduplicated

## Part 2: Anthropic Setup

### Step 5: Install the ant CLI

```bash
ANT_VERSION=1.9.1
ARCH=$(uname -m | sed 's/x86_64/amd64/' | sed 's/aarch64/arm64/')
OS=$(uname -s | tr '[:upper:]' '[:lower:]')

curl -fsSL \
  "https://github.com/anthropics/anthropic-cli/releases/download/v${ANT_VERSION}/ant_${ANT_VERSION}_${OS}_${ARCH}.tar.gz" \
  | tar -xz -C ~/bin ant

~/bin/ant --version
# ant version 1.9.1
```

### Step 6: Create Credentials, Environment, and Agent

```bash
# Create credentials file (keep separate from shell profile)
cat > ~/.ant-env << 'EOF'
export ANTHROPIC_API_KEY=sk-ant-api03-YOUR_KEY_HERE
EOF
chmod 600 ~/.ant-env
source ~/.ant-env

# Create a self-hosted environment (work queue)
~/bin/ant beta:environments create \
    --name supervisor \
    --config '{"type": "self_hosted"}'
# Returns env_01BJ21Cz... — save this

# Generate environment key in Console:
# https://platform.claude.com/workspaces/default/environments
# Click your environment > Generate environment key
# Returns sk-ant-oat01-... — save this

# Add both to credentials file
echo 'export ANTHROPIC_ENVIRONMENT_ID=env_01BJ21Cz...' >> ~/.ant-env
echo 'export ANTHROPIC_ENVIRONMENT_KEY=sk-ant-oat01-...' >> ~/.ant-env

# Create the brain agent with bash tool
~/bin/ant beta:agents create \
    --name supervisor-brain \
    --model claude-sonnet-4-6 \
    --tool bash
# Returns agent_01SBbV... — save this
```

### Step 7: Set the Brain's System Prompt

The brain needs to know about the sandboxed agents and how to call them.

Create `brain-system-prompt.txt`:

```text
You are a supervisor agent that decomposes complex requests into subtasks
and dispatches them to specialized worker agents.

Each agent runs in its own isolated OpenShell sandbox on the cluster with
kernel-enforced security policy (Landlock, seccomp, L7 network rules).
You never see raw data. You only see the summaries each agent returns.

To call an agent, use bash:
  python3 dispatch.py <agent_name> "<task description>"

Available agents:

1. data_analyst - Analyzes financial data, flags anomalies.
   Sandbox policy: deny-all network except internal DB and vLLM.

2. api_integrator - Queries external APIs with scoped tokens.
   Sandbox policy: scoped CRM API egress and vLLM only.

3. report_writer - Generates structured compliance reports.
   Sandbox policy: read-only filesystem, vLLM only.

When you receive a complex request:
1. Decompose it into subtasks, one per agent
2. Call each agent in parallel with clear, specific task descriptions
3. Collect the results
4. Synthesize a final answer from the agent summaries

Never process sensitive data yourself. Always delegate.
```

Update the agent via the Python SDK:

```python
import anthropic

client = anthropic.Anthropic()

with open("brain-system-prompt.txt") as f:
    system_prompt = f.read()

agent = client.beta.agents.update(
    agent_id="agent_01SBbV...",   # your agent ID
    system=system_prompt,
)
print(f"Updated agent: {agent.name}")
```

## Part 3: The Agent Code

### Step 8: dispatch.py (Sandbox Lifecycle Manager)

This runs in the worker pod (not inside sandboxes). For each dispatched agent, it:

1. Creates an OpenShell sandbox (default policy allows PyPI)
2. Installs dependencies inside the sandbox
3. Applies per-agent network policy via live `openshell policy update`
4. Uploads the agent script via base64
5. Runs the agent inside the sandbox
6. Collects stdout (summary only)
7. Deletes the sandbox

The full working `dispatch.py` is in the repo. Here is the sandbox lifecycle core:

```python
def run_in_sandbox(agent_name, task, config):
    sandbox_name = f"agent-{agent_name}-{os.getpid()}"

    try:
        # 1. Create sandbox (default policy allows PyPI, denies everything else)
        _run(f"openshell sandbox create --name {sandbox_name}")

        # 2. Install agent dependencies inside sandbox
        _run(f"openshell sandbox exec --name {sandbox_name} -- "
             f"pip3 install -q openai")

        # 3. Live policy update: allow vLLM, scoped to python3 binary only
        #    After this, sandbox can reach vLLM but nothing else.
        _run(f'openshell policy update {sandbox_name} '
             f'--add-endpoint "{VLLM_HOST}:443:full:rest:enforce" '
             f'--binary "/sandbox/.venv/bin/python3" '
             f'--wait')

        # 4. Upload agent script via base64 (avoids gRPC newline restriction)
        script = build_agent_script(config["system"], config["sample_data"], task)
        b64 = base64.b64encode(script.encode()).decode()
        _run(f'openshell sandbox exec --name {sandbox_name} -- '
             f'sh -c "echo {b64} | base64 -d > /sandbox/agent.py"')

        # 5. Run agent INSIDE sandbox (vLLM call goes through sandbox proxy)
        out, err, code = _run(
            f"openshell sandbox exec --name {sandbox_name} -- "
            f"python3 /sandbox/agent.py")

        return out

    finally:
        # 6. Always tear down
        _run(f"openshell sandbox delete {sandbox_name}")
```

The two-phase policy approach is intentional. Sandboxes start with a permissive
default (PyPI access for `pip install`). After setup, `openshell policy update`
narrows the policy to exactly what the agent needs. The `--wait` flag blocks until
the sandbox proxy has loaded the new policy revision, so there is no race between
policy application and agent execution.

### Why live policy update instead of static YAML?

We tested static policy YAML files (applied at `sandbox create` time with `--policy`).
They did not work: the proxy only recognized the last `network_policies` entry in the
YAML, ignoring earlier entries. Multiple named policies in a single YAML were silently
dropped.

`openshell policy update` works reliably because it is an incremental merge on the
running sandbox. Each `--add-endpoint` call adds one endpoint to the existing policy.
The `--binary` flag scopes which executables can use that endpoint. The `--wait` flag
confirms the policy is loaded before returning.

## Part 4: Deploy and Run

### Step 11: Deploy the Worker Pod

Create a Kubernetes deployment for the worker:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: supervisor-worker
spec:
  replicas: 1
  selector:
    matchLabels:
      app: supervisor-worker
  template:
    metadata:
      labels:
        app: supervisor-worker
    spec:
      containers:
      - name: worker
        image: your-registry/supervisor-worker:latest
        command: ["ant", "beta:worker", "poll", "--workdir", "/app"]
        env:
        - name: ANTHROPIC_ENVIRONMENT_ID
          valueFrom:
            secretKeyRef:
              name: anthropic-credentials
              key: environment-id
        - name: ANTHROPIC_ENVIRONMENT_KEY
          valueFrom:
            secretKeyRef:
              name: anthropic-credentials
              key: environment-key
        - name: OPENSHELL_GATEWAY
          value: "cluster-gw"
```

Create the secret and deploy:

```bash
# Create secret (do NOT put API key here, only the scoped environment key)
kubectl create secret generic anthropic-credentials \
    --from-literal=environment-id=env_01BJ21Cz... \
    --from-literal=environment-key=sk-ant-oat01-...

kubectl apply -f worker-deployment.yaml
```

The worker pod polls Anthropic's queue (outbound HTTPS). It has no inbound ports.
It creates OpenShell sandboxes on the cluster gateway for each dispatched agent.

### Step 12: Create a Session and Send a Task

From any machine with the Anthropic SDK (this does not need cluster access):

```python
import anthropic

client = anthropic.Anthropic()

session = client.beta.sessions.create(
    agent="agent_01SBbV...",               # your agent ID
    environment_id="env_01BJ21Cz...",      # your environment ID
)
print(f"Session: {session.id}")

client.beta.sessions.events.send(
    session_id=session.id,
    events=[{
        "type": "user.message",
        "content": [{"type": "text", "text": (
            "Run the quarterly business review. "
            "Cover revenue data analysis, pipeline status, "
            "and compliance posture."
        )}],
    }],
)
print("Message sent. Check worker pod logs.")
```

### Step 13: Watch the Sandboxed Execution

```bash
kubectl logs -f deployment/supervisor-worker
```

You should see:

```
level=INFO msg="claimed work" work_id=sesn_01DZ67bzm...
[dispatch] Agent: data_analyst (sandboxed)
[dispatch] Creating sandbox agent-data_analyst-12345 with policy-data-analyst.yaml
[dispatch] Agent reasoning inside sandbox with Qwen3-8B (cluster-internal)
[dispatch] Result from data_analyst: 3 anomalies flagged in EMEA...
[dispatch] Sandbox agent-data_analyst-12345 deleted

[dispatch] Agent: api_integrator (sandboxed)
...
[dispatch] Agent: report_writer (sandboxed)
...
level=INFO msg="dispatched tool" tool=bash is_error=false posted=true
```

Three sandboxes created on the cluster, three agents executed in isolation, three
sandboxes destroyed. Each agent reasoned with vLLM over the cluster-internal network,
constrained by its own security policy.

### Step 14: Read the Brain's Synthesized Response

```python
import anthropic

client = anthropic.Anthropic()

events = client.beta.sessions.events.list(
    session_id="sesn_01DZ67bzm...",  # your session ID
    limit=50,
)

for ev in events.data:
    ev_type = getattr(ev, "type", "")
    ct = getattr(ev, "content", None)

    if ev_type == "agent.message" and ct:
        for block in (ct if isinstance(ct, list) else [ct]):
            txt = getattr(block, "text", "")
            if txt and len(txt) > 100:
                print(txt)
```

## Appendix: Dev Mode (Local Testing)

For quick iteration without a cluster, you can run the worker locally and skip sandboxes.
This is useful for testing the brain-to-worker dispatch flow before deploying to the cluster.

```bash
source ~/.ant-env

# Install dependencies locally
pip install openai anthropic

# Run dispatch.py without sandboxes (agent reasoning happens in-process)
python3 dispatch.py data_analyst "Analyze Q3 revenue"

# Run the worker locally (no sandboxes, bash calls execute in-process)
~/bin/ant beta:worker poll --workdir /path/to/supervisor-pattern
```

This validates the Anthropic integration (brain dispatches, worker executes, results
flow back). But the agent reasoning runs unsandboxed in the worker process. Use this
for testing only. The production path is the cluster deployment with per-agent sandboxes.

## What Sandboxing Gives You

| Property | Without sandbox | With per-agent OpenShell sandbox |
|----------|----------------|--------------------------------|
| Filesystem | All agents share worker's FS | Per-agent, Landlock-enforced |
| Network | All agents share worker's network | Per-agent L7 policy (per-binary, per-URL-path) |
| Syscalls | Unrestricted | seccomp-filtered |
| Blast radius | Entire worker pod | One agent's sandbox |
| Credential exposure | All pod env vars | Only scoped env key, proxy-injected secrets |
| Post-session state | Persists in pod | Sandbox destroyed, no state leaks |
| vLLM access | Direct, unrestricted | Policy-enforced, cluster-internal only |

## Troubleshooting

**Worker pod doesn't pick up sessions:**
- Check `ANTHROPIC_ENVIRONMENT_ID` and `ANTHROPIC_ENVIRONMENT_KEY` in the secret
- Environment key is generated in the Console, not via the CLI
- If the worker claimed a stale session, restart the pod

**Sandbox can't reach vLLM:**
- Verify the vLLM service is reachable from the sandbox: check the policy YAML
  has the correct cluster-internal hostname and port
- On OpenShift, sandboxes and vLLM share the cluster network; policy rules
  control which sandboxes can reach which services

**dispatch.py returns empty results:**
- Qwen3 models use "thinking" mode by default, producing invisible chain-of-thought
- Set `DISABLE_THINKING=true` in the worker pod env
- Or add `extra_body={"chat_template_kwargs": {"enable_thinking": False}}` in local_agent.py

**"content must be an array" error when creating sessions:**
- `events.send` requires content as: `[{"type": "text", "text": "..."}]`

**Sandbox creation slow:**
- First sandbox on a gateway takes longer (image pull). Subsequent ones reuse the cache.
- Pre-pull the sandbox base image on cluster nodes
