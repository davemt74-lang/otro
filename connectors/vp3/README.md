# VP3 Connector Contract

HomeServer is application-neutral. VP3 is an authorized client that connects over the user's local HomeServer API rather than reading SQLite or local files directly.

## Local endpoint

Default:

`http://127.0.0.1:4377`

VP3 should first call `GET /api/v1/capabilities`. A compatible v0.4+ HomeServer reports `pairing_protocol: "claim-v1"`.

## Browser local-network access

A hosted VP3 page connects from HTTPS to the user's loopback HomeServer. Current Chromium browsers may ask the user for permission to access services on the local network. VP3 should explain that the connection is to the user's own HomeServer and let the browser present its normal permission prompt.

HomeServer allows browser CORS requests only from configured origins. Defaults:

- `https://vp3.me`
- `https://www.vp3.me`

Additional development or deployment origins can be supplied locally with `HOMESERVER_ALLOWED_ORIGINS` as a comma-separated list. Do not use a wildcard origin.

## Claim-v1 pairing

The v0.4 flow eliminates manual bearer-token copying.

1. VP3 calls `POST /api/v1/pairing/request`.
2. HomeServer returns a short one-time `code`, opaque `request_id`, high-entropy `claim_token`, accepted permissions and expiry.
3. VP3 keeps `request_id` and `claim_token` while pairing is pending.
4. The user opens HomeServer locally and approves the short code.
5. VP3 polls `POST /api/v1/pairing/status` with `request_id` and `claim_token`.
6. When `ready` is `true`, that same `claim_token` becomes the paired application's bearer token.
7. VP3 sends `Authorization: Bearer <claim_token>` to protected client APIs.

The claim token cannot authenticate before local owner approval. HomeServer stores only its SHA-256 hash.

### Request

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

### Status

```json
{
  "request_id": "...",
  "claim_token": "..."
}
```

Pending returns `{"status":"pending","ready":false,...}`. After local approval it returns `{"status":"approved","ready":true,...}`.

Re-pairing is authoritative: permissions omitted from the new request are revoked, and the old application token stops authenticating.

## Browser helper

`connectors/vp3/client.js` provides `VP3HomeServerConnector`:

```html
<script src="/path/to/client.js"></script>
<script>
  const homeServer = new VP3HomeServerConnector();
  await homeServer.pair(
    VP3HomeServerConnector.DEFAULT_PERMISSIONS,
    {
      onCode(code) {
        // Show: Approve code ABCD-EFGH in HomeServer
        console.log(code);
      }
    }
  );

  const identity = await homeServer.me();
  const knowledge = await homeServer.searchKnowledge('merchant partnerships');
</script>
```

The helper intentionally does not choose persistent credential storage for VP3. VP3 should decide whether the paired token lives in memory, session storage, encrypted local storage, or another protected credential mechanism appropriate to its architecture.

## Protected client endpoints

- `GET /api/v1/me` — identity and granted permissions.
- `GET /api/v1/agent` — primary agent profile; requires `agent.chat`.
- `GET /api/v1/knowledge?q=` — indexed local knowledge search; requires `knowledge.search`.
- `GET /api/v1/memory` — durable memory; requires `memory.read`.
- `POST /api/v1/memory` — add durable memory; requires `memory.write`.

Owner-only `/api/v1/control/*` routes and `/api/v1/pairing/approve` remain protected by the local HomeServer owner session. A VP3 browser connection cannot use those routes merely because it can reach loopback.
