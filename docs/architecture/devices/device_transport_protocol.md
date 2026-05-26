# Device Transport Protocol

The software product communicates with keyboards through device-native
transports. The keyboard does not connect to the PC bridge as a WebSocket
client.

## Transport Direction

```text
USB wired:
  keyboard -> PC
  standard HID for typing
  Vendor HID or CDC for control/config/status

2.4G:
  keyboard -> dongle -> PC
  private radio protocol on keyboard side
  USB HID + vendor control channel on PC side

Bluetooth:
  BLE HID for typing
  custom GATT service for config/control
```

WebSocket remains a local UI/test transport:

```text
UI/test client -> Local Core Service
```

It is not the firmware protocol.

## DeviceTransport Interface

All concrete transports implement the same logical interface.

```text
open()
close()
send_frame(frame)
read_frame()
get_capabilities()
get_status()
```

Initial implementations:

```text
SimulatedTransport
CdcSerialTransport
UsbVendorHidTransport
BleGattTransport
DongleVendorTransport
```

Development may keep the current WebSocket simulator, but it should be treated
as `SimulatedTransport`, not as the product device transport.

Current V1 status:

- `SimulatedTransport` and backend transport abstractions are implemented.
- Virtual input ingress is implemented for simulator/device test paths.
- Capability negotiation is implemented for the simulator path.
- Slot mapping and generation mismatch handling are implemented.
- Device snapshots are projected from Local Core state.
- Device config sync validates capabilities, chunks compiled profile payloads,
  and records simulator commit/reject results.
- Physical CDC, USB Vendor HID, BLE GATT, and dongle transports remain deferred.

## Protocol Layers

```text
Application command/event
  agent status, keyboard config, diagnostics
        |
        v
Device protocol message
  versioned, compact, transport-independent
        |
        v
Transport frame
  HID report, CDC packet, BLE GATT write/notify, dongle frame
```

The device protocol should not reuse UI JSON messages directly.

## Capability Negotiation

Every device connection starts with capability discovery.

Device identity fields:

```text
device_id
hardware_revision
firmware_version
protocol_version
device_family
transport_kind
```

Capability fields:

```text
max_payload_size
supported_message_types
supported_profile_features
supported_screen_widgets
supports_agent_slots
supports_config_sync
supports_firmware_update
```

The Local Core Service must not assume the device supports the latest features.

## Compact IDs and Slots

The device receives compact slot IDs instead of long strings.

```text
agent_slot_id
session_slot_id
run_slot_id
permission_slot_id
notification_slot_id
```

The Local Core Service owns slot mapping and sends slot snapshots with a
generation counter.

```text
slot generation changes -> device receives new mapping -> future frames use new slots
```

If a device sends a command with an old generation, the core should request
resync or reject the command with a structured error.

## Message Families

Initial message families:

```text
hello / capabilities
slot mapping
screen focus
notification
permission request
permission response
profile/config sync
diagnostics
heartbeat
error
```

Examples:

```text
HELLO_REQ
HELLO_RESP
CAPABILITIES_RESP
SLOT_MAP_BEGIN
SLOT_MAP_ITEM
SLOT_MAP_END
SCREEN_FOCUS_SET
NOTIFICATION_PUSH
PERMISSION_REQUEST_PUSH
PERMISSION_RESPONSE_CMD
PROFILE_SYNC_BEGIN
PROFILE_SYNC_CHUNK
PROFILE_SYNC_END
DIAGNOSTIC_LOG
HEARTBEAT
ERROR_RESP
```

Exact binary framing should be specified after hardware constraints and maximum
report sizes are measured.

## Snapshot and Resync

Devices reconnect by receiving a projected device snapshot:

1. transport opens
2. capabilities exchanged
3. slot map snapshot sent
4. active profile summary sent
5. screen focus sent
6. pending notification/permission summary sent
7. incremental events resume

The device snapshot is a projection of core state, not a separate source of
truth.

## Config Sync

Profile sync should be explicit and versioned.

```text
PC Core profile
  -> validate against device capabilities
  -> compile to device config subset
  -> send in chunks
  -> device validates and stages
  -> device commits or rejects
```

The device may store the safe offline subset of a profile, but the Local Core
Service remains the primary source of configuration truth.

## Transport Priority

Development priority:

1. Simulator transport
2. CDC serial or USB Vendor HID, whichever is easiest to bring up with firmware
3. USB Vendor HID as product-oriented wired control channel
4. BLE GATT
5. 2.4G dongle vendor channel

USB HID typing path is separate from the control/config channel.

## Error Handling

Detailed recovery policy is deferred, but transport errors must be observable.

Required error fields:

```text
code
message
transport_kind
device_id
frame_type, if applicable
recoverable
```

Examples:

```text
UNSUPPORTED_PROTOCOL_VERSION
PAYLOAD_TOO_LARGE
UNKNOWN_SLOT_GENERATION
CAPABILITY_MISMATCH
CONFIG_VALIDATION_FAILED
TRANSPORT_CLOSED
```

## Testing Expectations

Tests should cover:

- simulated transport frame round trip
- capability negotiation
- slot generation mismatch
- profile config validation against device capabilities
- device snapshot projection
- payload size boundary
- transport disconnect event

Current tests cover simulator negotiation, virtual input, slot generation
mismatch, device snapshot projection, config sync, and profile validation
against device capabilities.
