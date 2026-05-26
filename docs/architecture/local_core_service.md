# Local Core Service

The Local Core Service is the authoritative runtime for the software product.
It owns keyboard configuration, agent control state, device connections,
approval policy, persistence, and local APIs.

The browser UI used during development and the final desktop UI are clients of
the Local Core Service. They do not own product state.

## Product Role

```text
Development:
  Browser UI -> Local Core Service

Product:
  Desktop App shell -> Local Core Service
```

The current Python bridge should evolve into this service. It is not a throwaway
script; it is the MVP form of the local runtime.

## Current V2 Status

The current backend has moved beyond the original bridge-only MVP:

- WebSocket Local API is the development/test interface.
- `hello` establishes client identity, launch-token authentication, and
  capabilities.
- Structured `command` messages use `CommandEnvelope`, `CommandRouter`,
  `StateStore`, and `EventBus`.
- Async command dispatch is implemented for structured backend command
  handlers.
- `system.snapshot.request` returns a current snapshot through the Local API.
- Legacy messages remain supported for automation compatibility:
  `agent_launch`, `permission_response`, `interrupt`, and `list_sessions`.
- Structured agent lifecycle and permission commands cover launch/resume,
  interrupt, close, and permission responses behind capability/policy checks.
- Agent lifecycle commands are workspace-aware. The Local API can carry
  `workspace` in launch/resume payloads, service startup accepts `--workspace`,
  and default project workspace resolution prefers CLI workspace, then
  `AI_KEYB_WORKSPACE`, then a configured non-dot default, then a parent project
  root containing `software`, then the service start directory.
- SQLite is the primary app store for product state and permission history.
- The keyboard/device side has simulator transport, virtual input ingress,
  capability negotiation, slot mapping, projected snapshots, per-device focus,
  active tool state, config sync, notification queue, profile validation,
  lighting config, profile compilation, and active profile/import-export
  persistence.
- Codex native approval forwarding is implemented through `codex app-server`
  JSON-RPC over the stdio listen transport.
- Claude Code native approval forwarding is implemented through the Python Agent
  SDK permission callback path.
- The local hotkey harness connects as `desktop-ui` with client id
  `test-harness` for current high-risk real approval loopback testing. It is a
  temporary external input surface, not the product device transport.
- The smoke client supports `--workspace`, `--auto-start-service`, `--config`,
  `--service-start-timeout`, and `--wait-for-hotkey-approval` for real loopback
  control.
- Final backend virtual-input verification used the full pytest suite and
  focused import-boundary/Local API virtual-input checks. It did not rerun
  external real Codex or Claude CLI approval smoke.

See `implementation_status_v1.md` for the current implementation checklist and
known gaps.

## Responsibilities

The Local Core Service owns:

- process lifecycle for Codex and Claude Code adapters
- agent provider, instance, session, and run registries
- keyboard profile and configuration state
- device discovery and device transport sessions
- profile-to-device synchronization
- focus, notification, and approval policy state
- snapshot generation and event streaming
- persistence and migration
- local API authentication and client capability checks
- diagnostics and health reporting

It does not own:

- desktop installer, tray icon, or native shell UI
- firmware internals
- keyboard scan timing
- provider-specific CLI implementation details beyond adapter contracts

## Authority Model

```text
Local Core Service: source of truth
UI clients: views and command surfaces
Keyboard devices: limited views and physical control surfaces
Agent adapters: provider-specific event and command bridges
Device transports: byte/frame transport bridges
```

All mutating operations enter the service as commands. All state changes leave
the service as events. UI clients and devices should resync from snapshots after
connect or reconnect.

## Internal Modules

```text
src/app/
  startup, config loading, dependency wiring, lifecycle

src/core/
  state store
  event bus
  command router
  snapshot service
  focus manager
  notification center
  approval policy engine

src/agents/
  providers
  instances
  sessions
  runs
  permission queue
  Codex and Claude Code adapters

src/keyboard/
  profiles
  physical layouts
  keymap, layers, actions
  macros
  magnetic switch settings
  screen layouts
  agent bindings

src/devices/
  device manager
  capability model
  protocol codec
  slot mapper
  transport adapters
  config sync

src/local_api/
  HTTP API
  WebSocket event stream
  local IPC adapter, if used by the desktop shell
  API schemas and authentication

src/persistence/
  SQLite repositories
  migrations
  import/export

src/security/
  client identity
  capability checks
  risk classifier
  secret access facade

src/diagnostics/
  logs
  health checks
  support bundles
```

## Client Types

The service should distinguish clients by identity and capability:

```text
desktop-ui
browser-dev-ui
device-transport
test-client
automation-client
```

The default product path only grants broad control capabilities to the official
desktop UI. Browser development clients and automation clients should be
explicitly authorized.

## Startup Model

Development:

- service starts from CLI or dev script
- binds to `127.0.0.1`
- browser UI connects with a launch token
- smoke can auto-start the service with an explicit config and workspace for
  local loopback tests

Product:

- desktop app starts or connects to the service
- desktop app receives a launch token through a private channel
- service remains local-only
- desktop app monitors service health and can restart it

The desktop shell may be Tauri, Electron, or another native shell. The Local
Core Service boundary should not depend on that choice.

Agent launch workspace resolution is deterministic and should remain independent
from machine-specific absolute paths in tracked configuration. Product builds
should make workspace selection explicit through UI or private shell state.

## State Flow

```text
Command source
  UI / keyboard / automation / internal scheduler
        |
        v
Command Router
        |
        +--> Core domain services
        +--> Agent manager
        +--> Keyboard profile service
        +--> Device manager
        |
        v
State Store
        |
        v
Event Bus
        |
        +--> Local API event stream
        +--> Device screen/projected state sync
        +--> Persistence, when needed
```

Domain modules should not call each other through transport-specific APIs. They
should use commands, events, and direct service interfaces owned by the core
composition root.

## Migration From Current Bridge

The existing bridge can be migrated in phases:

1. Rename the concept from "device WebSocket bridge" to "Local Core Service MVP".
   Done for the local API and documentation language.
2. Bind development APIs to `127.0.0.1` by default. Done.
3. Separate UI clients from simulator/device clients. Done at the Local API
   identity/capability layer; product desktop shell is still deferred.
4. Introduce snapshot and event envelopes. Done for Local API structured paths.
5. Split `AgentProxy` into provider adapters. Partially done: Claude SDK and
   Codex app-server approval adapters exist; broader provider/instance manager
   extraction is still pending.
6. Replace the old `agent + session` model with provider, instance, session,
   and run registries. Partially done in repositories and architecture model;
   the compatibility WebSocket API still exposes `agent + session_id`.
7. Add device transport abstraction and keep WebSocket only as a simulator or
   UI/local API transport. Done for simulator/backend abstraction, virtual input
   ingress/projection, and config sync; physical USB, CDC, BLE, and 2.4G
   transports remain deferred.
8. Move persistence from session JSON toward SQLite. Done for app store,
   active profile, import/export, and permission history; legacy JSON remains
   import/export/scratch only.
9. Make launch workspace selection explicit. Done for CLI/env/config/default
   resolution and Local API launch payload propagation; product UX for
   workspace selection is still deferred.

Large file moves are not required at the beginning. The important part is to
move behavior toward the module responsibilities above.

## Non-Goals

- The Local Core Service does not expose LAN or cloud control APIs.
- The keyboard firmware does not own agent identity or long-lived session state.
- UI WebSocket messages are not the firmware/device protocol.
- The desktop app shell does not bypass the core to mutate device or agent
  state.
