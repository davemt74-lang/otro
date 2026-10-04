# Interactive Agent review — Section 7: Cloud/HomeServer transfer and retention

## Existing architecture and audit

Cloud owns canonical transcript documents and their retained audio. HomeServer owns
local transcription sessions, governed captures and existing Knowledge backup
items/attachments. Paired-app credentials, scoped grants, explicit sharing and the
existing minute/hour scheduler remain authoritative. This section extends those
services; it adds no duplicate ledger, schema, autonomous sharing or media engine.

The audit found: Cloud text import used separate Start/Append/Stop operations and
could reuse an unrelated active capture, retain partial text or truncate Unicode;
late shared-list/import responses could repaint closed/reopened surfaces; consent
and segment reads lacked one snapshot; backup acknowledgements were accepted
without proving committed bytes; lost begin acknowledgements duplicated uploads;
chunk/commit authorization was not rechecked against the source's current kind;
commit failures stranded moved files; damaged attachments were acknowledged as
present; capture limits were applied before insertion; expired inference could
publish after retention elapsed; and Cloud had no explicit retained-clip deletion.

## Repairs and acceptance criteria

1. Completed, explicitly shared, text-only HomeServer sessions import into the
   existing canonical Cloud tables as one atomic closed draft. An existing capture
   never receives imported words. Import and capture Start use the same user lock.
2. Source ID, contract, consent, manifest count, unique keys, valid Unicode, text
   limits and integer monotonic timing validate before any write. Valid Unicode is
   preserved; malformed or oversized documents fail whole rather than partially.
3. Retry resolves the original owner-scoped copy and preserves discarded state.
   A changed source cannot overwrite a hashed copy. Matching legacy partial imports
   resume into their original document; differing rows require owner review.
4. Shared-list and import UI use generations and abort cleanup. Requests have a
   20-second deadline including decoding, only one import runs, and late writes
   cannot navigate after close/pagehide. Retry is explicit and keeps source identity.
5. Local consent, segment counts and returned content use one SQLite read snapshot.
   Revocation prevents subsequent fetches. Cloud clearly identifies its imported
   copy as independent, with unidentified single-channel speaker attribution.
6. Cloud backup progress advances only on valid item IDs, digest/size assertions,
   integer offsets and a verified commit acknowledgement. Missing retained files
   and discarded/active sources cannot be silently treated as complete backups.
7. HomeServer reuses a matching pending upload with its durable offset, bounds
   incoming reservations, fsyncs appended bytes and rechecks paired-app ownership,
   current source existence and Knowledge-kind scope on every chunk and commit.
8. Attachment commit is serialized with Knowledge writes, verifies complete bytes
   and rolls back a moved file after database failure. Already-present replies
   inspect actual bytes; explicit same-source backup can repair damaged attachments.
9. The existing scheduler enforces capture expiry/count/bytes after insertion and
   during idle maintenance. Maintenance does not wait behind active capture or
   transfer locks. Expired inference cannot publish a new transcript result.
10. Hourly maintenance removes expired 24-hour incoming transfers and managed
    unreferenced attachments; capture maintenance removes managed one-hour capture
    orphans. Unrelated files and valid Knowledge items/transcripts are retained.
11. Owners can delete stopped Cloud audio clips while preserving transcript text.
    A durable deletion marker denies playback and delayed re-upload before unlink;
    retry repeats physical removal. Foreign owners cannot delete another copy.
12. Local deletion works for expired, missing or damaged captures and is idempotent.
    Managed storage rejects symlink directories; deleting a managed symlink removes
    the link rather than outside files. Knowledge backup copies are separately managed.

## Validation

- Cloud: `php tests/transfer_backup_section7.php` runs 16 canonical SQLite import
  cases and 12 acknowledgement/audio-deletion cases. Production services execute
  against real temporary database/file fixtures, including injected SQL failure.
- `node tests/transfer-section7.mjs`: 12 real Chromium UI cases with injected
  transports, exercising production import and retained-clip controllers.
- With `VP3_SECTION7_MYSQL_DSN`/`VP3_SECTION7_MYSQL_PASSWORD`,
  `php tests/transfer_integrity_section7.php` repeats transitions in MySQL/InnoDB
  and adds six parallel imports plus an import-versus-Start race (18 cases total).
- HomeServer: `python tests/transfer_retention_section7.py`, 18 database/filesystem
  cases, including failure recovery, scope narrowing, quota, consent snapshots,
  inference expiry and symlink deletion. Platforms unable to create symlinks skip
  those two cases explicitly; Linux core exercises both.
- Retain prior capture/recording, local transcription, Knowledge backup/privacy,
  scheduler, import and backup contracts plus existing core/recovery/release gates.
  Focused browser/MySQL jobs extend the existing runtime-journey workflow; HomeServer
  extends existing core and Windows gates. No new workflow or scheduler is added.

The software score covers these twelve criteria. A 10/10 acceptance score requires
focused and retained checks, green PR gates, merged code and verified release ZIPs.
It does not certify installed devices, network throughput or production storage.

## Limits and next sections

Sharing revocation governs future reads; it cannot recall an authorized snapshot
already fetched or independently imported Cloud text. Deleting either system's
copy does not delete the other's copy or external downloads. Cloud clip deletion
removes local retained audio; owner-managed HomeServer Knowledge copies require
separate removal. An in-flight playback/transfer may already hold bytes. If file
unlink fails, logical removal is durable and the owner retries physical removal.

Local capture maintenance may defer while capture holds its lock. Incoming uploads
expire after 24 hours, and orphan capture/attachment cleanup has one-hour/24-hour
grace respectively. Committed Knowledge items and transcript text have no newly
invented automatic TTL. An explicit backup repairs a damaged matching attachment;
these changes do not add an autonomous continuous cross-system reconciliation loop.

SQLite tests do not certify InnoDB locking; separate concurrent MySQL cases cover
that boundary. Transport/browser fixtures are not production network certification.
Installed acceptance should exercise actual paired scope narrowing, connection loss,
lost acknowledgements, source/backup deletion, retention across idle time and restart,
and document that independent copies are removed separately where intended.

Next: Section 8 Agent Chat/Brain/Knowledge integration; Section 9 missing
transcription/meeting capabilities, including shared-microphone recognition,
overlapping-speaker diarization, continuous camera/voice fusion and recording;
Section 10 installed performance/release acceptance. Standalone Tracky2 is separate.
