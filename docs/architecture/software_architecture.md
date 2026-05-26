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
- Claude Code native approval forwarding through the Python Agent SDK.
- Codex native approval forwarding through `codex app-server` JSON-RPC over the
  stdio listen transport.
- Smoke support for real loopback controls: `--workspace`,
  `--auto-start-service`, `--config`, `--service-start-timeout`, and
  `--wait-for-hotkey-approval`.
- Earlier real Codex approval and denial smoke tests produced
  `permission_ack.forwarded=true` evidence. The final backend virtual-input
  verification pass did not rerun external real Codex or Claude CLI smoke, and
  current docs must not claim final real external smoke coverage without fresh
  same-session evidence.

Final backend virtual-input verification recorded `pytest tests -q` as
`265 passed, 1 skipped in 3.49s`, with focused import-boundary and virtual-input
Local API checks passing.

Deferred from V2:

- formal frontend and desktop shell
- physical USB HID, CDC, BLE, and 2.4G device transports
- packaged service lifecycle and installer
- POSIX process-tree hardening for Codex app-server cleanup

See `implementation_status_v1.md` for operational acceptance details.

## Scope

The software repository contains both keyboard configuration and agent control.
They are not separate products.

The application owns:

- keyboard configuration
- profiles, layers, keymaps, macros, magnetic switch settings, screen settings
- device discovery, diagnostics, and firmware-facing control
- Codex and Claude Code instance management
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
  Agent adapters: Codex, Claude Code
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
  Codex app-server adapter
  Claude Code SDK adapter

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

- `src/agents/codex_app_server.py` owns the Codex JSON-RPC stdio client.
- `src/agents/adapters.py` owns native permission adapters for Codex app-server
  and Claude SDK.
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
- A screen layout shows the current Codex or Claude Code session state.
- A coding profile binds keys and screen cards to specific agent roles.

This means agent control is not an optional overlay. It is a first-class domain
inside the keyboard software.

## Agent Identity Model

Agent control must support multiple concurrent Codex and Claude Code instances.

The identity hierarchy is:

```text
AgentProvider
  codex | claude_code

AgentInstance
  one running or launchable Codex/Claude Code process or connection

AgentSession
  one conversation/thread/session owned by an instance

AgentRun
  one task, turn, or job inside a session
```

Software-facing events use a full agent reference:

```json
{
  "provider_id": "codex",
  "instance_id": "codex-software",
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

Codex app-server evidence includes JSON-RPC id and response write status.
Claude SDK evidence includes callback delivery and return status.

Real loopback acceptance currently uses the Local API smoke client plus the
temporary hotkey harness. Codex approval smoke launches through
`codex app-server`. Claude approval smoke launches through the Python Claude
Agent SDK path and requires the SDK dependency and provider authentication to be
available locally before native callback evidence can be produced.

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
