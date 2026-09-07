# HomeServer

HomeServer is a local-first private capability server for personal AI agents and explicitly authorized applications such as VP3.

The Windows desktop runtime listens on `127.0.0.1:4377`, stores durable state in SQLite, and provides owner-controlled agent chat, context retrieval, knowledge, memory, contacts, tasks/reminders, notifications, skills/tools, approvals, pairing, inference routing, token history, backup/restore, setup/diagnostics and an optional outbound Remote Bridge.

## Current v0.16 foundation

- Packaged Windows `HomeServer.exe` and per-user `HomeServerSetup.exe`
- Agent Chat as the primary owner workspace with a ChatGPT-style composer and live conversation sidebar
- Sidebar keeps New Chat, Approvals, Knowledge, Memory and Contacts visible; secondary workspaces live in the bottom user menu
- Canonical conversation history with per-chat rename and delete controls
- Agent Brain Context Engine with relevance-based Memory, Knowledge and Contacts retrieval
- Per-chat Memory/Knowledge/Contacts controls, context budget and local-only privacy mode
- Sanitized source attribution/history without exposing source content, filesystem paths, email addresses or phone numbers
- AGENT BRAIN provider settings for local Ollama plus user-owned Anthropic/Claude, OpenAI and OpenRouter inference
- ElevenLabs credential storage for voice integration
- Provider API keys protected with Windows DPAPI outside SQLite; APIs expose only configured state/key suffixes
- HomeServer-first inference routing for VP3, with paid VP3 cloud compute used only when no ready HomeServer/user-provider route exists
- Explicit compute sources: `homeserver_local`, `user_provider`, `vp3_cloud`
- Token Usage History with provider/model, input/output/total tokens, VP3 cloud debit and latest reported balance
- App-scoped usage history and app-scoped idempotency keys
- Watched local Knowledge Sources with scheduled synchronization and SQLite FTS indexing
- SQLite WAL database with transactional migrations through schema 14
- Supervised Windows tray runtime with graceful restart/shutdown and single-instance enforcement
- `%LOCALAPPDATA%\HomeServer\Data` as the installed Windows data location
- Windows DPAPI protection for owner, Remote Bridge and provider credentials
- Restricted recovery mode and startup-safe backup restore
- Persistent private Agent Brain and app-isolated conversations
- Durable memory, contacts, tasks, reminders and notification inbox
- Allowlisted Skills & Tools with content-safe auditing and approval-gated writes
- Browser-safe claim-token pairing for VP3/future clients
- Optional outbound-only Remote Bridge and deployable trusted relay

## Agent Brain Context Engine

HomeServer v0.16 makes private context retrieval part of the canonical chat path instead of treating Knowledge, Memory and Contacts as separate databases the user must query manually.

For each chat request HomeServer can retrieve relevant:

- Memory records
- Knowledge and watched-document results
- Contacts, when the caller is permitted to read them

Retrieval is relevance-based and bounded. A hard per-conversation character budget prevents large private datasets from being dumped into a model prompt, and the budget can be changed per chat within HomeServer-defined limits.

Each conversation stores independent context controls for:

- Memory on/off
- Knowledge on/off
- Contacts on/off
- cloud providers allowed/on-off
- maximum retrieved context size

For paired applications, these toggles are never an authorization mechanism. The paired app's current permissions remain the hard ceiling: `memory.read`, `knowledge.search` and `contacts.read` must still be granted before those context classes can be retrieved. If a permission is later revoked, context/source-history responses are filtered against the app's current permissions.

Retrieved private data is explicitly framed as **untrusted factual context**. The system prompt instructs the Agent Brain not to follow commands or instructions embedded inside Memory, documents, notes or contact notes, reducing prompt-injection risk from local content.

Source attribution is intentionally metadata-only. Chat can report the source kind, record ID, title and update timestamp, while retrieval/audit history does not duplicate the underlying private content. Generic paired-app responses do not expose watched-folder paths, contact email addresses or phone numbers through source attribution.

A conversation can also be switched to **Private / local-only** by disabling cloud providers. When that setting is active, HomeServer refuses a hosted user-provider route instead of silently transmitting private context; a configured local Ollama route can continue to serve the conversation.

Agent run and token-usage metadata records context counts and total retrieved size, not the retrieved private text itself.

The capabilities endpoint explicitly advertises:

- `agent.context`
- `agent.context.budget`
- `agent.context.sources`
- `agent.privacy.local_only`

This allows future clients such as VP3 to negotiate Context Engine support without guessing from HomeServer's version number.

