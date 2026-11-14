# VP3 Developer Voice — safety contract and hardware acceptance

## Status

**v0.3: local-only UI simulation, not microphone support.** This document separates
developer ergonomics from hardware-verified privacy. No mic is initialized,
no recordings are made, no audio transport is bound, and no existing OTRO
service is altered in this draft.

The production device/voice release gate remains **BLOCKED** pending delivery
of the Waveshare ESP32-S3-Touch-LCD-1.46 and live validation.

## What the existing OTRO code actually does

- app/services/hardware_adapters.py accepts a microphone component ready state
  without universally requiring a physically verified privacy switch. When
  a physical cutoff is *claimed* to be engaged, power-sense verification must
  pass or hardware_fault results.
- app/services/vp3_os.py keeps raw audio local to the HomeServer runtime and
  reports physical disconnect as verified only when actual switch and power
  telemetry support it.
- app/services/physical_agent.py routes agent_button press/release to
  device_audio.device_audio.start_capture/stop_capture.
- app/services/device_audio.py currently uses Windows PortAudio/sounddevice.
  **Merely advertising a ready ESP32 microphone would risk recording through
  the PC microphone instead of the wearable. This is forbidden.**
- The Waveshare sample MIC_MSM.cpp starts an offline wake-word task when
  MIC_Init is called. Do **not** call it to approximate explicit push-to-talk.

## Developer-friendly modes

| Mode | What is permitted | Reality claim |
| --- | --- | --- |
| UI-only (current default) | Touch, mode, status and PTT-preview UI | no microphone |
| Developer Voice preview (opt-in) | Local-only press/hold/release state transitions; stop; timeout; reconnect reset | no microphone, no recorded audio |
| Developer Voice hardware (future) | Explicit user-started capture of *the selected ESP32 mic* via a source-bound, authenticated stream | software mute, not physical cut |
| Verified hardware privacy (future) | Hardware cutoff + independent power-sense telemetry | physically verified mute only after testing |

## v0.3 simulator contract

- Default: disabled. Neither simulated button events nor a server command
  may automatically opt in.
- The owner may click **Enable PTT preview** in the local desktop simulation.
  This is only a UI acknowledgement, **not** OTRO authentication.
- Press -> ptt_requested; release -> turn_finished; hold -> cancelled;
  Stop -> cancelled; >30 seconds -> timed_out; reconnect -> disabled.
- Every state reports: simulated_only=true, audio_transport=unbound,
  capture_capable=false, microphone_ready=false, recording=false,
  audio_bytes=0, physical_mute_verified=false.
- No outbound audio, cloud egress, Whisper/Piper invocation, or device pairing.
- The current vp3-hw-v1 hello/state wire still advertises only agent_button,
  status_light and display. OTRO PhysicalAgent therefore cannot activate
  sounddevice capture from this wearable simulator.

## Mandatory work before real Developer Voice can be enabled

1. **Source binding:** Explicitly route a *wearable* audio stream to its
   authenticated ESP32 identity, distinct from a Windows input device.
   Never infer the source from a generic hardware-ready flag.
2. **Explicit consent and visibility:** Owner-authorized mic access, locally
   visible software-recording indicator, no passive/wakeword capture.
3. **Device controls:** Real touch PTT/Stop, max duration, cancellation on
   disconnect, reconnect, loss of authorization or device switch, plus
   stream buffer disposal.
4. **Transport:** Authenticated TLS/WSS (or a separately validated USB
   audio transport). Avoid cloud raw-audio processing by default. Do not
   put long-lived HomeServer owner secrets into firmware.
5. **Existing runtime:** Pass completed audio to OTRO's established Whisper
   / Agent Chat / Piper pipeline; do not create a separate LLM or chat stack.
6. **Physical validation:** Board microphone capture/playback, audio source
   identity, PTT timing, latency, power, reconnect, buffer deletion,
   consent, privacy indicator and absence of background capture.
7. **Privacy truthfulness:** Never label software mute as a physical power
   disconnect; production hardware privacy claims require measured cutoff
   and post-switch power sensing.

## Release criteria

- [x] Local-only PTT preview uses explicit opt-in and never captures audio.
- [x] Hold, release, Stop, timeout, disconnect and reconnect semantics tested.
- [x] Existing OTRO microphone source mismatch explicitly regression-tested.
- [ ] Actual ESP32 source-bound stream is implemented and permission-tested.
- [ ] OTRO voice pipeline tested against real wearable mic + speaker.
- [ ] USB/Wi-Fi network loss, auth revocation, privacy and timing tests pass.
- [ ] Board battery, heat and wearability verified off-hand and on-hand safely.
- [ ] All required CI and hardware acceptance checks green.

**Do not merge the physical wearable firmware or claim 10/10 completion until
the unchecked hardware gates are satisfied.** The existing production privacy
implementation stays intact.
