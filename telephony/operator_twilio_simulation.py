"""Phase 8A.3 synthetic Twilio wire exchange; never opens a connection."""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json

from .operator_twilio_contract import CallPlan, CallProgress, StreamBinding, TwilioEndpoints
from .operator_twilio_media import FixtureMediaRegistry, FixtureMediaSession


def exercise() -> dict:
    now = 1800000000
    account, call, stream, intent = "AC" + "a" * 32, "CA" + "b" * 32, "MZ" + "c" * 32, "d" * 32
    key = "synthetic-reference-only"
    endpoints = TwilioEndpoints("https://control.example.test", "wss://media.example.test")
    plan = CallPlan(intent, account, "+12025550101", "+12025550102", endpoints, now + 600)
    description = plan.create_description(now)
    binding = StreamBinding.fixture(plan, call, now + 300)

    def signature(url, form=None):
        data = url + "".join(k + v for k, v in sorted((form or {}).items()))
        return base64.b64encode(hmac.new(key.encode(), data.encode(), hashlib.sha1).digest()).decode()

    progress = CallProgress(plan, call)

    def status(value, seq):
        form = {"AccountSid": account, "CallSid": call, "From": plan.from_number,
                "To": plan.to_number, "Direction": "outbound-api",
                "CallbackSource": "call-progress-events", "CallStatus": value,
                "SequenceNumber": str(seq)}
        return progress.observe(form, signature(endpoints.status(intent), form), key)

    status("in-progress", 2)
    status("ringing", 1)  # Out-of-order delivery must not rewind state.
    url = endpoints.media(intent)
    session = FixtureMediaSession(binding, FixtureMediaRegistry(), key, url, signature(url), now)

    def event(payload, at=now):
        return session.receive(json.dumps(payload), at)

    event({"event": "connected", "protocol": "Call", "version": "1.0.0"})
    event({"event": "start", "sequenceNumber": "1", "streamSid": stream,
           "start": {"streamSid": stream, "accountSid": account, "callSid": call,
                     "tracks": ["inbound"], "customParameters": {"intent": intent, "nonce": binding.nonce},
                     "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1}}})
    incoming = event({"event": "media", "sequenceNumber": "2", "streamSid": stream,
                      "media": {"track": "inbound", "chunk": "1", "timestamp": "0",
                                "payload": base64.b64encode(b"\xff" * 160).decode()}})
    batch = session.queue_pcm(incoming.audio, session.generation, now)
    session.interrupt(now + 1)
    ack = event({"event": "mark", "sequenceNumber": "3", "streamSid": stream,
                 "mark": batch.messages[1]["mark"]}, now + 2)
    session.revoke("owner_takeover")
    carrier = status("completed", 3)
    return {"phase": "8A.3", "transport": "twilio-media-streams",
            "simulation_only": True, "execution_enabled": False, "live_certified": False,
            "call_plan_prepared": description["execution_enabled"] is False,
            "inbound_pcm_bytes": len(incoming.audio),
            "playback": ack.mark_outcome, "carrier": carrier,
            "media": session.check_health(now + 2)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Native Twilio Operator protocol simulation")
    parser.add_argument("--simulate", action="store_true", required=True,
                        help="Exercise synthetic signed callbacks and media frames without network")
    parser.parse_args()
    report = exercise()
    print(json.dumps(report, indent=2))
    if (report["playback"] != "cleared_or_unconfirmed"
            or not report["carrier"]["carrier_terminal"]
            or not report["media"]["close_required"]
            or report["execution_enabled"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
