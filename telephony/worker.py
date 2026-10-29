"""Durable SMS worker, separate from the latency-sensitive Twilio webhook."""

from __future__ import annotations

import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .app import Settings
from .providers import Carrier, CarrierError, TwilioCarrier
from .security import json_bytes, signed_envelope
from .state import PhoneState


def agent_response(event: dict, config: Settings) -> tuple[str, str]:
    callback = config.platform_callback(event["platform"])
    parsed = urlparse(callback)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("The platform phone adapter must use a configured HTTPS URL")
    if not config.service_secret:
        raise ValueError("Service signing is not configured")
    body = json_bytes({
        "event_id": event["event_id"], "channel": "sms",
        "from": event["sender"], "to": event["number"],
        "text": event["body"], "message": event["body"], "agent_id": event["agent_id"],
        "conversation_id": event.get("conversation_id", ""),
        "history": event.get("history", []),
        "caller_verified": False, "allow_actions": False,
    })
    stamp = int(time.time())
    req = Request(callback, method="POST", data=body, headers={
        "Content-Type": "application/json",
        "X-VP3-Timestamp": str(stamp),
        "X-VP3-Signature": signed_envelope(config.service_secret, stamp, body),
    })
    try:
        with urlopen(req, timeout=15) as result:
            response = json.loads(result.read(65536))
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        raise RuntimeError("Agent platform did not acknowledge the message") from exc
    if not isinstance(response, dict) or not response.get("ok"):
        raise RuntimeError("Agent platform declined the message")
    reply = response.get("reply", "")
    if not isinstance(reply, str):
        raise RuntimeError("Agent platform sent an invalid reply")
    # Explicitly ignore any attempted tool calls, recipient changes or actions.
    platform_id = response.get("conversation_id", "")
    return reply.strip()[:1200], platform_id[:100] if isinstance(platform_id, str) else ""


def process_one(state: PhoneState, config: Settings, carrier: Carrier,
                responder=agent_response) -> bool:
    event = state.take_sms()
    if not event:
        return False
    if int(time.time()) - int(event["created_at"]) > 900:
        # No surprise outbound response to a very old incoming text.
        state.fail_sms(event["event_id"])
        return True
    route = state.route(event["number"])
    if (not route or not route["allow_sms"] or route["platform"] != event["platform"]
            or route["agent_id"] != event["agent_id"]):
        state.fail_sms(event["event_id"])
        return True
    event.update(state.conversation(event["number"], event["sender"]))
    try:
        result = responder(event, config)
        reply, platform_id = result if isinstance(result, tuple) else (result, "")
    except (RuntimeError, ValueError):
        state.retry_sms(event["event_id"], int(event["attempts"]) + 1)
        return True
    if not reply:
        state.save_turn(event["number"], event["sender"], platform_id, event["body"], "")
        state.finish_sms(event["event_id"])
        return True
    if state.explicitly_opted_out(event["number"], event["sender"]):
        state.finish_sms(event["event_id"])
        return True
    try:
        provider_id = carrier.send_sms(event["number"], event["sender"], reply)
    except CarrierError:
        # A carrier timeout could mean the message actually went out.
        # Never replay an ambiguous outgoing message automatically.
        state.fail_sms(event["event_id"])
        return True
    state.save_turn(event["number"], event["sender"], platform_id, event["body"], reply)
    state.finish_sms(event["event_id"], provider_id)
    return True


def main() -> None:
    config = Settings.from_environment()
    state = PhoneState(config.database)
    carrier = TwilioCarrier(config.account_sid, config.auth_token)
    last_prune = 0.0
    while True:
        if time.monotonic() - last_prune > 3600:
            state.prune()
            last_prune = time.monotonic()
        if not process_one(state, config, carrier):
            time.sleep(2)


if __name__ == "__main__":
    main()
