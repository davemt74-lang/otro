from __future__ import annotations

import json
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
temp = tempfile.TemporaryDirectory(prefix="homeserver-authority-section14-")
os.environ["HOMESERVER_DATA_DIR"] = temp.name
from app.database import db, initialize_database
from app.services import action_policy, app_scopes, approval_federation, approvals, memory_continuity, pairing, tools
from app.services import local_file_actions_tools, local_file_actions_approvals, vp3_scheduling_tools, vp3_scheduling_approvals, vp3_commerce_agent_tools, vp3_commerce_agent_approvals

initialize_database()
for module in [local_file_actions_tools,local_file_actions_approvals,vp3_scheduling_tools,vp3_scheduling_approvals,vp3_commerce_agent_tools,vp3_commerce_agent_approvals]:
    module.install()
grants = {"tools.execute", "memory.write", "memory.read", "approvals.review", "files.write", "scheduling.write", "commerce.fulfill"}
paired = pairing.create_pairing_request("authority", "Authority", sorted(grants))
pairing.approve_pairing(paired["code"])
identity = pairing.authenticate(paired["claim_token"])
app_id = identity["id"]
source = "app:authority"
def proposal(key="scope.allowed"):
    return approvals.create_memory_write_request(source, {"memory_key":key,"content":"PRIVATE_AUTHORITY_CONTENT_14"})["result"]["request_id"]
def count():
    with db() as connection:return connection.execute("SELECT COUNT(*) FROM agent_memory").fetchone()[0]
def rejected(call, status=403):
    try:call()
    except (approvals.ApprovalError, tools.ToolError) as exc:
        assert exc.status_code==status,(type(exc),str(exc),exc.status_code)
        assert "PRIVATE_AUTHORITY_CONTENT_14" not in str(exc)
        return
    raise AssertionError("Expected authority denial")
def permission(name,allowed):
    with db() as connection:connection.execute("UPDATE app_permissions SET allowed=? WHERE paired_app_id=? AND permission=?",(int(allowed),app_id,name))
before=count()
request=proposal();permission("memory.write",False)
rejected(lambda:approvals.approve_request(request))
assert approvals._request_for_owner(request)["status"]=="pending" and count()==before
permission("memory.write",True)
app_scopes.save_scope(app_id,{"tool_names":["memory.list"]})
rejected(lambda:approvals.approve_request(request));assert count()==before
app_scopes.save_scope(app_id,{"memory_key_prefixes":["private."]})
rejected(lambda:approvals.approve_request(request));assert count()==before
app_scopes.save_scope(app_id,{})
action_policy.set_policy(app_id,"memory.write",action_policy.SENSITIVE_HIGH_IMPACT)
rejected(lambda:approval_federation.review_request_for_app("authority",request,"approve"));assert count()==before
action_policy.set_policy(app_id,"memory.write","inherit")
permission("approvals.review",False)
rejected(lambda:approval_federation.review_request_for_app("authority",request,"approve"))
permission("approvals.review",True)
# Recheck the reviewer at claim, after the initial remote review lookup.
original=approvals._reserve_request
def revoke_reviewer(item):
    permission("approvals.review",False)
    return original(item)
with patch.object(approvals,"_reserve_request",side_effect=revoke_reviewer):
    rejected(lambda:approval_federation.review_request_for_app("authority",request,"approve"))
permission("approvals.review",True)
approved=approvals.approve_request(request)
assert approved["status"]=="executed" and count()==before+1
rejected(lambda:approvals.approve_request(request),409)
# A claim does not freeze authority for the later tool invocation.
late=proposal();original_execute=tools.execute_tool
def revoke_before_execution(*args,**kwargs):
    permission("memory.write",False)
    return original_execute(*args,**kwargs)
with patch.object(tools,"execute_tool",side_effect=revoke_before_execution):
    rejected(lambda:approvals.approve_request(late))
assert approvals._request_for_owner(late)["status"]=="failed" and count()==before+1
permission("memory.write",True)
# The resource owner flag cannot broaden a deferred update or deletion.
memory=memory_continuity.list_federated_memories(owner=True)[0]
for action in ["memory.update","memory.delete"]:
    arguments={"canonical_id":memory["canonical_id"],"expected_revision":memory["record_revision"],"mutation_id":"section14-"+action}
    if action.endswith("update"):arguments["content"]="Changed"
    create=approvals.create_memory_update_request if action.endswith("update") else approvals.create_memory_delete_request
    rid=create(source,arguments)["result"]["request_id"]
    app_scopes.save_scope(app_id,{"memory_key_prefixes":["elsewhere."]})
    rejected(lambda:approvals.approve_request(rid));assert count()==before+1
    app_scopes.save_scope(app_id,{})
# Old identity snapshots cannot run tools after grants or policy change.
action_policy.set_policy(app_id,"memory.write",action_policy.SAFE_AUTOMATIC)
permission("memory.write",False)
rejected(lambda:tools.execute_tool(source,"memory.write",{"content":"PRIVATE_AUTHORITY_CONTENT_14"},grants))
permission("memory.write",True)
action_policy.set_policy(app_id,"memory.write",action_policy.APPROVAL_REQUIRED)
rejected(lambda:tools.execute_tool(source,"memory.write",{"content":"PRIVATE_AUTHORITY_CONTENT_14"},grants))
missing=proposal();pairing.revoke_paired_app("authority")
rejected(lambda:approvals.approve_request(missing),409);assert count()==before+1
assert approvals._request_for_owner(missing)["status"]=="denied"
paired=pairing.create_pairing_request("authority","Authority",sorted(grants));pairing.approve_pairing(paired["code"])
rejected(lambda:approvals.approve_request(missing),409)
old=proposal();claimed=approvals._reserve_request(approvals._request_for_owner(old))
paired=pairing.create_pairing_request("authority","Authority",sorted(grants));pairing.approve_pairing(paired["code"])
rejected(lambda:tools.execute_tool(source,"memory.write",claimed["arguments"],set(),owner=True,approval_request_id=old))
assert count()==before+1,'Re-pairing revived an earlier executing proposal'
# Owner proposals remain valid, but expiry is checked again during claim.
owner=approvals.create_memory_write_request("owner",{"content":"Owner"},owner=True)["result"]["request_id"]
loaded=approvals._request_for_owner(owner)
future=approvals._now()+timedelta(days=2)
with patch.object(approvals,"_now",return_value=future):rejected(lambda:approvals._reserve_request(loaded),409)
assert approvals.approve_request(owner)["status"]=="executed"
concurrent=approvals.create_memory_write_request("owner",{"content":"Once"},owner=True)["result"]["request_id"]
def approve_once(_):
    try:return approvals.approve_request(concurrent)["status"]
    except approvals.ApprovalError as exc:assert exc.status_code==409;return "conflict"
with ThreadPoolExecutor(2) as pool:outcomes=list(pool.map(approve_once,range(2)))
assert sorted(outcomes)==["conflict","executed"]
with db() as connection:
    audit=json.dumps([dict(row) for row in connection.execute("SELECT arguments_meta_json,result_meta_json,error FROM tool_runs")])
assert "PRIVATE_AUTHORITY_CONTENT_14" not in audit
print("SECTION14_HOMESERVER_AUTHORITY=PASS")
