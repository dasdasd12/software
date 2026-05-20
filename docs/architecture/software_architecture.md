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
  Codex adapter
  Claude Code adapter

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

## Deferred Topics

These topics are required, but will be specified after more implementation
details are available:

- detailed error recovery
- crashed agent restart behavior
- partially completed firmware update recovery
- transport half-open detection
- protocol mismatch handling beyond capability negotiation
- long-term session archival
