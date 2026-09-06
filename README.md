# HomeServer

HomeServer is a local-first private capability server for personal AI agents and authorized applications such as VP3.

The Windows desktop runtime runs a private FastAPI service on `127.0.0.1:4377`, stores durable data in SQLite, and opens a local control center for managing the primary agent, knowledge, memory, application pairing, permissions and activity.

## Current v0.2 foundation

- Windows tray application and PyInstaller `HomeServer.exe` build
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
- Windows CI with API/SQLite smoke test and executable build verification

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

## VP3 integration

VP3 does not read `homeserver.db`. It pairs with HomeServer and uses permission-checked local API endpoints. See [`connectors/vp3/README.md`](connectors/vp3/README.md).

## Build Windows executable

```bash
pyinstaller HomeServer.spec --clean --noconfirm
```

Output:

`dist/HomeServer.exe`

## Security model

HomeServer binds to loopback in the desktop runtime. Pairing codes are one-time and expire. Pairing codes and bearer tokens are stored only as SHA-256 hashes. Each connected application receives explicit capabilities that can be changed or revoked by the owner.

The next security phase will add owner-session protection for the local control surface, encrypted secret storage, backup/export encryption and a stronger automated pairing handshake so applications do not require manual token transfer.