## Local Knowledge Sync

HomeServer v0.15 added owner-managed watched folders. A watched source points at an existing local directory; HomeServer reads supported documents and maintains canonical `knowledge_items`/FTS rows as files change.

Supported file types:

- `.txt`
- `.md` / `.markdown`
- `.json`
- `.csv`
- `.html` / `.htm`
- `.pdf`
- `.docx`

Knowledge Source behavior:

- recursive or top-level-only scans
- configurable 30–3600 second refresh interval
- SHA-256 content change detection
- unchanged files skip extraction/reindexing
- edited files update the existing Knowledge item
- file moves/renames preserve the existing Knowledge item when safely identifiable
- deleted source files remove only their indexed HomeServer copy
- temporarily unavailable folders preserve the last good index and report an error
- manually deleted watched Knowledge rows self-repair on the next scan
- source removal never deletes or edits the user's original files
- symlinked directories are not traversed
- HomeServer's own private data directory cannot be configured as a source
- common development/cache paths such as `.git`, `node_modules`, `.venv` and `__pycache__` are excluded by default

The Knowledge page contains an owner-only Watched Folders workspace with source status, last scan, file counts, per-file errors, Scan Now, Pause/Resume and Remove controls.

Filesystem privacy boundary: watched Knowledge remains searchable by paired applications granted `knowledge.search`, but the generic paired-app Knowledge API suppresses the owner's absolute local filesystem paths. Absolute source paths are exposed only through owner-controlled Knowledge Sources endpoints/UI.

## Agent Brain and VP3 inference routing

In `auto` mode HomeServer selects the first ready inference route in this order:

1. local Ollama model (`homeserver_local`)
2. user-owned Anthropic/Claude API key (`user_provider`)
3. user-owned OpenAI API key (`user_provider`)
4. user-owned OpenRouter API key (`user_provider`)

A user may explicitly select a ready provider. ElevenLabs is stored alongside provider credentials for voice capabilities but is not an LLM inference route.

`/api/v1/capabilities` and `/api/v1/inference/status` expose non-secret readiness information so a paired client such as VP3 can determine whether HomeServer can satisfy an agent request before using paid cloud compute. A successful HomeServer-served chat returns its compute source and `cloud_tokens_debited: 0`.

If no local/user-provider route is ready, HomeServer reports `cloud_fallback_required: true`. VP3 may then use the user's paid cloud subscription/token balance and report the resulting charge through the permissioned usage API.

Provider credentials are never returned by an API. On Windows they are DPAPI-protected for the current Windows user at:

```text
%LOCALAPPDATA%\HomeServer\Data\security\provider-credentials.dat
```

## Token Usage History

The inference ledger records:

- source application
- compute source
- provider/model
- request kind
- prompt/input tokens
- completion/output tokens
- total model tokens
- VP3 billable token debit when applicable
- VP3-reported post-charge balance when applicable
- timestamp

HomeServer-served inference never creates a VP3 cloud debit. Cloud charge events use `(source_app_key, event_id)` as the idempotency boundary, preventing retry double-charges while allowing separate apps to reuse their own event identifiers. Paired apps granted `usage.read` can only read their own usage rows; the owner sees the complete ledger.

HomeServer does not mint or independently debit VP3 cloud tokens. The stored balance is the latest balance reported by the authorized cloud integration.

## Windows lifecycle and data safety

Installed Windows builds use:

```text
%LOCALAPPDATA%\HomeServer\Data
```

Primary paths include:

