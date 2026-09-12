from __future__ import annotations

import time
from typing import Any

from . import tools, vp3_commerce_agent_connector as connector

KEYS = {
    "vp3.commerce.catalog.search",
    "vp3.commerce.product.get",
    "vp3.commerce.orders.list",
    "vp3.commerce.order.get",
    "vp3.commerce.checkout.handoff",
    "vp3.commerce.fulfillment.update",
}
WRITE_KEYS = {"vp3.commerce.fulfillment.update"}

DEFINITIONS = {
    "vp3.commerce.catalog.search": {
        "key": "vp3.commerce.catalog.search", "name": "Search VP3 Commerce Catalog",
        "description": "Search the owner's Cloud-canonical VP3 catalog. Hidden products may be read by the paired private Agent but are explicitly marked unavailable for public checkout.",
        "mode": "read", "required_permissions": ["commerce.read"],
        "input_schema": {"type":"object","properties":{"query":{"type":"string","maxLength":240},"limit":{"type":"integer","minimum":1,"maximum":100},"public_only":{"type":"boolean"}},"additionalProperties":False},
    },
    "vp3.commerce.product.get": {
        "key":"vp3.commerce.product.get","name":"Get VP3 Commerce Product",
        "description":"Read one owner-scoped VP3 Commerce product with canonical price, terms digest and public checkout availability.",
        "mode":"read","required_permissions":["commerce.read"],
        "input_schema":{"type":"object","properties":{"product_id":{"type":"string","pattern":"^[A-Za-z0-9._:-]{1,160}$"}},"required":["product_id"],"additionalProperties":False},
    },
    "vp3.commerce.orders.list": {
        "key":"vp3.commerce.orders.list","name":"List VP3 Commerce Orders",
        "description":"Read owner-scoped canonical VP3 orders and fulfillment/payment status without provider credentials.",
        "mode":"read","required_permissions":["commerce.read"],
        "input_schema":{"type":"object","properties":{"limit":{"type":"integer","minimum":1,"maximum":100},"payment_status":{"type":"string","maxLength":40},"order_status":{"type":"string","maxLength":40}},"additionalProperties":False},
    },
    "vp3.commerce.order.get": {
        "key":"vp3.commerce.order.get","name":"Get VP3 Commerce Order",
        "description":"Read one owner-scoped canonical VP3 Commerce order and its safe item/fulfillment projection.",
        "mode":"read","required_permissions":["commerce.read"],
        "input_schema":{"type":"object","properties":{"order_id":{"type":"string","pattern":"^[A-Za-z0-9._:-]{1,160}$"}},"required":["order_id"],"additionalProperties":False},
    },
    "vp3.commerce.checkout.handoff": {
        "key":"vp3.commerce.checkout.handoff","name":"Prepare VP3 Checkout Handoff",
        "description":"Return the public VP3 Profile Commerce URL for an eligible product. This does not create an order, accept buyer terms, authorize a payment, or move money.",
        "mode":"read","required_permissions":["commerce.order"],
        "input_schema":{"type":"object","properties":{"product_id":{"type":"string","pattern":"^[A-Za-z0-9._:-]{1,160}$"}},"required":["product_id"],"additionalProperties":False},
    },
    "vp3.commerce.fulfillment.update": {
        "key":"vp3.commerce.fulfillment.update","name":"Update VP3 Commerce Fulfillment",
        "description":"Propose moving a fully-paid generic Profile Commerce order to processing or fulfilled. Execution requires local HomeServer owner approval.",
        "mode":"write","required_permissions":["commerce.fulfill"],
        "input_schema":{"type":"object","properties":{"order_id":{"type":"string","pattern":"^[A-Za-z0-9._:-]{1,160}$"},"status":{"type":"string","enum":["processing","fulfilled"]}},"required":["order_id","status"],"additionalProperties":False},
    },
}

