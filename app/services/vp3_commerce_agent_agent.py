from __future__ import annotations

from typing import Any, Callable

from . import action_policy, agent_tools, app_scopes, tools, vp3_commerce_agent_approvals, vp3_commerce_agent_connector

READS = {
    "homeserver_vp3_commerce_catalog_search":"vp3.commerce.catalog.search",
    "homeserver_vp3_commerce_product_get":"vp3.commerce.product.get",
    "homeserver_vp3_commerce_orders_list":"vp3.commerce.orders.list",
    "homeserver_vp3_commerce_order_get":"vp3.commerce.order.get",
    "homeserver_vp3_commerce_checkout_handoff":"vp3.commerce.checkout.handoff",
}
WRITES = {"homeserver_vp3_commerce_fulfillment_request":"vp3.commerce.fulfillment.update"}
DESCRIPTIONS = {
    "homeserver_vp3_commerce_catalog_search":"Search the owner's canonical VP3 Commerce catalog. Use private HomeServer context to recommend, but never invent or alter Cloud price or terms.",
    "homeserver_vp3_commerce_product_get":"Read canonical VP3 product details, price, terms digest and public checkout availability.",
    "homeserver_vp3_commerce_orders_list":"Review the owner's canonical VP3 Commerce orders and current payment/fulfillment states.",
    "homeserver_vp3_commerce_order_get":"Read one canonical VP3 Commerce order and its safe item/fulfillment projection.",
    "homeserver_vp3_commerce_checkout_handoff":"Prepare the public VP3 checkout URL. This does not create an order, accept buyer terms, authorize payment, or move money; the buyer completes checkout in VP3 Cloud.",
    "homeserver_vp3_commerce_fulfillment_request":"Propose moving a fully-paid generic Profile Commerce order to processing or fulfilled. Local HomeServer owner approval is required before Cloud mutation.",
}


def _connector_ready()->bool:
    try:return bool(vp3_commerce_agent_connector.status().get("configured"))
    except Exception:return False


def install()->None:
    if getattr(agent_tools,"_vp3_commerce_agent_v061_installed",False):return
    action_policy.APPROVAL_ONLY_WRITE_TOOLS.add("vp3.commerce.fulfillment.update")
    original_schemas:Callable[...,list[dict[str,Any]]]=agent_tools.model_tool_schemas
    def model_tool_schemas(granted_permissions:set[str]|None=None,*,owner:bool=False,allow_write_proposals:bool=False,source_app_key:str|None=None)->list[dict[str,Any]]:
        schemas=original_schemas(granted_permissions,owner=owner,allow_write_proposals=allow_write_proposals,source_app_key=source_app_key)
        if not _connector_ready():return schemas
        granted=set(granted_permissions or set());available={item["key"]:item for item in tools.list_tools(granted,owner=owner) if item.get("available")};scope=None if owner or not source_app_key else app_scopes.get_scope_for_source(source_app_key)
        for model_name,tool_key in READS.items():
            item=available.get(tool_key)
            if not item or (scope is not None and not app_scopes.tool_allowed(scope,tool_key)):continue
            schemas.append({"type":"function","function":{"name":model_name,"description":DESCRIPTIONS[model_name],"parameters":item["input_schema"]}})
        if allow_write_proposals:
            for model_name,tool_key in WRITES.items():
                item=available.get(tool_key)
                if not item or (scope is not None and not app_scopes.tool_allowed(scope,tool_key)):continue
                execution=agent_tools._execution_policy(source_app_key,tool_key,owner)
                if execution and execution["policy_mode"]==action_policy.SENSITIVE_HIGH_IMPACT:continue
                schemas.append({"type":"function","function":{"name":model_name,"description":DESCRIPTIONS[model_name],"parameters":item["input_schema"]}})
        return schemas
    agent_tools.model_tool_schemas=model_tool_schemas;original_execute=agent_tools.execute_model_tool
    def execute_model_tool(source_app_key:str,model_tool_name:str,arguments:dict[str,Any]|None,granted_permissions:set[str]|None=None,*,owner:bool=False)->dict[str,Any]:
        if model_tool_name in READS or model_tool_name in WRITES:
            if not _connector_ready():raise agent_tools._deny_unavailable(source_app_key,owner)
        if model_tool_name in READS:return tools.execute_tool(source_app_key,READS[model_tool_name],arguments or {},set(granted_permissions or set()),owner=owner)
        if model_tool_name not in WRITES:return original_execute(source_app_key,model_tool_name,arguments,granted_permissions,owner=owner)
        policy=agent_tools.get_policy()
        if not policy["enabled"] or not policy["allow_write_proposals"]:raise agent_tools._deny_unavailable(source_app_key,owner)
        tool_key=WRITES[model_tool_name];granted=set(granted_permissions or set());available={item["key"]:item for item in tools.list_tools(granted,owner=owner) if item.get("available")}
        if tool_key not in available:raise agent_tools._deny_unavailable(source_app_key,owner)
        execution=agent_tools._execution_policy(source_app_key,tool_key,owner)
        if execution and execution["policy_mode"]==action_policy.SENSITIVE_HIGH_IMPACT:raise agent_tools._deny_unavailable(source_app_key,owner)
        args=arguments or {}
        if not agent_tools._scope_allows(source_app_key,tool_key,args,execution):raise agent_tools._deny_unavailable(source_app_key,owner)
        result=vp3_commerce_agent_approvals.create_request(source_app_key,tool_key,args,owner=owner);request_id=str(((result.get("result") or {}).get("request_id") or "")) or None
        agent_tools._record_policy(execution,"approval_requested",request_id=request_id,reason="VP3 Commerce fulfillment requires local owner approval before Cloud execution.")
        return result
    agent_tools.execute_model_tool=execute_model_tool;agent_tools._vp3_commerce_agent_v061_installed=True
