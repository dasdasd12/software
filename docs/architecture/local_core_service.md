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

Product:

- desktop app starts or connects to the service
- desktop app receives a launch token through a private channel
- service remains local-only
- desktop app monitors service health and can restart it

The desktop shell may be Tauri, Electron, or another native shell. The Local
Core Service boundary should not depend on that choice.

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
2. Bind development APIs to `127.0.0.1` by default.
3. Separate UI clients from simulator/device clients.
4. Introduce snapshot and event envelopes.
5. Split `AgentProxy` into provider adapters.
6. Replace the old `agent + session` model with provider, instance, session,
   and run registries.
7. Add device transport abstraction and keep WebSocket only as a simulator or
   UI/local API transport.
8. Move persistence from session JSON toward SQLite.

Large file moves are not required at the beginning. The important part is to
move behavior toward the module responsibilities above.

## Non-Goals

- The Local Core Service does not expose LAN or cloud control APIs.
- The keyboard firmware does not own agent identity or long-lived session state.
- UI WebSocket messages are not the firmware/device protocol.
- The desktop app shell does not bypass the core to mutate device or agent
  state.
