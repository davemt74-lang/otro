from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from .services import tracky_cross_site_presence, tracky_federated_agent_context, tracky_federated_query, tracky_federated_world, tracky_federation_operations, tracky_federation_policy, tracky_federation_reconciliation, tracky_federation_sync, tracky_forecast_calibration, tracky_governed_actions, tracky_identity_continuity, tracky_mobile_transition, tracky_model_lifecycle, tracky_physical_context, tracky_physical_world_dashboard, tracky_site_topology
from .services.pairing import authenticate


router = APIRouter()


class ActivePerceptionRequest(BaseModel):
    request_type: str = Field(min_length=2, max_length=60)
    request_id: str | None = Field(default=None, min_length=8, max_length=128)
    correlation_id: str | None = Field(default=None, min_length=8, max_length=128)
    site_id: str | None = Field(default=None, max_length=100)
    target: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(default="", max_length=500)

class PhysicalActionProposal(BaseModel):
    intent_id: str | None = Field(default=None, min_length=8, max_length=128)
    correlation_id: str | None = Field(default=None, min_length=8, max_length=128)
    site_id: str | None = Field(default=None, max_length=100)
    origin_kind: str = Field(default="agent_suggestion", max_length=40)
    requested_mode: str = Field(default="suggest_only", max_length=40)
    device_key: str = Field(min_length=1, max_length=80)
    command: str = Field(min_length=1, max_length=40)
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=1, max_length=1000)
    source_event_id: str = Field(default="", max_length=128)


