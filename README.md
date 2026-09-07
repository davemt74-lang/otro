# HomeServer

HomeServer is a local-first private capability server for personal AI agents and explicitly authorized applications such as VP3.

The Windows desktop runtime listens on `127.0.0.1:4377`, stores durable state in SQLite, and provides local owner surfaces for Agent Chat, AGENT BRAIN, knowledge, memory, contacts, tasks/reminders, notifications, skills, tools, approvals, pairing, permissions, token usage history, backup/restore, setup, diagnostics and the optional outbound Remote Bridge.

## Current v0.14 foundation

- Packaged Windows `HomeServer.exe` and per-user `HomeServerSetup.exe`
- Agent Chat as the default owner UI
- ChatGPT-style centered conversation workspace and composer
- Live conversation history in the main sidebar with rename/delete actions
- Persistent private AGENT BRAIN shared only through explicit permissions
- Local-first inference routing across:
  - local Ollama
  - Claude / Anthropic using the user's API key
  - OpenAI using the user's API key
  - OpenRouter using the user's API key
- ElevenLabs credential storage for voice-capability integration
- Windows DPAPI protection for owner, Remote Bridge and provider credentials
- VP3-readable inference availability so the cloud site can prefer HomeServer before paid cloud compute
- Explicit compute-source reporting: `homeserver_local`, `user_provider`, or `vp3_cloud`
- VP3 cloud-token usage ledger with idempotent cloud charge events and balance history
- SQLite WAL database with transactional migrations (schema 12)
- Durable memory and local document ingestion/FTS search
- Private Contacts & Relationship Context
- First-class Tasks, due dates, priorities and recurring reminders
- Canonical notification inbox with read/dismiss state
- Allowlisted Skills & Tools with content-safe auditing
- Optional bounded Agent Tool Use, disabled by default
- Approval-gated memory-write and task-create proposals, separately disabled by default
- Browser-safe claim-token pairing for VP3 and future clients
- Optional outbound-only Remote Bridge, disabled by default
- Deployable trusted Remote Relay service and VP3 remote connector
- Local Backup & Restore with SHA-256 manifests and startup-time rollback protection
- Supervised tray runtime with graceful shutdown/restart and per-data-directory single-instance enforcement

## Local-first VP3 inference model

The primary v0.14 integration contract is intentionally simple:

1. VP3 asks HomeServer whether usable agent inference is available.
2. If HomeServer has a ready local Ollama model, VP3 can route the request to HomeServer. No VP3 cloud tokens are debited.
3. If HomeServer has a user-configured Anthropic, OpenAI or OpenRouter provider, VP3 can still route the request through HomeServer. The user's provider account is used and no VP3 cloud tokens are debited.
4. If HomeServer reports that no inference path is ready, VP3 may use its paid cloud subscription / purchased token balance.
5. VP3 cloud usage can be synchronized back to HomeServer as an idempotent usage event for the owner's Token Usage History.

`GET /api/v1/capabilities` includes a safe inference summary:

```json
{
  "inference": {
    "available": true,
    "selected_provider": "ollama",
    "model": "example-model",
    "compute_source": "homeserver_local",
    "cloud_fallback_required": false
  }
}
```

A paired app with `agent.chat` can also call:

```text
GET /api/v1/inference/status
```

A HomeServer-served chat returns `compute_source`, provider/model usage, and `cloud_tokens_debited: 0`. This makes VP3 billing decisions explicit instead of inferring them from model names.

HomeServer does not silently call VP3 paid cloud inference itself. The VP3 cloud application remains responsible for deciding when to use its billable fallback and for reporting the resulting billable usage event.

## Token usage history

HomeServer stores inference history in `inference_usage_events`.

Each row can include:

- stable event id
- source application
- compute source
- provider and model
- request kind
- prompt/input tokens
- completion/output tokens
- total model tokens
- VP3 billable token debit
- reported VP3 token balance after the charge
- timestamp

HomeServer/local and user-provider requests are recorded with a VP3 billable debit of zero. VP3 cloud requests use `compute_source=vp3_cloud` and can carry the purchased-token debit and post-charge balance.

Cloud synchronization is retry-safe because the event id is unique. Replaying the same VP3 charge notification does not debit or count it twice in HomeServer history.

Paired-app permissions:

- `usage.read`
- `usage.write`

Endpoints:

```text
GET  /api/v1/usage
POST /api/v1/usage/cloud
GET  /api/v1/control/usage
```

The owner UI exposes **Token Usage History** from the bottom user menu.

## AGENT BRAIN and provider credentials

AGENT BRAIN contains the primary agent identity/instructions, inference routing, provider credentials and bounded Agent Tool policy.

Provider credentials supported in v0.14:

- Anthropic / Claude
- OpenAI
- OpenRouter
- ElevenLabs

