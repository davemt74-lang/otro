# VP3 OS v0.20 — Hardware Adapters

VP3 OS v0.20 turns the v0.10 hardware contract into a real device boundary for
VP3 Node-class hardware while preserving one shared OS across every VP3 product.

## Scope

v0.20 adds:

- a lifecycle-managed hardware adapter service
- a bounded USB serial transport
- the `vp3-hw-v1` controller protocol
- ESP32-S3 reference firmware for the VP3 Node dev kit
- Agent button events
- status-light commands
- microphone/speaker electrical-state reporting
- a physical privacy-switch state
- microphone power-sense verification
- owner-only detailed hardware diagnostics
- sanitized paired-app hardware status

It does **not** yet start an Agent Voice conversation when the button is pressed.
That belongs in the next Physical Agent phase.

## VP3 Node prototype topology

```text
                         VP3 OS / OTRO
                      Mini PC / x86 / ARM
                              │
          ┌───────────────────┼────────────────────┐
          │                   │                    │
      USB Audio           USB Serial           Ethernet/Wi-Fi
          │                   │                    │
   Mic + Speaker        ESP32-S3 controller     VP3 Cloud
                              │
               ┌──────────────┼──────────────┐
               │              │              │
          Agent button    Status LED    Privacy switch
                                             │
                                  PHYSICAL microphone
                                      power disconnect
                                             │
                                      Power-sense line
```

The controller does not run the Agent Brain or Cognitive Runtime. It is a
bounded I/O controller. The computer continues to run the complete VP3 OS.

## Configuration

The adapter is intentionally disabled unless the installation enables it.

```text
VP3_OS_HARDWARE_PROFILE=vp3_node
VP3_OS_HARDWARE_ADAPTER=serial
VP3_OS_HARDWARE_PORT=COM7
VP3_OS_HARDWARE_BAUD=115200
```

On Linux the port might be `/dev/ttyACM0` or `/dev/ttyUSB0`.

For discovery without a fixed port, configure the controller USB VID/PID:

```text
VP3_OS_HARDWARE_USB_VID=303a
VP3_OS_HARDWARE_USB_PID=1001
```

If no port is specified, VP3 OS only selects a serial device when either the
configured VID/PID matches or the USB metadata identifies the device as VP3.
It does not connect to arbitrary serial ports.

## Serial protocol

Transport is newline-delimited UTF-8 JSON at 115200 baud by default. Messages
are capped at 8192 bytes.

### OS → controller hello

```json
{"type":"hello","protocol":"vp3-hw-v1","client":"vp3-os","version":"v0.20"}
```

### Controller → OS hello

```json
{
  "type": "hello",
  "protocol": "vp3-hw-v1",
  "controller_id": "vp3-node-devkit",
  "firmware": "0.20.0",
  "hardware_revision": "node-devkit-a",
  "components": [
    "agent_button",
    "status_light",
    "microphone",
    "speaker",
    "privacy_switch"
  ],
  "capabilities": [
    "status_light",
    "agent_button",
    "privacy_switch",
    "mic_power_cut",
    "mic_power_sense"
  ]
}
```

VP3 OS rejects state and events until this handshake succeeds. A serial controller that does not complete the handshake within three seconds is disconnected and retried rather than being allowed to hold the hardware channel indefinitely.

### Controller state

```json
{
  "type": "state",
  "seq": 10,
  "components": {
    "agent_button": {
      "present": true,
      "ready": true,
      "pressed": false
    },
    "status_light": {
      "present": true,
      "ready": true
    },
    "microphone": {
      "present": true,
      "ready": true
    },
    "speaker": {
      "present": true,
      "ready": true
    },
    "privacy_switch": {
      "present": true,
      "ready": true,
      "engaged": false,
      "physical_disconnect": true,
      "microphone_powered": true
    }
  }
}
```

State sequence numbers are monotonic. VP3 OS ignores stale/duplicate state. Each accepted state frame is treated as an authoritative snapshot for the components declared during the handshake; a declared component omitted from a later state is immediately marked unavailable so stale hardware readiness cannot survive a partial controller update.

### Agent button event

```json
{"type":"event","seq":21,"event":"agent_button","action":"press"}
```

Allowed actions are `press`, `release`, and `hold`.

### Privacy switch event

```json
{"type":"event","seq":22,"event":"privacy_switch","action":"engaged"}
```

The event alone never proves the microphone is off. Verification comes from a
subsequent state frame with power sense.

### Status-light command

```json
{
  "type": "command",
  "command_id": "light-...",
  "command": "status_light.set",
  "args": {"mode": "listening"}
}
```

Allowed modes:

- `off`
- `idle`
- `listening`
- `thinking`
- `speaking`
- `privacy`
- `error`
- `updating`

There is deliberately **no microphone-power-on command** in the v0.20
controller protocol.

## Physical privacy requirement

The privacy switch must work even when:

- VP3 OS is crashed
- the ESP32 is crashed
- USB serial is disconnected
- the network is offline
- software is malicious

For the Node prototype use a physical switch that directly interrupts the
microphone power rail. A DPDT switch is convenient:

- pole A physically cuts microphone power
- pole B reports the switch position to the controller

A separate post-switch power-sense line lets the controller verify whether the
microphone rail actually went low.

**Never connect a 5 V microphone rail directly to an ESP32 GPIO.** Use a proper
logic-level sense circuit, voltage divider with safe margins, comparator,
opto-isolator, or level shifter appropriate to the final board.

VP3 OS only reports:

`physical_microphone_disconnect_verified = true`

when all of these are true:

1. the controller handshake advertises `mic_power_cut`
2. the controller handshake advertises `mic_power_sense`
3. the privacy switch is engaged
4. the state reports a physical disconnect
5. the post-switch microphone power sense reports OFF

If the switch is engaged but that verification fails, VP3 OS moves the adapter
to `hardware_fault`, reports the microphone unavailable, and raw-audio work
cannot proceed.

## Owner API

Detailed hardware information remains owner-only:

- `GET /api/v1/control/vp3-os`
- `GET /api/v1/control/vp3-os/hardware/events`
- `POST /api/v1/control/vp3-os/hardware/status-light`

A paired application sees only the sanitized hardware-adapter projection
through:

- `GET /api/v1/vp3-os/status`
- the authenticated capability registry
- the existing `vp3.os.status` Remote Bridge operation

Serial port names, controller IDs and local transport configuration are not
included in the paired projection.

## Reference firmware

Reference firmware is located at:

`hardware/esp32/vp3_node_controller/vp3_node_controller.ino`

Prototype default pins:

| Signal | GPIO |
| --- | ---: |
| Agent button | 4 |
| Privacy switch sense | 5 |
| Microphone power sense | 6 |
| Status LED | 7 |

These are development defaults, not a final PCB pinout.

## Next phase

The next phase should connect hardware events to the existing VP3 intelligence:

**VP3 OS v0.30 — Physical Agent Runtime**

That phase can map:

```text
Agent button press
    ↓
local microphone session
    ↓
local STT
    ↓
existing Agent Brain / Cognitive Runtime
    ↓
local or authorized cloud reasoning
    ↓
local TTS
    ↓
speaker
```

The v0.20 adapter boundary should remain unchanged while Node, Desk, Studio,
Team Node and Pocket add product-specific device adapters behind it.
