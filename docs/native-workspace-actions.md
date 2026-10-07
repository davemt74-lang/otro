# Editing synchronized Cloud records

Deploy the matching Cloud release first, then replace HomeServer.exe and restart HomeServer using its existing data directory. Database migration 069 preserves approvals and adds a durable edit queue. Existing pairing and complete synchronized copies continue to work with older Cloud releases; direct editing requires the new source revision metadata.

Open a synchronized contact, personal knowledge item, or calendar event and select **Edit here**. Save queues changed fields for the owning Cloud account. Paused or unavailable synchronization retains the queue across restart. The displayed record changes after Cloud confirms the write and its updated snapshot arrives. Larger knowledge text and other record types remain available through **Manage in Cloud**. Linked video meetings use their existing Cloud editor.

Agent Chat can search full synchronized records, inspect supported fields and propose changes through `homeserver_workspace_update_request`. Enable Agent tools and write proposals in the existing tool settings. Proposals appear in Approvals; approval binds the exact source record, revision, fields and pairing authority. It queues the write and reports delivery separately. App callers cannot access these owner tools. Conversations that exclude Cloud data do not receive workspace tools; dataset exclusions also apply when tools execute.

Agent Brain displays edit timestamps and delivery states:

| State | Meaning |
| --- | --- |
| Waiting to send | Durable edit queued; an unsent change can be cancelled. |
| Awaiting acknowledgement | A request may already have committed at Cloud. |
| Saved in Cloud, waiting for sync | Receipt confirmed; updated source snapshot is pending. |
| Saved and synchronized | The confirmed source revision is visible in HomeServer. |
| Conflict | Cloud changed since the draft; reload and review before preparing a new edit. |
| Permission or pairing requires review | Original authority is unavailable; review the connection. |
| Edit needs correction | Cloud rejected the proposed fields. |
| Saved, source changed again | Cloud accepted the edit and subsequently changed the record. |

A lost acknowledgement retries the same change ID and request. Cloud records a receipt in the same transaction as the native write, so retries cannot overwrite a later source edit. Ownership and permissions are checked again even for a receipt replay. Drafts do not transfer to a new pairing or permissions generation. Copied schedules and reminders remain source managed, and this queue never creates local calendar events, tasks, or automation routines.

Acceptance includes real Python/PHP HTTP calls, indexed knowledge and original attachments, calendar timezone validation, stale revisions, acknowledgement loss, restart persistence, pairing changes, owner approval, request origin protection, browser retry preservation and Brain cancellation. Installed-device checks still require the actual Windows installation: restart pairing, automatic records/files, Brain timestamps and ambient speech with the selected settings.
