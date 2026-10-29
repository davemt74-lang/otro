"""Public-edge Twilio ingress and approval-gated, provider-neutral egress.

Deploy separately from HomeServer. All configured platforms remain the final
authority for knowledge, identity, tool use and memory.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlparse
from xml.sax.saxutils import escape

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from .providers import Carrier, CarrierError, TwilioCarrier
from .security import phone_number, twilio_valid, valid_envelope
from .state import PhoneState


@dataclass
class Settings:
    public_base: str = ""
    account_sid: str = ""
    auth_token: str = ""
    owner_token: str = ""
    service_secret: str = ""
    database: str = "./phone-data/phone.sqlite3"
    vp3_callback: str = ""
    stonefellow_callback: str = ""
    gelato_callback: str = ""

    @classmethod
    def from_environment(cls) -> "Settings":
        values = {field: os.getenv("VP3_PHONE_" + field.upper(), "")
                  for field in ("public_base", "account_sid", "auth_token", "owner_token",
                                "service_secret", "vp3_callback", "stonefellow_callback", "gelato_callback")}
        values["database"] = os.getenv("VP3_PHONE_DATABASE", "./phone-data/phone.sqlite3")
        return cls(**values)

    def configured_url(self, path: str) -> str:
        parsed = urlparse(self.public_base)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("An HTTPS VP3_PHONE_PUBLIC_BASE is required")
        return self.public_base.rstrip("/") + path

    def platform_callback(self, platform: str) -> str:
        return {"vp3": self.vp3_callback, "stonefellow": self.stonefellow_callback,
                "gelato": self.gelato_callback}.get(platform, "")


class RouteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    number: str
    platform: str
    agent_id: str = Field(min_length=1, max_length=100)
    active: bool = False
    allow_sms: bool = False
    allow_voice: bool = False
    sip_uri: str = ""


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: str
    from_number: str
    to_number: str
    agent_id: str
    payload: str = Field(default="", max_length=1200)
    approved_by: str = Field(min_length=1, max_length=120)
    lifetime_seconds: int = Field(default=900, ge=60, le=3600)


class ConsentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    number: str
    recipient: str
    channel: str
    granted: bool
    source: str = Field(min_length=3, max_length=240)


class OutboundRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approval_id: str
    operation: str
    from_number: str
    to_number: str
    payload: str = Field(default="", max_length=1200)


def create_app(config: Settings | None = None, *, state: PhoneState | None = None,
               carrier: Carrier | None = None) -> FastAPI:
    cfg = config or Settings.from_environment()
    db = state or PhoneState(cfg.database)
    provider = carrier or TwilioCarrier(cfg.account_sid, cfg.auth_token)
    app = FastAPI(title="VP3 Agent Phone Gateway", version="0.1.0")
    app.state.phone_state = db
    app.state.phone_config = cfg

    def owner(bearer: str) -> None:
        from hmac import compare_digest
        if not cfg.owner_token or not bearer.startswith("Bearer ") or not compare_digest(bearer[7:], cfg.owner_token):
            raise HTTPException(401, "Owner authorization required")

    async def trusted_service(request: Request, stamp: str, signature: str) -> None:
        body = await request.body()
        if len(body) > 8192 or not valid_envelope(cfg.service_secret, stamp, body, signature):
            raise HTTPException(401, "Invalid service signature")

    async def twilio_form(request: Request, path: str) -> dict[str, str]:
        body = await request.body()
        if len(body) > 8192:
            raise HTTPException(413, "Provider webhook too large")
        try:
            pairs = parse_qsl(body.decode("utf-8"), keep_blank_values=True, strict_parsing=True)
            if len(pairs) != len({name for name, _ in pairs}):
                raise ValueError("Duplicate form fields")
            form = dict(pairs)
            url = cfg.configured_url(path)
        except (ValueError, UnicodeDecodeError) as exc:
            raise HTTPException(400, "Invalid provider webhook") from exc
        if not cfg.account_sid or form.get("AccountSid") != cfg.account_sid:
            raise HTTPException(403, "Invalid provider account")
        if not twilio_valid(cfg.auth_token, url, form, request.headers.get("x-twilio-signature", "")):
            raise HTTPException(403, "Invalid provider signature")
        return form

    @app.get("/health")
    def health() -> dict:
        # Never expose account credentials, phone numbers or agent names.
        return {"ok": True, "provider": "twilio", "outbound_approval_required": True,
                "configured": bool(cfg.account_sid and cfg.auth_token and cfg.public_base)}

    @app.put("/v1/admin/routes")
    def route_put(payload: RouteRequest, authorization: str = Header(default="")) -> dict:
        owner(authorization)
        try:
            db.put_route(payload.number, payload.platform, payload.agent_id,
                         active=payload.active, sms=payload.allow_sms,
                         voice=payload.allow_voice, sip_uri=payload.sip_uri)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"ok": True, "number": payload.number, "active": payload.active}

    @app.post("/v1/admin/approvals")
    def approval_create(payload: ApprovalRequest, authorization: str = Header(default="")) -> dict:
        owner(authorization)
        try:
            if payload.operation == "call" and payload.payload:
                raise ValueError("Voice call approval payload must be empty")
            if payload.operation == "sms" and not payload.payload.strip():
                raise ValueError("SMS approval requires exact nonempty text")
            key = db.approve(payload.operation, payload.from_number, payload.to_number,
                             payload.payload, payload.agent_id, payload.approved_by,
                             lifetime=payload.lifetime_seconds)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"approval_id": key, "expires_in": payload.lifetime_seconds, "single_use": True}

    @app.put("/v1/admin/consents")
    def consent_put(payload: ConsentRequest, authorization: str = Header(default="")) -> dict:
        owner(authorization)
        try:
            db.consent(payload.number, payload.recipient, payload.channel, payload.granted, payload.source)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"ok": True, "recipient": payload.recipient, "channel": payload.channel,
                "granted": payload.granted}

    @app.post("/v1/outbound")
    async def outbound(request: Request, payload: OutboundRequest,
                       x_vp3_timestamp: str = Header(default=""),
                       x_vp3_signature: str = Header(default="")) -> dict:
        await trusted_service(request, x_vp3_timestamp, x_vp3_signature)
        try:
            frm, to = phone_number(payload.from_number), phone_number(payload.to_number)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        route = db.route(frm)
        if not route or (payload.operation == "sms" and not route["allow_sms"]) or (payload.operation == "call" and not route["allow_voice"]):
            raise HTTPException(403, "This phone route or channel is not active")
        if payload.operation not in {"sms", "call"} or (payload.operation == "sms" and not payload.payload.strip()) or (payload.operation == "call" and payload.payload):
            raise HTTPException(422, "Invalid outbound operation")
        if not db.consume(payload.approval_id, payload.operation, frm, to, payload.payload, route["agent_id"]):
            raise HTTPException(409, "Approval missing, expired, changed or already consumed")
        # Single use is consumed BEFORE touching the external network; uncertain sends
        # are never silently retried. An owner must approve a new attempt.
        try:
            if payload.operation == "sms":
                provider_id = provider.send_sms(frm, to, payload.payload)
            else:
                provider_id = provider.start_call(frm, to, cfg.configured_url("/v1/twilio/voice"))
        except (CarrierError, ValueError) as exc:
            raise HTTPException(502, "The carrier could not confirm the outbound request; review before retrying") from exc
        db.mark_provider(payload.approval_id, provider_id)
        return {"ok": True, "provider_id": provider_id}

    @app.post("/v1/twilio/sms")
    async def inbound_sms(request: Request) -> Response:
        form = await twilio_form(request, "/v1/twilio/sms")
        route = db.route(form.get("To", ""))
        if route and route["allow_sms"] and form.get("MessageSid"):
            try:
                sender = phone_number(form.get("From", ""))
                body = form.get("Body", "")[:2000]
                keyword = body.strip().upper()
                if keyword in {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"}:
                    db.consent(route["number"], sender, "sms", False, "inbound opt-out")
                elif keyword in {"START", "UNSTOP"}:
                    db.resubscribe_sms_replies(route["number"], sender)
                else:
                    db.inbound(form["MessageSid"], "sms", route, sender, body)
            except ValueError:
                pass
        # Twilio must be acknowledged quickly. A durable separate worker handles
        # the agent call and any reply, preventing long LLM delays in this webhook.
        return Response('<Response/>', media_type="application/xml")

    @app.post("/v1/twilio/voice")
    async def inbound_voice(request: Request) -> Response:
        form = await twilio_form(request, "/v1/twilio/voice")
        direction = form.get("Direction", "")
        number = form.get("From", "") if direction.startswith("outbound") else form.get("To", "")
        route = db.route(number)
        if not route or not route["allow_voice"] or not route["sip_uri"]:
            return Response('<Response><Reject reason="rejected"/></Response>', media_type="application/xml")
        if form.get("CallSid"):
            db.inbound(form["CallSid"], "voice", route, form.get("From", ""))
        # LiveKit dispatch policies choose the existing Agent by DID; the URI
        # never comes from caller input. No PSTN dialing or arbitrary SIP proxy.
        return Response(f'<Response><Dial><Sip>{escape(route["sip_uri"])}</Sip></Dial></Response>',
                        media_type="application/xml")

    return app


app = create_app()
