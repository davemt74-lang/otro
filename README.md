# HomeServer

HomeServer is a local-first private capability server for personal AI agents and explicitly authorized applications such as VP3.

The Windows desktop runtime listens on `127.0.0.1:4377`, stores durable state in SQLite, and provides owner-controlled Agent Chat, context retrieval, shared cognition, knowledge, memory, contacts, tasks/reminders, notifications, skills/tools, approvals, application pairing, inference routing, token history, backup/restore, setup/diagnostics and an optional outbound Remote Bridge.

## Current v0.17 foundation

- Packaged Windows `HomeServer.exe` and per-user `HomeServerSetup.exe`
- Agent Chat as the primary owner workspace with persistent conversations
- Agent Brain Context Engine with relevance-based Memory, Knowledge, Contacts and multi-app Awareness
- Canonical cross-app cognitive event ledger with app-scoped idempotency
- Background cognition jobs and a local cognitive scheduler
- Multi-app Awareness summaries built from related events without merging raw app histories
- Provenance-aware memory candidates and owner-reviewed consolidation into shared Agent Memory
- Durable memory types and metadata for working, episodic, semantic, preference, relationship and procedural context
- Manifest-driven Plugin Registry with event producers/subscriptions and bounded read-only Agent tools
- Plugin manifests do not download or execute arbitrary third-party code; executable handlers must be packaged and explicitly registered in-process
- Per-chat Memory/Knowledge/Contacts controls, context budget and local-only privacy mode
- Sanitized source attribution/history without exposing source content, filesystem paths, email addresses or phone numbers
- AGENT BRAIN provider settings for local Ollama plus user-owned Anthropic/Claude, OpenAI and OpenRouter inference
- ElevenLabs credential storage for future voice integration
- Provider API keys protected with Windows DPAPI outside SQLite; APIs expose only configured state/key suffixes
- HomeServer-first inference routing for paired apps, with paid VP3 cloud compute used only when no ready HomeServer/user-provider route exists
- Explicit compute sources: `homeserver_local`, `user_provider`, `vp3_cloud`
- Token Usage History with provider/model, input/output/total tokens, cloud debit and latest reported balance
- App-scoped usage history and app-scoped idempotency keys
- Watched local Knowledge Sources with scheduled synchronization and SQLite FTS indexing
- SQLite WAL database with transactional migrations through schema 15
- Supervised Windows tray runtime with graceful restart/shutdown and single-instance enforcement
- `%LOCALAPPDATA%\HomeServer\Data` as the installed Windows data location
- Windows DPAPI protection for owner, Remote Bridge and provider credentials
- Restricted recovery mode and startup-safe backup restore
- Persistent private Agent Brain and app-isolated conversation histories
- Shared durable memory, contacts, tasks, reminders and notification inbox
- Allowlisted Skills & Tools with content-safe auditing and approval-gated writes
- Browser-safe claim-token pairing for VP3/future clients
- Optional outbound-only Remote Bridge and deployable trusted relay

## Cognitive Runtime and multi-app awareness

HomeServer v0.17 adds a shared cognition layer above the existing Agent Brain rather than turning every connected application into a separate brain.

The model is:

```text
Apps / Plugins
    ↓
Capability + Permission Gateway
    ↓
Shared Cognitive Event Bus
    ↓
Cognitive Runtime
    ↓
Awareness + Memory Candidates
    ↓
Shared Memory / Knowledge / Contacts / Tasks
    ↓
Agent Brain
    ↓
Tools / Actions / Approvals
```

Connected applications publish observations as typed events. They do **not** silently write arbitrary observations directly into permanent Agent Memory.

A cognitive event can contain bounded metadata such as:

- originating application
- event type and stable event ID
- summary
- entity type/key
- correlation ID
- conversation reference when applicable
- importance
- privacy scope
- structured payload
- occurrence time

Event idempotency is scoped to the source application, so retries from one app do not create duplicate events while independent apps may use their own identifiers safely.

