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
