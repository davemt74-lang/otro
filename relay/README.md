# HomeServer Remote Relay

The Remote Relay is the deployable internet-facing counterpart to HomeServer v0.12's outbound Remote Bridge.

It is intentionally a **trusted relay**, not a public proxy and not an end-to-end encrypted payload layer.

## Architecture

```text
Remote VP3/browser
       |
       | HTTPS relay session + scoped HomeServer app token
       v
HomeServer Remote Relay
       ^
       | outbound WSS initiated by HomeServer
       |
Windows HomeServer
       |
       +-- canonical pairing / permissions / Agent APIs
```

The relay has two separate credentials:

1. **Relay session token** — identifies which claimed HomeServer the remote client may reach.
2. **HomeServer paired-app token** — determines which HomeServer capabilities that app may use.

The relay token never grants `agent.chat`, contacts, knowledge, memory, tools or any other HomeServer permission by itself. Protected requests are forwarded back to HomeServer with the paired-app token and HomeServer remains the final authorization authority.

## Device enrollment

HomeServer's Remote Bridge device ID is derived from its random device secret:

```text
hs- + first 24 hex characters of SHA-256(device_secret)
```

The relay verifies that relationship before registering or authenticating a device. This prevents another client from pre-registering or impersonating the same HomeServer device ID without the corresponding secret.

The first unclaimed connection receives a fresh 12-character claim code. The user enters that code into the remote client. A successful claim:

- marks that HomeServer device claimed
- invalidates the one-time claim code
- returns a high-entropy relay session token
- notifies the currently connected HomeServer that its relay claim is complete

Device secrets, relay session tokens and claim codes are stored only as SHA-256 hashes in relay SQLite state.

Unclaimed device registrations are automatically removed after the configured stale-enrollment TTL so abandoned or abusive registrations cannot consume relay capacity forever.

## HTTP / WebSocket surface

### HomeServer outbound socket

```text
GET/WS /bridge
Subprotocol: homeserver.bridge.v1
Authorization: Bearer <device secret>
X-HomeServer-Device: hs-...
```

Production HomeServer configuration should point at the TLS endpoint, for example:

```text
wss://relay.example.com/bridge
```

### Remote client API

```text
GET  /health
POST /v1/claim
GET  /v1/session
POST /v1/session/rotate
POST /v1/request
```

`/v1/request` accepts only the same named operation set that HomeServer's Remote Bridge allowlists. It cannot target arbitrary URLs, ports, filesystem paths, shell commands or owner-control routes.

Current operations:

- `capabilities`
- `pair.request`
- `pair.status`
- `chat`
- `conversations.list`
- `conversation.get`
- `contacts.search`
- `knowledge.search`
- `memory.read`
- `memory.write`
- `tools.list`
- `skills.list`
- `tool.execute`
- `action.status`

## Run locally

```bash
pip install -r relay/requirements.txt
HOMESERVER_RELAY_DATA_DIR=./relay-data \
uvicorn relay.app:app --host 127.0.0.1 --port 8080 --ws-max-size 262144
```

On Windows PowerShell:

```powershell
$env:HOMESERVER_RELAY_DATA_DIR = '.\relay-data'
uvicorn relay.app:app --host 127.0.0.1 --port 8080 --ws-max-size 262144
```

Then configure HomeServer Remote Bridge to:

```text
ws://127.0.0.1:8080/bridge
```

Plain `ws://` is only appropriate for loopback development. Production must terminate TLS and use `wss://`.

## Docker

Build:

```bash
docker build -f relay/Dockerfile -t homeserver-relay .
```

Run one relay instance with durable SQLite storage:

```bash
docker run --rm \
  -p 8080:8080 \
  -v homeserver-relay-data:/data \
  -e HOMESERVER_RELAY_ALLOWED_ORIGINS=https://vp3.me,https://www.vp3.me \
  homeserver-relay
```

Put the container behind a reverse proxy/load balancer that provides HTTPS/WSS and preserves WebSocket upgrades.

### Single-replica v1 boundary

Relay v1 keeps live HomeServer WebSocket connections in process memory while durable device/session/audit state lives in SQLite. Run **one relay application replica** for this version. Horizontal scaling will require a shared connection-routing/pub-sub layer before multiple replicas can safely serve the same claimed device.

## Configuration

Environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `HOMESERVER_RELAY_DATA_DIR` | `./relay-data` | SQLite data directory |
| `HOMESERVER_RELAY_ALLOWED_ORIGINS` | VP3 production origins | CORS allowlist |
| `HOMESERVER_RELAY_CLAIM_TTL` | `600` | One-time claim-code lifetime in seconds |
| `HOMESERVER_RELAY_CLAIM_ATTEMPTS` | `10` | Per-source in-process claim attempt limit |
| `HOMESERVER_RELAY_CLAIM_WINDOW` | `600` | Claim-attempt window in seconds |
| `HOMESERVER_RELAY_REQUEST_TIMEOUT` | `135` | Maximum forwarded request wait |
| `HOMESERVER_RELAY_MAX_MESSAGE_BYTES` | `262144` | Relay message/payload limit |
| `HOMESERVER_RELAY_MAX_DEVICES` | `10000` | Safety cap for registered devices |
| `HOMESERVER_RELAY_UNCLAIMED_TTL_HOURS` | `24` | Remove abandoned unclaimed devices after this many hours |
| `HOMESERVER_RELAY_EVENT_RETENTION_DAYS` | `30` | Retain metadata-only relay audit events for this many days |

The in-process claim limiter is defense in depth. Production ingress should also rate-limit `/v1/claim` and new `/bridge` enrollment attempts, enforce a small HTTP request-body limit, and cap connection/request rates appropriate for the deployment.

## Trust and privacy

The relay terminates HTTPS/WSS and can therefore see relayed request payloads and HomeServer paired-app bearer credentials. This is the same trusted-relay boundary documented by HomeServer v0.12.

The relay database deliberately does **not** persist:

- device secrets
- raw relay session tokens
- HomeServer paired-app tokens
- request payloads
- response payloads
- claim codes

Audit rows contain event/status/operation/request ID and small timing/status metadata only. Old audit rows are automatically pruned according to `HOMESERVER_RELAY_EVENT_RETENTION_DAYS`.

End-to-end application-payload encryption is a future protocol layer and is not claimed by this service.
