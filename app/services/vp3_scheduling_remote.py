from __future__ import annotations

from typing import Any, Callable

from . import remote_bridge

OPERATION="vp3.connector.configure"

def install()->None:
    if getattr(remote_bridge,"_vp3_scheduling_v059_installed",False):return
    original:Callable[[str,dict|None,str|None],dict]=remote_bridge.dispatch_remote_request
    def dispatch_remote_request(operation:str,payload:dict|None,bearer_token:str|None=None)->dict:
        if str(operation or "").strip()!=OPERATION:return original(operation,payload,bearer_token)
        body=payload if isinstance(payload,dict) else {}
        allowed={"endpoint","token","version","capabilities"}
        if set(body)-allowed:raise remote_bridge.RemoteBridgeError("VP3 scheduling connector payload contains unsupported fields.")
        if remote_bridge._payload_size(body)>32768:raise remote_bridge.RemoteBridgeError("VP3 scheduling connector payload is too large.")
        return original("tool.execute",{"tool_key":"vp3.connector.configure","arguments":body},bearer_token)
    remote_bridge.dispatch_remote_request=dispatch_remote_request;remote_bridge._vp3_scheduling_v059_installed=True
