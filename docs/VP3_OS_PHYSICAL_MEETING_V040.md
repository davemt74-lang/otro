# VP3 OS v0.40 — Physical Meeting Runtime

VP3 OS v0.40 turns the physical Agent hardware introduced in v0.20/v0.30 into
an explicit local meeting appliance while keeping the same Agent Brain,
Cognitive Runtime, Meeting Intelligence, privacy model and cloud bridge.

The phase also closes the v0.30 barge-in limitation: physical interruption now
cooperatively cancels an in-flight model request instead of merely suppressing
its late spoken response.

## Interaction model

Meeting recording is explicit. VP3 OS does not start ambient recording merely
because people are present or speaking.

A meeting can begin from:

- a long hold on the VP3 Agent button while idle
- the exact local voice commands `start meeting`, `start meeting mode`,
  `begin meeting`, or `begin meeting mode`
- the owner-only local control API

A meeting can end from:

- another long hold while recording
- the exact locally transcribed commands `end meeting`, `end meeting mode`,
  `stop meeting`, or `stop meeting mode`
- the owner-only local control API

The command transcript used to end a meeting is not stored as meeting content.

## Runtime flow

```text
Explicit start
    ↓
Physical Agent yields microphone ownership
    ↓
Headless local PortAudio stream
    ↓
Bounded speech segments
    ↓
Local Whisper STT
    ↓
Structured transcript segments in RAM
    ↓
Explicit end
    ↓
Local Meeting Intelligence / Ollama
    ↓
Summary / decisions / actions / questions / risks
task candidates / CRM candidates / follow-up draft
    ↓
Structured Agent Chat Meeting Card
    ↓
Private Cognitive Runtime meeting event
```

## Raw-audio boundary

Physical meeting audio is not persisted by v0.40.

PortAudio callback PCM is accumulated only until the current bounded speech
segment is flushed. The segment is wrapped as an in-memory WAV and sent to the
existing local Whisper service. Once transcription returns, the raw PCM segment
is discarded.

The runtime currently uses:

- 16 kHz
- mono
- 16-bit PCM
- approximately 900 ms trailing silence to close a speech segment
- 30-second maximum speech segment duration
- 500 transcript segments per physical meeting
- bounded pending transcription queue

If transcription cannot keep up and the bounded queue fills, the meeting fails
closed and is interrupted rather than allowing unbounded audio accumulation.

## Speaker model

The first physical-room implementation reports speaker identity as
`Room` / `room_channel`.

v0.40 does **not** claim diarization that the hardware cannot prove.

The existing LiveKit meeting-transcription runtime can continue to preserve
participant-track identity for online meetings. A later physical-room phase can
add a real local diarization model without changing the Meeting Card contract.

## Meeting Intelligence

v0.40 reuses the existing private VP3 Meeting Intelligence runtime. Final
physical transcript segments are analyzed by the configured local Ollama model
with:

- canonical owner context
- local memory
- local knowledge
- contacts
- cloud processing disabled

The result contains:

- summary
- key points
- decisions
- actions and commitments
- open questions
- risks
- topics
- task candidates
- CRM candidates
- follow-up draft
- Agent brief

Task and CRM entries are **candidates only**. Physical meetings do not
automatically execute tasks, CRM writes or other external actions.

## Agent Chat Meeting Card

A completed physical meeting creates a normal owner Agent Chat conversation
containing a structured `meeting` card.

The card stores the intelligence snapshot and safe meeting metadata:

- meeting ID
- title
- completed/interrupted state
- duration
- transcript-segment count
- transcript source hash
- intelligence fields listed above

It does not contain the raw PCM recording or the full meeting transcript.

Conversation APIs expose only the allowlisted Meeting Card metadata rather than
the underlying arbitrary message metadata JSON.

Interrupted meetings can also produce a card stating that the meeting ended
before final intelligence. This gives the owner an audit trail without asking
the model to infer from incomplete private audio after a privacy interruption.

## Physical privacy

The v0.20 electrical privacy requirement remains authoritative.

When the privacy switch is engaged during a physical meeting:

1. the microphone rail is physically cut by the hardware switch
2. VP3 OS receives the privacy event
3. local audio capture is cancelled
4. queued raw PCM segments are discarded
5. speaker playback is stopped
6. in-flight final meeting intelligence is cancelled
7. the meeting is marked interrupted
8. no new local capture starts until privacy is released and hardware reports
   the microphone ready again

No cloud fallback is used for physical meeting intelligence.

## True physical barge-in cancellation

v0.40 introduces an optional cooperative cancellation token used only by
physical Agent turns.

```text
button press while Agent turn is active
          ↓
cancel physical generation
          ↓
close active provider HTTP client
          ↓
stop provider/tool loop
          ↓
deny pending approval proposals from cancelled turn
          ↓
remove cancelled user turn
          ↓
prevent assistant response persistence
          ↓
begin new local microphone turn
```

The cancellation token is supported by:

- local Ollama
- OpenAI-compatible providers
- OpenRouter
- Anthropic

Ordinary web/API Agent Chat continues to use the existing non-cancellable call
path unless a caller explicitly supplies the physical-turn token.

Because the existing database status contract allows
`started/completed/failed`, a cancelled physical run is recorded as
`failed` with explicit `cancelled=true` metadata and a cancellation reason.
This preserves database compatibility while keeping cancellation auditable.

## Hardware states

The existing v0.20 status light is reused:

| Physical meeting state | Light |
| --- | --- |
| idle | idle |
| meeting starting | thinking |
| recording | listening |
| meeting ending | thinking |
| finalizing intelligence | thinking |
| privacy | privacy |
| error | error |
| disabled | off |

No controller firmware change is required for v0.40.

## APIs

Owner-only:

- `GET /api/v1/control/vp3-os/physical-meeting`
- `POST /api/v1/control/vp3-os/physical-meeting/start`
- `POST /api/v1/control/vp3-os/physical-meeting/end`
- `POST /api/v1/control/vp3-os/physical-meeting/interrupt`

The start endpoint accepts an optional meeting title.

Paired applications receive only a sanitized physical-meeting projection in
the existing VP3 OS status and capability registry:

- runtime state
- active/not active
- duration
- transcript-segment count
- speaker mode
- raw-audio-persisted=false

The paired projection does not expose meeting title, meeting ID, transcript
preview, transcript text or raw audio.

## Shared OS behavior

Physical Meeting Runtime is not specific to VP3 Node. The same service can run
on Desk, Studio, Team Node and other VP3 OS profiles whenever their hardware
adapter reports a ready microphone.

The hardware adapter remains the product-specific boundary; the meeting,
cognition and Agent Chat layers remain one shared VP3 OS.

## Next phase

A later **VP3 OS v0.50 — Ambient Agent Runtime** can explore wake-word and
room-presence features, but it should preserve v0.40's explicit recording and
physical privacy guarantees. Ambient awareness should never silently become
meeting recording.
