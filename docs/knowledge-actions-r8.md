# R8 — Knowledge-backed Chat Actions

The Chat canvas offers **Act on reviewed Knowledge** in Cloud, HomeServer and Chrome. Select one to six current annotations, saved Research reports or personal Knowledge notes within the conversation's existing all/project/folder scope. Knowledge off prevents preparation. Captured dates remain distinct from note edit times; historical evidence never claims live verification.

Choose a new canonical Cloud Agent task, an existing owned CRM contact, or a separate personal text Knowledge document. Original capture notes and R7 refresh notes remain protected evidence. New task titles and descriptions, or explicitly selected destination fields, remain editable before **Prepare exact action review**. **Draft from selected sources** uses the existing configured Agent model and usage accounting; it performs no browsing, record changes or generic tools. Source text is untrusted data. Invalid or unsupported model fields are rejected; manual field entry remains available.

The immutable preview records before/after fields, destination identity/revision, selected source pointers/revisions/dates, current conversation scope and lead namespace. Only a separately confirmed exact hash can apply it. The Cloud `chat_knowledge_actions_r8` table contains review/audit pointers, bounded source excerpts, immutable proposed changes and execution receipts, not a second Knowledge store.

Application uses existing `workspace_native_edit_v1` for CRM/document edits and `agent_brain_store_memory` for Agent tasks. A validated proposal UUID scopes the task identity independently from its title. The canonical writer and action receipt share one InnoDB transaction; current authentication, permissions, lead, conversation, source and scope are checked before and after the write. Changed destinations block stale approvals. Exact request retries do not duplicate tasks or record changes. Dismissed proposals cannot later execute. Original captures and report history remain available.

HomeServer uses the existing bounded paired HTTP proxy, current conversation privacy and permissions, resolved lead, per-destination Contacts/Memory/Knowledge permissions, session replacement fence and exact Cloud context validation. After applying, existing workspace synchronization delivers canonical records. Hash-only `knowledge_action_receipts_r8` entries travel in its existing Chat snapshot. The canvas and Agent Brain distinguish pending, received and superseded revisions; no duplicate native task/reminder or Knowledge record is created. The Windows database schema remains 80.

Chrome 22.18.0 adds one allowlisted `knowledge_actions_r8` runtime command and one shared canvas script; the extension key and retained storage identity remain unchanged. The deploy extension whitelist contains 25 files. Cloud upgrade creates review storage and ensures the existing workspace mutation receipt table.

## Acceptance gates

1. Current selected-source/project/folder/lead/account authority and honest evidence dates.
2. Configured-model suggestions are draft fields only; unsupported fields and tool injection fail.
3. Exact immutable before/after review with separate explicit apply or dismissal.
4. Actual canonical Agent task creation, same-title isolation and exact retry identities.
5. Actual existing CRM/document mutation writers, ownership and stale-destination protection.
6. Current source/scope checks, original evidence preservation and atomic final-authority rollback.
7. Actual Cloud export imported through native SQLite and existing workspace snapshots.
8. Exact hash-only pending/received/superseded receipts under current permissions and pairing.
9. Real mobile Cloud/native/Chrome canvases, safe rendering, no typed draft replacement or send, stale/revoked context clearing and Brain visibility.
10. Full candidate/main regressions, exact tested/merged source trees, Windows installer/restart/recovery/silent-upgrade/private-data checks and verified deploy packages.

Connection-recovery work remains on hold. Controlled CI providers/authentication/content do not certify the user's deployed live accounts or hardware.
