# Event and Command Model

The software architecture uses commands for requested changes and events for
observed changes. This keeps UI clients, keyboard devices, agent adapters, and
device transports from directly mutating each other's state.

## Core Rule

```text
Commands ask the system to do something.
Events state that something happened.
Snapshots describe the current complete state.
```

All mutating operations should enter through a command router. All accepted
state changes should be represented as events.

## Command Envelope

Commands use a consistent envelope regardless of source.

```json
{
  "command_id": "cmd_01",
  "type": "agent.run.interrupt",
  "target": {
    "provider_id": "codex",
    "instance_id": "codex-software",
    "session_id": "sess_01",
    "run_id": "run_01"
  },
  "source": {
    "kind": "keyboard",
    "device_id": "kbd_01",
    "client_id": "device_01"
  },
  "payload": {},
  "timestamp": 1779160000
}
```

Required fields:

- `command_id`: unique local command ID
- `type`: namespaced command type
- `source`: UI, keyboard, automation, adapter, or internal source
- `payload`: command-specific data

`target` is required for commands that address an agent, session, run, device,
profile, or permission request.

## Event Envelope

Events are append-friendly, ordered, and suitable for UI/device resync.

```json
{
  "event_id": "evt_01",
  "seq": 1234,
  "type": "agent.run.state_changed",
  "target": {
    "provider_id": "codex",
    "instance_id": "codex-software",
    "session_id": "sess_01",
    "run_id": "run_01"
  },
  "payload": {
    "state": "waiting_permission"
  },
  "timestamp": 1779160001
}
```

Required fields:

- `event_id`: unique event ID
- `seq`: monotonically increasing service-local sequence
- `type`: namespaced event type
- `payload`: event-specific data
- `timestamp`: service timestamp

The event stream may be persisted selectively. Runtime clients should not rely
on unbounded event history.

## Snapshot Model

Snapshots give clients a complete current view.

```json
{
  "snapshot_id": "snap_01",
  "last_event_seq": 1234,
  "agents": {},
  "sessions": {},
  "runs": {},
  "devices": {},
  "profiles": {},
  "active_tools": {},
  "notifications": [],
  "permissions": []
}
```

Connect/reconnect flow:

1. Client authenticates.
2. Client requests or receives a snapshot.
3. Client subscribes to events after `last_event_seq`.
4. Client applies events incrementally.
5. If the client detects a gap, it requests a fresh snapshot.

Keyboard devices may receive compact snapshot frames projected from the same
core state rather than the full UI snapshot.

Current V1 Local API behavior:

- `hello` registers client kind, client id, launch token, and capabilities.
- `command` carries a `CommandEnvelope`.
- `snapshot` returns the current service snapshot.
- `event` wraps `EventEnvelope` updates for structured subscribers.
- Legacy messages remain accepted and are internally routed where practical:
  `agent_launch`, `permission_response`, `interrupt`, and `list_sessions`.
- Reconnect-capable clients should request `system.snapshot.request` and then
  consume incremental events.

## Command Sources

```text
desktop-ui
browser-dev-ui
keyboard-device
automation-client
agent-adapter
internal-scheduler
test-client
```

Each source has capabilities. For example, a `test-client` should not be able to
approve real permission requests unless explicitly configured.

## Command Targets

Common target shapes:

```json
{
  "provider_id": "claude_code",
  "instance_id": "cc-hardware",
  "session_id": "sess_hw",
  "run_id": "run_09"
}
```

```json
{
  "device_id": "kbd_01"
}
```

```json
{
  "profile_id": "profile_coding"
}
```

```json
{
  "permission_id": "perm_01"
}
```

Commands from keyboard bindings may use symbolic targets such as
`focused_session` or `focused_permission`. The command router resolves them
through the focus manager before execution.

## Command Type Namespaces

Recommended namespaces:

```text
agent.instance.*
agent.session.*
agent.run.*
agent.permission.*
agent.focus.*
keyboard.profile.*
keyboard.config.*
keyboard.screen.*
keyboard.tool.*
device.transport.*
device.config.*
notification.*
system.*
```

Examples:

```text
agent.session.launch_or_resume
agent.run.interrupt
agent.permission.respond
agent.focus.set
keyboard.profile.activate
keyboard.config.update_key_binding
keyboard.tool.switch
keyboard.tool.next
device.config.sync_profile
notification.dismiss
system.snapshot.request
```

`keyboard.tool.*` commands select backend control modes per keyboard device;
they are not UI widgets. The initial configured tool IDs are:

```text
agent_control
session_list
permissions
profile_config
device_status
```

`keyboard.tool.switch` accepts `device_id` and `tool_id` from the command
target or payload, falling back to `source.device_id` for the device. Unknown
tools are rejected without changing state. `keyboard.tool.next` cycles the
configured tools in order for that device and wraps after the last tool.

Current V1 compatibility mapping:

```text
agent_launch         -> agent.session.launch_or_resume
permission_response  -> agent.permission.respond
interrupt            -> agent.run.interrupt
list_sessions        -> session list query path
```

The compatibility messages are retained for smoke tests and existing
automation. New local UI work should prefer structured `command` envelopes.

## Event Type Namespaces

Recommended namespaces:

```text
agent.instance.*
agent.session.*
agent.run.*
agent.output.*
agent.permission.*
agent.focus.*
keyboard.profile.*
keyboard.tool.*
device.*
notification.*
system.*
```

Examples:

```text
agent.instance.status_changed
agent.session.created
agent.run.state_changed
agent.output.delta
agent.permission.requested
agent.focus.changed
keyboard.tool.changed
keyboard.tool.rejected
device.connected
device.capabilities_updated
notification.created
system.snapshot.generated
```

## Validation

Every command should be validated in this order:

1. Envelope shape.
2. Client authentication.
3. Client capability.
4. Target existence.
5. Domain-specific preconditions.
6. Approval policy, if the command performs or approves a risky action.

Rejected commands should emit or return structured errors. They should not
partially mutate state.

For provider permissions that require native forwarding, a locally accepted
permission decision is not considered complete until the provider adapter has
successfully delivered the native response. If native forwarding fails, the
permission remains pending and the Local API returns `PERMISSION_FORWARD_FAILED`.

## Idempotency

Commands that may be retried should use `command_id` for idempotency. The core
may cache recent command results so duplicate client submissions do not repeat
side effects such as launching another agent process.

## Device Projection

The device protocol should not directly mirror the full event envelope. The
device manager projects relevant events into compact device frames using slot
IDs and bounded payloads.

```text
Core event -> screen/device projector -> device protocol frame
```

The UI API may expose the full JSON event envelope.

## Testing Expectations

Every domain command should have tests for:

- accepted path
- invalid envelope
- unauthorized client
- missing target
- policy rejection
- emitted event shape
- snapshot consistency after command completion