On Windows, provider keys are written to the HomeServer security directory using Windows DPAPI for the current Windows user. They are not stored in SQLite, returned through control APIs, or copied into activity/tool audit records. Control APIs expose only configured/not-configured state and a last-four suffix for recognition.

Inference selection defaults to **Auto — local first**. The automatic order is:

1. Ollama local
2. Anthropic
3. OpenAI
4. OpenRouter

The owner can explicitly prefer a configured provider. If that preferred provider is unavailable, HomeServer falls back through the ready HomeServer providers; if none are ready it reports `cloud_fallback_required=true` to VP3.

ElevenLabs is credentialed in AGENT BRAIN for voice features but is not used as the text inference provider.

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
- `security\provider-credentials.dat` — Windows-protected provider API keys
- `runtime\bootstrap-state.json` — non-secret startup/migration diagnostics

If a legacy `~/.homeserver` directory contains data and LocalAppData does not, the Windows launcher moves the complete legacy directory before importing the application runtime. If both locations contain data, HomeServer does not merge or delete either location; LocalAppData remains active and Setup & Diagnostics reports the conflict.

`HOMESERVER_DATA_DIR` overrides the default for development/testing or an explicitly managed local location.

HomeServer acquires a named Windows mutex before applying restores, opening SQLite or binding port `4377`. Quit and Restart request graceful shutdown, close the Remote Bridge and local server, release the instance mutex and only then relaunch when needed.

## Owner security and recovery

The persistent owner bootstrap secret, Remote Bridge device credential and provider API credentials are protected with Windows DPAPI for the current Windows user. The owner browser session itself remains process-local and is stored only in an HttpOnly, SameSite=Strict cookie; restart invalidates it.

If normal SQLite initialization fails, HomeServer starts a restricted local recovery application. Recovery can validate/stage a known-good backup, preserve unreadable live state for forensic recovery and request a supervised restart. Paired apps, Agent APIs and the Remote Bridge are not started in recovery mode.

## Tasks, reminders and notifications

Private tasks live in the same SQLite brain as memory, knowledge and contacts. A task can include title/description, status, priority, due/reminder dates, optional linked contact, provenance and one-time/daily/weekly/monthly recurrence.

The local scheduler creates canonical notifications without contacting cloud services. It does not execute arbitrary tasks, tools, HTTP calls, shell commands or workflows.

Owner workspace:

```text
/tasks
```

Paired-app capabilities:

- `tasks.read`
- `tasks.write`
- `notifications.read`

Agent/model-driven task creation remains approval-gated. A model can propose a task only when write proposals are enabled; the owner must approve it locally before execution.

## Skills, tools and approval-gated actions

Built-in tools remain allowlisted:

- `contacts.search`
- `knowledge.search`
- `memory.list`
- `memory.write`
- `tasks.list`
- `notifications.list`
- `tasks.create`

Model-readable tools are permission-filtered. Direct model writes are not exposed. When owner-controlled write proposals are enabled, the model can request a pending memory write or task creation for explicit local approval.

HomeServer intentionally exposes no shell, PowerShell, arbitrary HTTP or unrestricted filesystem tool.

## Remote Bridge and deployable relay

HomeServer's optional Remote Bridge makes an outbound connection so a broker-mediated client such as VP3 can reach the user's HomeServer without router forwarding or a public localhost listener.

Production broker URLs require `wss://`; `ws://` is accepted only for loopback development/test hosts. The bridge has an explicit operation map and is not an arbitrary TCP/HTTP tunnel. Protected operations are dispatched back through HomeServer's existing bearer-authenticated local APIs, so canonical paired-app permissions remain authoritative.

The existing remote `capabilities` operation now carries the HomeServer inference summary, allowing VP3 to make its local/user-provider versus paid-cloud routing decision over the same Remote Bridge contract.

The repository also contains the deployable Remote Relay service under `relay/`. Current trust model: **trusted WSS relay**. TLS protects transport to the relay, but the relay can see relayed application payloads and HomeServer bearer credentials. This is not end-to-end payload encryption.

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

Windows lifecycle, diagnostics, backup/restore, recovery and provider-secret control are intentionally absent from the paired-app permission catalog.

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

Windows CI validates schema upgrades, Agent Brain/inference routing, provider secret non-disclosure, usage idempotency, token history, conversation rename/delete contracts, JavaScript syntax, data bootstrap, single-instance behavior, owner security, recovery mode, Agent Tools/Approvals/Contacts/Tasks/Backup regressions, Remote Bridge security/protocol, packaged EXE startup, packaged restart/session rotation/shutdown, staged restore, installer upgrade preservation and distribution hashes.

Relay CI independently validates the deployable relay process, VP3 remote connector, Docker image and live container health.
