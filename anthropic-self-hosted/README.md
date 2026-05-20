# OpenShell + Anthropic Self-Hosted Sandboxes

Run [Anthropic Claude](https://docs.anthropic.com/en/docs/agents-and-tools/managed-agents) self-hosted agent sessions inside [NVIDIA OpenShell](https://github.com/NVIDIA/OpenShell) sandboxes on Kubernetes/OpenShift. Anthropic orchestrates the agent loop in the cloud; your infrastructure executes tool calls inside policy-governed sandboxes.

## What this demonstrates

1. **Local Docker sandboxes**: A worker on your Mac polls Anthropic for sessions, spawns an OpenShell sandbox per session via the local Docker driver.

2. **OpenShift/Kubernetes sandboxes**: Same worker, but sandboxes run as pods on a remote cluster. The gateway runs on-cluster via Helm; the CLI on your Mac talks to it through an NLB.

## Architecture

```
Anthropic Cloud
  |
  |  (work queue: sessions, tool calls, results)
  v
Your Mac
  ant beta:worker poll --on-work ./spawn.sh
    |
    |  (per session)
    v
  openshell sandbox create -g <gateway> --name ant-<session-id> -- ...
    |
    |  (gRPC: CreateSandbox, ConnectSupervisor, ForwardTcp)
    v
OpenShell Gateway (on-cluster or local)
    |
    v
Sandbox Pod/Container
  - ant CLI installed at boot
  - ant beta:worker run executes tool calls
  - egress governed by OpenShell policy
  - OCSF audit logging
```

## Versions tested

| Component | Version | Source |
|-----------|---------|--------|
| `ant` CLI | 1.9.1 | [anthropics/anthropic-cli](https://github.com/anthropics/anthropic-cli/releases/tag/v1.9.1) |
| `openshell` CLI | 0.0.43-dev / 0.0.44 | [NVIDIA/OpenShell](https://github.com/NVIDIA/OpenShell) (built from source or Homebrew) |
| OpenShell Gateway | 0.0.44 | `ghcr.io/nvidia/openshell/gateway:0.0.44` |
| OpenShell Supervisor | 0.0.44 | `ghcr.io/nvidia/openshell/supervisor:0.0.44` |
| Sandbox base image | latest | `ghcr.io/nvidia/openshell-community/sandboxes/base:latest` |
| Helm chart | 0.0.44 | `oci://ghcr.io/nvidia/openshell/helm-chart` |

## Prerequisites

- An Anthropic API key with access to self-hosted environments
- `ant` CLI v1.9.1+ installed (`~/bin/ant` or via GitHub releases)
- `openshell` CLI matching the gateway version
- For local mode: Docker running
- For OpenShift mode: `oc` CLI authenticated to a cluster

## Setup

### 1. Configure Anthropic credentials

Create `~/.ant-env` with your self-hosted environment credentials:

```bash
export ANTHROPIC_API_KEY=sk-ant-api03-...
export ANTHROPIC_ENVIRONMENT_ID=env_...
export ANTHROPIC_ENVIRONMENT_KEY=sk-ant-oat01-...
```

Get these from the [Claude Platform Console](https://platform.claude.com/workspaces/default/environments) by creating a self-hosted environment:

```bash
source ~/.ant-env
ant beta:environments create --name self-hosted --config '{"type": "self_hosted"}'
```

### 2a. Local Docker gateway

No extra setup needed. OpenShell bootstraps a local Docker gateway on first use:

```bash
openshell sandbox create -- echo "gateway ready"
```

### 2b. OpenShift/Kubernetes gateway

Deploy the gateway via Helm:

```bash
oc new-project openshell

helm upgrade --install openshell \
  oci://ghcr.io/nvidia/openshell/helm-chart \
  --version 0.0.44 \
  -n openshell \
  --set server.disableTls=true
```

Expose via LoadBalancer (not Route, see known issues):

```bash
oc patch svc openshell -n openshell -p '{"spec":{"type":"LoadBalancer"}}'
```

Wait for the external address:

```bash
oc get svc openshell -n openshell -w
```

Register the gateway:

```bash
LB=$(oc get svc openshell -n openshell -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')
openshell gateway add "http://${LB}:8080" --name openshift-cluster
```

### 3. Start the worker

```bash
source ~/.ant-env

# Local Docker
ant beta:worker poll --on-work ./scripts/spawn-docker.sh

# OpenShift
ant beta:worker poll --on-work ./scripts/spawn-openshift.sh
```

### 4. Send a session

From another terminal (or via the API):

```bash
source ~/.ant-env

# Create an agent with tools
AGENT_ID=$(ant beta:agents create \
  --model claude-sonnet-4-20250514 \
  --tools '["bash", "text_editor", "file_read"]' \
  --output-format json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")

# Start a session
SESSION_ID=$(ant beta:sessions create \
  --agent-id "$AGENT_ID" \
  --environment-id "$ANTHROPIC_ENVIRONMENT_ID" \
  --output-format json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")

# Send a message
ant beta:messages create \
  --session-id "$SESSION_ID" \
  --content "Run 'uname -a' and tell me where you're running"
```

The worker picks up the session, creates an OpenShell sandbox, and the agent's tool calls execute inside it.

## Scripts

| Script | Target | Description |
|--------|--------|-------------|
| `scripts/spawn-docker.sh` | Local Docker | Creates a sandbox via the local Docker gateway |
| `scripts/spawn-openshift.sh` | OpenShift/K8s | Creates a sandbox pod on the remote cluster |

Both scripts:
- Create a uniquely named sandbox per session (`ant-<session-id-prefix>`)
- Install the `ant` CLI inside the sandbox at boot
- Forward Anthropic session credentials into the sandbox
- Run `ant beta:worker run` to execute tool calls
- Clean up the sandbox on exit (`--no-keep`)

## Known issues

**OIDC + supervisor relay** ([NVIDIA/OpenShell#1470](https://github.com/NVIDIA/OpenShell/issues/1470)): When the gateway has OIDC authentication enabled, `ConnectSupervisor` and `RelayStream` RPCs are rejected because they are not in the OIDC-exempt list. The supervisor does not carry an OIDC token by design (it authenticates via mTLS). Workaround: disable OIDC on the gateway or wait for the upstream fix.

**OpenShift Routes break HTTP CONNECT**: The `sandbox connect` SSH relay uses HTTP CONNECT, which OpenShift's HAProxy-based Routes strip. Use a LoadBalancer service (TCP passthrough via NLB on AWS) or `kubectl port-forward` instead of a Route.

**CLI/gateway version alignment**: The CLI and gateway should be the same version. Proto mismatches between versions can cause subtle failures. Build the CLI from the same source as the gateway, or use matching release versions.
