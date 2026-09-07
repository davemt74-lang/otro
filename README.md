# HomeServer

HomeServer is a local-first private capability server for personal AI agents and explicitly authorized applications such as VP3.

The Windows desktop runtime listens on `127.0.0.1:4377`, stores durable state in SQLite, and provides local owner surfaces for the primary agent, chat, knowledge, memory, contacts, tasks/reminders, notifications, skills, tools, approvals, pairing, permissions, backup/restore, setup, diagnostics and the optional outbound Remote Bridge.

## Current v0.14 foundation

- Packaged Windows `HomeServer.exe` and per-user `HomeServerSetup.exe`
- Agent Chat is the primary owner workspace with a ChatGPT-style composer and live conversation sidebar
- Sidebar keeps New Chat, Approvals, Knowledge, Memory and Contacts visible while secondary workspaces live in the bottom user menu
- Canonical conversation history with per-chat rename and delete controls
- AGENT BRAIN provider settings for local Ollama plus user-owned Anthropic, OpenAI and OpenRouter inference
- ElevenLabs credential storage for future voice capabilities
- Provider API keys protected with Windows DPAPI outside SQLite; owner APIs expose only configured state and key suffixes
- HomeServer-first inference routing for VP3: local/user-provider compute is preferred before VP3 cloud fallback
- Explicit compute-source responses (`homeserver_local`, `user_provider`, `vp3_cloud`) and zero VP3 cloud debit for HomeServer-served chats
- Token Usage History with provider/model, input/output/total tokens, VP3 cloud debit and latest reported balance
- App-scoped usage history and app-scoped idempotency keys so paired applications cannot read or collide with another app's usage events
- SQLite WAL database with transactional migrations (schema 12)
- Supervised tray runtime with graceful shutdown/restart
- Per-data-directory Windows single-instance mutex
- `%LOCALAPPDATA%\HomeServer\Data` as the installed Windows data location
- Upgrade-safe migration from legacy `~/.homeserver` when unambiguous
- Windows DPAPI protection for owner, Remote Bridge and provider credentials
- Process-local owner browser sessions invalidated on restart
- First-run Setup & Diagnostics and restricted recovery mode
- Persistent private Agent Brain and app-isolated conversations
- Durable memory and local document ingestion/FTS search
- Private Contacts & Relationship Context
- First-class Tasks, due dates, priorities and recurring reminders
- Canonical notification inbox with read/dismiss state
- Local durable reminder scheduler
- Allowlisted Skills & Tools with content-safe auditing
- Optional bounded Agent Tool Use, disabled by default
- Approval-gated memory-write and task-create proposals, separately disabled by default
- Browser-safe claim-token pairing for VP3 and future clients
- Optional outbound-only Remote Bridge, disabled by default
- Deployable trusted Remote Relay service and VP3 remote connector
- Local Backup & Restore with SHA-256 manifests and startup-time rollback protection

## Agent Brain and VP3 inference routing

HomeServer v0.14 separates the user's private Agent Brain from VP3's paid cloud compute.

In `auto` mode HomeServer selects the first ready inference path in this order:

1. local Ollama model (`homeserver_local`)
2. user-owned Anthropic / Claude API key (`user_provider`)
3. user-owned OpenAI API key (`user_provider`)
4. user-owned OpenRouter API key (`user_provider`)

A user may explicitly choose a ready provider instead. ElevenLabs credentials are stored alongside the inference-provider credentials for voice integration, but ElevenLabs is not an LLM inference route.

The public capabilities response includes a non-secret inference status. VP3 can therefore decide whether HomeServer can satisfy an agent request before using paid cloud compute. A successful HomeServer-served chat returns its `compute_source` and `cloud_tokens_debited: 0`.

If no HomeServer inference route is ready, HomeServer reports `cloud_fallback_required: true`. VP3 may then use the user's paid cloud subscription/token balance. VP3 cloud usage can be reported back through the permissioned usage API, where it is stored separately from local/user-provider usage.

