# Agent Control Identity Model

Agent Control must support multiple Codex and Claude Code instances running at
the same time. The system cannot treat `codex` or `claude_code` as a single
runtime.

## Identity Hierarchy

```text
AgentProvider
  codex | claude_code

AgentInstance
  one configured or running provider instance

AgentSession
  one conversation/thread/session owned by an instance

AgentRun
  one task, turn, or job inside a session
```

This hierarchy allows combinations such as:

```text
codex-software / session A / run 1
codex-architect / session B / run 1
cc-hardware / session C / run 4
cc-debug / session D / run 2
```

## IDs

```text
provider_id:   codex | claude_code
instance_id:   stable local instance identifier
session_id:    provider-native or locally generated session identifier
run_id:        locally generated or provider-native run/turn identifier
permission_id: permission request identifier
```

Example full reference:

```json
{
  "provider_id": "claude_code",
  "instance_id": "cc-hardware",
  "session_id": "sess_hw_01",
  "run_id": "run_12"
}
```

## AgentProvider

Provider describes a supported agent family.

```json
{
  "provider_id": "codex",
  "display_name": "Codex",
  "adapter_kind": "cli",
  "capabilities": [
    "streaming_output",
    "interrupt",
    "permission_requests",
    "resume_session"
  ]
}
```

The two initial providers are:

```text
codex
claude_code
```

The adapter layer supports multiple modes behind the same provider model.
Current V1 defaults:

```text
codex:
  mode: app_server
  launch: codex app-server --listen stdio://
  native permission forwarding: JSON-RPC response to app-server stdin
  fallback: exec_json, read-only for approval forwarding

claude_code:
  mode: agent_sdk
  native permission forwarding: Python Agent SDK can_use_tool callback
  fallback: headless stream-json, non-native for dynamic approval forwarding
```

Provider identity should remain stable even if a particular instance switches
between app-server, SDK, CLI, or future remote-control modes.

## AgentInstance

An instance represents one configured runtime role.

```json
{
  "instance_id": "codex-software",
  "provider_id": "codex",
  "label": "Codex Software",
  "role": "software_developer",
  "workspace": "${PROJECT_ROOT}/software",
  "executable": "codex",
  "args": [],
  "status": "idle",
  "default_policy_id": "policy_standard",
  "created_at": 1779160000,
  "updated_at": 1779160000
}
```

Instances can be created manually or from workspace/profile presets.

Default product assumptions:

- instances are user-configurable
- workspace presets may auto-create common instances
- one instance may own multiple sessions
- one instance has one provider
- an instance label is what the UI and keyboard show to the user

Recommended initial presets:

```text
codex-software
  provider: codex
  workspace: software repo
  role: software developer

codex-architect
  provider: codex
  workspace: project root or both repos
  role: architecture and review

cc-hardware
  provider: claude_code
  workspace: hardware repo
  role: embedded developer
```

## AgentSession

A session represents one conversation, thread, or ongoing work context.

```json
{
  "session_id": "sess_01",
  "provider_id": "codex",
  "instance_id": "codex-software",
  "title": "Implement device transport abstraction",
  "workspace": "${PROJECT_ROOT}/software",
  "state": "active",
  "active_run_id": "run_03",
  "policy_id": null,
  "created_at": 1779160100,
  "updated_at": 1779160200
}
```

Default product assumptions:

- an instance may have multiple sessions
- a session belongs to exactly one instance
- a session has at most one active run in the first implementation
- future parallel runs may be added without changing the identity hierarchy

## AgentRun

A run represents one task, turn, or job execution inside a session.

```json
{
  "run_id": "run_03",
  "provider_id": "codex",
  "instance_id": "codex-software",
  "session_id": "sess_01",
  "state": "waiting_permission",
  "prompt_summary": "Run bridge tests",
  "started_at": 1779160150,
  "ended_at": null,
  "last_event_seq": 203
}
```

Initial run states:

```text
queued
running
waiting_permission
waiting_input
completed
failed
cancelled
offline
```

## AgentRef

Commands and events that target agent state use `AgentRef`.

```json
{
  "provider_id": "codex",
  "instance_id": "codex-software",
  "session_id": "sess_01",
  "run_id": "run_03"
}
```

Field requirements depend on command type:

- instance command: `provider_id`, `instance_id`
- session command: `provider_id`, `instance_id`, `session_id`
- run command: all four fields
- permission command: `permission_id` plus enough `AgentRef` to validate target

## Device Slot Mapping

Keyboard firmware should not parse or store long provider/session IDs. The
Local Core Service maps full IDs to compact slots.

```text
agent_slot_id -> provider_id + instance_id + display label
session_slot_id -> session_id
run_slot_id -> run_id
permission_slot_id -> permission_id
```

Slot mappings are per device and include a generation counter.

```json
{
  "device_id": "kbd_01",
  "generation": 7,
  "agent_slots": {
    "1": "codex-software",
    "2": "cc-hardware"
  },
  "session_slots": {
    "1": "sess_01",
    "2": "sess_hw_01"
  }
}
```

When a device reconnects, it should receive a fresh slot snapshot before it
handles focused actions.

## Display Identity

UI and keyboard screens should use labels, short labels, icons, and colors
rather than internal IDs.

```json
{
  "instance_id": "cc-hardware",
  "label": "Claude Hardware",
  "short_label": "CC-HW",
  "icon": "claude",
  "color": "#F97316"
}
```

Display identity is owned by the Local Core Service so it stays consistent
across UI and device projections.

## Migration From MVP

Current MVP fields such as:

```json
{
  "agent": "codex",
  "session_id": "sess_abc"
}
```

should migrate to:

```json
{
  "provider_id": "codex",
  "instance_id": "codex-default",
  "session_id": "sess_abc"
}
```

The initial migration can create default instances:

```text
codex-default
claude-default
```

Later, workspace-aware presets can replace these defaults.

## Compatibility Layer

The Local API still accepts legacy WebSocket messages that identify an agent by
`agent` and `session_id`. The service maps those messages into the provider
model internally where possible.

Current compatibility examples:

```text
agent_launch(agent="codex", session_id="new")
permission_response(agent inferred from pending request)
interrupt(session_id="sess_...")
list_sessions(agent="codex" | "claude" | "all")
```

Codex app-server also exposes provider-native identifiers:

```text
thread_id -> provider thread/session identifier
turn_id   -> provider turn/run identifier
item_id   -> provider item/tool-call identifier
jsonrpc_id -> native approval request id
```

The current Local API permission request ID is the native JSON-RPC id coerced to
a string for Codex app-server requests. This keeps the response route
deterministic because the JSON-RPC response must target that exact id.
