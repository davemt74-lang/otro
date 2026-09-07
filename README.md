# HomeServer

HomeServer is a local-first private capability server for personal AI agents and explicitly authorized applications such as VP3.

The Windows desktop runtime listens on `127.0.0.1:4377`, stores durable state in SQLite, and provides local owner surfaces for the primary agent, chat, knowledge, memory, contacts, skills, tools, approvals, pairing, permissions, backup/restore, setup and diagnostics.

## Current v0.11 foundation

- Packaged Windows `HomeServer.exe` and per-user `HomeServerSetup.exe`
- Supervised tray runtime with graceful shutdown/restart instead of a daemon API thread
- Per-data-directory Windows single-instance mutex
- `%LOCALAPPDATA%\HomeServer\Data` as the installed Windows data location
- Upgrade-safe migration from legacy `~/.homeserver` when the move is unambiguous
- Conflict preservation when both legacy and LocalAppData folders already contain data
- Windows DPAPI protection for the persistent owner bootstrap secret
- Process-local/ephemeral browser owner sessions that are invalidated on restart
- First-run **Setup & Diagnostics** workspace
- SQLite integrity/schema/foreign-key diagnostics
- Ollama reachability/model diagnostics
- Storage/free-space, backup, restore, startup and runtime diagnostics
- Start-with-Windows control using the current user's Windows startup registry
- Detection and cleanup of the v0.10 legacy Startup-folder shortcut
- Restricted recovery mode when the normal SQLite runtime cannot initialize
- Recovery-mode backup staging without mutating the broken live database
- SQLite WAL database with versioned transactional migrations
- Persistent primary agent configuration and app-isolated conversations
- Local-only Ollama provider with loopback URL enforcement
- Durable memory and local document ingestion/FTS search
- Private Contacts & Relationship Context
- Allowlisted Skills & Tools with content-safe auditing
- Optional bounded Agent Tool Use, disabled by default
- Approval-gated memory-write proposals, separately disabled by default
- Browser-safe claim-token pairing for VP3 and future clients
- Local Backup & Restore with SHA-256 manifests and startup-time rollback protection

## Windows lifecycle and data safety

### Data location

Installed Windows builds use:

```text
%LOCALAPPDATA%\HomeServer\Data
```

The primary paths are:

