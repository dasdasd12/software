# Software Backend V1 Implementation Status

This document records what the current backend implementation actually supports.
It is a status companion to the architecture documents, not a replacement for
the target architecture.

## Implemented

- Local Core Service runs as the software-side state owner for tests and local
  automation.
- Local API WebSocket supports `hello`, structured `command`, `snapshot`,
  `event`, and legacy compatibility messages:
  - `agent_launch`
  - `permission_response`
  - `interrupt`
  - `list_sessions`
- Local API security includes launch-token support, origin validation, client
  identity, and capability checks.
- Legacy messages are converted into the same internal command and permission
  paths where practical.
- Runtime state flows through `CommandEnvelope`, async `CommandRouter`,
  `StateStore`, and `EventBus` for structured command/event/snapshot paths.
- Structured agent lifecycle commands cover launch/resume, interrupt, close,
  and permission response handling.
- Unified permission command handling applies client capability checks and
  approval policy gates before provider-native forwarding.
- SQLite is the primary app store for product state and audit metadata.
- JSON import/export remains available for configuration interchange.
- Focus and symbolic target resolution are tracked per device.
- Keyboard bindings resolve active profile/layer input into command envelopes,
  including agent lifecycle, permission, focus, active-tool, and profile
  actions.
- Profile, keymap, lighting, active profile, import/export, and compiled device
  config paths are implemented for the backend model.
- Device backend has simulator transport, virtual input ingress, capability
  negotiation, slot mapping, slot generation mismatch handling, projected device
  snapshots, focus manager, active tool state, config sync, notification queue,
  and profile validation.
- Local API includes the backend virtual-input path and the smoke script exposes
  the `virtual-input` scenario.
- Diagnostics cover local API, database, device transport, profile validation,
  config sync, redaction, and import-boundary/path guards.

## Agent Adapters

### Codex

Codex defaults to app-server mode using the stdio listen transport:

```text
codex app-server
```

The app-server adapter uses newline-delimited JSON-RPC over stdio:

1. send `initialize`
2. send `initialized`
3. send `thread/start` with untrusted approval policy and user approvals
4. send `turn/start` with the user prompt
5. translate native approval requests into Local API `permission_request`
6. write the JSON-RPC response after a Local API `permission_response`

Supported native approval request methods:

```text
item/commandExecution/requestApproval -> accept | decline
item/fileChange/requestApproval       -> accept | decline
item/permissions/requestApproval      -> accept | decline
execCommandApproval                   -> approved | denied
applyPatchApproval                    -> approved | denied
```

`permission_ack.forwarded=true` is returned only after the JSON-RPC response has
been written to the app-server stdin. Evidence includes the adapter name, native
channel, JSON-RPC id, thread id, turn id, item id, command, cwd, decision, and
write confirmation.

Codex `exec --json` remains a fallback/legacy read-only path. It does not
support native approval forwarding and therefore must not be used for hard
approval acceptance tests.

### Claude Code

Claude Code uses the Python Agent SDK mode for native permission callbacks.
The old headless `-p --output-format stream-json` path remains useful for
streaming compatibility, but it is not the approval-forwarding path.

`permission_ack.forwarded=true` for Claude is returned only after the SDK
permission callback has received and returned the decision.

## Permission Semantics

- Real native forwarding is required for Codex app-server and Claude SDK
  permission requests.
- Fake and unsupported adapters may return `forwarded=false` only for tests,
  explicit fallback modes, or providers that do not expose a writable native
  permission channel.
- If native forwarding fails for a provider that requires forwarding, the Local
  API returns `PERMISSION_FORWARD_FAILED` and leaves the permission pending.
- Expired Codex app-server permission requests are declined through the native
  JSON-RPC channel so the provider does not wait forever.
- The Local API does not regress a session from a terminal state back to
  `WORKING` if a provider completes immediately after a permission response.

## Persistence

SQLite stores product and audit metadata including:

- profiles
- known devices
- agent instance presets
- sessions
- runs
- permission history
- approval policies
- UI preferences
- schema migration state

Permission history records forwarding outcome and native evidence:

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

Runtime logs, local smoke logs, `data/`, and session scratch files are ignored
by Git.

## Diagnostics and Smoke

The smoke script supports these scenarios:

```text
basic
permission
real-agent
approval-real
virtual-input
```

Focused final backend virtual-input checks:

```text
pytest tests/architecture/test_import_boundaries.py -q -> 4 passed
pytest tests/bridge/test_virtual_input_local_api.py -q -> 8 passed
```

The full final implementation suite result was:

```text
pytest tests -q -> 265 passed, 1 skipped in 3.49s
```

The smoke script also supports real approval scenarios:

```text
python scripts/local-api-smoke.py --scenario approval-real --agent codex  --decision approve --require-forwarded
python scripts/local-api-smoke.py --scenario approval-real --agent codex  --decision deny    --require-forwarded
python scripts/local-api-smoke.py --scenario approval-real --agent claude --decision approve --require-forwarded
```

The Codex smoke uses a harmless stdout command:

```text
python -c "print('codex approval smoke')"
```

Earlier backend approval work verified the hard Codex acceptance path with the
local Codex CLI:

- Local API receives `permission_request`
- smoke sends `permission_response`
- adapter writes the JSON-RPC response
- Local API returns `permission_ack.forwarded=true`
- Codex either executes the command after approval or reports that it was not
  run after denial
- app-server child processes are cleaned up after turn completion

The final backend virtual-input verification pass did not rerun real external
Codex or Claude CLI approval smoke. It relied on the final full pytest suite,
focused import-boundary checks, focused virtual-input Local API checks, and
smoke help coverage for the `virtual-input` scenario.

## Known Gaps

- There is no formal frontend or desktop shell yet.
- The service is still started manually or by scripts.
- Runtime paths are partly relative to the service working directory.
- POSIX process-tree cleanup should be revisited before Linux/macOS packaging.
- USB HID, CDC, BLE, and 2.4G hardware transports are not implemented yet.
- Codex app-server is the hard acceptance provider for command approval. Codex
  fallback `exec --json` remains non-forwarding.
- Physical keyboard interaction is still represented by simulator/virtual input
  paths in this backend scope.
