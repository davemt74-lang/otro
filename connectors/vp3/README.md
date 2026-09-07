# VP3 Connector Contract

HomeServer is application-neutral. VP3 is an authorized browser client that connects to the user's local HomeServer API rather than reading SQLite or local files directly.

## Local endpoint

Default: `http://127.0.0.1:4377`

VP3 should call `GET /api/v1/capabilities` first. HomeServer v0.5 reports `pairing_protocol: "claim-v1"`, `agent.chat`, conversations, knowledge search and memory capabilities.

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

HomeServer assembles the primary agent instructions, bounded recent conversation history, high-importance local memory and relevant SQLite FTS knowledge, then sends that context to the configured local Ollama model. v0.5 permits only a loopback Ollama provider.

Response:

```json
{
  "conversation_id": "...",
  "reply": "...",
  "provider": "ollama",
  "model": "llama3.2:3b",
  "run_id": 12,
  "context": {
    "memory_count": 3,
    "knowledge_count": 2
  }
}
```

Conversation APIs:

- `GET /api/v1/conversations`
- `GET /api/v1/conversations/{conversation_id}`

Other protected APIs:

- `GET /api/v1/me`
- `GET /api/v1/agent` — requires `agent.chat`
- `GET /api/v1/knowledge?q=` — requires `knowledge.search`
- `GET /api/v1/memory` — requires `memory.read`
- `POST /api/v1/memory` — requires `memory.write`

## Browser helper

`connectors/vp3/client.js` provides `VP3HomeServerConnector` with `pair()`, `chat()`, `conversations()`, `conversation()`, `searchKnowledge()`, `memory()` and `writeMemory()`.

The helper intentionally leaves persistent credential storage to VP3. HomeServer never requires VP3 to copy a long bearer token manually.

Owner-only `/api/v1/control/*` routes and `/api/v1/pairing/approve` remain behind the local owner-session gateway and are not granted by browser reachability.
