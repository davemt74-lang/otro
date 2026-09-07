# HomeServer

HomeServer is a local-first private capability server for personal AI agents and authorized applications such as VP3.

The Windows desktop runtime runs a private FastAPI service on `127.0.0.1:4377`, stores durable data in SQLite, and opens a local control center for managing the primary agent, knowledge, memory, application pairing, permissions and activity.

## Current v0.3 foundation

- Windows tray application and packaged `HomeServer.exe`
- Per-user `HomeServerSetup.exe` installer with optional Start-with-Windows
- Local control center at `http://127.0.0.1:4377/`
- SQLite database with WAL mode, foreign keys and versioned migrations
- Primary agent configuration
- Durable agent memory
- Local knowledge notes and document ingestion
- Private SQLite FTS5 knowledge index with chunked search
- TXT, Markdown, JSON, CSV, HTML, PDF and DOCX text extraction
- SHA-256 document duplicate detection
- One-time application pairing
- Hashed bearer tokens; raw tokens are not stored
- Per-application capability permissions
- Pause/revoke controls for connected applications
- Local activity/audit log
- VP3 connector contract
- Windows CI that tests database upgrades, secured APIs, the packaged executable and installer

## Data location

By default HomeServer stores its runtime data under:

`~/.homeserver/`

The SQLite database is:

`~/.homeserver/homeserver.db`

Imported knowledge files are copied locally to:

`~/.homeserver/knowledge/files/`

Set `HOMESERVER_DATA_DIR` to use another local directory.

## Knowledge ingestion

The control center can import local files up to 10 MB each. HomeServer keeps the imported source file on the user's machine, extracts readable text locally, chunks that text, and indexes the chunks in SQLite FTS5.

Supported v0.3 file types:

- `.txt`
- `.md` / `.markdown`
- `.json`
- `.csv`
- `.html` / `.htm`
- `.pdf`
- `.docx`

Identical imported files are detected by SHA-256 and are not stored twice. Existing pre-v0.3 knowledge records are indexed automatically after the migration is applied.

## Run locally

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python desktop/launcher.py
```

The tray menu opens the secured HomeServer control center or API documentation.

## VP3 integration

VP3 does not read `homeserver.db` or HomeServer's local files. It pairs with HomeServer and uses permission-checked local API endpoints. The `knowledge.search` capability returns indexed knowledge through the API without exposing direct filesystem access.

See [`connectors/vp3/README.md`](connectors/vp3/README.md).

## Build Windows distribution

```bash
pyinstaller HomeServer.spec --clean --noconfirm
```

The GitHub Actions workflow additionally builds the Inno Setup installer and publishes a `HomeServer-Windows` artifact containing:

- `HomeServer.exe`
- `HomeServerSetup.exe`
- `SHA256SUMS.txt`

## Security model

HomeServer binds to loopback in the desktop runtime. Pairing codes are one-time and expire. Pairing codes and bearer tokens are stored only as SHA-256 hashes. Each connected application receives explicit capabilities that can be changed or revoked by the owner.

Owner-only control routes require an ephemeral HomeServer owner session issued by the desktop tray runtime. A local client may request pairing, but it cannot approve its own request.

Imported documents remain local. Connected apps can query extracted/indexed knowledge only when the owner grants `knowledge.search`.
