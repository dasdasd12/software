# Profile Configuration Schema

Profiles are the product-level configuration unit. A profile contains keyboard
behavior, device-facing settings, screen layout, and agent bindings.

Agent sessions and runs are runtime state. They are not stored permanently
inside profiles.

## Ownership

The Local Core Service is the source of truth for profiles.

The keyboard may store a compact offline subset:

- active profile ID
- keymap and layers needed for normal typing
- magnetic switch settings
- basic macros that do not require the PC
- screen fallback preferences
- last known focus hint

Agent bindings depend on the Local Core Service. If the PC service is
unavailable, agent bindings should show or behave as unavailable rather than
silently performing unrelated actions.

## AppConfig

`AppConfig` is the top-level persisted application configuration.

```json
{
  "schema_version": "1.0",
  "active_profile_id": "profile_coding_default",
  "profiles": [],
  "known_devices": [],
  "agent_instance_presets": [],
  "workspace_bindings": [],
  "global_approval_policy_id": "policy_standard",
  "ui_preferences": {}
}
```

Expected fields:

- `schema_version`: migration version
- `active_profile_id`: currently selected profile
- `profiles`: user profiles
- `known_devices`: previously paired or connected keyboards/dongles
- `agent_instance_presets`: reusable Codex/Claude Code instance definitions
- `workspace_bindings`: workspace to profile/agent preferences
- `global_approval_policy_id`: default approval policy
- `ui_preferences`: UI-only settings

## Profile

```json
{
  "schema_version": "1.0",
  "id": "profile_coding_default",
  "name": "Coding",
  "version": 1,
  "target_device_family": "ai_keyboard_ch32h417",
  "tags": ["coding", "agent-control"],
  "keymap": {},
  "layers": [],
  "macros": [],
  "magnetic_config": {},
  "screen_layout": {},
  "agent_bindings": [],
  "profile_policy": {},
  "metadata": {}
}
```

Profiles initially target a device family, not an arbitrary hardware layout.
Layout migration can be added later.

## Device Family and Layout

```json
{
  "target_device_family": "ai_keyboard_ch32h417",
  "physical_layout_id": "ansi_75_ai_keyboard"
}
```

Default assumption:

```text
Profiles are device-family scoped first.
Future import/migration tools may translate between layouts.
```

## Magnetic Switch Config

Magnetic settings should include units and support default plus per-key
overrides.

```json
{
  "magnetic_config": {
    "unit": "mm",
    "default": {
      "actuation_mm": 1.2,
      "release_mm": 1.0,
      "rapid_trigger": true,
      "rapid_trigger_sensitivity_mm": 0.15
    },
    "per_key": {
      "K_W": {
        "actuation_mm": 0.8
      }
    },
    "modes": []
  }
}
```

Initial implementation should support:

- global default
- per-key override
- explicit units

Mode switching can be added after the basic model is stable.

## Screen Layout

Profiles store screen layout definitions, not live agent state.

```json
{
  "screen_layout": {
    "id": "screen_coding",
    "pages": [
      {
        "id": "agent_status",
        "title": "Agent",
        "widgets": [
          {
            "id": "focused_session_card",
            "type": "agent_session_card",
            "data_source": "agent.focused_session"
          },
          {
            "id": "notification_strip",
            "type": "notification_strip",
            "data_source": "notifications.recent"
          }
        ]
      }
    ]
  }
}
```

Default rendering direction:

```text
The device renders with LVGL from widget/state data.
The PC sends compact widget state and updates, not full video frames.
```

## Agent Bindings

Agent bindings connect keyboard inputs to agent commands through focus and
policy.

```json
{
  "id": "approve_focused",
  "trigger": {
    "source": "key",
    "key": "K_ENTER",
    "layer": "layer_fn",
    "event": "press"
  },
  "command": {
    "type": "agent.permission.respond",
    "decision": "approve",
    "target": "focused_permission"
  },
  "safety": {
    "allow_high_risk": false,
    "requires_screen_confirmation": true
  }
}
```

Default assumptions:

- agent bindings are first-class profile entries
- bindings resolve symbolic targets through the focus manager
- low-risk approvals may be handled from the keyboard
- high-risk approvals require desktop confirmation until a strong-confirm flow
  is designed

## Workspace Bindings

Workspace bindings let profiles and agent presets follow the active project.

```json
{
  "workspace_id": "software",
  "path": "D:\\UserData\\My Documents\\AI Keyboard\\software",
  "default_profile_id": "profile_coding_default",
  "default_agent_instance_id": "codex-software",
  "focus_policy": "last_active_in_workspace"
}
```

Profiles should refer to workspace and instance selectors rather than hard-code
runtime session IDs.

## Offline Behavior

When the Local Core Service is unavailable:

- normal typing, layers, magnetic settings, and safe local macros should keep
  working
- agent bindings should be disabled or show `Agent unavailable`
- high-risk macros or agent-triggering macros should not run
- the screen may show last-known local device status or fallback page

The keyboard must not invent agent decisions offline.

## Import and Export

Initial import/export can use JSON. SQLite should remain the primary internal
storage for the Local Core Service.

Cloud sync, sharing, and marketplace-style profile distribution are out of
scope for the first implementation, but schema metadata should leave room for:

- author
- created_at
- updated_at
- source
- compatibility
- migration history

## Validation Rules

Profiles should be rejected or marked invalid when:

- schema version is unsupported
- target device family is incompatible
- layer references are missing
- key IDs are unknown for the selected layout
- agent bindings target unavailable command types
- safety rules conflict with global policy
- magnetic values are outside firmware-supported ranges

Validation should produce user-facing diagnostics, not silent fallback.
