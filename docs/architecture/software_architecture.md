# Software Architecture

This document defines the software-side product architecture. It complements
`engineering_architecture.md`, which focuses on communication boundaries and
device transports.

## Product Shape

The final product is a desktop control application:

```text
Desktop App
  UI shell
  local core service
  device adapters
  agent adapters
```

During early development, the UI may run as a browser page connected to a local
service. That is an implementation convenience, not the final product boundary.

```text
Development shape:
  Browser UI -> Local Core Service

Product shape:
  Desktop UI shell -> Local Core Service
```

The architecture must support both shapes without tying core behavior to a
browser-only implementation.

## Current V2 Backend Status

The backend currently implements the Local Core Service path without a formal
frontend or desktop shell. The supported development/test surface is the Local
API WebSocket plus smoke scripts. A local hotkey harness is available for
external real loopback testing, but it is a temporary test input surface rather
than a product device transport.

Implemented backend capabilities:

- Local API client handshake, launch token, origin validation, client identity,
  and capability gates.
- Structured command/event/snapshot path using core envelopes, router, state
  store, and event bus.
- Async command routing and structured agent lifecycle commands for
  launch/resume, interrupt, close, and permission response.
- Workspace-aware agent launch/resume payloads. Default project workspace
  resolution prefers explicit `--workspace`, then `AI_KEYB_WORKSPACE`, then a
  configured non-dot default, then the nearest parent project root containing
  `software`, and finally the service start directory.
- Unified permission command handling with capability and policy gates.
- Legacy Local API compatibility for `agent_launch`, `permission_response`,
  `interrupt`, and `list_sessions`.
- SQLite app store with repositories and migrations for product/audit metadata.
- Per-device focus, symbolic target resolution, active profile, active tool, and
  virtual-input action dispatch.
- Profile/keymap/lighting validation, profile compilation, active
  profile/import-export persistence, and device config sync.
- Device simulator backend with virtual input ingress, capability negotiation,
  slot mapping, projected snapshots, focus state, active tool state,
  notification queue, config sync, and profile validation.
- Diagnostics, redaction, import-boundary, and path guard coverage.
- Claude Code is the active reference provider for launch, monitoring,
  interaction, and native approval loopback. The product-like path is the
  foreground Claude Code CLI with Local API hooks; the managed Python Agent SDK
  path remains useful for headless tests and automation.
- Codex native/foreground approval support exists in the repository and has
  produced real loopback evidence, but it is parked as a compatibility and
  regression path. New backend work should not require Codex parity or let
  Codex-specific behavior drive shared contracts.
- Smoke support for real loopback controls: `--workspace`,
  `--auto-start-service`, `--config`, `--service-start-timeout`, and
  `--wait-for-hotkey-approval`.
- Earlier real Claude Code foreground approve/deny smoke tests and Codex
  approve/deny smoke tests produced `permission_ack.forwarded=true` evidence.
  After the current roadmap reset, Claude Code is the active acceptance target
  and Codex evidence is regression-only.

Latest full backend verification after provider-loopback work recorded
`pytest tests -q` as `404 passed`.

Deferred from V2:

- formal frontend and desktop shell
- physical USB HID, CDC, BLE, and 2.4G device transports
- packaged service lifecycle and installer
- packaged lifecycle hardening for foreground/provider process cleanup

See `implementation_status_v1.md` for operational acceptance details.

## Scope

The software repository contains both keyboard configuration and agent control.
They are not separate products.

The application owns:

- keyboard configuration
- profiles, layers, keymaps, macros, magnetic switch settings, screen settings
- device discovery, diagnostics, and firmware-facing control
- Claude Code instance management
- parked Codex compatibility instance management, where enabled
- agent sessions, runs, permissions, notifications, and logs
- local APIs used by the UI and tests
- protocol conversion between UI, agent adapters, and keyboard devices

The key product idea is that keyboard configuration and agent control share one
local state model. A profile can contain both keyboard behavior and agent
bindings.

## Layered Architecture

```text
Desktop or Browser UI
  keyboard config
  agent control
  diagnostics
  firmware/profile management
        |
        v
Local API
  WebSocket / HTTP / local IPC
        |
        v
Application Core
  state store
  event bus
  command router
  profile service
  notification center
  approval policy engine
        |
        +-------------------+
        |                   |
        v                   v
Keyboard Domain        Agent Domain
  keymaps                agent registry
  layers                 session registry
  macros                 run registry
  magnetic config        permission queue
  screen layout          agent events
  agent bindings
        |                   |
        +---------+---------+
                  |
                  v
Adapters
  Device transports: USB Vendor HID, CDC, BLE GATT, 2.4G dongle, simulator
  Agent adapters: Claude Code primary, Codex parked compatibility
```

## Core Principle

The Local Core Service is the authoritative state owner.

```text
Local Core Service: source of truth
UI: view and command surface
Keyboard: limited view and physical control surface
Agent adapters: event and command integration points
Device adapters: transport integration points
```

The UI must not bypass the core to directly mutate device state. Agent adapters
must not directly write device frames. Keyboard configuration modules must not
directly depend on USB, BLE, or dongle implementation details.

## Major Modules

The repository should evolve toward these module boundaries:

```text
src/app/
  application lifecycle and service startup

src/core/
  state store
  event bus
  command router
  notification center
  approval policy engine

src/devices/
  device manager
  protocol codec
  capabilities
  transports/
    usb_hid
    cdc_serial
    ble_gatt
    dongle
    simulator

src/keyboard/
  profiles
  keymaps
  layers
  bindings
  macros
  magnetic switch settings
  lighting
  screen layouts
  agent bindings

src/agents/
  agent manager
  agent registry
  session registry
  run registry
  permission queue
  Claude Code foreground hook and SDK adapters
  Codex compatibility adapter, parked

src/local_api/
  WebSocket API
  HTTP API
  local IPC, if used by the desktop shell

src/diagnostics/
  logs
  health checks
  device and agent status reports
```

