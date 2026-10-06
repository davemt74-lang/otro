# Automatic workspace synchronization — release audit

Cloud native data stays authoritative; HomeServer has an account-bound offline replica. HomeServer native records stay authoritative; their approved text records are mirrored to Cloud. Replication never runs copied schedules, creates approvals, changes native IDs, or opens camera/microphone capture.

## Acceptance criteria

1. Local date and time on goals, decisions, tool runs, audited actions, health checks and maintenance notifications; unzoned SQLite/MySQL timestamps are UTC.
2. Automatic background synchronization enabled by default, independent of the HTTPS heartbeat and browser lifecycle.
3. Cloud account ownership predicates for profile, contacts/CRM, knowledge/folders, transcriptions, calendar, schedules/bookings, meetings, products/orders, agents/memory, chats, notifications, music and artist workspace.
4. Additional account-owned tables and their foreign-key descendants discovered from the installed schema; no administrator/global records inherited merely from account role.
5. Full UTF-8 records transferred in bounded packets; checksum and complete coverage before atomic replacement, including authoritative deletion and replay of lost receipts.
6. Owned Cloud upload attachments downloaded with a streamed SHA-256 check, atomic file replacement, and verified per-account read authorization.
7. Fresh paired account/session checks under the same account authority lock as disconnect; current HomeServer read permissions and collection/kind scope respected for outbound content.
8. New completed transcriptions synchronize text by default; explicit local-only/revoke and pre-existing private transcripts remain private; raw audio and local biometric speaker evidence remain excluded.
9. Offline browse surfaces, pagination/search, per-category sync times, visible errors, pause/resume and catch-up; no invisible foreground sync request or automatic capture.
10. Source tests, real SQLite/MySQL tests, cross-runtime transport, browser interaction, Windows startup/restart and final package verification green before release.

## Scope boundaries

The account ownership model selects records owned by the signed-in paired account and approved FK children. Public/global content and another account's data are not replicas. Existing credential stores, authorization sessions, billing authority and system execution queues are excluded. Native source edits still use the original governed APIs; replicated records are readable offline rather than a second writable database.

Cloud attachment coverage uses native upload paths stored on owned records. External third-party URLs are references. Files outside the Cloud uploads directory are not downloaded. HomeServer-to-Cloud mirrors carry approved text/metadata, not local raw recordings, voice enrollment material or an unrestricted copy of the HomeServer filesystem.

Snapshots currently have a 64 MiB per-category transfer capacity. Individual native Cloud attachments have a 4 GiB capacity. Failures preserve the previous complete copy and are reported; capacity limits do not silently truncate records or claim synchronization.

Real user account/data volume and installed Windows storage/network acceptance remain deployment checks.
