# Binding Scope and Behavior Model

This document replaces the older "Keymap, Layer, and Action" model for
device-facing keyboard behavior.

The firmware profile model is:

```text
control_id + ControlSignal
  -> active binding_scopes
  -> behavior
  -> RuntimeIntent
```

Software may keep compatibility UI labels such as "Fn layer", but the data model
written to the keyboard must use `binding_scopes`, `behaviors`, and
`interaction_rules`.

## Core Concepts

```text
ControlId
  stable ID shared by PC editor, Profile, Screen, diagnostics, and firmware
  examples: key_000, akey_012, enc_000, joy_000_x

ControlSignal
  profile-level signal produced by type+mode trigger algorithms
  examples: press, release, hold, tap, cw_step, ccw_step

BindingScope
  dispatch scope with activation state, priority, bindings, and fallback

Behavior
  named runtime action selected by dispatch

InteractionRule
  cross-control or cross-signal rule such as combo or SOCD
```

`ControlId` names an input source. Its physical meaning comes from the control
map and the profile assignment, not from the string alone.

## Physical and Control Map

The product currently targets one known keyboard layout. The editor can load the
fixed control map offline and does not need hardware communication to know the
basic layout.

```json
{
  "control_id": "akey_000",
  "source": "h417_key_scan",
  "control_index": 0,
  "default_label": "A"
}
```

Rules:

- `control_id` is stable across PC editor, `ProfilePackage`, diagnostics, and
  firmware logs.
- `control_index` is the compact runtime index used by `RuntimeTable`.
- Profile source references `control_id`; compiled runtime tables use indexes.
- Profile does not create new controls. Unassigned controls are allowed and
  produce no behavior.

## Binding Scopes

`binding_scopes` replace older software `layers`.

```json
{
  "base": {
    "priority": 0,
    "default_active": true,
    "bindings": {
      "akey_000": "b_keyboard_a"
    },
    "unbound": "b_no_op"
  },
  "fn": {
    "priority": 20,
    "default_active": false,
    "bindings": {
      "akey_000.press": "b_profile_next"
    },
    "unbound": "fallthrough"
  }
}
```

Dispatch lookup order:

```text
signal = control_id + event
lookup = scope.bindings[control_id.event]
      ?? scope.bindings[control_id]
      ?? scope.unbound
```

Rules:

- `base` is always active.
- Non-base scopes are inactive unless `default_active = true` or activated by
  an `overlay_control` behavior.
- Active scopes are evaluated by descending priority.
- Equal-priority scopes that bind the same signal are a schema error.
- Scopes select behavior; they do not merge behavior fields.

## Legacy Layer Mapping

Older software objects map to the new model as follows:

```text
Layer.id              -> BindingScope.id
Layer.priority        -> BindingScope.priority
Layer.default         -> BindingScope.default_active
Layer hold/toggle     -> overlay_control behavior
Binding.when.layer    -> binding scope membership
layer.momentary       -> overlay_control(action = hold)
layer.toggle          -> overlay_control(action = toggle)
layer.oneshot         -> overlay_control(action = oneshot)
```

The software UI may still display "Fn layer" because users understand that
language, but serialized device profiles should not reintroduce a separate
`layers` collection.

## Behavior Kinds

Device-facing behavior kinds are finite and compileable:

```text
host_input
macro_call
profile_switch
overlay_control
device_command
tap_hold
dks
no_op
```

Examples:

```json
{
  "b_keyboard_a": {
    "kind": "host_input",
    "input": {
      "report": "keyboard",
      "usage": "KC_A"
    }
  }
}
```

```json
{
  "b_profile_next": {
    "kind": "profile_switch",
    "target": "next"
  }
}
```

```json
{
  "b_fn_hold": {
    "kind": "overlay_control",
    "scope": "fn",
    "action": "hold"
  }
}
```

`agent.*`, `screen.*`, and software workspace commands are not compiled into the
device `DeviceProfile`. If a future firmware feature needs a local device
command, it must be represented as a bounded `device_command` with explicit
offline semantics.

## Interaction Rules

`interaction_rules` are for cross-control or cross-signal logic.

Examples:

- combo recognition
- SOCD direction arbitration
- member suppression and release-to-rearm behavior

Tap-hold and DKS remain behaviors because they are selected by one binding and
then choose child behaviors using time or travel state. Combo and SOCD live in
`interaction_rules` because they observe multiple controls before dispatch.

## Resolution Pipeline

```text
Control Data Layer
  -> type+mode trigger algorithm
  -> ControlSignal queue
  -> interaction_rules
  -> binding_scope dispatch
  -> behavior execution
  -> RuntimeIntent queue
  -> V5F report adaptation / allowed device request handling
```

V3F owns the deterministic offline keyboard behavior. V5F adapts
`RuntimeIntent` to USB/wireless reports and device-management requests. Local
Core is not in this realtime path.

## Offline Behavior

When the Local Core Service is unavailable:

- host input, profile switching, overlay controls, interaction rules, and safe
  local macros keep working from the device's local `ProfilePackage` slots
- agent commands are unavailable because they are software-side bindings
- screen may show local device state or projected state that was last known
- high-risk PC automation must not execute from firmware

The keyboard must remain usable as a keyboard without PC software.

## Validation Rules

Device profile validation should reject:

- unknown `control_id`
- unknown behavior reference
- recursive behavior graph
- unsupported behavior kind
- ambiguous same-priority binding conflicts
- overlay target scope that does not exist
- macro bounds above firmware capability
- interaction rule that references unavailable controls

Validation should happen before writing a `ProfilePackage` to a device slot and
again on device before compiling or installing a `RuntimeTable`.
