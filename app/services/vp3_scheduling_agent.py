from __future__ import annotations

from typing import Any, Callable

from . import action_policy, agent_tools, app_scopes, tools, vp3_scheduling_approvals

READS={
 "homeserver_vp3_calendar_overview":"vp3.schedule.overview",
 "homeserver_vp3_schedule_availability":"vp3.schedule.availability",
}
WRITES={
 "homeserver_vp3_booking_create_request":"vp3.booking.create",
 "homeserver_vp3_booking_reschedule_request":"vp3.booking.reschedule",
 "homeserver_vp3_booking_cancel_request":"vp3.booking.cancel",
}
DESCRIPTIONS={
 "homeserver_vp3_calendar_overview":"Review the owner's normalized VP3 personal and Team calendar configuration and upcoming bookings.",
 "homeserver_vp3_schedule_availability":"Check live VP3 personal or Team availability. Busy times from connected Google/Outlook calendars are applied in VP3 without exposing provider credentials.",
 "homeserver_vp3_booking_create_request":"Propose a new personal or Team VP3 booking. The booking is not created until local HomeServer owner approval.",
 "homeserver_vp3_booking_reschedule_request":"Propose moving an active personal VP3 booking. The calendar is not changed until local HomeServer owner approval.",
 "homeserver_vp3_booking_cancel_request":"Propose cancelling an active personal or Team VP3 booking. Cancellation requires local HomeServer owner approval.",
}

def install()->None:
    if getattr(agent_tools,"_vp3_scheduling_v059_installed",False):return
    original_schemas:Callable[...,list[dict[str,Any]]]=agent_tools.model_tool_schemas
    def model_tool_schemas(granted_permissions:set[str]|None=None,*,owner:bool=False,allow_write_proposals:bool=False,source_app_key:str|None=None)->list[dict[str,Any]]:
        schemas=original_schemas(granted_permissions,owner=owner,allow_write_proposals=allow_write_proposals,source_app_key=source_app_key);granted=set(granted_permissions or set());available={item["key"]:item for item in tools.list_tools(granted,owner=owner) if item.get("available")};scope=None if owner or not source_app_key else app_scopes.get_scope_for_source(source_app_key)
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
    agent_tools.model_tool_schemas=model_tool_schemas
    original_execute=agent_tools.execute_model_tool
    def execute_model_tool(source_app_key:str,model_tool_name:str,arguments:dict[str,Any]|None,granted_permissions:set[str]|None=None,*,owner:bool=False)->dict[str,Any]:
        if model_tool_name in READS:
            tool_key=READS[model_tool_name];return tools.execute_tool(source_app_key,tool_key,arguments or {},set(granted_permissions or set()),owner=owner)
        if model_tool_name not in WRITES:return original_execute(source_app_key,model_tool_name,arguments,granted_permissions,owner=owner)
        policy=agent_tools.get_policy()
        if not policy["enabled"] or not policy["allow_write_proposals"]:raise agent_tools._deny_unavailable(source_app_key,owner)
        tool_key=WRITES[model_tool_name];granted=set(granted_permissions or set());available={item["key"]:item for item in tools.list_tools(granted,owner=owner) if item.get("available")}
        if tool_key not in available:raise agent_tools._deny_unavailable(source_app_key,owner)
        execution=agent_tools._execution_policy(source_app_key,tool_key,owner)
        if execution and execution["policy_mode"]==action_policy.SENSITIVE_HIGH_IMPACT:raise agent_tools._deny_unavailable(source_app_key,owner)
        args=arguments or {}
        if not agent_tools._scope_allows(source_app_key,tool_key,args,execution):raise agent_tools._deny_unavailable(source_app_key,owner)
        result=vp3_scheduling_approvals.create_request(source_app_key,tool_key,args,owner=owner);request_id=str(((result.get("result") or {}).get("request_id") or "")) or None
        agent_tools._record_policy(execution,"approval_requested",request_id=request_id,reason="VP3 scheduling mutation requires local owner approval before cloud execution.")
        return result
    agent_tools.execute_model_tool=execute_model_tool;agent_tools._vp3_scheduling_v059_installed=True
