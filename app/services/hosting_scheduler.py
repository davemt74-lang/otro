from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from . import hosting_runtime

CONTRACT="vp3.hosting.scheduler.v2"
GLOBAL_MAX_INFLIGHT=32
SITE_MAX_INFLIGHT=8
ADMISSION_WAIT_SECONDS=2.0
DRAIN_WAIT_SECONDS=10.0


class SchedulerError(hosting_runtime.HostingError):
    pass


@dataclass
class _SiteState:
    inflight:int=0
    admitted:int=0
    rejected:int=0
    completed:int=0
    failed:int=0
    draining:bool=False
    last_rejection_reason:str|None=None
    condition:threading.Condition=field(default_factory=lambda:threading.Condition(threading.RLock()))


_GLOBAL_LOCK=threading.RLock()
_GLOBAL_CONDITION=threading.Condition(_GLOBAL_LOCK)
_GLOBAL_INFLIGHT=0
_SITES:dict[str,_SiteState]={}


def _state(site_id:str)->_SiteState:
    hosting_runtime._safe_site_id(site_id)
    with _GLOBAL_LOCK:
        state=_SITES.get(site_id)
        if state is None:
            state=_SiteState()
            _SITES[site_id]=state
        return state


def _snapshot_unlocked(site_id:str,state:_SiteState)->dict[str,Any]:
    return {
        "contract":CONTRACT,
        "site_id":site_id,
        "inflight":state.inflight,
        "admitted":state.admitted,
        "rejected":state.rejected,
        "completed":state.completed,
        "failed":state.failed,
        "draining":state.draining,
        "last_rejection_reason":state.last_rejection_reason,
        "site_max_inflight":SITE_MAX_INFLIGHT,
        "global_max_inflight":GLOBAL_MAX_INFLIGHT,
    }


def status(site_id:str)->dict[str,Any]:
    state=_state(site_id)
    with _GLOBAL_LOCK:
        result=_snapshot_unlocked(site_id,state)
        result["global_inflight"]=_GLOBAL_INFLIGHT
    return result


def inventory()->dict[str,Any]:
    with _GLOBAL_LOCK:
        items=[_snapshot_unlocked(site_id,state) for site_id,state in sorted(_SITES.items())]
        return {
            "contract":CONTRACT,
            "global_inflight":_GLOBAL_INFLIGHT,
            "global_max_inflight":GLOBAL_MAX_INFLIGHT,
            "site_max_inflight":SITE_MAX_INFLIGHT,
            "sites":items,
        }


def _admit(site_id:str,timeout:float)->None:
    global _GLOBAL_INFLIGHT
    state=_state(site_id)
    deadline=time.monotonic()+max(0.0,float(timeout))
    with _GLOBAL_CONDITION:
        while True:
            if state.draining:
                state.rejected+=1
                state.last_rejection_reason="draining"
                raise SchedulerError("Hosted site is draining for a runtime transition.",503)
            if state.inflight<SITE_MAX_INFLIGHT and _GLOBAL_INFLIGHT<GLOBAL_MAX_INFLIGHT:
                state.inflight+=1
                state.admitted+=1
                _GLOBAL_INFLIGHT+=1
                return
            remaining=deadline-time.monotonic()
            if remaining<=0:
                state.rejected+=1
                state.last_rejection_reason="capacity"
                raise SchedulerError("Hosted runtime is at request capacity.",503)
            _GLOBAL_CONDITION.wait(timeout=remaining)


def _release(site_id:str,failed:bool)->None:
    global _GLOBAL_INFLIGHT
    state=_state(site_id)
    with _GLOBAL_CONDITION:
        if state.inflight<=0 or _GLOBAL_INFLIGHT<=0:
            raise SchedulerError("Hosted runtime scheduler accounting underflow.",500)
        state.inflight-=1
        _GLOBAL_INFLIGHT-=1
        if failed:
            state.failed+=1
        else:
            state.completed+=1
        _GLOBAL_CONDITION.notify_all()


@contextmanager
def request_slot(site_id:str,timeout:float=ADMISSION_WAIT_SECONDS):
    _admit(site_id,timeout)
    failed=True
    try:
        yield status(site_id)
        failed=False
    finally:
        _release(site_id,failed)


def begin_drain(site_id:str,timeout:float=DRAIN_WAIT_SECONDS)->dict[str,Any]:
    state=_state(site_id)
    deadline=time.monotonic()+max(0.0,float(timeout))
    with _GLOBAL_CONDITION:
        state.draining=True
        _GLOBAL_CONDITION.notify_all()
        while state.inflight>0:
            remaining=deadline-time.monotonic()
            if remaining<=0:
                state.draining=False
                state.last_rejection_reason="drain_timeout"
                _GLOBAL_CONDITION.notify_all()
                raise SchedulerError("Hosted site could not drain active requests before the runtime transition.",409)
            _GLOBAL_CONDITION.wait(timeout=remaining)
        result=_snapshot_unlocked(site_id,state)
        result["global_inflight"]=_GLOBAL_INFLIGHT
        return result


def end_drain(site_id:str)->dict[str,Any]:
    state=_state(site_id)
    with _GLOBAL_CONDITION:
        state.draining=False
        _GLOBAL_CONDITION.notify_all()
        result=_snapshot_unlocked(site_id,state)
        result["global_inflight"]=_GLOBAL_INFLIGHT
        return result


def reset_for_tests()->None:
    global _GLOBAL_INFLIGHT
    with _GLOBAL_CONDITION:
        _SITES.clear()
        _GLOBAL_INFLIGHT=0
        _GLOBAL_CONDITION.notify_all()


def public_capability()->dict[str,Any]:
    return {
        "contract":CONTRACT,
        "global_max_inflight":GLOBAL_MAX_INFLIGHT,
        "site_max_inflight":SITE_MAX_INFLIGHT,
        "admission_wait_seconds":ADMISSION_WAIT_SECONDS,
        "drain_wait_seconds":DRAIN_WAIT_SECONDS,
        "per_site_isolation":True,
        "global_admission_control":True,
        "bounded_backpressure":True,
        "graceful_drain":True,
        "crash_accounting":True,
        "process_local_scheduler":True,
        "container_runtime":False,
    }