The current implementation does not need to match this structure immediately,
but new code should move toward these responsibilities.

Current implementation note:

- `scripts/local-agent-cli.py` and `scripts/claude-code-hook.py` own the
  current foreground Claude Code CLI loopback path used for product-like local
  interaction.
- `src/agents/adapters.py` still contains managed Claude SDK permission
  support for headless service tests and automation.
- Codex adapter and proxy code remains in the tree for regression coverage, but
  it is not an active development baseline.
- `src/bridge/agent_proxy.py` still orchestrates process lifecycle and legacy
  stream paths. It should be split further when provider/instance management is
  promoted out of the bridge compatibility layer.
- `scripts/local-hotkey-harness.py` connects to the Local API as
  `desktop-ui`/`test-harness` and injects virtual input for high-risk real
  approval loopback testing. It is outside the formal device transport
  boundary.

## Keyboard Configuration and Agent Control

Keyboard configuration and agent control interact through profiles and bindings.

Examples:

- `Fn+Enter` approves a permission request for the focused agent session.
- `Fn+Esc` interrupts the focused agent run.
- A rotary encoder scrolls the focused session output.
- A screen layout shows the current Claude Code session state.
- A coding profile binds keys and screen cards to specific agent roles.

This means agent control is not an optional overlay. It is a first-class domain
inside the keyboard software.

## Agent Identity Model

Agent control must support multiple concurrent Claude Code instances. The
provider model still allows additional providers, including the parked Codex
compatibility path, but new product behavior should be validated against
Claude Code first.

The identity hierarchy is:

```text
AgentProvider
  claude_code
  codex, if the parked compatibility provider is enabled

AgentInstance
  one running or launchable provider process or connection

AgentSession
  one conversation/thread/session owned by an instance

AgentRun
  one task, turn, or job inside a session
```

Software-facing events use a full agent reference:

```json
{
  "provider_id": "claude_code",
  "instance_id": "cc-software",
  "session_id": "thread-001",
  "run_id": "turn-012"
}
```

Device-facing messages should use compact slot IDs maintained by the Local Core
Service. The keyboard should not need to parse or persist long agent IDs.

```text
agent_slot_id -> provider + instance_id + display label
session_slot_id -> session_id
run_slot_id -> run_id
```

## Screen Focus and Notifications

The keyboard screen acts like a small message and control surface.

It has a focused target:

```text
ScreenFocus
  instance_id
  session_id
  optional run_id
```

Keyboard actions such as approve, reject, interrupt, scroll, and quick command
dispatch to the focused target by default.

Global notifications are separate from focus. The notification center can show
messages from any agent or session without stealing focus.

Examples:

- permission requested
- run completed
- run failed
- agent needs attention
- device warning

The user can open a notification to change the focused target.

## Approval Policy

Approval behavior must be configurable globally and per session.

```text
GlobalApprovalPolicy
  manual
  approve_low_risk
  ask_high_risk
  view_only

SessionApprovalPolicy
  inherit
  manual
  approve_low_risk
  view_only
```

Every permission request must include enough metadata for the policy engine:

- permission ID
- agent reference
- action type
- working directory or target scope
- risk level
- summary for display
- expiration, if applicable

High-risk operations must remain distinguishable from low-risk commands so the
UI and keyboard can apply different confirmation rules.

Current V1 approval forwarding rule:

```text
permission_ack.forwarded=true
  only after the provider-native permission response has been delivered
```

Claude Code foreground hook evidence includes hook delivery and response write
status. Claude SDK evidence includes callback delivery and return status.
Codex evidence is retained for the parked compatibility path and is no longer
the reference acceptance model.

Real loopback acceptance currently uses the Local API smoke client plus, when
needed, the temporary hotkey harness. The active acceptance path launches a
foreground Claude Code CLI session and verifies that the hook response has been
written back before `permission_ack.forwarded=true` is emitted.

## Snapshot and Event Model

The system should support both snapshots and live events.

```text
Snapshot:
  complete current state for UI or keyboard resync

Event:
  incremental state changes after the snapshot
```

The UI and keyboard should be able to reconnect by receiving a fresh snapshot
and then subscribing to events. This keeps state recovery independent from any
single transport.

## Persistent State

The software should persist product configuration, not every transient runtime
event.

Persisted state should include:

- profiles
- keymaps
- layers
- macros
- magnetic switch settings
- screen layouts
- agent bindings
- known devices
- agent instance presets
- workspace bindings
- approval policies
- UI preferences
- permission history with native forwarding evidence

Full agent output logs should be optional and user-controlled.

## Security Boundary

The Local Core Service can start processes, access devices, and respond to
permission requests. Its API must be treated as privileged.

Baseline rules:

- development local APIs listen on localhost only
- product desktop UI access uses a private local channel or token-protected
  localhost API
- WebSocket clients should be origin-checked when browser UI is used
- keyboard shortcuts should not silently approve high-risk operations
- remote web pages must not be able to control the local bridge
- secrets and external service tokens must not be stored in plain text config
- temporary test harnesses must be clearly separated from product device
  transports

## Deferred Topics

These topics are required, but will be specified after more implementation
details are available:

- detailed error recovery
- crashed agent restart behavior
- partially completed firmware update recovery
- transport half-open detection
- protocol mismatch handling beyond capability negotiation
- long-term session archival
