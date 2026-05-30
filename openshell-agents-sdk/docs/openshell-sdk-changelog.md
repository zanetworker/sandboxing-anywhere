# OpenShell Python SDK Changes (v0.0.44 to v0.0.52)

Changes to the OpenShell Python SDK between the version our extension was built against and the current upstream HEAD. None of these break our integration, all are improvements.

## 1. TlsConfig: three trust profiles instead of one

The old SDK required all three cert paths. If you wanted TLS at all, you had to provide a full mTLS setup. The new SDK makes every field optional, giving you three distinct trust profiles.

### What is TLS and why does it matter here?

When the SDK talks to the OpenShell gateway, it uses gRPC over the network. TLS encrypts that connection so nobody can sniff the commands you're sending or the data coming back. There are three levels of trust:

**Level 1: No TLS (plaintext)**

```
SDK -------- plaintext gRPC --------> Gateway
             (anyone can read this)
```

This is what we use with `disable_tls = true` on the local gateway. Fine for localhost, dangerous over a network.

**Level 2: Server TLS (CA-only)**

```
SDK -------- encrypted gRPC --------> Gateway
             (SDK verifies the gateway              presents a cert signed by a CA
              the SDK trusts)
```

The SDK trusts the gateway's identity but the gateway doesn't know who the SDK is. This is the same model as HTTPS in your browser: your browser trusts the server (via its certificate chain to a CA), but the server doesn't authenticate your browser with a cert.

