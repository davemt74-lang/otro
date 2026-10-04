# Interactive Agent review — Section 6: video meeting reliability

## Existing architecture and audit

Cloud owns the canonical meeting, invitation, attendance, transcript, Calendar,
CRM and Agent artifacts. LiveKit carries real-time media. HomeServer supplies the
existing governed local meeting transcription worker and physical-room runtime.
This section changes those controllers and services without adding a meeting
ledger, scheduler, recognition engine, recording engine or schema migration.

The audit found: disconnected rooms could accept late microphone/join completion;
poll responses could repaint departed surfaces or start duplicate loops; no
explicit reconnect state; repeated subscriptions attached duplicate DOM media;
screen-video unsubscribe discarded screen audio; sanitized participant IDs
collided; unbounded request/disconnect waits; token-time presence could race
meeting closure; repeated joins notified repeatedly; end left joined attendance;
late Agent responses revived active UI; and live HomeServer workers could be
replaced after receiving Stop but before their thread exited.

## Repairs and acceptance criteria

1. Canonical meeting/participant rows are reread under InnoDB locks for presence.
   Closed meetings and revoked invitations reject delayed joins; organizer roles
   are checked again. Explicit authorization/conflict errors reach the browser.
2. Repeat join/leave/end are idempotent. Genuine re-entry clears departure. End
   closes joined attendance and preserves cancelled/processed terminal states.
   Notification/CRM writes occur only for an actual attendance transition.
3. Read-only status checks detect closure even when transcription is disabled.
   Access revocation closes local media; transient feed failures preserve it.
4. Disconnect/pagehide/lease loss invalidate join and polling generations, abort
   outstanding reads and stop local tracks. Late permission results are stopped.
5. Requests have a 15-second deadline including response decoding; local room
   disconnect has a 5-second bound. A failed unload beacon cannot prevent cleanup.
6. SDK reconnect events expose a reconnecting state, preserve the room/lease,
   pause feed polling and resume one loop when recovered. Media controls serialize.
   Passive device enumeration does not request fresh capture permissions.
7. Track attachment is idempotent, participant IDs retain distinct DOM identities,
   and screen audio remains until its own unsubscribe. Departure detaches media.
8. Current transcript results are ordered and deduplicated before cursor advance.
   Stale results cannot update transcript/Agent context or restart old polling.
9. Local departure disables Live Agent state despite late responses and stops
   new automatic live-analysis timers. Canonical end still signals final review, including when provider deletion
   arrives before the HTTP end acknowledgement.
10. HomeServer job reuse verifies the paired app owner. Any live old worker thread
    blocks replacement, including stopping/failed/terminal statuses. Pruning cannot
    remove it before thread exit.
11. HomeServer tracks have one consumer, explicit unsubscribe cleanup and queued
    same-SID replacement after cancellation. Reconnect cannot undo Stop. Cleanup
    preserves failed/expired states and publishes stopped after an explicit Stop.
12. Room disconnect cleanup is bounded, closes remaining track consumers, ignores
    late subscriptions, and suppresses new callback attempts after Stop or an
    authoritative provider room closure/removal.

## Validation

- `node tests/meetings-section6.mjs`: 15 real Chromium controller cases with
  injected transport/provider events. These test the production controller and DOM.
- `php tests/meetings_integrity_section6.php`: 12 canonical SQLite service cases.
- With `VP3_SECTION6_MYSQL_DSN`/`VP3_SECTION6_MYSQL_PASSWORD`, the same fixture
  exercises real MySQL/InnoDB transitions and ten parallel join/end races.
- HomeServer: `python tests/meetings_reliability_section6.py`, 13 asynchronous
  lifecycle cases, plus retained v18.6/v18.8 transcription and physical-room cases.
- Retain existing capture/meeting/Agent contracts and core/recovery/release gates.
  Focused browser/MySQL acceptance extends the existing meeting workflow, keeping
  the 13-workflow CI set, with bounded jobs; no historical
  matrix is restarted to obtain a second result.

The software score covers these twelve criteria. A 10/10 acceptance score requires
the focused/retained checks and PR gates green, merged code and verified packages.
It is not certification of installed microphones, cameras, LiveKit deployment,
recognition accuracy, Internet reliability, or recording completeness.

## Limits and next sections

`recording_enabled` is existing configuration; the audited join path does not
implement video egress/recording. This release does not invent recording success.
Provider credentials and installed exercises are still needed for real network
loss, multiple browsers, device switching, playback permissions, meeting-end
delivery, private processing and hardware certification.

SQLite adapts SQL dialect and does not certify locking; the separate InnoDB races
exercise that boundary. Browser tests inject LiveKit events rather than proving a
live SFU deployment. Existing callback authorization, privacy routing and transcript
projection stay canonical. In-flight provider HTTP/STT operations may finish after
Stop; new callback attempts are suppressed, and the old thread stays exclusive.

Next: Section 7 Cloud/HomeServer transfer and retention; Section 8 Agent
Chat/Brain/Knowledge integration; Section 9 missing transcription/meeting
capabilities, including shared-microphone identification, overlap diarization,
camera/voice fusion and recording gaps; Section 10 installed performance/release
acceptance. Standalone Tracky2 remains separate.
