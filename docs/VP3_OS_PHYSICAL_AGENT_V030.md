# VP3 OS v0.30 — Physical Agent Runtime

VP3 OS v0.30 turns the v0.20 hardware adapter into a working physical Agent
interface. It does not create a second assistant stack: physical speech enters
the same owner Agent Chat, canonical context, Cognitive Runtime, model routing,
tools, memory, knowledge and voice-profile systems already used by VP3 OS.

## Interaction

v0.30 uses push-to-talk:

```text
Press Agent button
        ↓
local microphone capture
        ↓
Release Agent button
        ↓
local Whisper transcription
        ↓
existing owner Agent Chat / canonical context
        ↓
existing local-or-cloud model routing
        ↓
Agent reply
        ↓
existing Agent voice profile
        ↓
local Piper synthesis
        ↓
local speaker playback
```

A long hold is a physical cancel gesture.

Pressing the Agent button while VP3 is speaking is a barge-in. Speaker playback
is stopped and a new microphone capture begins. v0.30 does not attempt to
cancel an already-running model-provider request; it prevents its late result
from being spoken if the physical turn has been superseded.

## Privacy boundary

Raw microphone audio never enters Agent Chat, Cognitive Runtime or VP3 Cloud.

The audio path is:

```text
microphone → VP3 OS RAM → local Whisper → transcript
```

The transcript then follows the **existing Agent Chat policy**. If the
conversation allows cloud compute, a bounded authorized text context may be
sent to the configured cloud provider. If the conversation is Private, the
existing context runtime requires local Ollama.

Piper speech synthesis is local.

The Cognitive Runtime receives a private metadata event after a completed
physical turn. That event records only modality/run metadata such as local STT,
local TTS, compute source, conversation reference and run ID. It does not copy
the transcript, raw audio or reply text.

## Physical privacy switch

A `privacy_switch: engaged` hardware event immediately:

1. invalidates the active physical turn
2. cancels microphone capture
3. stops speaker playback
4. switches the physical runtime to `privacy`
5. changes the device status light to the privacy indication

A new microphone turn is rejected while the authoritative v0.20 hardware state
reports privacy engaged or the microphone unavailable.

This remains separate from v0.20's electrical verification: the hardware
controller must still prove that the post-switch microphone power rail is OFF
before VP3 OS calls the physical disconnect verified.

## Conversation continuity

Physical turns use the existing owner conversation namespace.

The Physical Agent keeps the current conversation for follow-up turns, allowing:

- "What should I work on?"
- "Tell me more about the first one."
- "Prepare that for my meeting."

to remain one normal VP3 Agent Chat conversation.

After 15 minutes of physical inactivity, the next physical turn starts a new
conversation. The owner can also reset it explicitly through the control API.

## Device audio backend

v0.30 adds a headless PortAudio backend using the `sounddevice` Python
package. Current VP3 OS configuration variables are:

```text
VP3_OS_AUDIO_INPUT_DEVICE=<optional device index or device name>
VP3_OS_AUDIO_OUTPUT_DEVICE=<optional device index or device name>
```

When unset, PortAudio uses the operating system's default devices.

Capture format is bounded to:

- PCM 16-bit
- mono
- 16 kHz
- maximum 60 seconds per physical capture

The resulting in-memory WAV is passed directly to the existing local Whisper
service. It is not written to persistent storage by the Physical Agent Runtime.

Piper output is validated as PCM WAV before local speaker playback.

## Status-light states

The v0.20 controller receives these states from the physical runtime:

| Physical Agent state | Light mode |
| --- | --- |
| idle | idle |
| listening | listening |
| transcribing | thinking |
| thinking | thinking |
| speaking | speaking |
| privacy | privacy |
| error | error |
| disabled | off |

## Owner API

Detailed physical-agent controls are owner-session only:

- `GET /api/v1/control/vp3-os/physical-agent`
- `POST /api/v1/control/vp3-os/physical-agent/listen`
- `POST /api/v1/control/vp3-os/physical-agent/release`
- `POST /api/v1/control/vp3-os/physical-agent/cancel`
- `POST /api/v1/control/vp3-os/physical-agent/reset-conversation`

The manual listen/release endpoints are development/service controls using the
same runtime as the physical button.

Paired VP3 applications receive only a sanitized projection through the
existing VP3 OS status/capability surfaces: running state, physical-agent state,
turn count, compute-source label and whether audio is currently capturing or
playing. Conversation IDs, session IDs, transcript sizes, local audio-device
selection and error detail are not included.

## Shared VP3 OS

Node is the first target but none of the physical-agent code is Node-specific.

```text
VP3 Node       ─┐
VP3 Desk        │
VP3 Studio      ├─ hardware adapter → Physical Agent Runtime → shared VP3 OS
VP3 Team Node   │
VP3 Pocket     ─┘
```

Products can use different microphones, speakers, controller boards and device
profiles while retaining the same conversation/runtime architecture.

## Next phase

A sensible next phase is **VP3 OS v0.40 — Ambient / Meeting Physical Runtime**:

- explicit meeting-mode start/stop from the physical button
- longer local recording sessions
- local transcription stream
- speaker/participant segmentation where available
- meeting decisions, tasks and commitments into the existing meeting and
  Cognitive Runtime systems
- privacy-light and recording-state hardware feedback

v0.30 deliberately stays focused on a bounded one-turn physical Agent loop.