Use case: your gateway is behind a public CA (Let's Encrypt) or your company's internal CA. You want encryption and server verification, but client authentication happens at a different layer (OIDC tokens, API keys).

**Level 3: Mutual TLS (mTLS)**

```
SDK -------- encrypted gRPC --------> Gateway
             (SDK verifies gateway cert,
              gateway verifies SDK cert)
```

Both sides present certificates. The gateway rejects connections from SDKs that don't have a valid client cert. This is the strongest trust model.

Use case: air-gapped environments where every connection must be authenticated at the transport layer. No token server, no OIDC provider, just certificates.

### Before (v0.0.44)

```python
@dataclass(frozen=True)
class TlsConfig:
    ca_path: pathlib.Path      # required
    cert_path: pathlib.Path    # required
    key_path: pathlib.Path     # required
```

Only one option: full mTLS. All three fields mandatory.

```python
# This was the ONLY way to use TLS
tls = TlsConfig(
    ca_path=Path("/certs/ca.crt"),       # CA that signed the gateway's cert
    cert_path=Path("/certs/client.crt"), # client cert (proves SDK identity)
    key_path=Path("/certs/client.key"),  # client private key
)
client = SandboxClient("gateway.corp.internal:18080", tls=tls)
```

If your gateway used a public CA (like an OIDC gateway on a cloud cluster), you still had to provide client certs even though the gateway didn't require them. There was no way to say "just trust the system CA store."

### After (v0.0.52)

```python
@dataclass(frozen=True)
class TlsConfig:
    ca_path: pathlib.Path | None = None
    cert_path: pathlib.Path | None = None
    key_path: pathlib.Path | None = None

    def __post_init__(self) -> None:
        if (self.cert_path is None) != (self.key_path is None):
            raise ValueError("cert_path and key_path must be set together")
```

Three profiles:

```python
# Profile 1: Full mTLS (same as before)
# Use when: air-gapped, every connection must present client cert
tls = TlsConfig(
    ca_path=Path("/certs/ca.crt"),
    cert_path=Path("/certs/client.crt"),
    key_path=Path("/certs/client.key"),
)

# Profile 2: CA-only (trust custom CA, no client cert)
# Use when: internal CA but auth happens via OIDC/bearer tokens
tls = TlsConfig(ca_path=Path("/certs/internal-ca.crt"))

# Profile 3: System roots (OS trust store)
# Use when: gateway has a public cert (Let's Encrypt, etc.)
tls = TlsConfig()
```

The validation ensures you can't accidentally set `cert_path` without `key_path` or vice versa.

### Real-world scenarios

| Scenario | TlsConfig | Why |
|----------|-----------|-----|
| Local dev (Docker driver) | `None` (no TLS) | Localhost, no network exposure |
| On-cluster (Helm, same namespace) | `TlsConfig()` | Cluster CA handles trust |
| Corporate gateway behind internal CA | `TlsConfig(ca_path=...)` | Custom CA, OIDC for auth |
| Air-gapped, zero-trust | `TlsConfig(ca_path=..., cert_path=..., key_path=...)` | Full mTLS, no external auth |
| RHOAI cluster (rhai-gw) | `TlsConfig()` + `bearer_token=...` | Public CA + OIDC |

### What the cert files actually contain

```
ca.crt (Certificate Authority)
    The root of trust. Contains the public key of the CA that signed the
    gateway's server certificate. The SDK uses this to verify "is this
    gateway who it claims to be?"

    If omitted, the OS trust store is used (same CAs your browser trusts).

client.crt (Client Certificate)
    Proves the SDK's identity to the gateway. Contains the SDK's public key
    and is signed by a CA the gateway trusts. The gateway checks: "is this
    client authorized to connect?"

    Only needed for mTLS. For OIDC/bearer auth, the token proves identity
    instead.

client.key (Client Private Key)
    The SDK's private key, paired with client.crt. Used to prove the SDK
    actually owns the certificate (via the TLS handshake). Never shared,
    never sent over the wire.

    Must be kept secret. If someone gets this file, they can impersonate
    the SDK.
```

## 2. SandboxRef.phase is now a backward-compat property

The phase field moved into a nested `status` sub-message.

### Before

```python
@dataclass(frozen=True)
class SandboxRef:
    id: str
    name: str
    phase: int       # direct field, values: 0=pending, 1=provisioning, 2=ready, 3=error
```

### After

```python
@dataclass(frozen=True)
class SandboxStatusRef:
    phase: int
    current_policy_version: int   # new: tracks which policy version is active

@dataclass(frozen=True)
class SandboxRef:
    id: str
    name: str
    status: SandboxStatusRef

    @property
    def phase(self) -> int:       # backward-compat, delegates to status
        return self.status.phase

    @property
    def current_policy_version(self) -> int:
        return self.status.current_policy_version
```

### Usage

```python
ref = client.get("my-sandbox")

# This works on both old and new SDK
if ref.phase == 2:
    print("sandbox is ready")

# New: check policy version (e.g., after updating sandbox policy at runtime)
print(f"policy version: {ref.current_policy_version}")
```

The `current_policy_version` matters for the new agentic approval loop (PR #1528) where an agent can request permission changes and the gateway updates the policy in-place. The version number lets you verify the update took effect.

## 3. Bearer token authentication (OIDC support)

The biggest functional addition. Enables connecting to OIDC-protected gateways.

### Before

```python
client = SandboxClient("gateway:18080", tls=tls, timeout=30)
# No way to pass an auth token. OIDC gateways reject with:
# "missing authorization header"
```

### After

```python
# Option A: static token (from CLI login, env var, etc.)
client = SandboxClient(
    "gateway:18080",
    tls=TlsConfig(),
    bearer_token="eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9..."
)

# Option B: dynamic token (refreshed every RPC)
def get_token() -> str:
    """Read current token from where the CLI caches it."""
    return Path("~/.config/openshell/tokens/rhai-gw.token").read_text().strip()

client = SandboxClient(
    "gateway:18080",
    tls=TlsConfig(),
    bearer_token=get_token   # callable, invoked per-RPC
)
```

How it works internally: a gRPC interceptor (`_BearerAuthInterceptor`) wraps every outgoing RPC and injects the `authorization: Bearer <token>` header. When `bearer_token` is a callable, the interceptor calls it each time, so expired tokens get transparently refreshed.

`from_active_cluster()` now reads the gateway's `auth_mode` from the on-disk metadata. If it's `oidc`, it wires up an automatic token refresher using `httpx` that exchanges refresh tokens in the background:

```python
# This now handles OIDC automatically
client = SandboxClient.from_active_cluster(cluster="rhai-gw")
# Internally: reads auth_mode=oidc, loads cached tokens, sets up refresher
```

### Why this matters for our extension

When we tested against the `rhai-gw` gateway, we got "missing authorization header." That was because the old SDK had no way to pass OIDC tokens. With the new SDK, `from_active_cluster()` handles it automatically. Our extension's `_resolve_openshell_client` calls `from_active_cluster()`, so OIDC would work out of the box without any changes to our code.

## 4. SandboxClient.close() method

### Before

```python
client = SandboxClient("gateway:18080")
# ... use it ...
# No close(). gRPC channel leaked until garbage collection.
# If using OIDC: httpx client also leaked.
```

### After

```python
client = SandboxClient("gateway:18080", bearer_token=get_token)
# ... use it ...
client.close()   # closes gRPC channel + httpx OIDC client
```

```python
def close(self) -> None:
    self._channel.close()
    if self._bearer_close is not None:
        with contextlib.suppress(Exception):
            self._bearer_close()
```

Idempotent (safe to call multiple times). Our extension already calls `self._openshell_client.close()` during shutdown, so this just makes the cleanup more thorough.

## 5. SandboxSession: higher-level sandbox handle

A new convenience class that wraps `SandboxClient` + a specific sandbox.

### Before

```python
client = SandboxClient.from_active_cluster()
ref = client.create(spec=spec, name="worker-1")
client.wait_ready(ref.name)

# Every call needs sandbox_id or sandbox_name
result = client.exec(ref.id, ["ls", "-la"])
client.exec(ref.id, ["python3", "-c", "print(42)"])
client.delete(ref.name)
client.close()
```

### After

```python
client = SandboxClient.from_active_cluster()
session = client.create_session(spec=spec, name="worker-1")
# wait_ready happens inside create_session

# Clean API, no ID passing
result = session.exec(["ls", "-la"])
session.exec_python("print(42)")
session.delete()
client.close()
```

`SandboxSession` holds the `sandbox_id`/`sandbox_name` and delegates to the underlying client. It also adds `exec_python()` which wraps code in `python3 -c`.

Our extension doesn't use this because we have our own session abstraction (`OpenShellSandboxSession` implementing `BaseSandboxSession`). But if someone builds a simpler OpenShell integration outside the Agents SDK, `SandboxSession` reduces boilerplate.

## 6. Proto refactor (status sub-message)

The gRPC protobuf definition changed. This is the wire format change behind items #2 and the policy versioning.

### Before

```protobuf
message Sandbox {
    string id = 1;
    string name = 2;
    SandboxPhase phase = 3;
}
```

### After

```protobuf
message Sandbox {
    string id = 1;
    string name = 2;
    SandboxStatus status = 3;
}

message SandboxStatus {
    SandboxPhase phase = 1;
    int32 current_policy_version = 2;
}
```

Our extension never touches the protobuf directly (we go through the Python SDK's `SandboxRef` dataclass), so this is invisible to us.

## 7. Agentic approval loop (PR #1528)

Not an SDK change per se, but a new gateway feature that the SDK now supports. An agent can request permission to access a resource that the current sandbox policy blocks. The gateway presents the request to a human approver, and if approved, updates the policy in-place. The `current_policy_version` field tracks these updates.

```
Agent: "I need to access api.github.com/repos/*/issues"
         |
         v
Gateway: presents approval request to human
         |
         v (approved)
Gateway: updates sandbox policy, bumps current_policy_version
         |
         v
Agent: retries, request succeeds
```

This is a significant feature for agentic workflows where you can't predict every API the agent will need upfront. Instead of pre-declaring all endpoints in YAML, the agent discovers what it needs and requests access dynamically.

## Summary

| Change | Breaking? | Impact on our extension |
|--------|-----------|------------------------|
| TlsConfig optional fields | No | None (follow-up: relax asserts for CA-only profile) |
| SandboxRef.phase property | No | None (backward-compat property) |
| Bearer token / OIDC | No | Positive (OIDC gateways now work via from_active_cluster) |
| close() method | No | Positive (our cleanup is now more thorough) |
| SandboxSession class | No | None (could simplify internals in follow-up) |
| Proto status nesting | No | None (SDK handles it) |
| Agentic approval loop | No | None (gateway feature, transparent to SDK callers) |

All changes are backward compatible. Our PR works with both the old and new SDK versions.