Existing meaningful HomeServer `activity_log` entries are also mirrored into the cognitive event stream. This lets Agent Chat, tool activity, application pairing, tasks and other canonical HomeServer actions participate in the same history without duplicating the old subsystems.

### Awareness

The cognitive runtime aggregates related activity into user-wide Awareness records. Awareness is a summary layer, not permission to expose every application's raw event payload.

Raw event history remains application-scoped through `events.read`. Cross-app Awareness requires the separate `awareness.read` permission and exposes summaries rather than other applications' raw event payloads.

The owner Agent Brain can retrieve relevant open Awareness during a chat turn. A paired application only receives that shared Awareness context when the owner has explicitly granted `awareness.read` to that application.

This gives HomeServer multi-app awareness without collapsing all app conversations and private histories into one global transcript.

### Cognitive loop

HomeServer now supports both request-time Agent reasoning and background event cognition:

```text
Observe → Contextualize → Reason/Rule → Decide → Act or Propose → Record → Consolidate
```

Not every event invokes an LLM. Deterministic local rules and aggregation can process routine events, keeping the cognitive layer inexpensive and compatible with local inference.

## Shared Memory and memory consolidation

`agent_memory` remains the shared durable memory store for the primary Agent Brain. Applications do not receive separate permanent brains by default.

v0.17 adds provenance and consolidation metadata around durable memory, including memory type, source application/event/entity information, confidence and reinforcement information.

Applications granted `events.write` can publish observations. An application can only request creation of a memory candidate when it also has the existing `memory.write` permission. A candidate is still distinct from durable memory.

The cognitive runtime can form candidates from repeated or important observations. The local owner can review pending candidates and accept or reject them from the **Cognition & Plugins** workspace. Accepted candidates become shared Agent Memory while retaining provenance back to the event/app that produced the observation.

This keeps durable Memory useful instead of allowing a noisy integration to turn every click or transient state change into a permanent fact.

## Plugin Bus

HomeServer v0.17 introduces a manifest-driven Plugin Registry.

A plugin manifest may describe:

- plugin ID/name/version
- requested HomeServer permissions
- event types it produces
- event types it subscribes to
- optional read-only Agent tools
- tool input schemas and required permissions
- compatibility/configuration metadata

Registration of a manifest is metadata registration only. HomeServer does **not** fetch and execute arbitrary code from a manifest, URL or package.

For a plugin Agent tool to become executable in v0.17:

1. the plugin must be active;
2. the tool must be declared as read-only;
3. an implementation must already be packaged with HomeServer and explicitly bound as an in-process handler;
4. the caller must have every permission required by the tool; and
5. the existing Agent Tool policy must permit tool use.

Mutation remains behind HomeServer's canonical audited tools and approval system. v0.17 does not create an arbitrary plugin-write escape hatch.

The owner **Cognition & Plugins** workspace shows runtime status, events, open Awareness, pending memory candidates and registered plugins.

## Agent Brain Context Engine

The Context Engine makes private context retrieval part of the canonical chat path instead of treating Knowledge, Memory, Contacts and Awareness as separate databases the user must query manually.

For each chat request HomeServer can retrieve relevant:

- Memory records
- Knowledge and watched-document results
- Contacts, when the caller is permitted to read them
- open multi-app Awareness, for the owner or callers granted `awareness.read`

Retrieval is relevance-based and bounded. A per-conversation character budget prevents large private datasets from being dumped into a model prompt.

Each conversation stores independent controls for:

- Memory on/off
- Knowledge on/off
- Contacts on/off
- cloud providers allowed/on-off
- maximum retrieved context size

These UI/context controls are never an authorization mechanism. A paired app's current permissions remain the hard ceiling. For example, `memory.read`, `knowledge.search`, `contacts.read` and `awareness.read` must be granted before those context classes can be used for that app.

Retrieved private data is explicitly framed as **untrusted factual context**. The Agent Brain is instructed not to follow commands embedded inside Memory, documents, notes, contacts or Awareness content.

Source attribution is metadata-oriented and avoids duplicating private source text. Generic paired-app responses do not expose watched-folder paths, contact email addresses or phone numbers through source attribution.

