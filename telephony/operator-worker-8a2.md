# Operator 8A.2 — authenticated worker registration contract

## Scope

This release provides the HomeServer worker client, a signed interoperable
registration protocol and a **local reference authority**, not the deployed
VP3 Cloud endpoint. It adds no HTTP listener or network sender, creates no
production worker credential, and does not change the existing inbound
LiveKit worker or public phone adapter. It cannot make a call.

The existing Phase 8A.1 session simulation remains separate. A registration
receipt does not authorize a session claim, media access, tools, recording,
recipient contact, spending or dialing. The only accepted capability names
are `session.recover` and `session.simulate`. A capability report describes
the worker's declared availability; it does not certify implementation,
media connectivity or a provider account.

## Wire protocol

Use protocol identifier `vp3.operator.worker.v1`. Frames consist of canonical
JSON body bytes and a lowercase 64-character hexadecimal HMAC-SHA256.
The future HTTPS binding must pin the configured Cloud origin, reject redirects,
cap request/response bodies at 4096 bytes, and perform a bounded authenticated
exchange; never discover a destination from a signed payload.

Canonical JSON: ASCII encoding, lexicographically sorted object keys, no
whitespace, no duplicate keys, no floats or non-finite numbers in any valid
field. All allowed strings are restricted ASCII identifiers or fixed enums.
Sign **the exact bytes** of the canonical body:

```
request_mac  = hex(HMAC_SHA256(key, "vp3.operator.worker.v1/request" + NUL + body))
response_mac = hex(HMAC_SHA256(key, "vp3.operator.worker.v1/response" + NUL + body))
request_hash = hex(SHA256(request_body))
```

The key is an independently generated 32-byte high-entropy secret, not the
Twilio credential, existing inbound phone signing key, general pairing secret,
or a user-chosen password. Provision its ID to an exact owner/device/worker
identity using the existing authenticated pairing/admin boundary in the future
Cloud implementation. Credentials and their capabilities are supplied only
to this reference process by trusted fixture code. Keys are not stored in
the scratch ledger; a SHA-256 key fingerprint prevents in-place key swaps
from inheriting a lease. Rotation requires a new credential ID and revocation
of the old credential. The eventual session claim must bind the credential ID
as well as boot and generation.

Request fields (no extensions or unknown fields accepted):

| Field | Validation |
| --- | --- |
| protocol | exact protocol identifier |
| action | register / heartbeat / retire |
| credential_id | 32 lowercase hex characters |
| owner_id | positive safe integer |
| device_id | 32 lowercase hex characters |
| worker_id | 3–64 ASCII letters/digits/underscore/hyphen |
| boot_id | new 32 lowercase hex characters per process boot |
| sequence | starts at 1; increments exactly by one |
| generation | 0 only during register; server-issued positive generation afterward |
| nonce | random 32 lowercase hex characters per request |
| sent_at | integer UTC epoch, at most 30 seconds old or 5 seconds ahead |
| capabilities | unique sorted list from provisioned simulation-only capabilities; empty on retire |

The signed response echoes protocol, action, the exact owner/device/worker/key,
boot, sequence, nonce and capabilities. It replaces the requested generation
with the authority-issued generation and adds `request_sha256`, `received_at`,
`lease_until`, `status` (registered/reporting/retired),
`execution_enabled:false`, and `live_certified:false`. It does not include
`sent_at`. A reporting lease ends 90 seconds after accepted receipt; retirement
uses lease_until=0. The client authenticates the entire response and exact
pending request, verifies time bounds and refuses a stale/mismatched response
or any claimed live authority.

Synthetic interoperability vector (the public example key is not a real key):

- key: hex bytes 00 through 1f
- canonical request:
```json
{"action":"register","boot_id":"33333333333333333333333333333333","capabilities":["session.recover","session.simulate"],"credential_id":"11111111111111111111111111111111","device_id":"22222222222222222222222222222222","generation":0,"nonce":"44444444444444444444444444444444","owner_id":1,"protocol":"vp3.operator.worker.v1","sent_at":1800000000,"sequence":1,"worker_id":"fixture-worker"}
```
- request HMAC: `9dd194caff6cf5485f5b2e7440f683d0e948f658c0a57894b7dc5f33c15d5daf`
- request SHA-256: `5146efef3ba073df025789d2cf63cd0506dd16e6fb21e28cb33cd71681e51532`

## Registration lifecycle

A new boot cannot displace a currently reporting boot. SQLite transactions
serialize concurrent registrations and heartbeats. After expiry or explicit
retirement, a distinct boot receives a higher generation; retired boot IDs
remain fenced. The reference stores at most 4096 boot identities per key;
rotate the credential before exhausting this bounded history. Exact duplicate
requests may retrieve the same receipt while fresh, without renewing a lease.
Changed payload at the same sequence, skipped sequence, an old boot, wrong
key or generation, and backwards clocks are rejected.

The client holds one unresolved request. A lost response can only cause a
byte-identical fresh retransmission, not another sequence or a new claim.
A stale or unresolved client reports no current capabilities. Reopening the
reference database retains the receipt and boot fences; revoked credentials
are excluded from its internal owner-scoped listing. The local fixture does
not implement immediate notification of revocation to a disconnected client:
future production media/dispatch authority must be checked separately and
must never depend on cached registration alone.

## Testing

```sh
python -m telephony.operator_worker_simulation --simulate
python -m unittest discover -s tests -p 'test_telephony_operator_worker_registration.py' -v
```

The demonstration uses a temporary private database and random synthetic
credential, reads no real configuration, and performs no network requests.
The existing gateway CI runs the new tests and both simulation demonstrations.

## Production integration gates

1. Deploy the matching Cloud authenticated receiver and credential provisioning
   through the existing account/HomeServer pairing boundary. Verify the PHP
   implementation against the wire vector and native MySQL races.
2. Add a separately reviewed outbound HTTPS exchange to the pinned Cloud
   endpoint; keep secrets out of logs/browser data and test transport
   ambiguity. Registration should refresh at a bounded cadence within its lease.
3. Add authoritative session claim/renew/revoke receipts and live readiness
   checks. Bind each exact Cloud occurrence, worker, route, approved recipient
   consent and reservation. Registration is never the grant to place a call.
4. Add transport adapters and verified worker deployment, owner interruption,
   cost enforcement, reconciliation, and separate physical provider acceptance.
   Keep autonomous execution disabled until all applicable gates pass.