- `homeserver.db` — SQLite state
- `knowledge\files\` — imported local source files retained by HomeServer
- `backups\` — validated local backup archives
- `restore\` — staged/last restore metadata
- `security\owner-bootstrap.dat` — Windows-protected owner bootstrap material
- `runtime\bootstrap-state.json` — non-secret startup/migration diagnostics

If a v0.10-era `~/.homeserver` directory contains data and the LocalAppData destination does not, the Windows launcher moves the complete legacy directory before importing the application runtime. If both locations contain data, HomeServer does **not** merge or delete either location; LocalAppData remains active and Setup & Diagnostics reports the conflict.

`HOMESERVER_DATA_DIR` still overrides the default for development, testing or an explicitly managed local location.

### Single instance and graceful lifecycle

HomeServer acquires a named Windows mutex derived from the active data directory before applying a restore, opening SQLite or binding port `4377`. A second process for the same HomeServer data directory exits immediately.

The tray runtime owns a real Uvicorn `Server` instance. **Quit** and **Restart HomeServer** request graceful shutdown, allow active requests to finish, close the server, release the instance mutex and only then relaunch when needed.

The tray now provides:

- Open HomeServer
- Setup & Diagnostics
- Open Data Folder
- Create Backup
- API Docs
- Restart HomeServer
- Quit

### Owner security

The owner bootstrap secret is no longer regenerated only in process memory. On the installed Windows application it is encrypted using Windows DPAPI for the current Windows user before being written to the HomeServer data directory.

The bootstrap secret is used only to establish the local owner session. The session token itself remains randomly generated per process and is stored only in the HttpOnly, SameSite=Strict local browser cookie. Restarting HomeServer invalidates existing owner browser sessions.

If a protected-secret file becomes unreadable, HomeServer preserves the invalid file with a timestamped name and creates a new protected bootstrap secret. It never falls back to storing the Windows secret as plaintext.

### First-run setup and diagnostics

The first interactive launch opens the owner-only `/system` workspace once. It recommends, but does not require:

1. configuring the primary agent
2. enabling a local Ollama model
3. pairing an application such as VP3
4. creating a recovery backup

Marking setup complete only dismisses onboarding. It does **not** enable Agent Tools, write proposals or app permissions.

Diagnostics report local SQLite integrity, schema version, foreign-key health, Ollama reachability, active data path, disk space, migration status, backup/restore state, Windows startup state, owner-secret protection mode and supervised-runtime availability.

### Recovery mode

Before the normal API starts, the Windows launcher performs a database/index preflight. If that fails, HomeServer serves a restricted local recovery application instead of starting a partially working normal API.

Recovery mode can:

- report that the normal database runtime is unavailable
- open the local data folder
- accept a known-good HomeServer backup using the owner bootstrap credential
- validate and stage that backup without modifying the broken live database
- request a supervised restart so the normal pre-server restore mechanism can apply it

Paired applications and normal Agent APIs are not started in recovery mode.

## Local Backup & Restore

A manual backup uses SQLite's backup API to produce a consistent database snapshot while HomeServer is running. Archives contain:

- `database/homeserver.db`
- imported files under `knowledge/files/`
- `manifest.json` with format version, HomeServer version, database schema version, size and SHA-256 for every payload file

HomeServer rejects restore archives with traversal paths, symbolic links, encrypted entries, unexpected files, duplicate/case-colliding paths, Windows-reserved names, invalid hashes, incompatible schemas, broken SQLite integrity/foreign keys or missing referenced knowledge files.

Restore never replaces a live SQLite file through an API request. A validated archive is staged, revalidated on the next launch, preceded by an automatic safety backup, and swapped while no API database connection is active. Failure rolls the old state back and clears the bad stage instead of entering a restart loop.

Portable ZIP backups are **not encrypted by the ZIP format**. Treat exported archives as private data.

Backup/restore remains owner-only and is not a VP3 or paired-app permission.

## Agent, knowledge and relationship capabilities

HomeServer includes:

- persistent private Agent Chat
- memory and knowledge context with independent app permissions
- local TXT/Markdown/JSON/CSV/HTML/PDF/DOCX ingestion
- SQLite FTS knowledge search and duplicate detection
- owner-managed private contacts and relationship notes
- app-isolated conversations
- `contacts.search`, `knowledge.search`, `memory.list` and `memory.write` tools
- Relationship Context and local research skills

The model receives only read tools that are both globally enabled and authorized for the current caller. HomeServer intentionally includes **no shell, PowerShell, arbitrary HTTP or unrestricted filesystem tool**.

## Approval-gated actions

Agent Tool Use ships disabled and has a hard owner-selected 1–3 tool-call budget per chat turn.

The model is never given direct `memory.write`. When write proposals are separately enabled, it may submit `homeserver_memory_write_request`, which creates a pending local approval. The memory is written only after the owner explicitly approves it through the canonical audited `memory.write` tool.

Contact mutation, backup/restore, Windows startup, restart/shutdown and other system operations are not model tools.

## VP3 browser bridge

VP3 connects through `claim-v1` pairing. After local owner approval, VP3 uses its claim token as a scoped bearer credential; it never reads SQLite or local files directly.

Available app capabilities remain independently permissioned, including `agent.chat`, `contacts.read`, `knowledge.search`, `memory.read`, `memory.write` and `tools.execute`. Windows lifecycle, owner diagnostics, backup/restore and recovery are intentionally absent from the pairing permission catalog.

See [`connectors/vp3/README.md`](connectors/vp3/README.md) and [`connectors/vp3/client.js`](connectors/vp3/client.js).

## Run locally

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python desktop/launcher.py
```

## Build Windows distribution

```bash
pyinstaller HomeServer.spec --clean --noconfirm
```

Windows CI validates migrations, legacy-data bootstrap, single-instance behavior, DPAPI owner protection, owner-system boundaries, recovery mode, the complete Agent/Tools/Approvals/Contacts/Backup regression suite, packaged EXE startup, packaged second-instance rejection, packaged recovery mode, packaged staged restore, silent installer upgrade preservation, installer output and SHA-256 distribution hashes.