Provider credentials are never returned by an API. On Windows they are protected for the current Windows user with DPAPI and stored at:

```text
%LOCALAPPDATA%\HomeServer\Data\security\provider-credentials.dat
```

## Token Usage History

Schema 12 adds an inference-usage ledger. Each row records:

- source application
- compute source
- provider and model
- request kind
- prompt/input tokens
- completion/output tokens
- total model tokens
- VP3 billable token debit, when applicable
- VP3-reported balance after a cloud charge, when applicable
- timestamp

HomeServer-served requests never create a VP3 cloud debit. They are retained as usage history so the owner can see local/user-provider compute alongside paid cloud activity.

Cloud charge notifications use an app-scoped idempotency key: `(source_app_key, event_id)`. Retrying the same charge from the same paired app does not double-count it, while a different paired app can legitimately use the same event identifier without collision. Paired apps granted `usage.read` can only read their own usage rows and summary; the local owner control surface can view the complete ledger.

The balance stored by HomeServer is the latest balance *reported by the authorized cloud integration*. HomeServer does not independently mint or debit VP3 cloud tokens.

## Windows lifecycle and data safety

Installed Windows builds use:

```text
%LOCALAPPDATA%\HomeServer\Data
```

Primary paths include:

- `homeserver.db` — SQLite state
- `knowledge\files\` — imported local source files
- `backups\` — validated local backup archives
- `restore\` — staged/last restore metadata
- `security\owner-bootstrap.dat` — Windows-protected owner bootstrap material
- `security\remote-bridge.dat` — Windows-protected Remote Bridge device credential
- `security\provider-credentials.dat` — Windows-protected model/voice provider credentials
- `runtime\bootstrap-state.json` — non-secret startup/migration diagnostics

If a legacy `~/.homeserver` directory contains data and LocalAppData does not, the Windows launcher moves the complete legacy directory before importing the application runtime. If both locations contain data, HomeServer does not merge or delete either location; LocalAppData remains active and Setup & Diagnostics reports the conflict.

`HOMESERVER_DATA_DIR` overrides the default for development/testing or an explicitly managed local location.

HomeServer acquires a named Windows mutex before applying restores, opening SQLite or binding port `4377`. Quit and Restart request graceful shutdown, close the Remote Bridge and local server, release the instance mutex and only then relaunch when needed.

The tray provides:

- Open HomeServer
- Tasks & Notifications
- Setup & Diagnostics
- Remote Bridge
- Open Data Folder
- Create Backup
- API Docs
- Restart HomeServer
- Quit

## Owner security and recovery

The persistent owner bootstrap secret, Remote Bridge device credential and provider credentials are protected with Windows DPAPI for the current Windows user. The owner browser session itself remains process-local and is stored only in an HttpOnly, SameSite=Strict cookie; restart invalidates it.

If normal SQLite initialization fails, HomeServer starts a restricted local recovery application. Recovery can validate/stage a known-good backup, preserve unreadable live state for forensic recovery and request a supervised restart. Paired apps, Agent APIs and the Remote Bridge are not started in recovery mode.

## Tasks, reminders and notifications

HomeServer includes first-class private tasks in the same SQLite brain as memory, knowledge and contacts.

Each task can include:

- title and description
- pending / in-progress / completed / cancelled status
- low / normal / high / urgent priority
- due date/time
- reminder date/time
- optional linked contact
- source application/provenance
- one-time, daily, weekly or monthly reminder recurrence

The local scheduler checks due reminders without contacting any cloud service. When a reminder becomes due it atomically advances or clears the task's `remind_at` value and creates one row in the canonical `notifications` table. The reservation update prevents the same reminder occurrence from being emitted twice by overlapping scheduler passes.

The scheduler does not execute arbitrary tasks, tools, HTTP calls, shell commands or workflows. It only creates local notifications.

Owner workspace:

```text
/tasks
```

Paired-app capabilities:

- `tasks.read`
- `tasks.write`
- `notifications.read`

A paired app granted `tasks.write` may directly create/update tasks through the explicit app API. Agent/model-driven task creation is different: the model is never offered direct `tasks.create`; it can only propose a task when write proposals are enabled, and the owner must approve it locally before execution.

## Skills, tools and approval-gated actions

Built-in tools remain allowlisted. Current tools include:

- `contacts.search`
- `knowledge.search`
- `memory.list`
- `memory.write`
- `tasks.list`
- `notifications.list`
- `tasks.create`

The Task & Reminder Manager skill groups task/notification capabilities but grants no permissions by itself.

Model-readable functions are permission-filtered. For tasks these are:

- `homeserver_tasks_list`
- `homeserver_notifications_list`

The model is never offered direct `memory.write` or `tasks.create`. When owner-controlled write proposals are enabled it may receive:

- `homeserver_memory_write_request`
- `homeserver_task_create_request`

Those functions create pending local approval requests only. The durable write occurs through the canonical audited tool after explicit owner approval. Task titles/descriptions and memory bodies are not duplicated into tool/activity audit metadata.

HomeServer intentionally exposes no shell, PowerShell, arbitrary HTTP or unrestricted filesystem tool.

## Remote Bridge and deployable relay

HomeServer's optional Remote Bridge makes an outbound connection so a broker-mediated client such as VP3 can reach the user's HomeServer without router forwarding or a public localhost listener.

Production broker URLs require `wss://`; `ws://` is accepted only for loopback development/test hosts. The bridge has an explicit operation map and is not an arbitrary TCP/HTTP tunnel. Protected operations are dispatched back through HomeServer's existing bearer-authenticated local APIs, so canonical paired-app permissions remain authoritative.

