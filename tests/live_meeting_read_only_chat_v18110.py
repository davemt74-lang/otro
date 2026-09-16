from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import brain_api
from app.services import context_chat


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    default_request = brain_api.ChatRequest(message="hello")
    assert_true(default_request.read_only is False, "read_only must default to false")

    request = brain_api.ChatRequest(message="meeting question", read_only=True)
    captured: dict = {}
    original = context_chat.chat

    def fake_chat(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return {"reply": "ok", "read_only": bool(kwargs.get("read_only"))}

    context_chat.chat = fake_chat
    try:
        permissions = {
            "memory.read",
            "knowledge.search",
            "contacts.read",
            "tasks.create",
            "memory.write",
        }
        result = brain_api._chat_or_http(
            "app:vp3",
            request,
            include_memory=True,
            include_knowledge=True,
            include_contacts=True,
            tool_permissions=permissions,
            owner_tools=False,
        )
    finally:
        context_chat.chat = original

    kwargs = captured.get("kwargs") or {}
    assert_true(kwargs.get("read_only") is True, "API boundary must propagate read_only")
    assert_true(
        set(kwargs.get("tool_permissions") or set()) == permissions,
        "read_only must not strip context authorization before canonical context is built",
    )
    assert_true(result.get("read_only") is True, "read-only marker must survive the API boundary")

    dangerous = {"memory.read", "tasks.create", "memory.write"}
    assert_true(
        context_chat._model_tool_permissions(True, dangerous) == set(),
        "read-only chat must expose zero model tool permissions",
    )
    assert_true(
        context_chat._model_tool_permissions(False, dangerous) == dangerous,
        "normal chat must preserve canonical model tool permissions",
    )

    source = inspect.getsource(context_chat.chat)
    assert_true(
        "granted_permissions=_model_tool_permissions(read_only, canonical.model_tool_permissions)" in source,
        "canonical context permissions and model tool permissions must stay separated",
    )
    assert_true(
        "owner=bool(owner_tools and not read_only)" in source,
        "read-only owner chat must not inherit owner tool execution authority",
    )
    assert_true(
        '"read_only": bool(read_only)' in source,
        "chat response and audit metadata must expose the enforced read-only state",
    )

    print("HomeServer Phase 18.11 read-only live chat contract passed")


if __name__ == "__main__":
    main()
