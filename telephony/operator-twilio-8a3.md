# Operator 8A.3 — native Twilio transport contract

## Design decision

Use Twilio Programmable Voice and bidirectional Media Streams for the new
Operator call path. LiveKit is not a dependency of these modules. Existing
LiveKit inbound phone, meetings, and other VP3 integrations are unchanged.
Twilio places the PSTN call; an independent, persistent secure WebSocket
worker will process audio while the browser is closed. This release is
the tested **wire/transport reference**, not that deployed network service.

Official provider contracts:
- https://www.twilio.com/docs/voice/api/call-resource
- https://www.twilio.com/docs/voice/twiml/stream
- https://www.twilio.com/docs/voice/media-streams/websocket-messages
- https://www.twilio.com/docs/usage/security

## Included

- Exact planned account, intent, E.164 source/destination, fixed HTTPS control
  origin, fixed WSS media origin, finite ring and call duration. A Calls API
  request description preserves repeated StatusCallbackEvent form parameters.
  No request is sent. No arbitrary TwiML, external callback URL, or user/model
  supplied route is accepted. Recording defaults off.
- Exact carrier leg checks against a trusted future REST snapshot. A JSON
  object alone is not carrier authentication. The status reducer validates
  the existing Twilio signature contract against the fixed per-intent URL,
  checks account, CallSid, direction and both telephone numbers, and orders
  bounded status events. Duplicate events are idempotent; inconsistent
  progress requires reconciliation; an older callback cannot reopen a
  terminal leg. Completion is not proof of a successful conversation.
- A TwiML fixture for a consented bidirectional stream with per-intent
  parameters, one-time nonce and a final Hangup when the stream closes.
  This is not real consent or a deployed voice webhook. Until a genuine
  authority and consent gate exist, the live response must be Hangup.
- Strict ordered connected/start/media/mark/DTMF/stop message handling.
  A local reference registry atomically permits a single stream per exact
  account/call/intent; one spent binding cannot reopen in that registry.
  It is bounded to 128 fixture bindings and is not durable across processes.
  Upgrade validation uses the exact configured WSS URL, not untrusted Host
  headers. Real Twilio canonicalization and framework ingress, including
  duplicate form parameters and any trailing-slash behavior, must be checked
  against the official SDK in staging.
- Bounded canonical base64, raw 8 kHz mono G.711 mu-law / signed PCM16LE
  conversion without audio-file headers, monotonic sequences/chunks/timestamps,
  one bounded 200 ms outbound segment in flight, acknowledgement deadline,
  and no recording or audio persistence. Resampling to selected STT/TTS
  providers and provider processing are not part of this phase.
- Generation-fenced interruptions: return a clear message, block previous
  synthesis, and classify cleared marks as uncertain. A returned message batch
  is not a network send or proof of hearing. The eventual socket executor
  must serialize check/generation/send/clear/close so no already-queued batch
  can be published after revocation. A future owner-takeover workflow will
  require a separate verified connection/participant transition.
- Revocation fences Agent media, requests clear/close, and never claims that
  the PSTN call is finished. Known-state cancellation request descriptions
  are not sent. Uncertain call creation is never retried by this reference.

## Explicit acceptance boundary

The new code contains no Twilio SDK client, HTTP or WebSocket server, network
sender, provider credential provisioning, dialing job, recording, audio
provider, public endpoint, or automatic process launch. Use only synthetic
fixtures. All exercise reports say execution_enabled=false and
live_certified=false. Neither this nor the simulation registration/ledger
grants authority to place a call.

Run from the repository root:

    python -m telephony.operator_twilio_simulation --simulate
    python -m unittest discover -s tests -p 'test_telephony_operator_twilio_transport.py' -v

The existing phone gateway regression workflow runs the new suite and all
three Operator demonstrations.

## Next integration gates

1. Implement Cloud-side authoritative registration, call-occurrence and
   recipient consent bindings, one-use dispatch reservations and spend caps.
   Use the actual paired-worker identity and lease, not a local fixture.
2. Provision a public TLS WebSocket edge with authenticated service routing
   to the private worker. Do not expose the HomeServer app or private ports.
   Pin origins, verify Twilio with its SDK, enforce frame/time/resource
   limits and rate limits. Use authenticated outbound-only worker links.
   Persist exact call/stream claims atomically across all workers and
   process restarts. A signed websocket handshake alone grants no media.
3. Implement exact signed call progress and read-only REST reconciliation in
   Cloud; no duplicate call creation on an uncertain provider response.
   Keep terminal carrier state separate from stream/recording/outcome state.
4. Add independent, consented real speech adapters; continuously recheck
   authority, time and budget, and stop on revoked consent or stale lease.
   Gate disclosure and affirmative consent before any recording or
   conversational speech processing. Test media interruption and cancellation.
5. Prototype owner monitoring/join/takeover with a verified Twilio conference
   or controlled call transfer. Bidirectional Media Streams is not itself
   a multiparty conference: it receives only the inbound track. Do not claim
   seamless concurrent owner/Agent speech until the selected topology has
   passed staging tests.
6. Physically test one explicitly approved consenting test contact, actual
   Twilio call/stream signatures, format, clearing, timing, disconnect and
   terminal carrier receipts, then review production enablement separately.

No new live calls, costs, or recordings are authorized by this release.
