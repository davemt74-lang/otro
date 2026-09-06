# HomeServer

HomeServer is a local-first private capability server for personal AI agents and authorized applications such as VP3.

The Windows desktop runtime runs a private FastAPI service on `127.0.0.1:4377`, stores durable data in SQLite, and opens a local control center for managing the primary agent, knowledge, memory, application pairing, permissions and activity.

## Current v0.2 foundation

- Windows tray application and PyInstaller `HomeServer.exe` build
- Per-user `HomeServerSetup.exe` installer
- Optional Start-with-Windows and Desktop shortcuts
- Local control center at `http://127.0.0.1:4377/`
- SQLite database with WAL mode and foreign keys
- Primary agent configuration
- Local knowledge records and search
- Durable agent memory
- One-time application pairing
- Hashed bearer tokens; raw tokens are not stored
- Per-application capability permissions
- Pause/revoke controls for connected applications
- Local activity/audit log
- VP3 connector contract
- Windows CI that tests the source runtime and the packaged executable before publishing distribution artifacts

## Windows distribution

The Windows workflow produces:

- `HomeServer.exe` — portable desktop application
- `HomeServerSetup.exe` — per-user installer that does not require administrator rights
- `SHA256SUMS.txt` — SHA-256 checksums for both binaries

The installer places HomeServer under the current user's Local App Data programs directory. A Start Menu shortcut is created automatically; Desktop and Start-with-Windows shortcuts are optional installer tasks.

## Data location

By default HomeServer stores its database under:

`~/.homeserver/homeserver.db`

Set `HOMESERVER_DATA_DIR` to use another local directory.

Documents and other large source files should remain on disk. SQLite stores structured records, extracted/indexable content, metadata, permissions and relationships rather than becoming a general file container.

## Run locally

```bash
python -m venv .venv
.venv\\Scripts\\activate
pip install -r requirements.txt
python desktop/launcher.py
```

The tray menu opens the HomeServer control center or API documentation.

For automated packaged-runtime verification, `HomeServer.exe --headless` runs the secured local service without creating a tray icon.

## VP3 integration

VP3 does not read `homeserver.db`. It pairs with HomeServer and uses permission-checked local API endpoints. See [`connectors/vp3/README.md`](connectors/vp3/README.md).

## Build Windows executable

```bash
pyinstaller HomeServer.spec --clean --noconfirm
```

Output:

`dist/HomeServer.exe`

The CI workflow then builds the Inno Setup installer from `installer/HomeServer.iss`.

## Security model

HomeServer binds to loopback in the desktop runtime. Pairing codes are one-time and expire. Pairing codes and application bearer tokens are stored only as SHA-256 hashes. Each connected application receives explicit capabilities that can be changed or revoked by the owner.

Owner control actions use a separate process-local authorization boundary. Opening HomeServer from the tray exchanges an ephemeral bootstrap token for an HttpOnly, SameSite=Strict browser session cookie. Pairing approval and `/api/v1/control/*` routes are blocked without that owner session, so a local client cannot request and approve its own pairing.

Future hardening includes encrypted secret storage, signed Windows binaries, encrypted backup/export, and a stronger automated pairing handshake for remote or cross-device clients.
