# Persistence Model

The Local Core Service should use SQLite as its primary persistent store. JSON
is useful for import/export and early prototypes, but the product model has too
many relationships for long-term JSON-only storage.

Current V1 status:

- SQLite app store and migrations are implemented.
- Repositories cover profiles, known devices, agent instance presets, sessions,
  runs, permission history, approval policies, UI preferences, and workspace
  bindings.
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

Profiles are stored in normalized or semi-structured SQLite tables, then
exported/imported as JSON.

The exported profile schema should be stable and versioned. The internal DB
schema may evolve with migrations.

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

`evidence` is provider-specific but must be structured JSON. For Codex
app-server it includes fields such as native channel, JSON-RPC id, thread id,
turn id, item id, command, cwd, decision, and response write status. For Claude
SDK it includes callback delivery/return evidence.

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

The keyboard may persist only the offline subset needed to remain useful without
the PC:

- active profile ID
- compiled keymap/layer data
- magnetic config
- safe local macros
- basic screen fallback settings
- last focus hint

The device is not the source of truth for full profile or agent state.

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