The repository also contains the deployable Remote Relay service under `relay/`. The relay session selects which HomeServer a remote client can reach; the separate HomeServer app credential controls what that client may do.

Current trust model: **trusted WSS relay**. TLS protects transport to the relay, but the relay can see relayed application payloads and HomeServer bearer credentials. This is not end-to-end payload encryption.

Owner Control, pairing approval, Windows lifecycle, backup/restore, recovery, shell, arbitrary HTTP and unrestricted filesystem access are not relay capabilities.

See:

- `connectors/vp3/README.md`
- `connectors/vp3/REMOTE.md`
- `relay/README.md`

## Local Backup & Restore

A manual backup uses SQLite's backup API to produce a consistent database snapshot and includes imported knowledge files plus a SHA-256 manifest. Restore validates archive paths, hashes, SQLite integrity/foreign keys/schema compatibility and referenced files before staging.

Restore never replaces a live SQLite file through an API request. A staged restore is revalidated on startup, preceded by an automatic safety backup and applied before the normal API opens SQLite. Failure rolls state back or enters restricted recovery safely.

Portable ZIP backups are not encrypted by the ZIP format. Treat exported archives as private data.

Backup/restore remains owner-only and is not a VP3/paired-app permission.

## VP3 pairing model

VP3 connects locally through `claim-v1` pairing or remotely through the deployable relay plus the same HomeServer pairing model. The relay does not create a second authorization system.

Each capability is independently permissioned. Current permission families include:

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

Windows lifecycle, diagnostics, backup/restore and recovery are intentionally absent from the app permission catalog.

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

Windows CI validates the agent-first shell integration contract, migrations, inference routing/usage isolation, legacy-data bootstrap, single-instance behavior, DPAPI owner protection, recovery mode, Agent/Tools/Approvals/Contacts/Tasks/Backup regressions, Remote Bridge security/protocol, packaged EXE startup, packaged restart/session rotation/shutdown, the packaged outbound relay permission boundary, staged restore, installer upgrade preservation and distribution hashes.

Relay CI independently validates the deployable relay process, VP3 remote connector, Docker image and live container health.
