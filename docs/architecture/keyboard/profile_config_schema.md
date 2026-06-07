# Device Profile and Workspace Preset Schema

This document is aligned with the current keyboard configuration HTML
architecture under `docs/architecture/keyboard_config_site`.

The word `Profile` has two different meanings in older software documents. This
document fixes that split:

- `DeviceProfile`: keyboard behavior that can be written to the device as a
  `ProfilePackage`.
- `WorkspacePreset`: software-only product preset that may reference a device
  profile, screen config, lighting config, agent bindings, and workspace
  preferences.

Only `DeviceProfile` / `ProfilePackage` is shared with firmware as the native
keyboard profile artifact.

## Ownership

The Local Core Service owns the PC-side library, import/export, editing UI
state, workspace presets, and device synchronization workflow.

The device owns the committed contents of its local `DeviceProfileStore`:

- five user `ProfilePackage` slots
- one hidden factory-default slot
- active slot runtime state in `DeviceState`
- `DeviceSettings` used before profile load

When the device is disconnected, its local slots are still the source of truth
for what the keyboard runs. When the PC reconnects, Local Core reads device slot
metadata and reconciles it with the PC-side library or mirror. Local Core must
not assume that its last cached copy is still authoritative.

## AppConfig

`AppConfig` is the top-level software-side persisted application configuration.
It is not written to firmware as a single object.

```json
{
  "schema_version": "1.0",
  "active_workspace_preset_id": "preset_coding_default",
  "device_profile_library": [],
  "workspace_presets": [],
  "screen_configs": [],
  "lighting_configs": [],
  "agent_binding_sets": [],
  "known_devices": [],
  "agent_instance_presets": [],
  "workspace_bindings": [],
  "global_approval_policy_id": "policy_standard",
  "ui_preferences": {}
}
```

Expected fields:

- `schema_version`: software application migration version.
- `active_workspace_preset_id`: active software preset for the desktop UI.
- `device_profile_library`: PC-side reusable `DeviceProfile` sources.
- `workspace_presets`: product presets that combine independent resources.
- `screen_configs`: screen page/widget/media configuration.
- `lighting_configs`: RGB/lighting configuration and rules.
- `agent_binding_sets`: software-side agent command bindings.
- `known_devices`: previously connected keyboards/dongles and slot mirrors.
- `agent_instance_presets`: reusable agent instance definitions.
- `workspace_bindings`: workspace-to-preset preferences.
- `global_approval_policy_id`: default approval policy.
- `ui_preferences`: UI-only settings.

## WorkspacePreset

`WorkspacePreset` is a software product convenience. It can group independent
configuration resources, but it is not a firmware `ProfilePackage`.

```json
{
  "id": "preset_coding_default",
  "name": "Coding",
  "device_profile_id": "dev_profile_coding_keyboard",
  "screen_config_id": "screen_coding",
  "lighting_config_id": "lighting_coding",
  "agent_binding_set_id": "agent_bindings_coding",
  "default_agent_instance_id": "cc-software",
  "focus_policy": "last_active_in_workspace",
  "metadata": {}
}
```

Rules:

- A preset may reference a `DeviceProfile`, but it does not extend or override
  firmware profile semantics.
- Screen and lighting configuration stay independent from `DeviceProfile`.
- Agent bindings stay in Local Core. They are not compiled into the keyboard
  offline profile.
- Runtime session IDs, provider-specific request IDs, and absolute machine paths
  must not be stored in reusable presets.

## DeviceProfile

`DeviceProfile` describes keyboard input behavior. It is the source object that
is packed into `ProfilePackage` and compiled into firmware `RuntimeTable`.

The detailed schema is maintained in the HTML profile page. Software code should
model the same boundaries:

```json
{
  "schema_version": "1.0",
  "identity": {
    "id": "dev_profile_coding_keyboard",
    "name": "Coding Keyboard",
    "revision": 1,
    "target_device_family": "ai_keyboard_ch32h417"
  },
  "defaults": {},
  "control_assignments": {},
  "control_overrides": {},
  "behaviors": {},
  "binding_scopes": {},
  "interaction_rules": {},
  "macro_defs": {},
  "report_rate_policy": {},
  "input_guard_policy": {},
  "metadata": {}
}
```

