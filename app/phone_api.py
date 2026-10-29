"""HomeServer's signed, read-only phone Agent adapter.

Do not expose the HomeServer to the PSTN. Invoke this through the existing
authenticated outbound relay path, after pairing app:phone-gateway and granting
a deliberately public-scoped secondary Agent persona.
"""

from __future__ import annotations

import json
import os
import re

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from telephony.security import valid_envelope
from .database import db
from .services import agent_routing, context_chat


router = APIRouter(prefix="/api/v1/phone", tags=["phone-agent"])
EVENT_ID = re.compile(r"^[A-Za-z0-9_-]{6,100}$")


class PhoneAgentMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    event_id: str
    channel: str
    agent_id: int = Field(ge=1)
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str = ""
    caller_verified: bool = False
    allow_actions: bool = False


@router.post("/agent")
async def phone_agent_message(request: Request,
                              x_vp3_timestamp: str = Header(default=""),
                              x_vp3_signature: str = Header(default="")) -> dict:
    secret = os.environ.get("VP3_PHONE_VP3_CALLBACK_SECRET", "")
    raw = await request.body()
    if len(raw) > 8192 or not valid_envelope(secret, x_vp3_timestamp, raw, x_vp3_signature):
        raise HTTPException(401, "Invalid phone service signature")
    try:
        payload = PhoneAgentMessage.model_validate_json(raw)
    except Exception as exc:
        raise HTTPException(422, "Invalid phone event") from exc
    if payload.channel != "sms" or payload.caller_verified or payload.allow_actions or not EVENT_ID.fullmatch(payload.event_id):
        raise HTTPException(403, "Unsupported phone authorization")

    # Do not let a phone call select the private owner persona. Secondary Agent
    # access additionally requires the existing paired-app Agent grant.
    try:
        agent = agent_routing.resolve_agent("app:phone-gateway", payload.agent_id)
        if bool(agent["is_primary"]):
            raise HTTPException(403, "The private owner Agent is not phone-public")
    except agent_routing.AgentRoutingError as exc:
        raise HTTPException(exc.status_code, "Phone Agent is not authorized") from exc

    with db() as conn:
        row = conn.execute("SELECT state,response_json,agent_id FROM agent_phone_receipts WHERE event_id=?",
                           (payload.event_id,)).fetchone()
        if row:
            if int(row["agent_id"]) != payload.agent_id:
                raise HTTPException(409, "Phone event is bound to another Agent")
            if row["state"] == "complete" and row["response_json"]:
                return json.loads(row["response_json"])
            raise HTTPException(409, "Phone event requires review; it will not be replayed")
        conn.execute("INSERT INTO agent_phone_receipts(event_id,agent_id,state) VALUES (?,?,'processing')",
                     (payload.event_id, payload.agent_id))

    try:
        result = context_chat.chat(
            "app:phone-gateway", payload.message,
            payload.conversation_id or None, agent_id=payload.agent_id,
            include_memory=False, include_knowledge=True, include_contacts=False,
            tool_permissions=set(), owner_tools=False, read_only=True,
            context_options={"include_memory": False, "include_contacts": False},
        )
        response = {"ok": True, "reply": str(result["reply"])[:1200],
                    "conversation_id": str(result["conversation_id"])}
    except Exception as exc:
        with db() as conn:
            conn.execute("UPDATE agent_phone_receipts SET state='needs_review' WHERE event_id=?", (payload.event_id,))
        raise HTTPException(503, "Phone Agent turn unavailable; manual review required") from exc
    with db() as conn:
        conn.execute("UPDATE agent_phone_receipts SET state='complete',response_json=? WHERE event_id=?",
                     (json.dumps(response, separators=(",", ":")), payload.event_id))
    return response
