# HomeServer v2.0 release baseline

## Working baseline carried forward

HomeServer v19.5 + VP3 Cloud v13.06 is the verified connection baseline.

The normal owner flow is:

1. Generate a one-time pairing key in VP3 Cloud.
2. Paste the key into HomeServer.
3. HomeServer stores the resulting HTTPS session securely using Windows DPAPI.
4. HomeServer maintains the outbound VP3 HTTPS relay automatically.
5. VP3 Cloud can queue requests over that session and HomeServer returns results through the same channel.
6. Reconnection after restart or temporary network loss is automatic.

The v13.06 Cloud fix is part of the baseline: schema/DDL work must stay outside the active MySQL polling transaction and internal poll failures must not be reported as authentication failures.

## Versioning rule

Starting with this release, the HomeServer application and the VP3 Cloud HomeServer package use one shared product version.

Current shared version: **2.3**

Protocol and schema identifiers such as `v1300`, `v1200`, migration numbers, API filenames, and database schema versions remain independent internal compatibility identifiers. They are not product release numbers and must not be renamed merely to match the product version.

## Windows packaging contract

HomeServer v2.0 keeps the exact working v19.5 Windows packaging architecture:

- PyInstaller builds the single `HomeServer.exe`.
- Inno Setup builds `HomeServerSetup.exe`.
- Inno `AppId` remains `{F94F980E-7B18-4FA3-A9B8-75A2EDE04777}`.
- Install path remains `%LOCALAPPDATA%\Programs\HomeServer`.
- Start menu, optional desktop shortcut, startup registry entry, and post-install launch behavior remain unchanged.
- Private HomeServer data stays outside the installer payload and must survive upgrades.
- No launcher, bootstrapper, installer technology, EXE layout, or ZIP layout change is allowed unless a concrete defect requires it.

The v2.0 release must prove upgrade takeover from the installed v19.5 line.

## Release artifact contract

The Windows CI artifact contains:

- `HomeServer.exe`
- `HomeServerSetup.exe`
- `SHA256SUMS.txt`
- `RELEASE.json`

The user-facing ZIP is named `homeserver-v2.0.zip`.

The matching Cloud deploy ZIP is named `vp3-cloud-v2.0.zip`.
