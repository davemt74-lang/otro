# VP3 Connector Contract

HomeServer is application-neutral. VP3 is an authorized browser client that connects to the user's local HomeServer API rather than reading SQLite or local files directly.

## Local endpoint

Default: `http://127.0.0.1:4377`

VP3 should call `GET /api/v1/capabilities` first. HomeServer v0.6 reports `pairing_protocol: "claim-v1"`, Agent Brain conversations, knowledge, memory, skills and tools capabilities.

## Browser pairing

1. VP3 calls `POST /api/v1/pairing/request`.
2. HomeServer returns a short approval `code`, opaque `request_id`, high-entropy `claim_token`, accepted permissions and expiry.
3. The claim token cannot authenticate yet.
4. The user approves the short code in the local HomeServer control center.
5. VP3 polls `POST /api/v1/pairing/status` with the request ID and claim token.
6. When `ready` becomes `true`, the same claim token becomes VP3's bearer credential.

HomeServer stores only the SHA-256 hash of that credential. Re-pairing rotates the token and revokes permissions omitted from the new request.

Browser CORS defaults to `https://vp3.me` and `https://www.vp3.me`; no wildcard origin is enabled. Additional deployment origins must be explicitly configured on HomeServer with `HOMESERVER_ALLOWED_ORIGINS`.

## Agent Brain

With `agent.chat`, VP3 can use the same private HomeServer agent as the local Control Center. Each calling application receives an isolated conversation namespace: a VP3 token cannot list or fetch the owner's local chats or another application's chats.

`POST /api/v1/chat`

```json
{
  "message": "What do we know about merchant partnerships?",
  "conversation_id": null
}
```

HomeServer assembles the primary agent instructions and bounded recent conversation history. Memory and knowledge are only added when VP3 separately has `memory.read` and `knowledge.search`. v0.6 permits only a loopback Ollama provider.

Conversation APIs:

- `GET /api/v1/conversations`
- `GET /api/v1/conversations/{conversation_id}`

## Skills & Tools

VP3 can discover the safe local capability registry after pairing:

- `GET /api/v1/tools`
- `GET /api/v1/skills`
- `POST /api/v1/tools/{tool_key}/execute`

Tool execution requires `tools.execute` **and** the tool's underlying permission. `tools.execute` never substitutes for data access.

Current v0.6 tools:

| Tool | Mode | Required permissions |
| --- | --- | --- |
| `knowledge.search` | read | `tools.execute`, `knowledge.search` |
| `memory.list` | read | `tools.execute`, `memory.read` |
| `memory.write` | write | `tools.execute`, `memory.write` |

Example:

```json
POST /api/v1/tools/knowledge.search/execute
{
  "arguments": {
    "query": "merchant partnerships",
    "limit": 5
  }
}
```

The owner can globally disable any built-in tool. Denied and completed tool attempts are audited locally, but raw search queries, returned knowledge excerpts and memory bodies are not copied into the tool-run audit table.

HomeServer v0.6 exposes no arbitrary shell, PowerShell, HTTP or unrestricted filesystem tool.

## Other protected APIs

- `GET /api/v1/me`
- `GET /api/v1/agent` — requires `agent.chat`
- `GET /api/v1/knowledge?q=` — requires `knowledge.search`
- `GET /api/v1/memory` — requires `memory.read`
- `POST /api/v1/memory` — requires `memory.write`

## Browser helper

`connectors/vp3/client.js` provides `VP3HomeServerConnector` with `pair()`, `chat()`, `conversations()`, `conversation()`, `searchKnowledge()`, `memory()`, `writeMemory()`, `tools()`, `skills()` and `executeTool()`.

The helper intentionally leaves persistent credential storage to VP3. HomeServer never requires VP3 to copy a long bearer token manually.

Owner-only `/api/v1/control/*` routes and `/api/v1/pairing/approve` remain behind the local owner-session gateway and are not granted by browser reachability.
