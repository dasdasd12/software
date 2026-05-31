# Software Engineering Architecture

This document records the implementation architecture for the software
repository. Older MVP notes may describe the keyboard device as a WebSocket
client. That is no longer the product architecture.

## Architecture Decision

The keyboard does not connect to the PC bridge through Ethernet or WebSocket.

Software communicates with the keyboard through device-native transports:

- USB Vendor HID or CDC for wired control, configuration, diagnostics, and
  display events.
- USB HID through a 2.4G dongle for wireless input, plus a vendor control
  interface exposed by the dongle.
- BLE GATT for Bluetooth configuration and low-rate control, while normal input
  remains BLE HID.

WebSocket remains useful inside the PC application, between the local bridge and
browser UI, desktop UI, tests, or automation clients.

## System Boundary

Software owns:

- PC bridge/service
- device discovery and transport adapters
- WebHID/HIDAPI/CDC/BLE access paths
- configuration UI and APIs
- Claude Code process adapters as the active provider baseline
- parked Codex compatibility adapter code, where enabled
- agent event normalization
- WebSocket/HTTP interface for local UI clients
- protocol conversion between agent events and device protocol frames

Software does not own:

- key scan timing
- magnetic switch signal processing
- USB HID keyboard report generation in firmware
- BLE or 2.4G radio firmware behavior
- hardware display rendering internals beyond sending display/status data

## Process Architecture

```text
Claude Code
  foreground CLI hooks, SDK-specific APIs, stdout, or JSONL fallback
Codex, parked compatibility
  app-server/proxy, stdout, or JSONL fallback
        |
        v
Agent adapters
  normalize agent events and commands
        |
        v
Bridge core
  sessions, permissions, routing, state cache
        |
        +--> UI API
        |      WebSocket / HTTP JSON for local UI and tests
        |
        +--> Device API
               transport-independent device protocol
               USB Vendor HID / CDC / BLE GATT / dongle channel
```

The bridge should treat UI clients and keyboard devices as separate consumers of
the same normalized agent/session state.

## Recommended Module Boundaries

```text
src/bridge/
  agent_proxy.py       agent process integration
  protocol_unifier.py  Claude-first event normalization, legacy provider support
  session_manager.py   session and permission state
  server.py            local UI WebSocket/HTTP entry point

src/agents/
  adapters.py          Claude SDK and provider permission adapters
  codex_app_server.py  parked Codex app-server compatibility client

src/devices/
  manager.py           simulated/device transport manager
  virtual_input.py     simulator input frame decoding
  command_adapter.py   device input to command adapter
  session.py           simulated transport session loop
  protocol_codec.py    device message codec
  projection.py        core state to device snapshot projection
  projection_runtime.py snapshot/event projection runtime
  config_sync.py       backend profile/config sync
  slot_mapper.py       compact slot mapping
  transports/          simulator and future physical transports

src/device/
  device_protocol.*    shared message model and frame handling
  agent_manager.*      firmware-facing model, if compiled for device tests
```

The current WebSocket device simulator remains useful as a test harness, but it
should be treated as a PC-side simulation transport, not the real hardware
transport.

Current backend virtual-input core status:

- The implemented path is backend-only and uses simulator/virtual input for
  device interaction.
- Virtual input frames are decoded into keyboard input events, resolved through
  active profile bindings, converted to command envelopes, and dispatched
  through the async command router.
- Device projection sends compact state back to simulated devices from the same
  Local Core state used by the Local API.
- Device config sync is transport-independent and currently verified against
  simulator behavior.
- Physical USB HID, CDC, BLE, and 2.4G transports remain future adapter work.

## Device Access Layer

The software side should converge on a `DeviceTransport` abstraction:

```text
DeviceTransport
  open()
  close()
  send_frame(frame)
  read_frame()
  get_capabilities()
  get_status()
```

Concrete adapters can include:

- `UsbVendorHidTransport`
- `CdcSerialTransport`
- `BleGattTransport`
- `DongleVendorTransport`
- `SimulatedTransport`

Upper layers should not know whether a keyboard is connected through USB, 2.4G,
or BLE.

## Device Protocol Role

The bridge converts high-level state into compact device protocol messages:

- session list
- active agent status
- permission request summary
- log/status stream fragments
- user action acknowledgements
- configuration reads and writes
- device diagnostics

The bridge must shield firmware from agent-specific churn. Claude Code is the
active reference provider; if its CLI, hook, JSONL, or SDK behavior changes,
only the software adapter layer should change. Codex-specific changes should
not drive new firmware or shared backend contracts while Codex is parked.

Current V1 adapter status:

- Claude Code foreground command/tool approval uses the Claude Code hook path.
- Claude managed command/tool approval uses the Python Agent SDK permission
  callback.
- Legacy stream-json parsing remains for compatibility but is not the active
  dynamic approval path.
- Codex app-server/proxy approval support exists but is parked.
- Codex `exec --json` remains a non-forwarding fallback.
- `permission_ack.forwarded=true` remains reserved for provider-native delivery.
  The active hard acceptance path is real Claude Code foreground approval
  loopback.

## Local UI API

WebSocket/HTTP JSON is still the right shape for local UI clients because it is
easy to debug, browser-friendly, and independent from the embedded transport.

```text
Browser/Desktop UI -> local bridge:
  WebSocket or HTTP JSON

Local bridge -> keyboard:
  device protocol over USB/BLE/2.4G control channel
```

Do not reuse UI WebSocket messages as firmware protocol messages.

## Out of Scope

These are not current implementation targets:

- keyboard device as a WebSocket client
- keyboard device as an Ethernet host on the product path
- firmware running TLS or cloud API clients
- direct firmware integration with Claude Code, Codex, or any other agent

Network-facing bridge features are PC-local unless a future product decision
explicitly changes this.

## Development Workflow

Cross-repository work should start from the protocol contract, then split into
hardware and software tasks.

Suggested flow:

1. Define or review the protocol change and acceptance criteria against the
   Claude Code backend path.
2. Hardware implements firmware support in the hardware repository.
3. Software implements or updates the matching transport and bridge behavior.
4. Perform final integration review across both repositories.

Software PRs should report:

- changed files
- tests run
- transport path tested
- whether hardware protocol changed
- compatibility notes for existing firmware or simulators

Backend-only changes should additionally report:

- Local API compatibility impact
- provider-native approval evidence, if permission handling changed
- process cleanup behavior for foreground hook, SDK, or parked app-server agents
