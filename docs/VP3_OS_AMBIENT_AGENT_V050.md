# VP3 OS v0.50 — Ambient Agent Runtime

VP3 OS v0.50 adds an opt-in Ambient Agent coordination layer on top of the
v0.30 Physical Agent and v0.40 Physical Meeting runtimes.

Ambient mode is **off by default**.

Its job is to coordinate optional room-presence, wake-word, voice-activity and
proactive local speech events. It does not create a second assistant stack and
it does not turn VP3 hardware into an always-recording device.

## Privacy contract

v0.50 deliberately separates ambient awareness from ambient recording.

Ambient Runtime itself:

- never opens the microphone
- never starts streaming capture
- never calls speech-to-text directly
- never creates ambient transcripts
- never creates ambient memory candidates
- never persists room-presence history into Cognitive Runtime
- never exposes room occupancy to paired applications
- never marks a spoken notification as read or dismissed

The physical privacy switch remains authoritative.

If privacy is engaged, Ambient Runtime immediately blocks wake turns and
proactive speech. A wake turn already opened by Ambient is cancelled.

## Optional hardware events

The existing `vp3-hw-v1` protocol remains backward compatible.

v0.50 adds optional allowlisted events that future VP3 Desk, Team Node, Pocket
or smart-microphone controllers may emit:

```json
{"type":"event","seq":101,"event":"presence_sensor","action":"present"}
{"type":"event","seq":102,"event":"presence_sensor","action":"absent"}
{"type":"event","seq":103,"event":"wake_word","action":"detected"}
{"type":"event","seq":104,"event":"voice_activity","action":"started"}
{"type":"event","seq":105,"event":"voice_activity","action":"stopped"}
```

No raw phrase text is accepted in the hardware event contract.

A controller may also report the optional `presence_sensor` component:

```json
{
  "presence_sensor": {
    "present": true,
    "ready": true,
    "occupied": true
  }
}
```

The current v0.20 reference ESP32 firmware does not need to change. Existing
Node hardware simply does not advertise these optional capabilities.

## Wake flow

Ambient Runtime never performs speech recognition itself.

A trusted local wake-word detector may emit `wake_word:detected`. Ambient
then asks the existing Physical Agent Runtime to open one bounded microphone
turn.

```text
local wake detector
        ↓
wake_word:detected
        ↓
Ambient Agent
        ↓
Physical Agent begin_listening()
        ↓
existing local microphone capture
        ↓
voice_activity:stopped
        ↓
Physical Agent finish_listening()
        ↓
existing Whisper → Agent Chat → Cognitive Runtime → Agent Voice loop
```

If a `voice_activity:stopped` event never arrives, Ambient Runtime cancels the
wake-owned turn after the configured timeout.

Wake handling therefore inherits all existing v0.40 barge-in cancellation,
provider cancellation, tool/approval safety and privacy behavior.

## Presence

Presence can be configured in one of two modes:

- `sensor_required` — proactive speech requires a local presence event
- `assume_present` — useful on VP3 hardware without a presence sensor

Presence state is stored only in Ambient Runtime memory. It is not emitted as a
Cognitive Runtime event and is not included in paired-app status.

A wake event itself also establishes local runtime presence because a person
has explicitly addressed the device.

## Proactive local voice

Ambient can announce existing unread HomeServer notifications through the
primary Agent's configured local Piper voice.

This is a delivery layer over the existing notification system. It does not
create a second notification database.

Default behavior:

- proactive voice enabled only after Ambient itself is enabled
- presence sensor required
- speak warning-level notifications
- speak title only
- 30-second cooldown
- maximum 6 announcements per hour

Owner settings may choose:

- info / warning / error levels
- title only vs. title + body
- sensor-required vs. assume-present
- cooldown
- hourly rate limit
- wake timeout

A successfully spoken notification ID is stored in a bounded local delivery
ledger so restarts do not repeatedly announce the same notification.

Speaking a notification does **not** set `read_at` or `dismissed_at`.

## Audio ownership

Ambient proactive speech only begins when:

- Ambient is enabled
- privacy is not engaged
- presence policy allows speech
- no Physical Meeting is active
- Physical Agent is idle/error
- microphone is not capturing
- speaker is not already playing
- speaker hardware reports ready
- cooldown and hourly limits allow delivery

If the user presses the Agent button while Ambient is speaking, the existing
device-audio capture path stops speaker playback and the Physical Agent takes
over. Ambient does not force the status light back to idle after that handoff.

## Cognitive Runtime

Room-presence changes are intentionally **not persisted**.

A successful notification announcement may emit a private metadata-only event:

```text
ambient.notification_announced
```

The event may include local identifiers such as notification ID, task ID,
level and detail mode. It does not include the notification title or body and
it sets `memory_candidate=false`.

This lets the broader VP3 Cognitive Runtime know that a reminder was delivered
without copying private spoken content into the cognition event.

## Owner controls

Owner-only endpoints:

- `GET /api/v1/control/vp3-os/ambient`
- `PUT /api/v1/control/vp3-os/ambient/settings`
- `POST /api/v1/control/vp3-os/ambient/announce-test`

The HomeServer Control Center also includes an **Ambient Agent** section with:

- master enable
- wake enable
- proactive voice enable
- presence policy
- announcement detail
- notification levels
- cooldown
- wake timeout
- hourly announcement limit
- local runtime status
- hardware capability status
- local voice test

## Paired-app boundary

Paired applications receive only:

- Ambient Agent version
- enabled / disabled
- wake enabled
- proactive voice enabled
- generic active / disabled state
- explicit false flags for ambient microphone capture, transcription and memory

They do not receive:

- current room occupancy
- presence timestamps
- wake timestamps
- notification IDs
- notification titles or bodies
- local hardware presence details

## Product compatibility

The same Ambient Runtime can run across VP3 hardware products:

```text
VP3 Node       ─┐
VP3 Desk        │
VP3 Studio      ├─ optional ambient hardware events → shared Ambient Runtime
VP3 Team Node   │
VP3 Pocket     ─┘
```

Hardware products may differ in sensors and local wake engines while retaining
one VP3 OS behavior and one Agent/Cognitive architecture.

## Next phase

After v0.50 is hardened, a logical next phase is **VP3 OS v0.60 — Room &
Device Automation Runtime**: explicit local device actions, room/device
discovery, approval policy, and ambient-triggered *suggestions* without silently
executing physical-world actions.
