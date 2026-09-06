# HomeServer

HomeServer is a local-first private capability server for personal AI agents and authorized applications such as VP3.

## v0.1 foundation

- FastAPI local service bound to `127.0.0.1:4377`
- SQLite database with WAL mode, foreign keys, schema versioning, agents, memory, knowledge, paired apps, permissions, notifications and activity history
- One-time application pairing codes
- SHA-256 hashed pairing codes and bearer tokens at rest
- Permission-scoped application access
- Initial VP3-compatible API contract
- Windows system-tray launcher
- PyInstaller Windows executable build
- Windows CI that compiles the code, initializes SQLite and builds `HomeServer.exe`

## Local development

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 4377
```

Open `http://127.0.0.1:4377/docs` for the current API control surface.

HomeServer stores runtime data in `%USERPROFILE%\.homeserver` by default. Set `HOMESERVER_DATA_DIR` to override that location.

## Pairing contract

1. An app requests pairing at `POST /api/v1/pairing/request` with an app key, display name and requested permissions.
2. HomeServer returns a temporary pairing code that expires after ten minutes.
3. The local owner approves that code at `POST /api/v1/pairing/approve`.
4. HomeServer returns the raw bearer token once. Only its hash is stored locally.
5. The paired app uses `Authorization: Bearer <token>` for authorized API calls.

Initial permissions are `agent.chat`, `knowledge.search`, `memory.read`, `memory.write`, and `notifications.read`.

## Windows build

```powershell
pyinstaller HomeServer.spec --clean --noconfirm
```

The resulting executable is created at `dist/HomeServer.exe`.

## Architecture

VP3 is an authorized client of HomeServer, not the owner of HomeServer data. The SQLite database and private source files remain under local user control; connected products receive only the capabilities explicitly granted to them.