SKILLS = (
    {"key":"vp3.commerce-selling","name":"VP3 Commerce Selling","description":"Search the canonical VP3 catalog, explain offers and prepare a buyer-controlled checkout handoff.","tools":["vp3.commerce.catalog.search","vp3.commerce.product.get","vp3.commerce.checkout.handoff"]},
    {"key":"vp3.commerce-operations","name":"VP3 Commerce Operations","description":"Review canonical VP3 orders and propose bounded fulfillment updates through local approval.","tools":["vp3.commerce.orders.list","vp3.commerce.order.get","vp3.commerce.fulfillment.update"]},
)
SKILL_KEYS = {str(skill["key"]) for skill in SKILLS}


def _connector_ready() -> bool:
    try:
        return bool(connector.status().get("configured"))
    except Exception:
        return False


def _opaque(value: Any, name: str) -> str:
    out = str(value or "").strip()
    if not out or len(out) > 160 or not all(ch.isalnum() or ch in "._:-" for ch in out):
        raise tools.ToolError(f"{name} must be a valid opaque identifier.")
    return out


def _execute(key: str, args: dict[str, Any]) -> dict[str, Any]:
    if key == "vp3.commerce.catalog.search":
        return connector.request("vp3.commerce.catalog.search", {"query":str(args.get("query") or "")[:240],"limit":max(1,min(100,int(args.get("limit") or 40))),"public_only":bool(args.get("public_only",False))})
    if key == "vp3.commerce.product.get":
        return connector.request("vp3.commerce.product.get", {"product_id":_opaque(args.get("product_id"),"product_id")})
    if key == "vp3.commerce.orders.list":
        payload={"limit":max(1,min(100,int(args.get("limit") or 40)))}
        for field in ("payment_status","order_status"):
            value=str(args.get(field) or "").strip()
            if value: payload[field]=value[:40]
        return connector.request("vp3.commerce.orders.list", payload)
    if key == "vp3.commerce.order.get":
        return connector.request("vp3.commerce.order.get", {"order_id":_opaque(args.get("order_id"),"order_id")})
    if key == "vp3.commerce.checkout.handoff":
        return connector.request("vp3.commerce.checkout.handoff", {"product_id":_opaque(args.get("product_id"),"product_id")})
    if key == "vp3.commerce.fulfillment.update":
        status=str(args.get("status") or "").strip().lower()
        if status not in {"processing","fulfilled"}: raise tools.ToolError("status must be processing or fulfilled.")
        key_value=str(args.get("idempotency_key") or "").strip()
        if not key_value or len(key_value)>160: raise tools.ToolError("Approved fulfillment execution requires a stable idempotency key.")
        return connector.request("vp3.commerce.fulfillment.update", {"order_id":_opaque(args.get("order_id"),"order_id"),"status":status,"idempotency_key":key_value})
    raise tools.ToolError("Unsupported VP3 Agent Commerce tool.")