- `homeserver.db` — SQLite state
- `knowledge\files\` — copies of manually imported documents only
- `backups\` — validated local backup archives
- `restore\` — staged/last restore metadata
- `security\owner-bootstrap.dat` — Windows-protected owner bootstrap material
- `security\remote-bridge.dat` — Windows-protected Remote Bridge credential
- `security\provider-credentials.dat` — Windows-protected model/voice provider credentials
- `runtime\bootstrap-state.json` — non-secret startup/migration diagnostics

Watched folders are referenced in SQLite; their original files are not copied into `knowledge\files\` and are never modified by source synchronization.

If legacy `~/.homeserver` data exists and LocalAppData does not, the Windows launcher moves the complete legacy directory before importing the runtime. If both locations contain data, HomeServer does not merge/delete either location; LocalAppData remains active and Setup & Diagnostics reports the conflict.

`HOMESERVER_DATA_DIR` overrides the default for development/testing or an explicitly managed local location.

## Owner security and recovery

Owner control endpoints require the local process-issued owner session. Persistent owner bootstrap material, Remote Bridge credentials and provider keys are protected with Windows DPAPI. Browser owner sessions are process-local, HttpOnly and SameSite=Strict; restart invalidates them.

If normal SQLite initialization fails, HomeServer starts a restricted recovery application. Recovery can validate/stage a known-good backup, preserve unreadable live state for forensic recovery and request a supervised restart. Paired apps, Agent APIs, Knowledge Sources scheduler and Remote Bridge are not started in recovery mode.

## Tasks, reminders and notifications

HomeServer stores private tasks in the same SQLite brain as memory, knowledge and contacts. Tasks support status, priority, due/reminder times, optional contacts and one-time/daily/weekly/monthly recurrence.

The local scheduler only creates notifications and advances reminder state; it does not execute arbitrary tools, HTTP requests, shell commands or workflows. Model-driven task creation can only create an approval request when owner-controlled write proposals are enabled.

Paired-app permissions include `tasks.read`, `tasks.write` and `notifications.read`.

## Skills, tools and approval-gated actions

Built-in allowlisted tools include:

- `contacts.search`
- `knowledge.search`
- `memory.list`
- `memory.write`
- `tasks.list`
- `notifications.list`
- `tasks.create`

Model-readable functions are permission-filtered. Direct model mutation is not exposed: memory/task writes are proposal-only and require local owner approval before the canonical audited tool executes.

HomeServer intentionally exposes no arbitrary shell, PowerShell, HTTP proxy or unrestricted filesystem tool.

## Remote Bridge

The optional Remote Bridge creates an outbound connection so a broker-mediated application such as VP3 can reach HomeServer without router forwarding or a public localhost listener.

Production broker URLs require `wss://`; `ws://` is accepted only for loopback development/test hosts. The bridge has an explicit operation map and dispatches protected operations through HomeServer's bearer-authenticated local APIs, preserving paired-app permissions.

Current trust model: **trusted WSS relay**. TLS protects transport, but the relay can see relayed application payloads and bearer credentials; this is not end-to-end payload encryption.

Owner control, pairing approval, Windows lifecycle, backup/restore, Knowledge Source management, recovery, shell, arbitrary HTTP and unrestricted filesystem access are not relay capabilities.

See `connectors/vp3/README.md`, `connectors/vp3/REMOTE.md` and `relay/README.md`.

## Backup and restore

A manual backup uses SQLite's backup API to produce a consistent database snapshot and includes manually imported Knowledge files plus a SHA-256 manifest. Watched source originals are not bundled; their source configuration/index metadata lives in SQLite and the source can be rescanned when the folder is available.

Restore validates archive paths, hashes, SQLite integrity/foreign keys/schema compatibility and referenced imported files before staging. Restore never replaces a live SQLite database from an HTTP request; staged state is revalidated and applied before normal startup with an automatic pre-restore safety backup.

Portable ZIP backups are not encrypted. Treat exported archives as private data.

## VP3 pairing model

VP3 connects locally through `claim-v1` pairing or remotely through the deployable relay plus the same HomeServer pairing model. Each capability remains independently permissioned.

Current permission families include:

- `agent.chat`
- `contacts.read`
- `knowledge.search`
- `memory.read`
- `memory.write`
- `notifications.read`
- `tasks.read`
- `tasks.write`
- `tools.execute`
- `usage.read`
- `usage.write`

Knowledge Source management, Windows lifecycle, diagnostics, backup/restore and recovery are owner-only and intentionally absent from paired-app permissions.

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

Windows CI validates JavaScript syntax, the agent-first shell, schema migrations, Knowledge Sources synchronization/privacy, inference routing/usage isolation, legacy-data bootstrap, single-instance behavior, DPAPI owner protection, recovery mode, Agent/Tools/Approvals/Contacts/Tasks/Backup regressions, Remote Bridge security/protocol, packaged EXE startup/restart/shutdown, packaged relay permissions, staged restore, installer upgrade preservation and distribution hashes.

Context Engine CI separately validates relevance-based Memory/Knowledge/Contacts retrieval, context budgets, source-attribution privacy, prompt-injection framing, local-only hosted-provider refusal, paired-app permission ceilings and the Agent Chat context UI contract.

Knowledge Sync CI separately validates that the Knowledge Sources module is actually loaded by the owner UI and runs the folder-sync regression as a fast integration gate.

Relay CI independently validates the deployable relay process, VP3 remote connector, Docker image and live container health.