def _paired_app(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    identity = authenticate(authorization.removeprefix("Bearer ").strip())
    if identity is None:
        raise HTTPException(status_code=401, detail="Invalid or revoked token")
    return identity


def _physical_reader(identity: dict = Depends(_paired_app)) -> dict:
    if "awareness.read" not in identity.get("permissions", []):
        raise HTTPException(status_code=403, detail="Permission required: awareness.read")
    return identity


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except tracky_physical_context.TrackyPhysicalError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except tracky_governed_actions.TrackyActionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except tracky_forecast_calibration.TrackyCalibrationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except tracky_model_lifecycle.TrackyLifecycleError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except tracky_site_topology.TrackySiteTopologyError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except tracky_federated_world.TrackyFederatedWorldError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except tracky_federation_sync.TrackyFederationSyncError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except tracky_federation_reconciliation.TrackyFederationReconciliationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except tracky_mobile_transition.TrackyMobileTransitionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except tracky_identity_continuity.TrackyIdentityContinuityError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except tracky_federated_agent_context.TrackyFederatedAgentContextError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except tracky_federated_query.TrackyFederatedQueryError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except tracky_federation_policy.TrackyFederationPolicyError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/api/v1/tracky/capabilities")
def paired_tracky_capabilities(identity: dict = Depends(_physical_reader)) -> dict:
    return {
        "tracky": tracky_physical_context.public_capability(),
        "app": identity["app_key"],
    }


@router.get("/api/v1/tracky/context")
def paired_tracky_context(identity: dict = Depends(_physical_reader)) -> dict:
    return {
        **tracky_physical_context.current_context(),
        "app": identity["app_key"],
    }


@router.post("/api/v1/tracky/active-perception")
def paired_tracky_active_perception(
    payload: ActivePerceptionRequest,
    identity: dict = Depends(_physical_reader),
) -> dict:
    return _call(
        tracky_physical_context.active_perception,
        payload.request_type,
        request_id=payload.request_id,
        correlation_id=payload.correlation_id,
        site_id=payload.site_id,
        target=payload.target,
        reason=payload.reason,
        requested_by=identity["app_key"],
    )


@router.get("/api/v1/tracky/active-perception/{request_id}")
def paired_tracky_active_perception_status(
    request_id: str,
    identity: dict = Depends(_physical_reader),
) -> dict:
    return _call(tracky_physical_context.request_status, request_id)


@router.get("/api/v1/tracky/model-lifecycle")
def paired_tracky_model_lifecycle(
    identity: dict = Depends(_physical_reader),
) -> dict:
    return {
        "lifecycle": tracky_model_lifecycle.current_report(),
        "health": tracky_model_lifecycle.health_summary(),
        "capability": tracky_model_lifecycle.public_capability(),
        "app": identity["app_key"],
    }


@router.get("/api/v1/tracky/site-topology")
def paired_tracky_site_topology(
    identity: dict = Depends(_physical_reader),
) -> dict:
    return {
        "topology": tracky_site_topology.current_topology(),
        "capability": tracky_site_topology.public_capability(),
        "app": identity["app_key"],
    }


@router.get("/api/v1/tracky/federated-world")
def paired_tracky_federated_world(
    site_id: str | None = None,
    identity: dict = Depends(_physical_reader),
) -> dict:
    return {
        "world": tracky_federation_policy.filter_world_report_for_local(
            tracky_federated_world.current_report(site_id), site_id
        ),
        "capability": tracky_federated_world.public_capability(),
        "app": identity["app_key"],
    }


@router.get("/api/v1/tracky/federation-sync")
def paired_tracky_federation_sync(
    identity: dict = Depends(_physical_reader),
) -> dict:
    return {
        "sync": tracky_federation_sync.status(),
        "capability": tracky_federation_sync.public_capability(),
        "app": identity["app_key"],
    }


@router.get("/api/v1/tracky/federation-reconciliation")
def paired_tracky_federation_reconciliation(
    identity: dict = Depends(_physical_reader),
) -> dict:
    return {
        "reconciliation": tracky_federation_reconciliation.current_report(),
        "capability": tracky_federation_reconciliation.public_capability(),
        "app": identity["app_key"],
    }


@router.get("/api/v1/tracky/federation-operations")
def paired_tracky_federation_operations(
    identity: dict = Depends(_physical_reader),
) -> dict:
    return {
        "operations": tracky_federation_operations.current_report(),
        "capability": tracky_federation_operations.public_capability(),
        "app": identity["app_key"],
    }


@router.get("/api/v1/tracky/physical-world-dashboard")
def paired_tracky_physical_world_dashboard(
    site: str = "",
    identity: dict = Depends(_physical_reader),
) -> dict:
    return {
        "dashboard": tracky_physical_world_dashboard.current_report(site),
        "capability": tracky_physical_world_dashboard.public_capability(),
        "app": identity["app_key"],
    }


@router.get("/api/v1/tracky/cross-site-presence")
def paired_tracky_cross_site_presence(
    identity: dict = Depends(_physical_reader),
) -> dict:
    return {
        "presence": tracky_cross_site_presence.current_report(),
        "capability": tracky_cross_site_presence.public_capability(),
        "app": identity["app_key"],
    }


@router.get("/api/v1/tracky/mobile-transitions")
def paired_tracky_mobile_transitions(
    active_only: bool = False,
    identity: dict = Depends(_physical_reader),
) -> dict:
    report = tracky_federation_policy.filter_mobile_report_for_local(
        tracky_mobile_transition.current_report(active_only=active_only)
    )
    return {
        "mobile_transitions": report,
        "agent_context": tracky_mobile_transition.agent_context(report),
        "capability": tracky_mobile_transition.public_capability(),
        "app": identity["app_key"],
    }


@router.get("/api/v1/tracky/identity-continuity")
def paired_tracky_identity_continuity(
    entity_ref: str | None = None,
    identity: dict = Depends(_physical_reader),
) -> dict:
    report = tracky_federation_policy.filter_identity_report_for_local(
        tracky_identity_continuity.current_report()
    )
    resolved = tracky_federation_policy.resolve_identity_for_local(entity_ref) if entity_ref else None
    return {
        "identity_continuity": report,
        "agent_context": tracky_identity_continuity.agent_context(report),
        "resolved_identity": resolved,
        "capability": tracky_identity_continuity.public_capability(),
        "app": identity["app_key"],
    }


@router.get("/api/v1/tracky/federated-agent-context")
def paired_tracky_federated_agent_context(
    identity: dict = Depends(_physical_reader),
) -> dict:
    return {
        "federated_agent_context": tracky_federated_agent_context.current_context(refresh=True),
        "capability": tracky_federated_agent_context.public_capability(),
        "app": identity["app_key"],
    }


@router.get("/api/v1/tracky/federated-query")
def paired_tracky_federated_query(
    intent: str = "current_state",
    site_id: str | None = None,
    target_ref: str | None = None,
    since_ms: int = 0,
    until_ms: int = 0,
    limit: int = 50,
    query_id: str | None = None,
    identity: dict = Depends(_physical_reader),
) -> dict:
    request = {
        "intent": intent,
        "site_id": site_id,
        "target_ref": target_ref or "",
        "since_ms": since_ms,
        "until_ms": until_ms,
        "limit": limit,
        "query_id": query_id or "",
    }
    return {
        "query": _call(tracky_federated_query.execute_query, request),
        "capability": tracky_federated_query.public_capability(),
        "app": identity["app_key"],
    }


@router.get("/api/v1/tracky/federated-query/audit")
def paired_tracky_federated_query_audit(
    limit: int = 50,
    identity: dict = Depends(_physical_reader),
) -> dict:
    return {
        "audit": _call(tracky_federated_query.recent_query_audit, limit),
        "capability": tracky_federated_query.public_capability(),
        "app": identity["app_key"],
    }


@router.get("/api/v1/tracky/federation-policy")
def paired_tracky_federation_policy(
    identity: dict = Depends(_physical_reader),
) -> dict:
    return {
        "federation_policy": tracky_federation_policy.current_report(),
        "capability": tracky_federation_policy.public_capability(),
        "app": identity["app_key"],
    }


@router.get("/api/v1/tracky/calibration")
def paired_tracky_calibration(
    identity: dict = Depends(_physical_reader),
) -> dict:
    return {
        "calibration": tracky_forecast_calibration.current_report(),
        "summary": tracky_forecast_calibration.summary(),
        "app": identity["app_key"],
    }


@router.post("/api/v1/tracky/actions/propose")
def paired_tracky_action_propose(
    payload: PhysicalActionProposal,
    identity: dict = Depends(_physical_reader),
) -> dict:
    return _call(
        tracky_governed_actions.propose_device_action,
        intent_id=payload.intent_id,
        correlation_id=payload.correlation_id,
        site_id=payload.site_id,
        origin_kind=payload.origin_kind,
        requested_mode=payload.requested_mode,
        device_key=payload.device_key,
        command=payload.command,
        arguments=payload.arguments,
        reason=payload.reason,
        requested_by=identity["app_key"],
        granted_permissions=set(identity.get("permissions") or []),
        source_event_id=payload.source_event_id,
    )


@router.get("/api/v1/tracky/actions/{intent_id}")
def paired_tracky_action_status(
    intent_id: str,
    identity: dict = Depends(_physical_reader),
) -> dict:
    return _call(tracky_governed_actions.action_status, intent_id)


@router.post("/api/v1/tracky/cloud-sync")
def paired_tracky_cloud_sync(identity: dict = Depends(_physical_reader)) -> dict:
    return _call(tracky_physical_context.sync_cloud)


@router.get("/api/v1/tracky/cloud-sync")
def paired_tracky_cloud_sync_status(identity: dict = Depends(_physical_reader)) -> dict:
    return {
        "sync": tracky_physical_context.sync_status(),
        "app": identity["app_key"],
    }
