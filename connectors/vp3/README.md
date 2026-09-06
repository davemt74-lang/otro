# VP3 Connector Contract

HomeServer is application-neutral. VP3 is an authorized client that connects over the local HomeServer API rather than reading the SQLite database directly.

## Local endpoint

Default HomeServer URL:

`http://127.0.0.1:4377`

## Pairing

1. VP3 requests pairing with `POST /api/v1/pairing/request`.
2. HomeServer returns a one-time code that expires after ten minutes.
3. The owner approves the code from the HomeServer control center.
4. HomeServer displays the new bearer token once.
5. VP3 stores that token in its own protected credential storage and sends it as `Authorization: Bearer <token>`.

Example request:

```json
{
  "app_key": "vp3",
  "app_name": "VP3",
  "permissions": [
    "agent.chat",
    "knowledge.search",
    "memory.read",
    "memory.write",
    "notifications.read"
  ]
}
```

HomeServer ignores unknown permission names. The owner can later disable individual permissions, pause the connection, or revoke it from Connected Apps.

## Current client endpoints

- `GET /api/v1/me` — identity and granted permissions.
- `GET /api/v1/agent` — primary agent profile; requires `agent.chat`.
- `GET /api/v1/knowledge?q=` — local knowledge search; requires `knowledge.search`.
- `GET /api/v1/memory` — durable memory; requires `memory.read`.
- `POST /api/v1/memory` — add durable memory; requires `memory.write`.

The HomeServer SQLite file is never an integration surface. All VP3 access must go through permission-checked API endpoints.
