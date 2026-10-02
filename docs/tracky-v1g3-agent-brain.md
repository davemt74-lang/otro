# Tracky 1G3 — governed Agent Eyes context for Agent Brain

This section implements the first Agent Brain integration. It uses the existing
HomeServer native worker and `tracky_active_perception_requests` ledger. It adds
no camera service, capture route, background worker or physical memory store.

In Agent Chat, start an owner conversation, then enable **Agent Eyes · local
read-only chat** in Agent Brain context. The option is off by default. Enabling
it permanently binds that conversation to local Ollama and read-only model
behavior, including after opt-out: prior replies can contain observation data.
Use a new conversation for cloud providers or tools. If local inference is not
configured, the chat fails closed with the existing local-model guidance.

The camera is separately owner-approved from the Tracky dashboard. Complete a
short supervised observation, then return to Agent Chat within 60 seconds.
Switching views retains the existing camera-stop behavior; Agent Chat never
starts capture or renews its owner presence lease. Extended sessions remain
gated by the current installed-owner exercises. Physical hardware acceptance
must still be completed on the actual installed Windows device.

The context reader binds the last completed canonical request to the current
process's Agent Eyes session, verifies the provider and owner requester, and
checks completion time, session state, privacy and the exact current model digest
and owner-review record against the existing session evidence. It
rechecks session identity and authority after reading the ledger. Restart,
stop, privacy revocation, lost presence, new sessions, stale/future times and
missing evidence yield no observation. Old rows cannot resume physical context.

Only the detector's allowlisted coarse category (`none`, `one`, `multiple`)
reaches the prompt. Arbitrary provider summaries, camera indices, image data,
identity links and device details do not. Unknown detector output is explicitly
uninterpretable. Haar output has no calibrated confidence and cannot establish
who is present, objects, activity, emotions, safety or independent hardware
certification. A recent observation is not a live view or verified presence.

Canonical context reserves at most 1,600 characters within the existing total
budget. If the full structured fragment cannot fit, it is omitted rather than
truncated. Retrieval provenance retains only state, a request fingerprint,
character usage and the local-only policy; detector categories are not copied
into run metadata. Existing conversation history can contain the model's reply
and is retained locally under the owner's normal conversation lifecycle.
Physical-context conversations cannot invoke model tools or write memory.

Migration 067 defaults existing conversations to opt-out and preserves a
monotonic local-only binding when enabled. No permission is added for paired
apps or Cloud; source identity and explicit local owner context settings are
both required. Onboarding's passive operational status remains unchanged.

Validation: synthetic real-worker/ledger/owner-chat integration, authorization,
privacy/review revocation, freshness, session replacement, untrusted provider
prose, unknown detector values, local inference routing, persistent local chat
history, context budgets, UI behavior and migration upgrade coverage. These
tests are software evidence, not installed-device hardware certification.

## 1G3B — freshness and evidence explanations

Agent Chat now shows a checked Agent Eyes status snapshot beside its context
settings. A recent permitted observation includes its age, the 60-second limit
and only the allowlisted possible-face-region category. Expired observations
show age without retaining the category. Other failures show static reasons
and owner guidance: camera privacy, stopped session, lost owner presence,
missing owner approval, changed or unverified model, unmatched session review,
missing or invalid ledger evidence, unverifiable timing, and uninterpretable
detector output. The local agent receives the same reason and safe guidance;
it cannot substitute a past reply for a fresh observation. Confidence remains
uncalibrated. Retrieval provenance still contains no categories or reasons.

**Refresh status** uses an authenticated, owner-conversation-only read at
`GET /api/v1/control/conversations/{conversation_id}/agent-eyes-context`.
The same `agent_eyes_context` projection accompanies owner conversation reads
and context policy saves. Paired-app conversation responses omit it entirely.
Opt-out checks no camera state. Owner reads use `Cache-Control: no-store`.
**Open Tracky** navigates to the existing dashboard; it does not grant consent,
start capture or heartbeat the worker. Follow the existing camera review,
recovery and short supervised observation flow there, then return to chat.

