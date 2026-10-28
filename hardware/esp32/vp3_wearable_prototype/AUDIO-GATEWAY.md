# Phase 2 — VP3 wearable source-bound voice gateway (v0.1)

**Status: software development only; hardware NOT accepted.**
Separate branch: feature/vp3-wearable-voice-gateway-v01.
Base: feature/vp3-wearable-prototype-v01 (draft PR #363).
Do not deploy, enable in production or merge until all hardware gates pass.

## Why we need this

The original OTRO Physical Agent opens a Windows PortAudio input via
app/services/device_audio.py. Reporting a wearable microphone as ready does
not make that audio come from the ESP32. This gateway accepts *explicitly
uploaded* audio from an authenticated wearable identity instead.

It reuses OTRO's existing pairing and revocation database, Whisper
(local_voice.transcribe), canonical Agent Chat (context_chat.chat), and
Piper (local_voice.synthesize). There is no new AI runtime, audio model,
device-token database or duplicated chat implementation.

## Feature gate

The route is registered but **disabled by default**. Only a deliberately
configured OTRO test instance with VP3_WEARABLE_AUDIO_ENABLED=1 can serve
audio requests. It is not enabled on the user's deployed HomeServer.

Requests from non-local clients require the *actual trusted ASGI HTTPS
scheme*. Never trust a client-supplied X-Forwarded-Proto. HostGator shared
hosting and the existing subdomain route have NOT been verified as a
suitable TLS reverse proxy for this new API. That is an independent
deployment acceptance gate.

## Pairing and least privilege

- Owner approves a normal VP3 paired application using the existing claim-v1
  pairing flow. Its app_key must identify exactly one wearable in the form
  vp3-wearable-..., with approved agent.chat AND devices.control permissions.
- The wearable sends its own revocable pairing token using the Authorization:
  Bearer header over HTTPS. **Never store the HomeServer owner bootstrap
  token or Cloud API keys in the wearable.**
- The server checks the token, live revocation state, required scopes and
  exact device identity on every request. This uses existing OTRO pairing
  functions; no new credentials store.
- No owner tools or general tool execution is granted to Agent Chat.
  Personal memory, knowledge and contacts are disabled in this initial
  audio route. Responses are the agent's spoken WAV only.
- The firmware must have explicit user consent before starting a microphone
  turn and a visible capture indicator. The server cannot prove a button was
  physically held, so this firmware/user-interface test is still required.

## HTTPS turn protocol (only for test devices)

1. POST /api/v1/wearable/audio/start
   JSON: {"device_id":"vp3-wearable-...","intent":"press"}
   Response: one-time turn_id + 20s expiry + required audio format.
2. ESP32 records **mono, 16,000 Hz, signed 16-bit PCM** after explicit press.
   There is no background microphone capture. The vendor MIC_Init sample
   starts wake-word recognition, so it must NOT be used as a PTT shortcut.
3. On release, POST /api/v1/wearable/audio/finish/{turn_id}
   Content-Type: audio/wav
   Body: WAV up to 20 seconds and the byte size cap.
   One-time consumption prevents replay; no arbitrary Windows mic selected.
4. OTRO transcribes locally with Whisper, calls the existing read-only
   paired-app Agent Chat, synthesizes with Piper, and sends a WAV response.
   The device plays the returned audio using its speaker driver.
5. At any point, POST /api/v1/wearable/audio/stop/{turn_id}
   to cancel. An expired turn, revoked token, wrong device identity, invalid
   WAV or duplicate completion is rejected. Stop/cancel closes active work
   cooperatively through the existing cancellation objects.

This is **turn-based HTTPS**, not continuous full-duplex streaming or a
websocket. A future low-latency stream may use WSS after the actual board,
subdomain ingress and authentication are tested.

## Privacy and availability

- Default disabled, explicit owner pairing, bounded sessions (8),
  20-second PTT limit and bounded input/reply WAV sizes.
- No raw audio is uploaded to VP3 Cloud by this gateway. The HomeServer
  local Whisper provider may use its established temporary files, governed
  by the pre-existing local voice cleanup path.
- No server-side permanent recording, log of transcript, or token storage.
  Stop, timeout, token revocation and reconnect must invalidate turns.
- If local Whisper/Piper or Agent Chat are unavailable, the request fails
  explicitly; it must not silently fall back to Windows microphone or
  unauthorized Cloud processing.
- A software capture indicator is not evidence of a physical mic power cut.

## Tests

From the OTRO root:

    python tests/vp3_wearable_voice_gateway_v01.py

The test uses an isolated temporary OTRO DB, real claim-v1 pairing, fake
Whisper/Agent Chat/Piper functions and synthetic PCM WAV. It checks 401/403/
409/413/415/422/426/503, source binding, cancellation, revocation, replay
prevention, HTTPS checks and router registration. No real microphone, speaker,
LLM, internet service or HomeServer installation is required.

## Release acceptance still pending

- [x] Source-bound paired wearable identity; replay and revocation checks
- [x] Default-off endpoint; strict WAV size/format and turn timeout
- [x] Existing local STT / scoped Agent Chat / TTS integration seam
- [x] Simulated audio, cancel, wrong-device and malformed-input tests
- [ ] Real ESP32 PTT capture and speaker playback firmware
- [ ] Physical source identity and microphone activity indicators verified
- [ ] Actual subdomain TLS proxy and remote network security tested
- [ ] Hardware disconnect, mic privacy, battery and charging tested
- [ ] Audio latency, jitter, reconnect, revocation during processing, Stop
      after browser disconnect and concurrent-device load tested on hardware
- [ ] Required green CI on the final commit and documented owner acceptance

**No merge or production deployment until the physical integration is 10/10.**
