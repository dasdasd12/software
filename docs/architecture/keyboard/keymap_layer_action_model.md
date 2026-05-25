# Keymap, Layer, and Action Model

The keymap model is a trigger-to-action system. It should not be limited to a
static HID keycode table because the product needs normal keyboard input,
macros, screen navigation, profile switching, and agent control.

## Core Concepts

```text
PhysicalLayout
  names the available physical keys

Layer
  defines activation and priority

Binding
  maps a trigger under conditions to an action

Action
  HID output, macro, layer control, screen command, or agent command
```

## Physical Layout

Physical layout provides stable key IDs.

```json
{
  "physical_layout_id": "ansi_75_ai_keyboard",
  "keys": [
    {
      "id": "K_ENTER",
      "row": 4,
      "col": 13,
      "label": "Enter"
    }
  ]
}
```

Key IDs should remain stable across UI, firmware config, and profile files.

## Layer

Layers define activation, priority, and display name.

```json
{
  "id": "layer_fn",
  "name": "Fn",
  "priority": 10,
  "activation": {
    "type": "hold_key",
    "key": "K_FN"
  }
}
```

Initial activation types:

```text
default
hold_key
toggle_key
oneshot
profile_mode
```

Layer objects should not contain all key behavior. Behavior belongs in
`bindings` so the same action model can be used across layers.

## Binding

```json
{
  "id": "bind_fn_enter_approve",
  "trigger": {
    "source": "key",
    "key": "K_ENTER",
    "event": "press"
  },
  "when": {
    "layer": "layer_fn"
  },
  "action": {
    "type": "agent.permission.respond",
    "target": "focused_permission",
    "decision": "approve"
  }
}
```

Trigger fields:

- `source`: key, encoder, screen_button, system
- `key`: physical key ID, when applicable
- `event`: press, release, hold, tap, double_tap, rotate_left, rotate_right

Condition fields:

- active layer
- profile mode
- device mode
- focused screen page
- agent availability

## Action Types

Initial action namespaces:

```text
hid.*
layer.*
macro.*
profile.*
screen.*
agent.*
keyboard.tool.*
device.*
```

Examples:

```json
{
  "type": "hid.key",
  "keycode": "KC_A"
}
```

```json
{
  "type": "macro.run",
  "macro_id": "macro_commit_prefix"
}
```

```json
{
  "type": "layer.momentary",
  "layer_id": "layer_fn"
}
```

```json
{
  "type": "screen.navigate",
  "direction": "next_page"
}
```

```json
{
  "type": "agent.run.interrupt",
  "target": "focused_run"
}
```

```json
{
  "type": "agent.permission.respond",
  "target": "focused_permission",
  "decision": "approve"
}
```

```json
{
  "type": "keyboard.tool.switch",
  "target": {
    "tool_id": "permissions"
  }
}
```

```json
{
  "type": "keyboard.tool.next"
}
```

## Agent Actions

Agent actions must pass through the command router and approval policy.

They should not directly call Codex or Claude Code adapters.

Supported initial targets:

```text
focused_agent
focused_session
focused_run
focused_permission
workspace_default
preferred_instance
```

Examples:

```json
{
  "type": "agent.session.launch_or_resume",
  "provider_id": "codex",
  "instance_selector": "workspace_default"
}
```

```json
{
  "type": "agent.focus.next_session"
}
```

## Tool Actions

Tool actions are backend control-mode commands owned by the keyboard runtime,
not screen widgets. They pass through the command router as service-required
actions and never call agent providers or bridge adapters directly.

Initial tools:

```text
agent_control
session_list
permissions
profile_config
device_status
```

`keyboard.tool.switch` selects a known tool for the input device. Bindings
should normally put `tool_id` in the target or action payload; the command
source supplies the originating `device_id`. `keyboard.tool.next` advances to
the next configured tool for that device, selecting the first configured tool
when none is active.

## Macro Model Boundary

Macros are action sequences. They should support more than text injection, but
high-risk steps must be policy-gated.

```json
{
  "id": "macro_commit_prefix",
  "name": "Commit Prefix",
  "steps": [
    {
      "type": "text",
      "value": "feat: "
    }
  ]
}
```

Possible future macro steps:

```text
text
key_chord
delay
screen_command
agent_prompt
agent_command
```

Macros that trigger agent actions or shell/file operations must go through the
same command and approval policy path as direct agent bindings.

## Resolution Pipeline

```text
Raw input event
  -> physical key/encoder identity
  -> active layer set
  -> matching bindings
  -> binding priority resolution
  -> action validation
  -> command or device output
```

HID output can be firmware-local when possible. Agent, screen, and profile
commands should be resolved by the Local Core Service unless a compact offline
behavior is explicitly defined.

## Offline Behavior

When the Local Core Service is unavailable:

- `hid.*`, safe `layer.*`, and safe local `macro.*` actions may run
- `agent.*` actions become unavailable
- `keyboard.tool.*` actions become unavailable
- `screen.*` actions may navigate local fallback pages
- high-risk macro steps are blocked

The firmware should expose clear unavailable state for UI/screen display.

## Validation Rules

Bindings are invalid when:

- trigger key does not exist in layout
- layer reference is missing
- action type is unknown
- target selector cannot be resolved for its action family
- action violates global safety policy
- firmware capabilities do not support required local behavior

Validation should happen before syncing a profile to the device.

## Testing Expectations

Tests should cover:

- layer priority resolution
- key press/release binding
- agent symbolic target resolution
- unavailable agent target behavior
- macro safety gating
- profile validation before device sync
