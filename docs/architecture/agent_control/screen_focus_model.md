# Screen Focus Model

The keyboard screen is a compact message and control surface. It can display
different agent instances, sessions, runs, or notifications. Keyboard actions
target the current focus unless a binding explicitly targets something else.

## Focus Is Per Device

`ScreenFocus` is stored per physical keyboard or simulator.

```json
{
  "device_id": "kbd_01",
  "mode": "session",
  "target": {
    "provider_id": "codex",
    "instance_id": "codex-software",
    "session_id": "sess_01",
    "run_id": null
  },
  "selected_notification_id": null,
  "updated_at": 1779160000
}
```

Default assumption:

```text
Each device has independent focus.
```

This allows multiple keyboards or simulators to view/control different sessions
without fighting over a global active target.

## Focus Modes

Initial focus modes:

```text
global_dashboard
instance
session
run
notification
device_status
```

Mode meanings:

- `global_dashboard`: overview of active agents and notifications
- `instance`: one Codex or Claude Code instance
- `session`: one agent session
- `run`: one active or historical run
- `notification`: one selected notification
- `device_status`: keyboard/device diagnostics

## Default Command Targeting

Keyboard actions resolve symbolic targets through focus:

```text
focused_agent
focused_session
focused_run
focused_permission
selected_notification
```

Examples:

```text
Fn+Esc -> agent.run.interrupt -> focused_run
Fn+Enter -> agent.permission.respond(approve) -> focused_permission
Knob -> keyboard.screen.scroll -> current focus page
Next button -> agent.focus.next_session
```

If a focused target no longer exists, the focus manager should fall back to the
nearest valid target:

```text
run -> session -> instance -> global_dashboard
```

## Notifications Do Not Steal Focus By Default

Notifications are global and can arrive from any agent, session, device, or
system component.

Default assumption:

```text
Notifications do not automatically change screen focus.
```

Critical notifications may show a badge, banner, vibration, sound, or temporary
overlay, but they should not silently redirect action targets. The user can open
a notification to change focus.

## Permission Requests

Permission requests create notifications and enter the permission queue.

If the focused session has a pending permission, `focused_permission` resolves
to that request.

If multiple permissions exist:

1. Prefer pending permission for the focused run.
2. Then focused session.
3. Then focused instance.
4. Then highest priority global pending permission.

High-risk permission requests may require desktop UI confirmation even if they
are visible on the keyboard.

## Focus Selection Rules

Recommended initial rules:

- when user opens a notification, set focus to its target
- when user launches or resumes a session from the keyboard, focus that session
- when a focused session starts a new run, focus remains on the session unless
  the screen page explicitly switches to run mode
- when a run completes, focus stays on the session
- when an instance exits, focus falls back to global dashboard
- workspace presets may set initial focus when a profile activates

## Workspace and Profile Integration

Profiles can declare preferred focus behavior:

```json
{
  "profile_policy": {
    "agent_focus_policy": "last_active_in_workspace"
  }
}
```

Initial focus policies:

```text
manual
last_active_in_workspace
preferred_instance
global_dashboard
```

Profiles should not store runtime session IDs as permanent config. They may
refer to instance selectors such as `workspace_default` or `role:software_dev`.

## Device Projection

The Local Core Service projects focus state to the device using compact slots:

```text
focus_mode: session
agent_slot_id: 1
session_slot_id: 3
run_slot_id: 0
notification_slot_id: 0
generation: 7
```

The device should display labels sent by the core, not infer provider/session
meaning from IDs.

## UI Responsibilities

The desktop/browser UI should expose:

- current focus per connected keyboard
- focus history
- pending notifications
- manual focus selection
- profile focus policy settings
- visible explanation when a keyboard action cannot resolve a target

## Edge Cases

- If a keyboard is offline, its last focus may be persisted as a hint.
- On reconnect, the core sends snapshot and slot mapping before accepting
  focus-dependent commands.
- If a session is deleted while focused, focus falls back to its instance or the
  dashboard.
- If a notification expires while selected, focus returns to the previous target
  if still valid.
