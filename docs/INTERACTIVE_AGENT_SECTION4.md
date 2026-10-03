# Interactive Agent review — Section 4: Transcript document integrity

Repairs extend the canonical Cloud listening/workspace/page controllers and
HomeServer Dictate/local transcript service. No database migration.

## Behavior

- HomeServer backs up accepted unsaved text and a pending Stop in a bounded browser
  outbox with immutable segment/session keys. After an authenticated owner list,
  recovery retries the original document and never starts a microphone or shares
  with Cloud. Export/clear recovery are available when a document is unavailable
  or a server limit prevents saving. Storage quota failure stops capture and keeps
  accepted text in memory; keep that page open or export before leaving.
- Local segment timing uses the original capture timestamp plus the resumed
  session timeline, rather than page uptime or Whisper delivery time. SQLite append
  order survives clock reset; returned timeline/index fields support recovery.
  Start, append, Stop, share and deletion serialize their SQLite writes. UTF-8
  document limits use the same byte count for saved and incoming text.
- Both services accept a retry of identical already-saved text after completion;
  conflicting keys and new text after completion fail without reopening. Cloud's
  original start key resolves its original document before considering another
  active document. Existing owner/consent checks still apply.
- Cloud restores the backup's document identity before append, refuses missing or
  mismatched documents, and preserves blocked text for export. Pending saves,
  Stop, recovery and retained-audio uploads prevent switching capture ownership.
  Stop finalization is serialized. Session clocks use monotonic elapsed time and
  saved segment timing when resumed.
- Editor loads, save results, library reloads, manifests and continuous pages are
  tied to the document/view request that started them. A-B-A selection and old page
  responses cannot replace the latest selection. Debounced edits snapshot their
  original document; per-document writes are ordered and reads wait for its queued
  writes. Text/title edits use per-user/document browser recovery, acknowledged
  only for the exact saved value. Paged transcripts retain their existing read-only
  prose view. Recovery controls remain available from the sidebar.

## Acceptance

Run `node tests/transcription-section4.mjs` in either repository: sixteen Cloud and
eight HomeServer browser cases. HomeServer also runs
`python tests/transcription_integrity_section4.py`: eight cases against real temporary
SQLite databases, including concurrent Start, replay and limit enforcement.
Cloud runs `php tests/transcription_integrity_section4.php`: five canonical PHP
service cases using SQLite with SQL dialect adaptation. These PHP tests do not
certify InnoDB lock behavior. Retain Sections 1–3 and existing runtime/authorization
regressions in the established PR and merged-main release workflows.

## Installed acceptance and next sections

On installed Windows/Cloud browsers, capture offline, reload, reconnect, replay a
lost acknowledgement, switch documents during a delayed save, Stop during a save,
and resume after a reload. Confirm recovery targets the original document, timestamps
remain monotonic, text is not duplicated, and no microphone or Cloud sharing starts
on recovery. Test private-mode/storage quota behavior: failure requires keeping
unsaved text in the page or exporting it. Recovery stores text in browser storage
until acknowledged or explicitly cleared; it does not retain raw audio. Cross-tab
live capture/recovery behavior still needs installed-browser observation.

Software fixtures and CI do not certify physical microphones, speech accuracy,
browser permissions, installed native providers, or live video meetings.

Next sections: 5 participant tracking/identity; 6 video meeting reliability;
7 transfer/retention; 8 Chat/Brain/Knowledge integration; 9 missing capabilities;
10 performance and installed release acceptance. Sections 1–3 remain completed.
