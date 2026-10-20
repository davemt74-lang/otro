# KB3 — OTRO private self-hosted Food Monster connections

This phase adds local-only owner control routes for connecting multiple
independently self-hosted Food Monster instances. It uses the exact
`food-monster-vp3-v1` protocol merged into `davemt74-lang/gelato` KB2
(PR #378). It does NOT target `pizzathehut`.

## Supported installation addresses

The user chooses any **publicly resolvable HTTPS domain/subdomain** during
connection. The current deployment is `https://gelato.vp3.me`, but no URL is
hardcoded. Authentication requires a separately approved code from the
corresponding Food Monster owner page `/vp3-connections.php`.

## Connection flow

1. The HomeServer owner logs in to the self-hosted Food Monster's protected
   `/vp3-connections.php`, selects read scopes, and generates a 10-minute
   single-use code.
2. Local HomeServer receives `origin`, `pairing_code`, `label` from the
   owner-controlled endpoint. The origin is strictly normalized: HTTPS,
   no path, port, credentials, query or fragment, no IP literals or local
   naming; DNS must resolve exclusively to public addresses.
3. HomeServer reads that origin's public metadata-only discovery and
   validates the protocol, stable instance UUID and knowledge capability.
4. At the **same** origin, it atomically redeems the one-time code. The
   returned identity must match the discovery identity exactly.
5. HomeServer persists only the origin, instance ID, remote connection ID,
   label, scopes and health metadata in SQLite feature schema.
6. The bearer token is stored separately in the security folder under
   Windows DPAPI protection. Other supported local platforms use a
   permission-restricted, atomically replaced file.
7. Each source is queried independently using outbound HTTPS POST with
   no redirects, proxy environment use, or unverified TLS.
8. Search results are attributed to their installation. There is no
   automatic cross-restaurant access.

## Local API contract (only owner-local, never Remote Bridge)

- `GET /api/v1/control/food-monster/connections`: list public connection
  metadata, never credential values.
- `POST /api/v1/control/food-monster/connections`: connect with JSON
  `{"origin":"https://gelato.vp3.me","pairing_code":"fmlink_...","label":"Gelato"}`
- `POST /api/v1/control/food-monster/connections/{local_id}/search`: JSON
  `{"q":"pizza","type":"menu","limit":10}`
- `DELETE /api/v1/control/food-monster/connections/{local_id}`: delete the
  local connection record and its stored secret. **IMPORTANT: Food Monster
  KB2 does not yet support authenticated remote self-revocation.** The
  response explicitly says that the source-side admin must revoke the link
  separately on the Food Monster owner page.

No new endpoint is added to the paired-app bearer namespace or Remote
Bridge, and no credentials are placed in Agent Chat prompts, exported
inventory, SQLite, or ordinary application logs.

## Security and current limitations

- HomeServer `/api/v1/control` is the existing loopback-only owner surface.
  This must **not** be published on an untrusted LAN or to the internet.
- HTTPS origin parsing, DNS public-address checks and redirect blocking
  reduce SSRF risk. A future hardened transport should pin the validated
  DNS resolution through the TLS connection to eliminate any
  DNS-rebinding/time-of-check race.
- This phase includes live **read-only** menu/recipe query endpoints; it
  does not yet inject them into Agent Chat or synchronize them into the
  persistent Knowledge index.
- Owner source revocation immediately makes live queries fail. Existing
  cached knowledge is not imported in this phase.
- Local disconnect cannot revoke a still-valid source-side credential;
  always revoke it from the Food Monster owner UI until the follow-up
  bidirectional revocation protocol is implemented.
- The first version does not provide a public-cloud pairing redirect,
  SaaS directory or an MCP server.

## Acceptance

Idempotent feature schema; independent-source isolation; strict URL validation; token
secrecy; per-source grant validation; scoped search; local disconnect and
explicit source-side revocation warning. Dedicated CI exercises the tests
without real restaurant credentials.
