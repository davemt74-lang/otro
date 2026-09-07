# HomeServer

HomeServer is a local-first private capability server for personal AI agents and authorized applications such as VP3.

The Windows desktop runtime runs on `127.0.0.1:4377`, stores durable state in SQLite, and provides a local Control Center for the primary agent, private chat, knowledge, memory, pairing, permissions and activity.

## Current v0.5 foundation

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
- Browser-safe claim-token pairing with local owner approval
- Hashed application credentials and per-app capability permissions
- Local activity/audit trail
- Windows CI that tests database upgrades, security boundaries, Agent Brain behavior, the packaged executable and installer

## Local model privacy

v0.5 supports Ollama as the first model provider. The configured URL must resolve to `localhost`, `127.0.0.1`, or `::1`; remote model-provider URLs are rejected. The default is `http://127.0.0.1:11434`, disabled until the owner selects a model and enables it.

HomeServer sends a bounded prompt to Ollama containing the primary agent instructions, up to six high-importance memory items, up to four relevant knowledge results, and up to eight recent conversation messages. Knowledge and memory excerpts are explicitly treated as supporting data rather than higher-priority instructions.

## Agent Chat

The local Control Center includes Agent Chat and persistent owner conversations. Paired applications with `agent.chat` use `POST /api/v1/chat`; their conversation history is isolated by application key.

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

Then open HomeServer from the tray, configure the primary agent, use **Detect Ollama** to find installed local models, save/enable the provider, and open **Agent Chat**.

## VP3 browser bridge

VP3 connects to the local loopback API through `claim-v1` pairing. The future credential returned to VP3 remains unusable until the user approves the short code locally. After approval, VP3 automatically detects readiness and uses the claim token as its bearer credential; no manual long-token copying is required.

See [`connectors/vp3/README.md`](connectors/vp3/README.md) and [`connectors/vp3/client.js`](connectors/vp3/client.js).

## Build Windows distribution

```bash
pyinstaller HomeServer.spec --clean --noconfirm
```

CI also builds `HomeServerSetup.exe`, verifies SHA-256 hashes, launches the packaged executable against `/api/v1/health`, and publishes the `HomeServer-Windows` artifact.
