# KB4B — Verified Food Monster disconnection from VP3 HomeServer

This phase completes the other half of source-side revocation introduced in
the original Gelato Food Monster repository, PR #379.

## Owner-local disconnect

`DELETE /api/v1/control/food-monster/connections/{local_id}`

The HomeServer identifies the chosen installed Food Monster from the existing
per-source registry, reads that single connection's protected bearer credential,
and uses outbound HTTPS POST to the exact pinned public origin:

```http
POST /api/food-monster-vp3.php
Authorization: Bearer <per-connection credential>
Content-Type: application/json

{"action":"revoke"}
```

The source response must contain `schema=food-monster-vp3-v1`,
`revoked=true`, and the **exact expected instanceId and remote connectionId**.
Only then does HomeServer mark `remoteRevoked=true`. In every disconnect
path it destroys the local credential and removes the local connection row.

Successful response:

```json
{
  "disconnected": true,
  "remoteRevoked": true,
  "remoteRevocationRequired": false,
  "message": "Connection revoked on Food Monster and removed from HomeServer."
}
```

If the source is offline, the credential is missing, the bearer has already
been revoked, or the source response does not match the pinned IDs, HomeServer
still destroys its local credential and returns
`remoteRevocationRequired=true`. In that case the user must open the same
Food Monster instance's protected `/vp3-connections.php` owner page and
revoke the lingering connection there.

## Threat boundaries

- Remote HTTP calls are made only to the owner-approved public HTTPS origin.
- No general outbound agent network tool or Slack integration is introduced.
- Tokens are never returned to the chat canvas or stored in plain SQLite.
- One connected restaurant's bearer cannot revoke another restaurant.
- Remote identity mismatches and network errors never claim successful
  revocation.
- An existing assistant reply may still contain source-grounded facts in
  chat history; source revocation prevents **new** authorized live reads but
  does not retroactively erase prior replies.
- This build is exercised by deterministic connector tests; live hosted
  Gelato deployment integration tests are a distinct release acceptance step.

The current example origin `https://gelato.vp3.me` is supplied by the owner
at runtime, not hardcoded into this feature.
