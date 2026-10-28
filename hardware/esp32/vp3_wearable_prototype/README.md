# VP3 round wearable — isolated prototype v0.1

**Status:** software-only prototype ready for protocol tests; physical board compilation, touchscreen, USB handshake and power behavior are **not yet verified**. No production rollout or merge is authorized.

Target: **Waveshare ESP32-S3-Touch-LCD-1.46**, 412×412 capacitive round screen. USB-C for initial power + data. No camera. Audio capture deliberately disabled. Host computing stays on the user's existing **Windows OTRO HomeServer**; **SOFTWARE / HostGator VP3 Cloud** remains unchanged.

## Why this folder exists

The original OTRO firmware at hardware/esp32/vp3_node_controller targets a different devkit: its GPIO4–7 assignments conflict with the Waveshare touch/backlight/power controls. This folder is additive. **Do not flash the existing node-controller firmware to the Waveshare board.** No original controller, service, OTRO deployment, or SOFTWARE file was changed.

## 1. Run the local simulator before the board arrives

From the OTRO repository root (Python 3.10+; no third-party packages needed for the mock server):

    python hardware/esp32/vp3_wearable_prototype/simulator_bridge.py

On that same computer, open **http://127.0.0.1:8765/**.

The round touch UI can be used with mouse, touch or keyboard. Press/release/hold sends simulated agent_button events. State controls simulate OTRO status_light.set commands. The protocol log exposes the same JSON frames the real USB serial board will use. Reconnect simulates a new handshake.

**Important:** This is an in-memory, loopback-only fixture. It **does not** talk to actual OTRO services, VP3 Cloud, HostGator, or your remote HomeServer subdomain. It does not capture audio, access a microphone, or store credentials. It's safe to use while another agent works on OTRO.

## 2. Run the focused OTRO adapter regression

With OTRO's normal Python dependencies installed, from the repository root:

    python tests/vp3_wearable_prototype_v01.py

Checks:
- Existing vp3-hw-v1 handshake and component inventory (agent_button, status_light, display)
- Touch events, monotonic sequence handling, duplicate suppression and reconnect
- OTRO's existing status_light.set command via a fake serial port
- Unsupported commands/modes rejected in the mock
- No mic claims; physical mic power disconnect is never reported as verified
- Raw microphone tasks are deferred by OTRO placement policy
- New firmware retains its no-audio/no-privacy-claim contract

No live OTRO service or physical hardware is required.

## 3. Firmware when the Waveshare board is delivered

The VP3_Wearable.ino file is an **overlay sketch**, not a standalone replacement for the vendor display/touch driver tree.

1. Download Waveshare's official [ESP32-S3-Touch-LCD-1.46 GitHub repository](https://github.com/waveshareteam/ESP32-S3-Touch-LCD-1.46). Use the vendor's example/Arduino-3.1.1/examples/LVGL_Arduino/ directory and its documented library set (lvgl v8-compatible vendor bundle).
2. In a separate copy of that example directory, rename the folder to VP3_Wearable, **remove only the copied example entry sketch** LVGL_Arduino.ino, and place our VP3_Wearable.ino there. Keep the vendor .cpp/.h driver files in that copied sketch folder. Do not modify the vendor original.
3. In Arduino IDE use the ESP32-S3 board settings required by the Waveshare Arduino 3.1.1 sample, including Flash/PSRAM and **USB CDC On Boot**. Verify these settings against the vendor instructions for your exact revision. The current firmware has **not yet been compiled or flashed**.
4. Connect the round board to your Windows computer with a **USB-C data cable**, not a charge-only cable. Start without the LiPo battery connected; USB-C alone is sufficient for the first screen/serial tests. Select the observed COM port in Arduino IDE and flash. Verify first that screen, touch and serial monitor work at **115200 baud**.
5. After the serial monitor is closed, configure the existing OTRO adapter on the Windows HomeServer to use the board's observed COM port:
       VP3_OS_HARDWARE_ADAPTER=serial
       VP3_OS_HARDWARE_PORT=COMx
       VP3_OS_HARDWARE_BAUD=115200
   Replace COMx with the actual port. Settings take effect only when OTRO's existing adapter service reads its environment. **Do this on a controlled test instance, not the live deployed service, until validated.**

### Serial contract

| Direction | Frame |
| --- | --- |
| Device → HomeServer | hello with protocol vp3-hw-v1 and controller_id vp3-wearable-waveshare-146 |
| Device → HomeServer | state each second, with agent_button, status_light, display |
| Device → HomeServer | event agent_button with press, release, hold |
| HomeServer → device | hello re-handshake |
| HomeServer → device | command status_light.set with eight existing allowed modes |
| Device → HomeServer | ack referencing command_id |

Only a **USB serial link** is implemented in v0.1, even though the ESP32-S3 has Wi-Fi/BLE hardware. **No public internet endpoint, pairing, or credentials on the board.** Cloud and remote access remain through the existing OTRO + SOFTWARE architecture.

### Mic privacy / battery / camera

- No microphone initialization, capture, speech streaming, or audio playback in this build. The physical microphone privacy cut and power-sense verification from the original controller are **not** present on this board. The prototype must **never claim verified microphone disconnect**.
- No camera in this phase.
- For first tests use USB-C power. If you later install a 3.7 V LiPo, confirm **battery connector pitch, polarity, and charger specification on the physical board** against the exact protected battery; never force a connector or assume red/black wire positions are standardized. Charge/heat checks belong off-hand before wearing it.
- Never wear prototype electronics against the hand during initial charge, firmware, temperature, or fault tests.

## Definition of ready for a later integration PR

- [x] Simulator UI and in-memory protocol frames authored
- [x] Isolated firmware source and adapter regression test authored
- [ ] Focused CI green on the draft PR
- [ ] Official Waveshare driver toolchain compiles the overlay sketch
- [ ] Actual 412×412 touchscreen shows UI and touch events
- [ ] USB COM handshakes correctly with a **test** OTRO adapter
- [ ] HomeServer floating agent response to agent_button is observed
- [ ] Battery polarity/charging/heat tests completed safely
- [ ] No privacy claims or audio activation without verified hardware design

**No merge or production deployment until hardware tests and required checks are green.**
