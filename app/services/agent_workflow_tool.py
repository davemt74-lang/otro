from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

from . import agent_routing, agent_tools, context_chat

_CURRENT_PARENT_AGENT_ID: ContextVar[int | None] = ContextVar("agent_workflow_parent_agent_id", default=None)
_CURRENT_CONVERSATION_ID: ContextVar[str | None] = ContextVar("agent_workflow_conversation_id", default=None)
_DELEGATION_DEPTH: ContextVar[int] = ContextVar("agent_workflow_delegation_depth", default=0)
_INSTALLED = False


@contextmanager
def worker_scope() -> Iterator[None]:
    token = _DELEGATION_DEPTH.set(_DELEGATION_DEPTH.get() + 1)
    try:
        yield
    finally:
        _DELEGATION_DEPTH.reset(token)


def delegation_depth() -> int:
    return int(_DELEGATION_DEPTH.get())


def current_parent_agent_id() -> int | None:
    return _CURRENT_PARENT_AGENT_ID.get()


def current_conversation_id() -> str | None:
    return _CURRENT_CONVERSATION_ID.get()


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    original_chat = context_chat.chat
    original_schemas = agent_tools.model_tool_schemas
    original_execute = agent_tools.execute_model_tool

    def scoped_chat(
        source_app_key: str,
        message: str,
        conversation_id: str | None = None,
        *,
        agent_id: int | None = None,
        include_memory: bool = True,
        include_knowledge: bool = True,
        include_contacts: bool = False,
        context_options: dict[str, Any] | None = None,
        tool_permissions: set[str] | None = None,
        owner_tools: bool = False,
    ) -> dict[str, Any]:
        agent = agent_routing.resolve_agent(source_app_key, agent_id, owner=owner_tools)
        parent_token = _CURRENT_PARENT_AGENT_ID.set(int(agent["id"]))
        conversation_token = _CURRENT_CONVERSATION_ID.set(str(conversation_id) if conversation_id else None)
        try:
            return original_chat(
                source_app_key,
                message,
                conversation_id,
                agent_id=agent_id,
                include_memory=include_memory,
                include_knowledge=include_knowledge,
                include_contacts=include_contacts,
                context_options=context_options,
                tool_permissions=tool_permissions,
                owner_tools=owner_tools,
            )
        finally:
            _CURRENT_CONVERSATION_ID.reset(conversation_token)
            _CURRENT_PARENT_AGENT_ID.reset(parent_token)

    def workflow_schemas(
        granted_permissions: set[str] | None = None,
        *,
        owner: bool = False,
        allow_write_proposals: bool = False,
        source_app_key: str | None = None,
    ) -> list[dict[str, Any]]:
        schemas = original_schemas(
            granted_permissions,
            owner=owner,
            allow_write_proposals=allow_write_proposals,
            source_app_key=source_app_key,
        )
        if delegation_depth() > 0:
            return schemas
        parent_agent_id = current_parent_agent_id()
        if parent_agent_id is None:
            return schemas
        from . import agent_workflows

        policy = agent_workflows.get_policy()
        if not policy.get("enabled"):
            return schemas
        try:
            workers = agent_workflows.available_workers(
                str(source_app_key or "owner"),
                int(parent_agent_id),
                owner=owner,
            ).get("items", [])
        except (agent_workflows.AgentWorkflowError, agent_routing.AgentRoutingError):
            workers = []
        if not workers:
            return schemas
        schema = agent_workflows.model_tool_schema(enabled=True)
        if schema is not None:
            schemas.append(schema)
        return schemas

    def workflow_execute(
        source_app_key: str,
        model_tool_name: str,
        arguments: dict[str, Any] | None,
        granted_permissions: set[str] | None = None,
        *,
        owner: bool = False,
    ) -> dict[str, Any]:
        from . import agent_workflows

        if model_tool_name != agent_workflows.MODEL_DELEGATE_TOOL_NAME:
            return original_execute(
                source_app_key,
                model_tool_name,
                arguments,
                granted_permissions,
                owner=owner,
            )
        parent_agent_id = current_parent_agent_id()
        if parent_agent_id is None or delegation_depth() > 0:
            raise agent_tools.AgentToolError("Agent delegation is not available in this execution context.")
        try:
            with worker_scope():
                return agent_workflows.execute_model_delegation(
                    source_app_key,
                    parent_agent_id=int(parent_agent_id),
                    conversation_id=current_conversation_id(),
                    arguments=dict(arguments or {}),
                    granted_permissions=set(granted_permissions or set()),
                    owner=owner,
                )
        except agent_workflows.AgentWorkflowError as exc:
            raise agent_tools.AgentToolError(str(exc)) from exc

    context_chat.chat = scoped_chat
    agent_tools.model_tool_schemas = workflow_schemas
    agent_tools.execute_model_tool = workflow_execute