`DeviceProfile` may contain:

- control `type` and `mode` assignments
- trigger parameters such as actuation threshold, rapid trigger delta, debounce,
  deadzone, and encoder detent parameters
- per-control parameter overrides
- behavior definitions such as host input, macro call, profile switch,
  overlay/scope control, device command, tap-hold, DKS, and no-op
- `binding_scopes`, which replace older software `layers`
- interaction rules such as combo and SOCD
- safe offline macro bytecode source
- profile-local report-rate policy, used only when global report rate is
  disabled in `DeviceSettings`
- profile-local input guard policy, used only when global guard is disabled in
  `DeviceSettings`

`DeviceProfile` must not contain:

- screen layout, page definitions, media, image, video, or animation resources
- lighting effects or lighting trigger rules
- `DeviceSettings`
- `DeviceState`
- calibration measurements, calibration offsets, sensor baselines, or hardware
  acquisition facts
- CH585 pairing records, wireless host records, or current connection state
- Local Core agent bindings, provider names, session IDs, permission IDs, or
  workspace focus policy

## ProfilePackage

`ProfilePackage` is the PC/device/screen exchange container for one
`DeviceProfile`.

The first implementation should use the binary container defined in the HTML
`ProfilePackage` page:

```text
ProfilePackageBinary
  header
  section directory
  source_profile_json       # canonical JSON bytes, required
  runtime_table_cache       # optional RuntimeTableBinary bytes
  resource_estimate         # optional
  metadata                  # optional
```

The canonical source bytes are the integrity source for `source_hash` and
package CRC. Software import/export may offer readable JSON, but slot writes and
readback must preserve the canonical package semantics.

## Binding Scopes Instead of Layers

Older software documents used `layers`. In the device profile model, the same
runtime role is represented by `binding_scopes` plus `overlay_control`
behaviors.

Compatibility mapping:

```text
legacy layer id        -> binding_scope id
legacy layer priority  -> binding_scope priority
legacy hold/toggle     -> overlay_control behavior
legacy layer binding   -> binding_scope.bindings entry
```

Do not introduce another behavior inheritance system on top of
`binding_scopes`.

## Agent Bindings

Agent bindings are software-side `agent_binding_sets`. They connect UI shortcuts
or software-controlled input events to Local Core commands.

They may be referenced by `WorkspacePreset`, but they are not part of
`DeviceProfile`.

When the PC service is unavailable:

- the device must not invent agent decisions
- agent actions are unavailable or shown as unavailable
- normal keyboard behavior, profile switching, and safe local macros continue
  from the device's local `DeviceProfileStore`

## Screen and Lighting Config

Screen and lighting are independent resources:

```text
ScreenConfig
  pages/widgets/media/resources
  CPU/status widgets
  image/animation/video presentation rules

LightingConfig
  effects
  palettes
  zones
  runtime signal rules
```

They may consume runtime signals produced by the keyboard engine, but they are
not embedded in `DeviceProfile`.

## Validation Rules

Device profiles should be rejected or marked invalid when:

- schema version is unsupported
- target device family is incompatible
- referenced `control_id` is not in the known keyboard/control map
- required `type + mode` parameters are missing
- behavior references are missing, recursive, or unsupported by device
  capabilities
- binding scope priority conflicts are ambiguous
- macro bounds exceed firmware capability
- profile-local report rate exceeds device capability
- profile-local input guard conflicts with schema rules

Workspace presets should be validated separately:

- referenced device profile, screen config, lighting config, and agent binding
  set exist
- agent targets can be resolved by Local Core
- high-risk actions are covered by approval policy
- reusable presets do not store runtime session IDs or machine-local absolute
  paths

Validation should produce user-facing diagnostics, not silent fallback.