A conversation can be switched to **Private / local-only** by disabling cloud providers. HomeServer then refuses a hosted provider route rather than silently transmitting private context; a ready local Ollama route may continue to serve the conversation.

Agent-run and token-usage metadata records context counts and sizes, not the complete retrieved private context.

## Local Knowledge Sync

HomeServer v0.15 added owner-managed watched folders. A watched source points at an existing local directory; HomeServer reads supported documents and maintains canonical `knowledge_items`/FTS rows as files change.

Supported file types include `.txt`, `.md`, `.markdown`, `.json`, `.csv`, `.html`, `.htm`, `.pdf` and `.docx`.

Knowledge Source behavior includes:

- recursive or top-level-only scans
- configurable 30–3600 second refresh interval
- SHA-256 content change detection
- unchanged-file fast path
- update-in-place for edited files
- move/rename preservation when safely identifiable
- removal of only the indexed HomeServer copy when a source file disappears
- preservation of the last good index when a folder is temporarily unavailable
- self-repair when a watched Knowledge row is manually deleted
- no modification/deletion of the user's source files
- no traversal of symlinked directories
- rejection of HomeServer's own private data directory as a source
- default exclusions for `.git`, `node_modules`, `.venv`, `__pycache__` and similar paths

Filesystem privacy boundary: watched Knowledge remains searchable by paired applications granted `knowledge.search`, but the generic paired-app Knowledge API suppresses absolute local filesystem paths. Absolute source paths are owner-only.

## Agent Brain and inference routing

In `auto` mode HomeServer selects the first ready inference route in this order:

1. local Ollama model (`homeserver_local`)
2. user-owned Anthropic/Claude API key (`user_provider`)
3. user-owned OpenAI API key (`user_provider`)
4. user-owned OpenRouter API key (`user_provider`)

A user may explicitly select a ready provider. ElevenLabs is stored alongside provider credentials for future voice capabilities but is not an LLM inference route.

`/api/v1/capabilities` and `/api/v1/inference/status` expose non-secret readiness information so a paired client can determine whether HomeServer can satisfy a request before using paid cloud compute. A successful HomeServer-served chat returns its compute source and `cloud_tokens_debited: 0`.

If no local/user-provider route is ready, HomeServer reports `cloud_fallback_required: true`. A cloud application such as VP3 may then use its paid subscription/token balance and report the resulting charge through the permissioned usage API.

Provider credentials are never returned by an API. On Windows they are DPAPI-protected at:

```text
%LOCALAPPDATA%\HomeServer\Data\security\provider-credentials.dat
```

## Token Usage History

The inference ledger records source application, compute source, provider/model, request kind, prompt/input tokens, completion/output tokens, total model tokens, cloud billable debit when applicable, reported post-charge balance and timestamp.

HomeServer-served inference never creates a VP3 cloud debit. Cloud charge events use `(source_app_key, event_id)` as the idempotency boundary. Paired apps granted `usage.read` can only read their own usage rows; the owner sees the complete ledger.

HomeServer does not mint or independently debit VP3 cloud tokens. The stored balance is the latest balance reported by the authorized cloud integration.

## Remote Bridge

The optional Remote Bridge creates an outbound connection so a broker-mediated application can reach HomeServer without router forwarding or a public localhost listener.

Production broker URLs require `wss://`; `ws://` is accepted only for loopback development/test hosts. The bridge uses an explicit operation allowlist and dispatches protected operations through HomeServer's bearer-authenticated loopback APIs, preserving the same paired-app permissions used locally.

v0.17 Remote Bridge operations include the existing pairing/chat/conversation/Knowledge/Memory/Contacts/Skills/Tools actions plus bounded routes for:

- inference status
- cognitive event emit/list
- Awareness list
- active plugin list
- cloud usage reporting
- app-scoped usage history

Conversation IDs are treated as path-safe opaque identifiers, matching HomeServer's UUID-like conversation IDs.

