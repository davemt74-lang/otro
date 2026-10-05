# Section 12 — Contacts, CRM, tasks and calendar

This audit follows Section 11 and the completed Cloud stem/music audits. It reviews the existing Cloud and HomeServer services and their Agent Chat/Brain context; the standalone Tracky2 project is outside this section.

## Repairs and coverage

- HomeServer contact, task and calendar changes now hold one SQLite write transaction across current revision checks, native changes, federation observations/tombstones, activity and mutation receipts. Nested database helpers reuse the owned connection with savepoints; unrelated services keep their existing connection behavior.
- Concurrent identical creates return one record. Receipt failures roll back the native change; competing stale updates reject. Contact revisions cover the full mutable fields, including long notes.
- Owner contact/task editors submit current revisions and stable create mutation IDs, preserve newer forms during older saves, and reject late search results. Owner APIs retain compatibility with older clients while the current editors use revision checks.
- Cloud CRM mutations and upserts serialize under the current account row. Retry receipt, projection and native changes commit together; inactive accounts cannot mutate. Legacy ownerless CRM contacts use NULL consistently.
- Cloud calendar creation serializes idempotency references, including cancelled records, so retries cannot recreate cancelled work. Update/cancel read current state under the owner lock; the event/meeting editor carries revisions. Invalid dates and nonexistent local times reject instead of silently normalizing.
- Late provider token refreshes cannot reconnect a disconnected or replaced calendar. Provider calls recheck current connection and write capability; late busy data cannot restore disconnected status.
- Existing contact classes and authority IDs remain distinct. Existing Team/member visibility, governed Agent approvals, CRM follow-ups, reminder recurrence, calendar availability/booking integration and provider routes are retained.

## Validation and acceptance

New Python tests use the complete production migration chain and actual services, with concurrent creates/edits, forced receipt failures, long notes, owner stale edits, scheduler concurrency and nested rollback. Node fixtures execute the production owner search loaders with reversed responses. PHP integration tests cover the current Calendar and CRM writers, owner/revision guards, rollback, source-reference replay and provider refresh cancellation.

The section is ready for software acceptance only after exact-head and merged-main checks pass and both deploy packages are verified against the merged source. Installed-device behavior, real provider OAuth/delivery and deployment time zones remain owner acceptance exercises.

## Owner checks

1. Create/edit contacts and CRM relationships in their native authority; confirm search, linked identity and Agent context agree. Cloud mirrors remain read-only on HomeServer.
2. Submit two stale edits and verify one rejects. Retry an interrupted create and confirm one record. Check long private notes and unrelated-account rejection.
3. Create tasks with linked contacts and reminders; complete/cancel them, test recurrence and restart, and verify reminders do not duplicate.
4. Create/edit/cancel personal and Agent calendar events and linked meetings. Verify Phoenix time, a daylight-saving transition and all-day ranges.
5. Disconnect a provider while refresh/sync is pending; verify it stays disconnected. Reconnect explicitly and test busy time and booking writeback.
6. Exercise permitted and rejected actions through Agent Chat and inspect current Agent Brain/context after changes.

No schema migration or new external provider is introduced by this section.
