# HomeServer

HomeServer is a local-first private capability server for personal AI agents and authorized applications such as VP3.

The Windows desktop runtime runs on `127.0.0.1:4377`, stores durable state in SQLite, and provides a local Control Center for the primary agent, private chat, knowledge, memory, contacts, skills, tools, approvals, pairing, permissions, backup/restore and activity.

## Current v0.10 foundation

- Windows tray application and packaged `HomeServer.exe`
- Per-user `HomeServerSetup.exe` installer with optional Start-with-Windows
- SQLite WAL database with versioned, transactional migrations
- Persistent primary agent configuration and conversations
- Local-only Ollama provider with loopback URL enforcement
- Bounded context assembly from instructions, conversation history, memory and FTS knowledge
- App-isolated conversations for VP3 and other paired clients
- Durable agent memory and local document ingestion/search
- Private Contacts & Relationship Context with owner CRUD and permissioned app reads
- Allowlisted Skills & Tools capability layer
- Built-in `contacts.search`, `knowledge.search`, `memory.list`, and `memory.write` tools
- Built-in Relationship Context skill over `contacts.search`
- `tools.execute` plus each underlying capability enforced independently for paired apps
- Owner-controlled global tool policies and content-safe tool-run auditing
- Optional Agent Tool Use, disabled by default, with a hard 1–3 call budget
- Model-visible direct tools limited to read operations
- Approval-gated memory-write proposals, separately disabled by default
- Local Approvals workspace with approve/deny controls and request history
- Browser-safe claim-token pairing and status-only action tracking for the originating app
- **Local Backup & Restore** with SQLite snapshots, portable ZIP archives, SHA-256 manifests, staged restore validation, pre-restore safety backups and startup-time rollback protection
- Windows CI covering migrations, privacy/security boundaries, contacts, Agent Brain, tools, approvals, backup/restore, packaged EXE restore startup and installer output

## Local Backup & Restore

v0.10 adds owner-only portability for the full local HomeServer state.

A manual backup uses SQLite's backup API to produce a consistent database snapshot while HomeServer is running. The archive contains:

- `database/homeserver.db`
- imported files under `knowledge/files/`
- `manifest.json` with format version, HomeServer version, database schema version, size and SHA-256 for every payload file

The archive deliberately excludes temporary WAL/SHM files, backup history, restore work directories, and runtime owner-session secrets.

### Restore safety model

Restore never replaces a live SQLite file through an API request.

1. The owner uploads a HomeServer backup through **Backup & Restore**.
2. HomeServer validates the ZIP path structure, entry count, expanded size, duplicate paths, symbolic links, manifest shape, every SHA-256 hash, SQLite integrity, foreign keys, schema compatibility, and referenced knowledge files.
3. The validated archive is staged locally and marked `pending_restart`.
4. HomeServer re-validates the staged files on the next launch before the API server starts.
5. If a current database exists, HomeServer creates an automatic `pre-restore` backup first and checkpoints WAL state.
6. Database and knowledge storage are swapped while no API database connection is active.
7. Older compatible backups are migrated forward and the knowledge index is repaired if needed.
8. If application or validation fails after staging, the old database/files are restored, the failed stage is cleared, the failure is recorded locally, and HomeServer continues starting instead of entering a restart loop.

Backup creation, download, deletion, restore staging and restore cancellation are all owner-session routes under `/api/v1/control/*`. Backup/restore is **not** a paired-app permission and is not exposed to VP3.

### Backup privacy

v0.10 ZIP backups are portable but **not encrypted by the ZIP format**. They can contain private memory, contacts, knowledge and application credential hashes. Keep exported archives somewhere you control and protect them like any other private data backup.

## Contacts & relationship context

v0.9 made people first-class local HomeServer entities. The owner can create, search, edit and delete contacts containing:

- display / first / last name
- organization
- email and phone
- relationship label
- local relationship notes

Contact records remain in the user's SQLite database. Owner CRUD activity records only the contact ID and action; contact details and notes are not duplicated into the activity log.

Paired applications receive no contact access unless the owner grants `contacts.read`. The protected browser API is:

- `GET /api/v1/contacts?q=` — requires `contacts.read`

The read-only `contacts.search` tool requires both `tools.execute` and `contacts.read`. Its tool-run audit stores query length and result count rather than the query text or returned contact notes.

When Agent Tool Use is enabled, `homeserver_contacts_search` may be exposed to Ollama only for conversations whose caller is independently authorized to use `contacts.search`. The model receives no contact create/update/delete function.

## Skills & Tools security model

HomeServer uses a typed allowlist rather than arbitrary execution:

- `contacts.search` — read-only, requires `tools.execute` + `contacts.read`
- `knowledge.search` — read-only, requires `tools.execute` + `knowledge.search`
- `memory.list` — read-only, requires `tools.execute` + `memory.read`
- `memory.write` — mutating, requires `tools.execute` + `memory.write` for direct app execution

HomeServer intentionally includes **no shell, PowerShell, arbitrary HTTP, or unrestricted filesystem tool**.

The owner can disable any built-in tool globally. Tool-run audit records contain tool identity, source, permissions, status, duration, safe lengths/numeric settings and result IDs/counts. Raw search text, contact notes, knowledge excerpts and memory bodies are not duplicated into `tool_runs` or activity metadata.

## Agent Tool Use

Agent Tool Use is owner-controlled and ships disabled. When enabled, HomeServer can expose permission-filtered Ollama function schemas during Agent Chat.

Read tools (`contacts.search`, `knowledge.search`, and `memory.list`) can execute directly through the audited registry. Paired apps only receive each schema when their token has `tools.execute` plus the corresponding read permission.

The owner chooses a hard maximum of 1–3 executed model tool calls per chat turn. Once the budget is exhausted, HomeServer requests the final Ollama answer without tools. Tool-result messages stay inside the local Ollama exchange and are not persisted as conversation messages.

## Approval-gated actions

The first model-driven mutation path is **memory-write proposals**.

This is a second owner control, separate from enabling Agent Tools, and is **off by default**. When enabled:

1. Ollama may receive `homeserver_memory_write_request` only when `memory.write` itself is currently available.
2. The model can submit proposed memory content, key and importance.
3. HomeServer validates the proposal and creates a local `action_requests` record with a 24-hour expiry.
4. No memory is written at proposal time.
5. The owner reviews the exact proposed payload and source in Approvals.
6. Approve reserves the request and runs the existing audited `memory.write` tool exactly once.
7. Deny makes no mutation.

The model is never given a direct `memory.write` function. Contact mutations and backup/restore actions are also not exposed to the model.

Proposal payloads are stored once in the local `action_requests` table because the owner must be able to review what is being requested. Tool/activity audit tables contain only content-safe metadata and request/run IDs.

For paired apps, proposal availability requires `agent.chat` + `tools.execute` + `memory.write`, plus the owner-level proposal setting. Apps cannot approve actions. They can only query the status of their own request ID; that status response does not return the proposed content.

## Local model privacy

The Ollama URL must resolve to `localhost`, `127.0.0.1`, or `::1`; remote provider URLs are rejected. The default is `http://127.0.0.1:11434` and is disabled until the owner selects a model and enables it.

HomeServer sends bounded local context to Ollama. Memory, knowledge, contact-search results and tool-result excerpts are treated as untrusted supporting data rather than instructions.

## Data location

By default HomeServer stores runtime data under `~/.homeserver/`:

- SQLite: `~/.homeserver/homeserver.db`
- imported knowledge files: `~/.homeserver/knowledge/files/`
- local backup archives: `~/.homeserver/backups/`
- staged/last restore metadata: `~/.homeserver/restore/`

Set `HOMESERVER_DATA_DIR` to use another local directory.

## Run locally

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python desktop/launcher.py
```

Then open HomeServer from the tray, configure the primary agent and local Ollama provider, and use **Agent Chat**, **Contacts**, **Skills & Tools**, **Approvals**, or **Backup & Restore**. Agent Tool Use and memory-write proposals remain off until explicitly enabled in **My Agent**.

## VP3 browser bridge

VP3 connects through `claim-v1` pairing. After local owner approval, VP3 uses its claim token as a scoped bearer credential; it never reads SQLite or local files directly.

VP3 can use direct protected capabilities according to its permissions, including chat, contacts, knowledge, memory and direct tools. `contacts.read` is independent from other permissions. Backup/restore remains owner-only and is not a pairing permission. When the owner enables proposal-capable Agent Tools and VP3 has `agent.chat`, `tools.execute`, and `memory.write`, its chat may create a pending memory-write request. Approval remains local-owner-only.

See [`connectors/vp3/README.md`](connectors/vp3/README.md) and [`connectors/vp3/client.js`](connectors/vp3/client.js).

## Build Windows distribution

```bash
pyinstaller HomeServer.spec --clean --noconfirm
```

CI also builds `HomeServerSetup.exe`, verifies SHA-256 hashes, launches the packaged executable against `/api/v1/health`, stages and applies a real restore through the packaged launcher, and publishes the `HomeServer-Windows` artifact.