Current trust model: **trusted WSS relay**. TLS protects transport, but the relay can see relayed application payloads and bearer credentials; this is not end-to-end payload encryption.

Owner control, pairing approval, Windows lifecycle, backup/restore, Knowledge Source management, recovery, arbitrary HTTP, shell/PowerShell and unrestricted filesystem access are not relay capabilities.

See `connectors/vp3/README.md`, `connectors/vp3/REMOTE.md` and `relay/README.md`.

## Tasks, tools and approvals

HomeServer stores private tasks in the same SQLite brain as Memory, Knowledge, Contacts and cognitive state. Tasks support status, priority, due/reminder times, optional contacts and one-time/daily/weekly/monthly recurrence.

The task scheduler creates notifications and advances reminder state; it does not execute arbitrary shell commands, HTTP requests or workflows.

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

## Windows lifecycle, backup and recovery

Installed Windows builds use:

```text
%LOCALAPPDATA%\HomeServer\Data
```

Primary paths include:

- `homeserver.db` — SQLite state, including cognition/plugin metadata
- `knowledge\files\` — copies of manually imported documents only
- `backups\` — validated local backup archives
- `restore\` — staged/last restore metadata
- `security\owner-bootstrap.dat` — Windows-protected owner bootstrap material
- `security\remote-bridge.dat` — Windows-protected Remote Bridge credential
- `security\provider-credentials.dat` — Windows-protected model/voice provider credentials
- `runtime\bootstrap-state.json` — non-secret startup/migration diagnostics

Watched folders are referenced in SQLite; their originals are never modified by synchronization and are not copied into `knowledge\files\`.

A manual backup uses SQLite's backup API to produce a consistent database snapshot and includes manually imported Knowledge files plus a SHA-256 manifest. Watched-source originals are not bundled.

Restore validates archive paths, hashes, SQLite integrity/foreign keys/schema compatibility and referenced imported files before staging. Restore is applied before normal startup with an automatic pre-restore safety backup.

Portable ZIP backups are not encrypted. Treat exported archives as private data.

If normal SQLite initialization fails, HomeServer starts a restricted recovery application. Paired apps, Agent APIs, cognition, Knowledge Sources and Remote Bridge are not started as normal capabilities in recovery mode.

## Pairing permissions

Each paired application receives its own token and explicit permissions. Current permission families include:

- `agent.chat`
- `awareness.read`
- `contacts.read`
- `events.read`
- `events.write`
- `knowledge.search`
- `memory.read`
- `memory.write`
- `notifications.read`
- `plugins.read`
- `tasks.read`
- `tasks.write`
- `tools.execute`
- `usage.read`
- `usage.write`

`events.read` returns only that application's raw event rows. `awareness.read` is deliberately separate because it can expose user-wide cognitive summaries.

Knowledge Source management, plugin registration/status changes, memory-candidate owner decisions, Windows lifecycle, diagnostics, backup/restore and recovery remain owner-controlled.

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

The main Windows CI validates JavaScript syntax, cognition UI/runtime, schema migrations, Knowledge Sources synchronization/privacy, inference routing/usage isolation, legacy-data bootstrap, single-instance behavior, DPAPI owner protection, recovery mode, Agent/Tools/Approvals/Contacts/Tasks/Backup regressions, Remote Bridge security/protocol, packaged EXE startup/restart/shutdown, packaged relay permissions, staged restore, installer upgrade preservation and distribution hashes.

Dedicated Cognition CI validates schema 15, event idempotency, cross-app Awareness, memory candidates/consolidation, plugin manifests/read tools, Agent awareness permission isolation, scheduler lifecycle and the Cognition UI contract.

Context Engine CI validates relevance-based Memory/Knowledge/Contacts retrieval, context budgets, source-attribution privacy, prompt-injection framing, local-only hosted-provider refusal, paired-app permission ceilings and Agent Chat context behavior.

Knowledge Sync CI validates the Knowledge Sources module and folder-sync regression as a fast integration gate. Relay CI independently validates the deployable relay process, VP3 remote connector, Docker image and live container health.
