# HomeServer v2.1 — Shared Agent Fabric & Connection Cognition

## Release baseline

Product version: **2.1**

HomeServer v2.1 upgrades directly from the verified v2.0 release and preserves the exact v2.0 Windows packaging contract:

- PyInstaller builds the single `HomeServer.exe`.
- Inno Setup builds `HomeServerSetup.exe`.
- Inno AppId remains `{F94F980E-7B18-4FA3-A9B8-75A2EDE04777}`.
- Install location, shortcuts, startup behavior, private data location and post-install launch behavior remain unchanged.
- The CI upgrade gate explicitly exercises **v2.0 → v2.1** takeover.
- Distribution layout remains `HomeServer.exe`, `HomeServerSetup.exe`, `SHA256SUMS.txt`, and `RELEASE.json`.

## Shared Agent fabric

Once VP3 Cloud and HomeServer are paired, they operate as one logical Agent environment without merging or overwriting their native databases.

The v2.1 exchange covers:

- Agent Brain / memory
- Knowledge
- Contacts
- Tasks and commitments
- Notifications
- HomeServer capabilities and connection state

Each source remains authoritative for its own records. Shared records carry explicit source provenance. HomeServer stores VP3 Cloud data in a dedicated Cloud-owned mirror cache rather than rewriting local memory, contacts, knowledge, tasks, or notifications.

The VP3 paired-app identity is permission gated. The shared exchange requires all of:

- `memory.read`
- `knowledge.search`
- `contacts.read`
- `tasks.read`
- `notifications.read`

Other paired apps do not automatically inherit the VP3 Cloud mirror.

## Round-trip verification

The authenticated `system.ping` operation proves:

VP3 Cloud → HTTPS request queue → HomeServer worker → local dispatch → HTTPS result → VP3 Cloud.

The response includes the HomeServer version, device identity, received timestamp and nonce echo. Cloud records request ID, last successful round-trip timestamp and measured latency.

## Connection cognition

HomeServer connection state is Agent Brain context.

Material transitions are recorded; routine heartbeat refreshes are deduplicated.

Canonical transitions include:

- `homeserver.connected`
- `homeserver.disconnected`
- `homeserver.status_changed`
- round-trip verification

A disconnect or connection error invalidates stale round-trip verification. VP3 Cloud creates a priority `homeserver_needs_attention` user notification, and the existing Agent Chat attention channel promotes it to the conversation.

Agent Chat polls live HomeServer state through the normal attention cycle, so outage detection does not depend on the HomeServer Settings page being open.

## Profile Agent

The user's Profile Agent participates in the same HomeServer fabric, but the existing Profile Agent privacy boundary remains authoritative.

HomeServer data is denied to the public Profile Agent by default. The owner must explicitly allow the corresponding resource type through the central Agent data policy:

- HomeServer Agent Brain
- HomeServer Knowledge
- HomeServer Contacts
- HomeServer Tasks

Profile Agent HomeServer retrieval is logged through the existing data-usage audit path. HomeServer notifications are not exposed to public Profile Agent conversations.

## Compatibility identifiers

The product version is 2.1 on both VP3 Cloud and HomeServer. Existing internal identifiers such as `v1300`, `v1200`, migration numbers, and older capability schema identifiers remain unchanged unless their protocols themselves change.
