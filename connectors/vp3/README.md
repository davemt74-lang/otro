# VP3 Connector Contract

HomeServer is application-neutral. VP3 is an authorized browser client that connects to the user's local HomeServer API rather than reading SQLite or local files directly.

## Local endpoint

Default: `http://127.0.0.1:4377`

VP3 should call `GET /api/v1/capabilities` first. HomeServer v0.7 reports `pairing_protocol: "claim-v1"`, Agent Brain conversations, knowledge, memory, skills, direct tools, and optional read-only Agent Tool Use.

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

HomeServer assembles the primary agent instructions and bounded recent conversation history. Memory and knowledge are only added when VP3 separately has `memory.read` and `knowledge.search`. v0.7 permits only a loopback Ollama provider.

Conversation APIs:

- `GET /api/v1/conversations`
- `GET /api/v1/conversations/{conversation_id}`

## Agent Tool Use

v0.7 adds optional read-only tool use during Agent Chat. This is controlled only by the local HomeServer owner and is **disabled by default**.

When enabled, HomeServer may expose the following Ollama function tools to a VP3 chat:

- `homeserver_knowledge_search` → `knowledge.search`
- `homeserver_memory_list` → `memory.list`

VP3 does not receive these automatically. Its paired token must still satisfy all normal capability checks:

- knowledge agent tool: `agent.chat` + `tools.execute` + `knowledge.search`
- memory agent tool: `agent.chat` + `tools.execute` + `memory.read`

The owner also sets a hard 1–3 executed-tool-call limit per chat turn. After the limit is reached, HomeServer requests the final Ollama response without exposing tools again.

`memory.write` is **never** offered to the model. Agent Tool Use also exposes no shell, PowerShell, arbitrary HTTP, or unrestricted filesystem capability.

Every model-requested tool call is routed through the same audited HomeServer registry used for direct tool execution. Tool result messages stay inside the local Ollama exchange and are not persisted as conversation messages. Agent run records keep the tool-call count and tool-run IDs; the existing tool audit remains content-safe.

VP3 cannot enable, disable, or raise the Agent Tool budget through its bearer token. Those controls remain owner-only under `/api/v1/control/agent-tools`.

## Direct Skills & Tools

VP3 can discover the safe local capability registry after pairing:

- `GET /api/v1/tools`
- `GET /api/v1/skills`
- `POST /api/v1/tools/{tool_key}/execute`

Direct tool execution requires `tools.execute` **and** the tool's underlying permission. `tools.execute` never substitutes for data access.

Current tools:

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
