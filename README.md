# HomeServer

HomeServer is a local-first private capability server for personal AI agents and authorized applications such as VP3.

The Windows desktop runtime runs on `127.0.0.1:4377`, stores durable state in SQLite, and provides a local Control Center for the primary agent, private chat, knowledge, memory, skills, tools, pairing, permissions and activity.

## Current v0.6 foundation

- Windows tray application and packaged `HomeServer.exe`
- Per-user `HomeServerSetup.exe` installer with optional Start-with-Windows
- SQLite WAL database with versioned, transactional migrations
- Persistent primary agent configuration
- **Agent Brain** with persistent conversations and run tracking
- Local-only Ollama provider with loopback URL enforcement
- Automatic bounded context assembly from agent instructions, recent conversation history, durable memory and FTS knowledge
- App-isolated conversations for VP3 and other paired clients
- Durable agent memory
- Local TXT, Markdown, JSON, CSV, HTML, PDF and DOCX knowledge ingestion
- SQLite FTS5 chunked knowledge search and SHA-256 duplicate detection
- **Allowlisted Skills & Tools capability layer**
- Built-in `knowledge.search`, `memory.list`, and `memory.write` tools
- `tools.execute` gate plus each tool's underlying data permission
- Owner-controlled global tool enable/disable policies
- Dedicated local tool-run audit records that do not duplicate raw search text or memory bodies
- Browser-safe claim-token pairing with local owner approval
- Hashed application credentials and per-app capability permissions
- Local activity/audit trail
- Windows CI that tests database upgrades, security boundaries, Agent Brain behavior, tool execution, the packaged executable and installer

## Skills & Tools security model

v0.6 introduces a typed, allowlisted tool registry instead of arbitrary local execution. The first tools are:

- `knowledge.search` — read-only, requires `tools.execute` + `knowledge.search`
- `memory.list` — read-only, requires `tools.execute` + `memory.read`
- `memory.write` — mutating, requires `tools.execute` + `memory.write`

Built-in skill manifests group those tools into **Local Research** and **Memory Manager**. Skills do not add permissions; they are manifests over the underlying tool capabilities.

The owner can disable any built-in tool globally from **Skills & Tools**. Tool runs record the tool name, source application, permission requirements, status, duration, result counts/IDs, and safe argument metadata such as query/content length. Raw search queries, returned knowledge excerpts, and memory bodies are not copied into `tool_runs`.

v0.6 intentionally includes **no shell, PowerShell, arbitrary HTTP, or unrestricted filesystem tool**. Model-driven autonomous tool calling is deferred until this execution boundary is proven independently.

## Local model privacy

v0.6 supports Ollama as the first model provider. The configured URL must resolve to `localhost`, `127.0.0.1`, or `::1`; remote model-provider URLs are rejected. The default is `http://127.0.0.1:11434`, disabled until the owner selects a model and enables it.

HomeServer sends a bounded prompt to Ollama containing the primary agent instructions, up to six high-importance memory items, up to four relevant knowledge results, and up to eight recent conversation messages. Knowledge and memory excerpts are explicitly treated as untrusted supporting data rather than higher-priority instructions.

## Agent Chat

The local Control Center includes Agent Chat and persistent owner conversations. Paired applications with `agent.chat` use `POST /api/v1/chat`; their conversation history is isolated by application key.

Memory and knowledge are only added to a paired application's chat context when that app separately has `memory.read` and `knowledge.search` respectively.

Each run is recorded locally with provider, model, source application, retrieved-context counts, duration and completion/failure state.

## Data location

By default HomeServer stores runtime data under `~/.homeserver/`:

- SQLite: `~/.homeserver/homeserver.db`
- imported knowledge files: `~/.homeserver/knowledge/files/`

Set `HOMESERVER_DATA_DIR` to use another local directory.

## Run locally

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python desktop/launcher.py
```

Then open HomeServer from the tray, configure the primary agent, use **Detect Ollama** to find installed local models, save/enable the provider, and open **Agent Chat** or **Skills & Tools**.

## VP3 browser bridge

VP3 connects to the local loopback API through `claim-v1` pairing. The future credential returned to VP3 remains unusable until the user approves the short code locally. After approval, VP3 automatically detects readiness and uses the claim token as its bearer credential; no manual long-token copying is required.

VP3 can discover skills/tools with `GET /api/v1/skills` and `GET /api/v1/tools`, then invoke an allowed tool with `POST /api/v1/tools/{tool_key}/execute`. Tool execution never bypasses its underlying permission.

See [`connectors/vp3/README.md`](connectors/vp3/README.md) and [`connectors/vp3/client.js`](connectors/vp3/client.js).

## Build Windows distribution

```bash
pyinstaller HomeServer.spec --clean --noconfirm
```

CI also builds `HomeServerSetup.exe`, verifies SHA-256 hashes, launches the packaged executable against `/api/v1/health`, and publishes the `HomeServer-Windows` artifact.