The visible opted-in chat refreshes status every five seconds. This is passive
status polling, not perception. The UI clears observations on navigation,
visibility changes, new chats, opt-out and read failures. It expires categories
locally at 60 seconds even when a request stalls, counting request transit time
conservatively. Privacy and session changes are checked on every server read;
the displayed snapshot may precede the next check. Late responses cannot
repopulate another chat or undo a newer policy response. Policy writes are
serialized to preserve the owner's opt-in/opt-out order; reloads wait for the
pending writes. Completed observations that cross the deadline during the
server's final authority check are discarded.

No schema migration or Cloud deployment is required for this section. The
existing sticky local-only/read-only conversation policy, camera lifecycle,
ledger, owner-review requirements and 1,600-character fragment ceiling remain
in force. Tests cover real synthetic worker/ledger/API/chat integration,
paired-app isolation, no capture or heartbeat, reason redaction, revocation,
deadline crossing, delayed responses, write ordering, opt-out and navigation.
Both PR Core and the merged-main Windows workflow run the focused suites.

Installed-device camera acceptance remains outstanding. Broader scene
understanding, multi-camera fusion and unattended perception require additional
governed design and independent physical acceptance; they are not implemented
or enabled here.

## 1G3C — Local scene understanding

Optional scene inference extends the existing native Agent Eyes capture and
canonical request ledger. It is off by default and process-bound; restart does
not resume capture or restore scene approval. Complete native camera acceptance,
select an exact installed local Ollama vision model and existing room in Tracky,
run one scene-test observation, inspect its output and camera release, then accept
that installed test. Start a new supervised session to contribute scene data to
an opted-in, permanently local-only Agent Chat history.

The owner-controlled loopback Ollama provider is reused without downloading or
changing models. `/api/tags` digest and `/api/show` vision capability are checked
at configuration, with digest rechecks before/after inference and on projection.
Remote models/URLs, redirects and proxy environment settings are rejected.
Local Ollama is a trusted service: these metadata checks are not cryptographic
proof of executed weights or protection against a compromised local server.

A single transient frame is resized to at most 640 pixels and bounded to 192 KiB
of JPEG. The camera is released before model HTTP inference. The existing shared
capture lock, owner presence lease, cancellation, privacy, watchdog, per-session
sample/wall budgets and native CPU accounting remain active. Scene output uses
at most 256 tokens, a 5-second result-acceptance deadline and a 64-KiB response
ceiling inside the existing 7-second provider timeout. Late output is rejected;
slow installed models fail closed. HomeServer cancellation closes its local HTTP
client; the external Ollama process manages its own CPU/GPU work. HTTP cancellation
and `keep_alive: 0` are not physical proof that all model compute stopped.

Only up to eight distinct labels from a fixed common-object vocabulary, setting
(indoor/outdoor/unclear), lighting (bright/dim/unclear), an owner-selected room and
uncalibrated uncertainty are projected. No captions, OCR, identity, face templates,
people labels, action/safety/emotion judgments or arbitrary model prose enter chat.
The canonical ledger retains only this validated semantic result and local review
binding. No frames are saved. Scene observations expire after 60 seconds and are
omitted on model, provider, room, consent or privacy changes. Disable scene inference
to revoke its process-bound review and stop its camera session.

Section C does not ingest these observations into the shared scene graph or export
them to Cloud. Section D introduces a separate owner opt-in for semantic sharing.

Software review: 10/10 requires all focused and regression CI checks green on the
published PR and merged main, including Windows executable/upgrade checks. The
installed owner's scene output and release acceptance remain required on their
actual device; synthetic tests are not hardware certification.

Primary API contract: https://github.com/ollama/ollama/blob/main/docs/api.md