def install() -> None:
    if getattr(tools,"_vp3_commerce_agent_v061_installed",False): return
    for key,definition in DEFINITIONS.items():
        existing=tools.TOOL_DEFINITIONS.get(key)
        if existing not in (None,definition): raise RuntimeError(f"Tool definition collision: {key}")
        tools.TOOL_DEFINITIONS[key]=definition
    for skill in SKILLS:
        if not any(item.get("key")==skill["key"] for item in tools.SKILL_DEFINITIONS): tools.SKILL_DEFINITIONS=(*tools.SKILL_DEFINITIONS,skill)
    original_list_tools=tools.list_tools;original_list_skills=tools.list_skills;original_set_tool_enabled=tools.set_tool_enabled
    def list_tools(granted_permissions:set[str]|None=None,*,owner:bool=False)->list[dict[str,Any]]:
        items=original_list_tools(granted_permissions,owner=owner)
        return items if _connector_ready() else [item for item in items if item.get("key") not in KEYS]
    def list_skills(granted_permissions:set[str]|None=None,*,owner:bool=False)->list[dict[str,Any]]:
        if _connector_ready(): return original_list_skills(granted_permissions,owner=owner)
        tool_items={item["key"]:item for item in original_list_tools(granted_permissions,owner=owner)}
        skills=[]
        for skill in tools.SKILL_DEFINITIONS:
            if skill.get("key") in SKILL_KEYS: continue
            required=set();available=True
            for tool_key in skill["tools"]:
                item=tool_items.get(tool_key)
                if item is None:
                    available=False
                    continue
                required.update(item["required_permissions"])
                if not owner: required.add(tools.TOOL_EXECUTE_PERMISSION)
                available=available and bool(item["available"])
            skills.append({**skill,"required_permissions":sorted(required),"available":available})
        return skills
    def set_tool_enabled(tool_key:str,enabled:bool)->dict[str,Any]:
        if tool_key in KEYS and not _connector_ready(): raise tools.ToolError("VP3 Agent Commerce connector is not configured.",409)
        return original_set_tool_enabled(tool_key,enabled)
    tools.list_tools=list_tools;tools.list_skills=list_skills;tools.set_tool_enabled=set_tool_enabled
    original_meta=tools._safe_argument_metadata
    def safe_meta(key:str,args:dict[str,Any])->dict[str,Any]:
        if key in KEYS:return {"argument_keys":sorted(k for k in args if k!="idempotency_key"),"product_id":str(args.get("product_id") or "")[:32],"order_id":str(args.get("order_id") or "")[:32],"status":str(args.get("status") or "")[:24]}
        return original_meta(key,args)
    tools._safe_argument_metadata=safe_meta
    original_execute=tools.execute_tool
    def execute_tool(source_app_key:str,tool_key:str,arguments:dict[str,Any]|None,granted_permissions:set[str]|None=None,*,owner:bool=False)->dict[str,Any]:
        if tool_key not in DEFINITIONS:return original_execute(source_app_key,tool_key,arguments,granted_permissions,owner=owner)
        if not _connector_ready():raise tools.ToolError("VP3 Agent Commerce connector is not configured.",409)
        tool=tools._tool_definition(tool_key);source=source_app_key.strip() or ("owner" if owner else "app:unknown");actor="owner" if owner else "app";granted=set(granted_permissions or set());required=[] if owner else sorted({tools.TOOL_EXECUTE_PERMISSION,*tool["required_permissions"]});payload=dict(arguments or {});missing=tools._missing_permissions(tool,granted,owner)
        if missing:raise tools.ToolError(f"Missing tool permissions: {', '.join(missing)}.",403)
        if not tools._policy_map().get(tool_key,True):raise tools.ToolError("Tool is disabled by the HomeServer owner.",403)
        started=time.perf_counter();meta=tools._safe_argument_metadata(tool_key,payload)
        try: result=_execute(tool_key,payload)
        except tools.ToolError as exc:
            run_id=tools._record_run(tool_key=tool_key,source_app_key=source,actor_type=actor,status="failed",required_permissions=required,arguments_meta=meta,duration_ms=int((time.perf_counter()-started)*1000),error=str(exc));raise tools.ToolError(f"{exc} Run {run_id} was recorded.",exc.status_code) from exc
        except connector.VP3CommerceAgentConnectorError as exc:
            run_id=tools._record_run(tool_key=tool_key,source_app_key=source,actor_type=actor,status="failed",required_permissions=required,arguments_meta=meta,duration_ms=int((time.perf_counter()-started)*1000),error=str(exc));raise tools.ToolError(f"{exc} Run {run_id} was recorded.",exc.status_code) from exc
        except Exception as exc:
            run_id=tools._record_run(tool_key=tool_key,source_app_key=source,actor_type=actor,status="failed",required_permissions=required,arguments_meta=meta,duration_ms=int((time.perf_counter()-started)*1000),error="Internal VP3 Agent Commerce tool failure.");raise tools.ToolError(f"VP3 Agent Commerce tool failed safely. Run {run_id} was recorded.",500) from exc
        run_id=tools._record_run(tool_key=tool_key,source_app_key=source,actor_type=actor,status="completed",required_permissions=required,arguments_meta=meta,result_meta={"keys":sorted(result.keys()),"version":connector.VERSION},duration_ms=int((time.perf_counter()-started)*1000));return {"run_id":run_id,"tool":tool_key,"result":result}
    tools.execute_tool=execute_tool;tools._vp3_commerce_agent_v061_installed=True
