# HomeServer

HomeServer is a local-first private capability server for personal AI agents and authorized applications such as VP3.

The Windows desktop runtime runs on `127.0.0.1:4377`, stores durable state in SQLite, and provides a local Control Center for the primary agent, private chat, knowledge, memory, skills, tools, pairing, permissions and activity.

## Current v0.7 foundation

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
- **Optional read-only Agent Tool Use** for Ollama chat, disabled by default
- Owner-controlled per-chat Agent Tool budget of 1–3 calls
- Model-visible Agent Tools limited to `knowledge.search` and `memory.list`
- Browser-safe claim-token pairing with local owner approval
- Hashed application credentials and per-app capability permissions
- Local activity/audit trail
- Windows CI that tests database upgrades, security boundaries, Agent Brain behavior, direct tool execution, agent tool use, the packaged executable and installer

## Skills & Tools security model

HomeServer uses a typed, allowlisted tool registry instead of arbitrary local execution. The built-in tools are:

- `knowledge.search` — read-only, requires `tools.execute` + `knowledge.search`
- `memory.list` — read-only, requires `tools.execute` + `memory.read`
- `memory.write` — mutating, requires `tools.execute` + `memory.write`

Built-in skill manifests group those tools into **Local Research** and **Memory Manager**. Skills do not add permissions; they are manifests over the underlying tool capabilities.

The owner can disable any built-in tool globally from **Skills & Tools**. Tool runs record the tool name, source application, permission requirements, status, duration, result counts/IDs, and safe argument metadata such as query/content length. Raw search queries, returned knowledge excerpts, and memory bodies are not copied into `tool_runs`.

HomeServer intentionally includes **no shell, PowerShell, arbitrary HTTP, or unrestricted filesystem tool**.

## Agent Tool Use

v0.7 can optionally let the local Ollama model request safe read-only tools while composing an Agent Chat answer. This feature is separate from direct tool permissions and ships **disabled by default**.

When the owner enables **Agent read tools** in **My Agent**:

- the model may receive only `knowledge.search` and `memory.list` function schemas
- `memory.write` is never exposed to the model
- each tool request is routed back through the same audited HomeServer tool registry
- global tool-disable policies still apply
- paired apps still need `tools.execute` plus the underlying read permission before their chat can expose that tool
- the owner chooses a hard maximum of 1–3 executed tool calls per chat turn
- once the budget is exhausted, HomeServer requests a final Ollama answer without any tools attached
- tool result messages are used only inside the local model exchange and are not stored as conversation messages
- `agent_runs` stores only tool-call counts and tool-run IDs, while `tool_runs` keeps the existing content-safe audit metadata

An app with `agent.chat` but without `tools.execute` receives normal chat with no autonomous tools. An app with `tools.execute` but without `memory.read` cannot expose or execute `memory.list` through Agent Chat.

## Local model privacy

v0.7 supports Ollama as the first model provider. The configured URL must resolve to `localhost`, `127.0.0.1`, or `::1`; remote model-provider URLs are rejected. The default is `http://127.0.0.1:11434`, disabled until the owner selects a model and enables it.

HomeServer sends a bounded prompt to Ollama containing the primary agent instructions, up to six high-importance memory items, up to four relevant knowledge results, and up to eight recent conversation messages. Knowledge, memory, and tool-result excerpts are treated as untrusted supporting data rather than higher-priority instructions.

## Agent Chat

The local Control Center includes Agent Chat and persistent owner conversations. Paired applications with `agent.chat` use `POST /api/v1/chat`; their conversation history is isolated by application key.

Memory and knowledge are only added to a paired application's normal chat context when that app separately has `memory.read` and `knowledge.search` respectively. Agent Tool exposure has its own additional `tools.execute` requirement.

Each run is recorded locally with provider, model, source application, retrieved-context counts, optional tool-call count/tool-run IDs, duration and completion/failure state.

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

Then open HomeServer from the tray, configure the primary agent, use **Detect Ollama** to find installed local models, save/enable the provider, and open **Agent Chat** or **Skills & Tools**. Agent read tools remain off until explicitly enabled in **My Agent**.

## VP3 browser bridge

VP3 connects to the local loopback API through `claim-v1` pairing. The future credential returned to VP3 remains unusable until the user approves the short code locally. After approval, VP3 automatically detects readiness and uses the claim token as its bearer credential; no manual long-token copying is required.

VP3 can discover skills/tools with `GET /api/v1/skills` and `GET /api/v1/tools`, then invoke an allowed tool with `POST /api/v1/tools/{tool_key}/execute`. Direct tool execution never bypasses its underlying permission.

When Agent read tools are enabled by the HomeServer owner, a VP3 chat can also use only the read tools its token is independently authorized to use. VP3 cannot enable Agent Tool Use through its bearer token.

See [`connectors/vp3/README.md`](connectors/vp3/README.md) and [`connectors/vp3/client.js`](connectors/vp3/client.js).

## Build Windows distribution

```bash
pyinstaller HomeServer.spec --clean --noconfirm
```

CI also builds `HomeServerSetup.exe`, verifies SHA-256 hashes, launches the packaged executable against `/api/v1/health`, and publishes the `HomeServer-Windows` artifact.
