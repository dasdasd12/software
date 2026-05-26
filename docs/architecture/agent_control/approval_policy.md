# Approval Policy

Approval Policy controls which agent actions may run automatically, which need
user confirmation, and which are blocked. It applies to permission requests from
Codex, Claude Code, keyboard bindings, automation clients, and future agent
integrations.

## Policy Scopes

Policies can exist at multiple scopes.

```text
global
provider
instance
session
profile
```

Default assumption:

```text
Session policy may be equal to or stricter than global policy.
It cannot silently become more permissive than global policy.
```

This prevents a session-specific setting from weakening the user's baseline
safety rules.

## Approval Modes

Initial modes:

```text
manual
approve_low_risk
ask_high_risk
view_only
deny_all
```

Meanings:

- `manual`: ask for every permission request
- `approve_low_risk`: auto-approve low-risk requests, ask for the rest
- `ask_high_risk`: ask for high/critical requests, allow low/medium if rules
  permit
- `view_only`: no mutating agent actions are approved from this scope
- `deny_all`: block requests without asking

The exact rule merge behavior should be implemented by a policy engine rather
than scattered across UI, keyboard bindings, or agent adapters.

## Permission Request Model

```json
{
  "permission_id": "perm_01",
  "target": {
    "provider_id": "codex",
    "instance_id": "codex-software",
    "session_id": "sess_01",
    "run_id": "run_03"
  },
  "action_type": "shell_command",
  "tool_name": "shell",
  "summary": "Run bridge tests",
  "details": {
    "command": "pytest tests/bridge"
  },
  "risk_level": "low",
  "policy_decision": "ask",
  "state": "pending",
  "expires_at": 1779160300
}
```

Required fields:

- permission ID
- target `AgentRef`
- action type
- human-readable summary
- risk level
- current state

## Risk Levels

Initial risk levels:

```text
low
medium
high
critical
destructive
```

Suggested initial classification:

```text
low:
  read-only commands
  local tests
  compile/build commands
  non-destructive status checks

medium:
  file writes inside workspace
  dependency installation inside project environment
  starting local development servers

high:
  git commit
  firmware flashing
  broad file modifications
  commands affecting external devices
  network operations with side effects

critical:
  git push
  credential or secret changes
  installer/system configuration changes
  commands outside workspace

destructive:
  recursive delete
  force reset
  disk/partition operations
  irreversible firmware or bootloader operations
```

The risk classifier should be conservative when uncertain.

## Keyboard Approval Rules

Default assumption:

```text
Keyboard shortcuts may directly approve low-risk requests only.
High-risk and above require desktop UI confirmation, or a future explicit
strong-confirm flow.
```

Strong confirmation can be designed later. It might involve:

- long press
- full summary on keyboard screen
- physical confirm sequence
- matching desktop UI confirmation

Until that exists, high-risk approval should be routed to the desktop UI.

## Policy Decision Results

Policy engine outputs:

```text
allow
deny
ask
require_desktop_confirm
require_strong_confirm
```

The decision should include an explanation for UI and logs.

```json
{
  "decision": "require_desktop_confirm",
  "reason": "git push is critical risk and cannot be approved by keyboard"
}
```

## Merge Order

Recommended merge order:

```text
global baseline
provider policy
instance policy
profile policy
session policy
request-specific classifier
client capability
```

The strictest applicable safety constraint wins.

Example:

- global allows low-risk auto-approval
- session asks for everything
- result: ask for everything in that session

Example:

- global requires desktop confirmation for high-risk
- keyboard binding asks to approve high-risk
- result: require desktop confirmation

## Client Capability

Policy is not the only gate. The command source must also have capability.

Examples:

```text
desktop-ui:
  may approve low, medium, high, critical with proper UI confirmation

keyboard-device:
  may approve low directly
  may request high-risk approval flow

browser-dev-ui:
  may approve only with launch token and origin validation

test-client:
  cannot approve real permission requests by default

automation-client:
  capability must be explicitly granted
```

## Native Agent Permission Channel

Default implementation path for real provider approval:

```text
Core permission decision
  -> provider adapter
  -> native provider permission channel
  -> permission_ack with forwarding evidence
```

The UI and keyboard should not depend on provider-specific permission formats.
Agent adapters translate core decisions into provider-native responses.

Current V1 provider paths:

```text
Codex:
  codex app-server with stdio listen transport
  item/commandExecution/requestApproval -> accept | decline
  item/fileChange/requestApproval       -> accept | decline
  item/permissions/requestApproval      -> accept | decline
  execCommandApproval                   -> approved | denied
  applyPatchApproval                    -> approved | denied

Claude Code:
  Python Agent SDK can_use_tool callback -> PermissionResultAllow/Deny
```

`permission_ack.forwarded=true` may only be returned after the adapter has
confirmed that the native response path completed. For Codex app-server this
means the JSON-RPC response was written to stdin. For Claude SDK this means the
permission callback received and returned the decision.

If forwarding fails for a provider that requires native forwarding, the Local
API returns `PERMISSION_FORWARD_FAILED` and keeps the request pending. Fake or
unsupported adapters may return `forwarded=false` only in explicit test/fallback
paths.

Expired Codex app-server requests are declined through the native JSON-RPC
channel so the provider does not remain blocked on an approval request that the
Local Core Service has already pruned.

## Audit Trail

Permission history should persist metadata:

- permission ID
- target agent/session/run
- action type
- risk level
- decision
- source client
- timestamp
- summary
- forwarded status
- native request metadata
- forwarding evidence

Full command details may be sensitive and should follow data retention settings.

## Testing Expectations

Tests should cover:

- global manual policy
- low-risk auto approval
- session stricter than global
- keyboard denied for high-risk approval
- desktop confirmation required for critical actions
- expired permission request
- provider adapter receives final decision
