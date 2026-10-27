# VP3 floating Agent and Agent Eyes — v1342

## Delivered behavior

The authenticated member/sidebar, public-page footer and admin footer mount one
shared orb. HomeServer's universal shell mounts the same orb, presentation CSS,
camera UI and presence lifecycle. This is an application overlay, not a desktop
overlay above other applications.

The orb is draggable and can be minimized/restored. Its saved placement is
scoped to the Cloud account or local HomeServer owner. Listening is blue,
thinking green, audible speech pulses, and saved recording has an independent
red ring. Quiet/idle is neutral. Reduced-motion preference disables animation.
Stop uses existing conversation, speech and recording controls.

Other Cloud pages expose a sticky chatbar and button. The popup lazily embeds
the existing authenticated chat canvas, including existing provider, tool,
quota, draft and interruption behavior. Messages transfer to the canonical
form only after the child is ready and has no conflicting draft. The parent
clears its copy after the child acknowledges receipt. Closing does not destroy
the conversation or reopen it on speech. Messages check both origin and iframe
window. The child never mounts another orb or camera controller.

HomeServer moves its existing chat panel into the popup and restores the same
DOM objects on close. Its separate Tasks/System/Cloud pages use the same-origin
HomeServer chat embed. The existing chat form also gets the Chat button.

## Camera, participants and engagement

Start camera is an explicit browser action. No camera or microphone opens on
page load. The existing pinned HomeServer Human 3.3.6 model loads on demand;
this does not add a prepackaged camera/model runtime to the deployment ZIP.
Frames, portraits and embeddings are never sent in the event APIs or LLM
requests. HomeServer privacy status is rechecked before/after observations, and
the new camera participates in existing camera-conflict checks. Stop, hidden
page, disconnect, inference/privacy failure and navigation release its tracks.

Normalized live boxes show temporary track IDs. A local match needs three
consented samples, three stable matching observations, adequate similarity and
separation from a runner-up. Ambiguous/duplicate/revoked/deleted matches become
unidentified. These thresholds are engineering gates, not measured biometric
accuracy, liveness, account authentication or authorization.

Local visual enrollment requires one person in view and an unchecked-by-default
participant consent control. An introduction can create an ordinary existing
Studio/Tracky participant; it creates no website user account or automatic
contact. Cloud names/consent use existing owner-scoped Studio profiles, while
samples use an account-scoped browser IndexedDB. HomeServer reuses its existing
Tracky participant store and atomic patches. Enrolling/forgetting visual samples
preserves voice samples, voice binding/clone preferences and participant notes.

Greetings are automatic, with no per-arrival approval. Three seconds of stable
presence precedes an arrival; brief missed frames do not end a visit. Departure
expires after eight seconds. Known participants have a ten-minute greeting
cooldown and the room has a thirty-second spacing limit. A single unfamiliar
participant is greeted and asked their name; multiple unknown people do not get
an ambiguous name/enrollment invitation. Greeting opt-out is remembered. Camera
and recognition enrollment do not silently enable microphone capture.

Cloud speech reuses the existing notification/premium voice path, including
Agent Voice preference, echo/interruption integration and cancellation. Pages
without a bell initialize that path without adding a header control or polling
Chat. HomeServer reuses its current Piper/browser output pipeline and preserves
Strict Local. Orb pulses follow audible-playback events rather than TTS fetch.
The greeting event records the offered on-screen greeting; it is not proof that
browser autoplay or a physical speaker played sound.

## Existing Brain timeline integration

Only arrival, departure, identity change and greeting summaries are persisted.
Per-frame geometry, raw samples and credentials are rejected by the Cloud
endpoint. Current owned participant records resolve labels; a client label or
identity claim cannot grant account authority. The endpoint requires Chat
permission and CSRF for writes, bounds payload size and rate, and deduplicates
session/event IDs in the existing `agent_event_inbox`. Its trust marker means
the authenticated reporting session, not verified physical identity.

Cloud adds these canonical events to the existing Brain and History drawer and
stores explicitly provisional summaries in existing scoped Brain memory. It
does not use a separate presence database or trigger generic event actions.
HomeServer writes private, low-importance `agent_eyes.*` events to its existing
cognitive timeline. HomeServer visual identities remain local; this release
does not change its existing identity-stripping Cloud relay policy.

No database migration or new provider credential path is introduced.

## Verification and installed acceptance

Executable acceptance covers presence stability, expiry/cooldowns, ambiguity,
revocation, owner isolation, canonical event dedupe, rejected raw payloads,
mobile placement, reduced motion, drafts, one canonical chat form, window/origin
checks, camera/model-start cancellation and real Chromium IndexedDB behavior.
Existing voice/capture/transcription/participant suites remain required.

Camera model outputs and provider HTTP are mocked in browser/service fixtures.
These checks do not certify webcam accuracy or physical microphone/speaker
behavior. After deployment, start Eyes with a real camera, introduce a person,
check known/unknown greetings, stop/revoke, interrupt speech, navigate to another
page and inspect the private Brain timeline. Test poor light, multiple people,
browser permission denial and a HomeServer privacy-switch change separately.
