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
- PH3D-A: the dedicated signed `/v1/voice/resolve` contract binds a LiveKit
  worker's call SID and from/to numbers to the **carrier-signed** Twilio ingress,
  an active DID assignment, and its unchanged Agent persona. This uses a
  separate `VP3_PHONE_VOICE_WORKER_SECRET`, not the public webhook signature or
  the outbound approval secret. Unrecognized, stale, reassigned, and disabled
  calls fail closed.
- PH3D-A: `telephony.voice_turns` is the provider-independent transcript bridge.
  For VP3 it delivers each recognized utterance through the existing signed
  HomeServer `/api/v1/phone/agent` adapter. HomeServer routes voice and SMS to
  the **same canonical `context_chat.chat`**, keeping per-call conversation IDs
  and forbidding private owner access, tool actions, and automatically retried
  uncertain turns. Speech engines are intentionally not modified here.
- PH3D-B: optional `telephony.livekit_agent` is a LiveKit audio worker.
  It accepts only verified Twilio SIP participants, resolves their exact call
  via the PH3D-A carrier-signed gateway, uses VAD/STT/TTS with interruptible
  playback, and feeds **every recognized utterance to the existing VP3 Main
  Chat**. Its LiveKit `LLM` interface is only a signed Main Chat bridge: there
  is no additional AI model, persona, knowledge store, or tool executor.
  Interrupted requests finish in order; duplicate STT message IDs are not
  submitted again, and uncertain HomeServer outcomes block subsequent turns.
- PH3G: an optional Cloud-origin SIP resolver consumes Cloud PH3G's
  carrier-signed parent call receipt and single-child SIP binding. It preserves
  the existing read-only HomeServer Chat adapter and agent-grant checks.

## PH3G Cloud-owned Twilio number → HomeServer real-time media

If the number is registered with VP3 Cloud and Twilio calls Cloud's
`api/phone-voice-inbound-v130.php`, keep that webhook. Cloud's PH3G opt-in
produces a TLS SIP `<Dial>` with short-lived `X-VP3-Parent`, `X-VP3-Issued`,
and `X-VP3-Proof` headers. Configure the LiveKit inbound trunk's
`headers_to_attributes` mappings exactly:

| SIP header | LiveKit participant attribute |
| --- | --- |
| `X-VP3-Parent` | `vp3.parent` |
| `X-VP3-Issued` | `vp3.issued` |
| `X-VP3-Proof` | `vp3.proof` |

Configure the *separate* worker process:

```bash
VP3_PHONE_RESOLVE_BACKEND=cloud
VP3_PHONE_PUBLIC_BASE=https://your-vp3-cloud.example
VP3_PHONE_VOICE_WORKER_SECRET=identical_private_cloud_ph3g_worker_secret
```

In this mode the worker sends its signed POST to Cloud's
`/api/phone-voice-stream-resolve-v150.php` instead of the standalone gateway's
`/v1/voice/resolve`. It supplies the carrier-provided SIP child CallSid,
trusted trunk caller and destination, and three mapped SIP invite headers.
Cloud verifies its HMAC, parent Twilio receipt, live number assignment,
short-lived proof, and single-child claim. Cloud returns a *separately
configured HomeServer secondary Agent ID*, not the Cloud Agent integer ID.
The worker then uses the existing signed `/api/v1/phone/agent` adapter,
which independently enforces the paired-app Agent grant and read-only turns.
Before **each** recognized utterance, the worker also calls Cloud's signed
`/api/phone-voice-stream-active-v150.php`; disabling or reassigning a number
then blocks subsequent Agent turns, including after SIP was connected.

If the SIP headers are missing, trunk identity changes, proof expires, Cloud
route changes, or HomeServer's grant is absent, the worker refuses audio.
Cloud's signed `<Dial action>` offers voicemail after an unsuccessful SIP
connection. This bridge is **not live** until trunk/worker provisioning and
real staging-call checks for carrier attributes, transcription, interruptions,
disclosure, and consent. Never expose HomeServer HTTP ports publicly.

### PH3H non-billable readiness check

In the worker's isolated Python environment, with service variables configured,
run `python -m telephony.preflight`. It produces a JSON checklist and returns
zero only when local settings are ready for a supervised staging call. It makes
**no provider requests, purchases, calls, model requests or recordings** and
never displays credential values. Nonzero exit means fix the failed checks.

From VP3 Cloud **Admin → Phone**, click **Check PH3H streaming readiness** on
the intended registered number. The Cloud performs a read-only Twilio number
GET to confirm its inbound Voice URL and POST method; this is not a call. Only
an administrator can run this check. Confirm both preflight reports before
arranging a controlled live staging call.

