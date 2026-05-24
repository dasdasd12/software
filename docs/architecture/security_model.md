# Security Model

The software can start local processes, control keyboard devices, respond to
agent permission requests, and access user workspaces. Its local API is
privileged and must not be exposed casually.

## Baseline Rules

```text
Local API is local-only.
No LAN binding by default.
No unauthenticated command endpoints.
High-risk actions require explicit confirmation.
Secrets are not stored in plaintext config.
```

Development may use browser UI over localhost. Product builds should prefer a
desktop shell with a private local channel or launch-token protected localhost
API.

Current V1 status:

- Local API binds to loopback by default.
- `hello` carries launch token, client kind, client id, and capabilities.
- WebSocket Origin validation is implemented for browser clients.
- Permission responses pass through client capability checks and approval
  policy.
- Test clients do not receive real permission approval capability by default.
- Keyboard/device clients can directly approve only policy-allowed low-risk
  requests; high-risk requests require desktop confirmation.
- Real Codex and Claude permission responses must be delivered through their
  native provider channels before `forwarded=true` is returned.

## Network Binding

Default:

```text
host: 127.0.0.1
```

Avoid:

```text
host: 0.0.0.0
```

`0.0.0.0` may be used only for explicit local debugging with a clear warning.
The product should not expose control APIs to the LAN.

## Client Identity

Clients must identify as one of:

```text
desktop-ui
browser-dev-ui
device-transport
test-client
automation-client
```

Each client type receives capabilities. Example:

```text
desktop-ui:
  full product UI capabilities

browser-dev-ui:
  development UI capabilities with token and origin validation

device-transport:
  device command capabilities, bounded by device identity and policy

test-client:
  test-only capabilities

automation-client:
  disabled by default; explicit user opt-in required
```

## Browser Development UI

If a browser UI connects to the core:

- bind only to loopback
- use a per-launch random token
- require token for HTTP and WebSocket
- validate WebSocket Origin
- deny CORS by default
- do not expose unauthenticated command endpoints

This protects against random web pages controlling the local service.

## Desktop App

Final desktop integration should use one of:

- private local IPC
- OS named pipe
- localhost with ephemeral port and launch token
- Tauri/Electron sidecar supervision with private token exchange

The desktop shell should start, monitor, and stop or reconnect to the Local Core
Service.

## Permission and Risk Boundary

Policy engine decisions are separate from client capability.

```text
client auth -> client capability -> risk classification -> approval policy
```

Keyboard shortcuts may directly approve low-risk actions only. High-risk,
critical, and destructive actions require desktop confirmation until a stronger
physical confirmation flow is designed.

## Secrets

Do not store these in YAML, JSON, SQLite plaintext, or logs:

- API keys
- OAuth tokens
- service credentials
- provider session secrets

Use the OS secret store or provider-native credential storage.

Logs and diagnostics should redact common secret patterns.

## Local Workspace Access

Agent adapters operate inside user workspaces and may run commands. Commands
outside the configured workspace should be classified as higher risk.

The core should track:

- workspace path
- source client
- target agent/session/run
- command summary
- risk level

## Device Trust

Connected keyboards are trusted only as bounded input devices.

Device-originated commands must still pass:

- device identity validation
- slot generation validation
- command schema validation
- client capability check
- approval policy

A keyboard device cannot silently bypass policy.

## Automation Clients

Automation is useful but risky. Default stance:

```text
automation clients are disabled unless explicitly enabled by the user
```

When enabled, automation clients should have scoped tokens and limited
capabilities.

## Audit Trail

Persist metadata for security-sensitive actions:

- permission decisions
- high-risk command attempts
- failed authentication
- device pairing or trust changes
- agent instance launch/stop
- settings that weaken security

Audit logs should avoid storing full secrets or full command outputs by default.

Current permission history stores forwarding evidence and native request
metadata. Evidence may include command summaries and cwd. Retention and redaction
settings should be tightened before product packaging.

## Security Tests

Tests should cover:

- service does not bind LAN by default
- missing token rejected
- invalid Origin rejected for browser WebSocket
- test client cannot approve real permission
- keyboard cannot approve high-risk permission directly
- secret redaction in logs
- policy rejects commands outside allowed scope
