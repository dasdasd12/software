# Persistence Model

The Local Core Service should use SQLite as its primary persistent store. JSON
is useful for import/export and early prototypes, but the product model has too
many relationships for long-term JSON-only storage.

Current V1 status:

- SQLite app store and migrations are implemented.
- Repositories cover PC-side device profiles, workspace presets, known devices,
  agent instance presets, sessions, runs, permission history, approval policies,
  UI preferences, and workspace bindings.
- JSON import/export remains available for interchange and diagnostics.
- Legacy session JSON is not the product state authority.

## Storage Locations

Recommended Windows locations:

```text
%APPDATA%\AI Keyboard\
  app.db
  exports\
  profiles\

%LOCALAPPDATA%\AI Keyboard\
  logs\
  cache\
  diagnostics\
  transcripts\
```

Secrets should use the OS secret store rather than plaintext config files.

## Data Categories

Persist in SQLite:

- PC-side device profile library
- workspace presets
- keymaps
- binding scopes
- macros
- magnetic switch settings
- screen configs
- lighting configs
- agent binding sets
- known devices
- agent instance presets
- workspace bindings
- sessions metadata
- runs metadata
- permission history
- approval policies
- UI preferences
- schema migrations

Persist as files:

- exported profiles
- optional transcripts
- diagnostic bundles
- firmware packages
- large logs

Store in OS secret store:

- API keys
- OAuth tokens
- sensitive credentials

## Runtime vs Persistent State

Persistent configuration:

```text
profiles
workspace bindings
agent instance presets
approval policies
known devices
UI preferences
```

Runtime state:

```text
active process handles
transport handles
live event queues
current device connections
in-memory output buffers
```

Runtime state may produce persisted metadata, but raw runtime handles must never
be serialized.

## Agent Transcript Policy

Default assumption:

```text
Do not persist full agent transcripts by default.
Persist metadata and recent buffers only.
Full transcript persistence is user-controlled.
```

Suggested modes:

```text
off
metadata_only
recent_buffer
full_transcript_per_workspace
```

Transcript storage may contain sensitive code, prompts, or credentials. It must
respect privacy and retention settings.

## Profile Storage

PC-side device profiles are stored in normalized or semi-structured SQLite
tables, then exported/imported as JSON or packaged as `ProfilePackage` when
written to a device slot.

The exported `DeviceProfile` schema should be stable and versioned. The
internal DB schema may evolve with migrations. Software-side
`WorkspacePreset` records are separate and may reference device profiles,
screen configs, lighting configs, and agent binding sets.

## Sessions and Runs

Persist metadata:

- provider ID
- instance ID
- session ID
- title
- workspace
- state
- created/updated timestamps
- last known run ID

Do not assume a persisted session can always resume. Resume capability depends
on the provider adapter and native CLI/SDK support.

## Permission History

Persist permission metadata:

- permission ID
- target agent/session/run
- action type
- risk level
- decision
- source client
- timestamp
- summary
- forwarded status
- adapter/native request metadata
- forwarding evidence

Full details may be sensitive and should follow the user's data retention
setting.

Current V1 permission history payload records:

```text
permission_id
session_id
run_id
action_type
risk_level
decision
source_client
timestamp
summary
forwarded
evidence
native
```

`evidence` is provider-specific but must be structured JSON. The active
reference provider is Claude Code: foreground hook evidence records native hook
channel, request/session metadata, decision, and response write status; managed
SDK evidence records callback delivery/return status. Parked Codex
compatibility evidence may still include native channel, JSON-RPC id, thread
id, turn id, item id, command, cwd, decision, and response write status.

## Migration

SQLite migrations should be explicit and tested.

```text
schema_migrations
  version
  applied_at
  description
```

Migration rules:

- migrations are forward-only for normal app startup
- backup before destructive migrations
- exported profiles carry their own schema version
- unsupported profile versions should produce actionable errors

## Import and Export

JSON import/export should support:

- single profile export
- full local config export
- redacted diagnostics export
- profile import with validation

Imports should not silently overwrite active profiles or policies. They should
create new IDs or ask for explicit replacement.

## Device-Side Persistence

The keyboard persists the resources needed to remain useful without the PC:

- `DeviceSettings`
- five user `ProfilePackage` slots plus the hidden factory-default slot
- current active slot runtime fact in `DeviceState`
- compiled `RuntimeTable` cache when valid
- `ScreenConfig` and `LightingConfig`, once those resources are defined
- `CalibrationData`
- CH585 wireless/pairing state owned by CH585 or mirrored through H417 policy

The device is the source of truth for its committed local profile slots while
disconnected. Local Core stores the PC-side library and mirrors device slots
after synchronization.

The device is not the source of truth for software-only workspace presets,
agent bindings, long-lived agent state, or provider session history.

## Testing Expectations

Tests should cover:

- DB migration from empty database
- profile create/update/delete
- export and re-import round trip
- permission metadata persistence
- native approval forwarding evidence persistence
- transcript retention modes
- schema version rejection
- backup before risky migrations