The staging operator must independently confirm: LiveKit dispatch and mapped
SIP headers, Cloud's signed parent/child resolution, the HomeServer secondary
Agent grant and per-turn revocation, speech disclosure and intelligibility,
barge-in, failed trunk attempts and voicemail fallback. Record these results
outside the preflight; **`live_certified` remains false** until actual calls
have been performed and reviewed. Do not enable production real-time calls
based on a static readiness score alone.

### PH3I-B optional worker health check-in

The separate LiveKit launcher can make a low-volume, signed **outbound** health
check-in to Cloud, every 30 seconds. It uses the already configured PH3G
`VP3_PHONE_VOICE_WORKER_SECRET` and the fixed `VP3_PHONE_PUBLIC_BASE`; no
additional credentials, inbound HomeServer port, phone number or caller audio
is exposed. Enable it in the worker's private service environment only after
the Cloud PH3I-B upgrade is deployed:

```bash
VP3_PHONE_RESOLVE_BACKEND=cloud
VP3_PHONE_WORKER_HEALTH_ENABLED=1
VP3_PHONE_WORKER_ID=vp3-phone
```

The Cloud **Admin → Phone → PH3I-B worker health** panel shows the last
authenticated check-in and the worker's local preflight result. A check-in
expires from **reporting** to **stale/offline** after 90 seconds without new
heartbeats. This only proves the launcher is alive and could reach Cloud;
it cannot prove that LiveKit rooms, carrier trunks, voice recognition or
playback work. Those require the PH3H supervised staging-call gates. Check-in
is **off by default**, and the launcher reports nothing until all required
worker credentials are supplied. No third-party provisioning is performed.

## What is **not** live yet

- No Twilio credentials, telephone number, SIP trunk, LiveKit dispatch rule or
  or deployed real-time Agent worker is provisioned by code. PH3D-B implements
  the **optional worker**, but it is not a verified live calling deployment.
  Twilio/LiveKit provisioning, provider credentials, SIP metadata validation and
  actual audio/barge-in testing are still required before enabling calls.
- PH3D-B initially uses explicitly selected **LiveKit Inference** STT/TTS.
  The separate HomeServer ElevenLabs/FISH work is **not** replaced or silently
  used by this worker; connecting those local/provider adapters and per-user
  voice preferences requires an explicit later integration.
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
export VP3_PHONE_VOICE_WORKER_SECRET=a_distinct_long_livekit_worker_hmac_secret
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

## PH3D-B optional real-time worker (HomeServer Main Chat)

Install this in **its own Python 3.11+ environment/process**. The main
HomeServer pins a different LiveKit SDK and must not be upgraded or have its
ElevenLabs/FISH integrations overwritten to deploy this optional worker.

```bash
python -m venv .venv-phone-worker
. .venv-phone-worker/bin/activate
python -m pip install -r telephony/requirements-livekit-worker.txt
```

Provide the following environment variables using your private service manager
(do **not** commit or package actual values):

| Variable | Purpose |
| --- | --- |
| `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | Authenticated LiveKit room and inference access |
| `VP3_PHONE_PUBLIC_BASE` | Fixed HTTPS gateway origin with verified Twilio ingress |
| `VP3_PHONE_VOICE_WORKER_SECRET` | Separate HMAC key for `/v1/voice/resolve` |
| `VP3_PHONE_ACCOUNT_SID` | Expected Twilio account SID (`AC...`) |
| `VP3_PHONE_LIVEKIT_TRUNK_ID` | Exact trusted SIP trunk ID; do not accept arbitrary callers |
| `VP3_PHONE_AGENT_NAME` | LiveKit dispatch name (default `vp3-phone`) |
| `VP3_PHONE_VP3_CALLBACK` | Signed private HomeServer `/api/v1/phone/agent` URL |
| `VP3_PHONE_VP3_CALLBACK_SECRET` | Independent HomeServer Main Chat callback HMAC |
| `VP3_PHONE_SPEECH_BACKEND` | Currently must be `livekit-inference` |
| `VP3_PHONE_STT_MODEL`, `VP3_PHONE_TTS_MODEL`, `VP3_PHONE_TTS_VOICE` | Explicit LiveKit Inference speech choices; voice can be blank |
| `VP3_PHONE_RESOLVE_BACKEND` | `edge` by default; set `cloud` only for PH3G Cloud-owned numbers |

Set the `VP3_PHONE_VP3_CALLBACK` to
`http://127.0.0.1:4377/api/v1/phone/agent` only if this worker runs on the
same host as VP3. Otherwise use the authenticated, **outbound-paired Remote
Relay**, not a public HomeServer port. `VP3_PHONE_VOICE_WORKER_SECRET` and
`VP3_PHONE_VP3_CALLBACK_SECRET` must be distinct; the worker refuses to start
without them or without model selection. Start it under supervision:

