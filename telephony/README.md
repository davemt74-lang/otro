# VP3 Agent Phone Gateway — Section PH1

This is a **public-edge service**, not a new AI brain. It accepts carrier calls
and texts, maps an owned phone number to an existing Agent persona, and invokes
that platform's existing Agent API. It ships with a Twilio driver; `Carrier` is
the provider-neutral port for a future Telnyx or other driver.

## What is implemented

- Signed Twilio SMS and voice callbacks; callback validation uses the fixed
  HTTPS public base, not a caller-supplied Host header.
- Owner-managed assignment of number → VP3 / Stonefellow / Gelato + Agent ID.
- Durable inbound SMS queue, per-caller hourly cap, deduplication by MessageSid,
  bounded provider callbacks and per-caller conversation history.
- Separate worker for signed HTTPS platform callbacks. Only the `reply` and
  `conversation_id` fields are accepted; requested actions are ignored.
- One-time, exact-recipient/content outbound approvals, separately signed by a
  service credential, consumed *before* a provider send. Unknown send outcomes
  are flagged for review, never automatically retried.
- Per-owned-number and recipient consent receipts for each channel; inbound
  STOP/START keywords update SMS opt-out and block pending replies after STOP.
  START resumes solicited replies but never constitutes approval for proactive
  outbound messages.
- Voice callbacks bridge to an owner-configured LiveKit SIP destination.
- HomeServer inbound adapter under `/api/v1/phone/agent` invokes the existing
  `context_chat.chat` read-only and only for an explicitly granted, secondary
  Agent persona. It cannot promote an unknown caller to an owner.

## What is **not** live yet

- No Twilio credentials, telephone number, SIP trunk, LiveKit dispatch rule or
  real-time Agent worker is provisioned by code. Voice calls are only bridged
  when these are configured and verified. Barge-in comes from the LiveKit Agent
  worker, not this HTTP service.
- Public SMS can be sent to Gelato's existing `/api/public-agent.php` as its
  callback, provided its public Agent and provider are enabled. This gives
  callers **published public knowledge only**; no employee/private data or
  operations. Its response is handled as a plain conversational reply.
- Stonefellow needs a separate, explicitly scoped phone service identity and
  signed adapter before its authenticated `/api/agent-chat.php` may be used.
  **Never** place a site session cookie or administrator credential in this
  gateway. The callback must remain unset until that adapter is verified.
- For VP3, the new HomeServer adapter is **private**: route the signed event
  through the existing paired, outbound-initiated Remote Relay. Do not open a
  HomeServer port to Twilio or publish `/api/v1/phone/agent` directly.
- Owner authorization and agent grants must be connected to the existing VP3
  Connections/Approvals UI; the first stage exposes credentialed admin APIs.
- SMS A2P registration, contact consent/opt-out controls, recording consent,
  AI disclosure and live device/call acceptance are deployment prerequisites.

## Deploy the standalone edge service

Use Python 3.11+ on an HTTPS-capable host. For the prototype:

```bash
pip install fastapi==0.115.6 uvicorn==0.34.0 httpx==0.28.1
export VP3_PHONE_PUBLIC_BASE=https://phone.example.com
export VP3_PHONE_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
export VP3_PHONE_AUTH_TOKEN=your_twilio_auth_token
export VP3_PHONE_OWNER_TOKEN=a_distinct_long_owner_token
export VP3_PHONE_SERVICE_SECRET=a_distinct_long_outbound_request_hmac_secret
export VP3_PHONE_VP3_CALLBACK_SECRET=a_distinct_vp3_callback_secret
export VP3_PHONE_STONEFELLOW_CALLBACK_SECRET=a_distinct_stonefellow_callback_secret
export VP3_PHONE_GELATO_CALLBACK_SECRET=a_distinct_gelato_callback_secret
export VP3_PHONE_DATABASE=/var/lib/vp3-phone/phone.sqlite3
export VP3_PHONE_GELATO_CALLBACK=https://restaurant.example.com/api/public-agent.php
uvicorn telephony.app:app --host 127.0.0.1 --port 8081
```

Configure a TLS reverse proxy with an exact, stable `VP3_PHONE_PUBLIC_BASE`
origin and preserve request bodies. Do not expose port 8081 directly. Put
`telephony.worker` in a supervised *separate* process using the same database
and env vars. Run only one SMS worker in PH1. The SQLite volume must be durable,
private to the process, encrypted at rest by the host and backed up.

```bash
python -m telephony.worker
```

Point each provisioned Twilio number's `A MESSAGE COMES IN` webhook to
`https://phone.example.com/v1/twilio/sms` and voice webhook to
`https://phone.example.com/v1/twilio/voice` (POST). A configured LiveKit SIP
destination and dispatch rule are required before enabling voice on a route.

## Administrative contract

The owner API uses `Authorization: Bearer <VP3_PHONE_OWNER_TOKEN>`; credentials
must be server-side, never placed in a browser or deployment ZIP.

`PUT /v1/admin/routes`:

```json
{"number":"+16025550100","platform":"gelato","agent_id":"public-restaurant-agent","active":true,"allow_sms":true,"allow_voice":false,"sip_uri":""}
```

`PUT /v1/admin/consents` records a consent receipt with `number` (your assigned
number), `recipient`, `channel` (`sms` or `call`), `granted` and a nonempty
`source` identifying how/where consent was obtained. Revocations are applied
immediately, including to pending approvals. This receipt is a **technical
control**, not a substitute for collecting legally valid channel-specific
consent. Keep any required opt-in evidence in the proper protected CRM.

`POST /v1/admin/approvals` creates a short-lived approval for **one** exact
`operation` (`sms` or `call`), `from_number`, `to_number`, `agent_id`, `payload`
(SMS text; empty for calls), `approved_by`, and optional `lifetime_seconds`.

`POST /v1/outbound` uses that approval ID with the same operation/from/to/text.
It requires `X-VP3-Timestamp` (Unix seconds) and `X-VP3-Signature`, a hex SHA256
HMAC over `<timestamp>.<raw HTTP body>` using `VP3_PHONE_SERVICE_SECRET`.
The HMAC expires after five minutes. A signed request **does not** grant an
approval by itself. An approval can only be consumed once. An unconfirmed
carrier send must receive a fresh owner approval to retry.

The gateway-to-platform callback uses the same signing algorithm, but **not the
same secret**. Each connected platform receives a different configured
`VP3_PHONE_<PLATFORM>_CALLBACK_SECRET` so a compromised restaurant or music
site cannot sign another platform's events or outbound operations. A JSON
payload with `event_id`, `agent_id`, `channel`, `from`, `to`, `message`, `text`,
`conversation_id`, `history`, `caller_verified:false`, `allow_actions:false`.
The platform must verify the HMAC and independently enforce its Agent permissions.
Gateway `history` is for public conversational continuity only, not a proof of
identity or an authorization to access a private Agent.

## Tests

```bash
python -m unittest discover -s tests -p test_telephony_gateway.py -v
```

Source tests use a fake carrier and never make billable calls or send texts.
Run live Twilio signature, SIP, consent, send-failure and Agent permission tests
in a controlled staging environment before enabling actual phone numbers.

## Privacy and operations

Inbound events store phone numbers and SMS text in local SQLite for processing.
Completed/failed events and inactive conversation history are pruned after
30 days; stuck processing rows are retained for review. Configure encrypted
storage and access controls before accepting real users. Never treat caller ID
or a caller's claimed name as authentication. No auto-call, bulk-messaging,
recording or private-data retrieval is enabled by this section.
