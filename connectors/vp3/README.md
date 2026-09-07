# VP3 Connector Contract

HomeServer is application-neutral. VP3 is an authorized browser client that connects to the user's local HomeServer API rather than reading SQLite or local files directly.

## Local endpoint

Default: `http://127.0.0.1:4377`

VP3 should call `GET /api/v1/capabilities` first. HomeServer v0.8 reports `pairing_protocol: "claim-v1"`, Agent Brain conversations, knowledge, memory, skills, direct tools, optional Agent Tool Use and local action approvals.

## Browser pairing

1. VP3 calls `POST /api/v1/pairing/request`.
2. HomeServer returns a short approval `code`, opaque `request_id`, high-entropy `claim_token`, accepted permissions and expiry.
3. The claim token cannot authenticate yet.
4. The user approves the short code in the local HomeServer Control Center.
5. VP3 polls `POST /api/v1/pairing/status` with the request ID and claim token.
6. When `ready` becomes `true`, the same claim token becomes VP3's bearer credential.

HomeServer stores only the SHA-256 hash of that credential. Re-pairing rotates the token and revokes permissions omitted from the new request.

Browser CORS defaults to `https://vp3.me` and `https://www.vp3.me`; no wildcard origin is enabled.

## Agent Brain

With `agent.chat`, VP3 can use the same private HomeServer agent as the local Control Center. Each app receives an isolated conversation namespace.

Memory and knowledge are only added to chat context when the app separately has `memory.read` and `knowledge.search`.

Conversation APIs:

- `POST /api/v1/chat`
- `GET /api/v1/conversations`
- `GET /api/v1/conversations/{conversation_id}`

## Agent Tool Use

Agent Tool Use is controlled only by the local HomeServer owner and is disabled by default. Read tools require the usual paired-app capabilities:

- knowledge: `agent.chat` + `tools.execute` + `knowledge.search`
- memory read: `agent.chat` + `tools.execute` + `memory.read`

HomeServer enforces a hard owner-selected 1–3 executed-tool-call limit per chat turn. Tool result messages stay inside the local Ollama exchange and are not stored as conversation messages.

## Approval-gated memory-write proposals

v0.8 adds an optional model function named `homeserver_memory_write_request`. It **does not write memory**.

For VP3 to receive this proposal function, all of the following must be true:

- the HomeServer owner enabled Agent Tool Use
- the owner separately enabled memory-write proposals
- the global `memory.write` tool is enabled
- VP3 has `agent.chat`
- VP3 has `tools.execute`
- VP3 has `memory.write`

A successful proposal returns a request ID inside the normal chat response:

```json
{
  "tools": {
    "call_count": 1,
    "action_request_ids": ["..."]
  }
}
```

The request remains `pending` and no memory row is created. The owner reviews the exact payload in the local **Approvals** workspace.

VP3 may check only the status of a request it originated:

`GET /api/v1/action-requests/{request_id}`

Example response:

```json
{
  "request": {
    "id": "...",
    "action_key": "memory.write",
    "status": "pending",
    "created_at": "...",
    "expires_at": "...",
    "execution_tool_run_id": null
  }
}
```

The status response intentionally omits the proposed memory content and safe argument metadata. A different paired app receives `404` for that request ID.

VP3 has **no approval/deny API**. Only owner-session routes can decide an action:

- `GET /api/v1/control/action-requests`
- `POST /api/v1/control/action-requests/{request_id}/approve`
- `POST /api/v1/control/action-requests/{request_id}/deny`

Approval reserves the request and executes the existing audited `memory.write` tool once. Denial makes no mutation. Pending requests expire after 24 hours.

## Direct Skills & Tools

VP3 can discover and invoke the direct capability registry:

- `GET /api/v1/tools`
- `GET /api/v1/skills`
- `POST /api/v1/tools/{tool_key}/execute`

Direct tool execution requires `tools.execute` and the tool's underlying permission.

| Tool | Mode | Required permissions |
| --- | --- | --- |
| `knowledge.search` | read | `tools.execute`, `knowledge.search` |
| `memory.list` | read | `tools.execute`, `memory.read` |
| `memory.write` | write | `tools.execute`, `memory.write` |

The owner can globally disable any built-in tool. HomeServer exposes no shell, PowerShell, arbitrary HTTP or unrestricted filesystem tool.

## Other protected APIs

- `GET /api/v1/me`
- `GET /api/v1/agent` — requires `agent.chat`
- `GET /api/v1/knowledge?q=` — requires `knowledge.search`
- `GET /api/v1/memory` — requires `memory.read`
- `POST /api/v1/memory` — requires `memory.write`

## Browser helper

`connectors/vp3/client.js` provides `VP3HomeServerConnector` with `pair()`, `chat()`, conversation helpers, knowledge/memory helpers, `tools()`, `skills()`, `executeTool()` and `actionRequest()`.

Persistent claim-token storage remains VP3's responsibility. Owner-only `/api/v1/control/*` routes and pairing approval remain behind HomeServer's local owner-session gateway.