```bash
python -m telephony.livekit_agent start
```

LiveKit dispatch must target the configured agent name; only a LiveKit SIP
participant with matching **carrier account, SIP trunk, CallSid, and from/to
numbers** will be accepted. The gateway independently verifies this binding
against the original Twilio-signed voice webhook, current DID/Agent assignment,
and allowed call state. Some carrier/trunk setups do not expose every required
SIP attribute. Those calls are intentionally refused rather than treated as
authenticated; verify the metadata on a **staging call** before enabling users.

No phone caller receives owner permissions merely by caller ID or voice match.
Only a separately granted public/secondary VP3 agent is available, in read-only
mode, with **no tool actions**. This worker does not start recordings; a spoken
AI disclosure is sent at the beginning. Validate local consent/disclosure
requirements before live deployment. Inbound calls only are supported by this
worker's current carrier binding; outbound media routing needs separate tests.

The telephone conversation uses the same `context_chat.chat` engine and
conversation continuity as browser Main Chat, with a different (restricted)
caller authorization. LiveKit handles speech and interruption, and the signed
HomeServer endpoint handles Agent reasoning. This worker does not invoke a
second LLM or create a separate Agent brain.

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

## PH3D-A signed live-call contract (worker integration)

After receiving a SIP participant, the worker must obtain the Twilio CallSid,
called number and caller number from **verified carrier/SIP metadata** (or a
carrier-verified binding passed by trusted dispatch). Do not assume a default
LiveKit SIP participant exposes every Twilio-specific attribute; if any binding
is missing or cannot be authenticated, refuse the call instead of guessing. Then
posts `{ "call_sid": "CA<32 hex>", "to_number": "+1...", "from_number": "+1..." }`
to `POST /v1/voice/resolve`. The body is signed with
`VP3_PHONE_VOICE_WORKER_SECRET` using the same timestamp/HMAC envelope format,
but with an **independent** secret. The gateway looks up the exact call event
stored after Twilio's verified voice webhook; it never trusts a SIP worker's
assertion that a caller owns the destination or an Agent ID. The returned
`platform`, `agent_id`, `call_sid`, `caller_verified:false`, and
`allow_actions:false` are an *unprivileged* identity binding, not consent to
record or execute tools. Calls without an E.164 caller number are denied by
this PH3D-A path and require a future explicit anonymous-caller policy.

For a VP3 binding, construct `telephony.voice_turns.MainChatVoiceSession` and
send a recognized utterance using `session.turn(text)`. On a co-located
HomeServer voice worker, the callback URL is
`http://127.0.0.1:4377/api/v1/phone/agent` (loopback only), with the distinct
`VP3_PHONE_VP3_CALLBACK_SECRET`. For remote placement, use an authenticated
HTTPS service endpoint routed through the already paired outbound Remote Relay;
**never** publish a HomeServer phone adapter directly to the internet. Voice
turn IDs are `CA<32 hex>_t1`, `..._t2`, etc.; the first reply supplies a
canonical conversation ID which subsequent recognized turns reuse. An
unconfirmed response *blocks* further turns; it must not trigger a second LLM
execution or an automatic retry. The optional PH3D-B LiveKit worker attaches
this contract to the audio lifecycle once a trusted SIP route is provisioned.

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
python -m pip install fastapi==0.115.6 httpx==0.28.1 -r telephony/requirements-livekit-worker.txt
python -m unittest discover -s tests -p 'test_telephony_*.py' -v
```

The 35 source tests use a fake carrier and canonical Chat, with no billable
calls, texts, recordings, or speech-provider network access.
Run live Twilio signature, SIP, consent, send-failure and Agent permission tests
in a controlled staging environment before enabling actual phone numbers.

## Privacy and operations

Inbound events store phone numbers and SMS text in local SQLite for processing.
Completed/failed events and inactive conversation history are pruned after
30 days; stuck processing rows are retained for review. Configure encrypted
storage and access controls before accepting real users. Never treat caller ID
or a caller's claimed name as authentication. No auto-call, bulk-messaging,
recording or private-data retrieval is enabled by this section.
