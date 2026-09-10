from __future__ import annotations


def install() -> None:
    """Expose governed local-file reads to the model through existing Agent Tool plumbing."""
    from . import agent_tools

    additions = {
        "homeserver_files_list": "files.list",
        "homeserver_file_read": "files.read",
    }
    for model_name, tool_key in additions.items():
        existing = agent_tools.MODEL_TOOL_NAMES.get(model_name)
        if existing not in (None, tool_key):
            raise RuntimeError(f"Agent tool name collision: {model_name}")
        agent_tools.MODEL_TOOL_NAMES[model_name] = tool_key
