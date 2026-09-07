# VP3 Connector Contract

HomeServer is application-neutral. VP3 is an authorized client that connects through HomeServer APIs rather than reading SQLite or local files directly.

## Local endpoint

Default: `http://127.0.0.1:4377`

VP3 should call `GET /api/v1/capabilities` first. HomeServer v0.13 reports claim-v1 pairing, Agent Brain conversations, contacts, knowledge, memory, tasks/reminders, notifications, skills, direct tools, optional Agent Tool Use and local action approvals.

Windows lifecycle, Setup & Diagnostics, owner security, recovery mode and Backup & Restore are deliberately not pairing capabilities.

## Browser pairing

1. VP3 calls `POST /api/v1/pairing/request`.
2. HomeServer returns a short approval code, opaque request ID, high-entropy claim token, accepted permissions and expiry.
3. The claim token cannot authenticate yet.
4. The user approves the short code locally.
5. VP3 polls `POST /api/v1/pairing/status`.
6. When `ready` is true, that same claim token becomes VP3's bearer credential.

HomeServer stores only the credential hash. Re-pairing rotates the token and removes permissions omitted from the new request.

## Permission model

Useful v0.13 permissions include:

- `agent.chat`
- `contacts.read`
- `knowledge.search`
- `memory.read`
- `memory.write`
- `notifications.read`
- `tasks.read`
- `tasks.write`
- `tools.execute`

Permissions are independent. For example, `tasks.read` does not grant `tasks.write`, and neither makes an Agent Tool available unless the app also has `tools.execute`.

## Agent Brain

With `agent.chat`, VP3 can use the same private HomeServer agent as the local Control Center. Each paired app has an isolated conversation namespace.

Memory and knowledge are only injected into ordinary chat context when the app separately has `memory.read` and `knowledge.search`. Contacts/tasks/notifications are not injected automatically; they are accessed through their permissioned APIs or read tools.

Conversation APIs:

- `POST /api/v1/chat`
- `GET /api/v1/conversations`
- `GET /api/v1/conversations/{conversation_id}`

## Contacts

`GET /api/v1/contacts?q=<query>` requires `contacts.read`. Contact create/update/delete remains owner-controlled.

Browser helper:

```js
connector.contacts('Synthetic Organization')
```

## Tasks, reminders and notifications

Direct app APIs:

- `GET /api/v1/tasks` — `tasks.read`
- `POST /api/v1/tasks` — `tasks.write`
- `PATCH /api/v1/tasks/{task_id}` — `tasks.write`
- `GET /api/v1/notifications` — `notifications.read`

Tasks may contain due/reminder times, priority, optional contact linkage and daily/weekly/monthly recurrence. The local HomeServer scheduler turns due reminders into canonical notification rows; it does not execute arbitrary actions.

Local connector helpers include task and notification access. The remote connector exposes the same capabilities through HomeServer tools and the relay.

## Direct Skills & Tools

VP3 can discover and invoke the direct capability registry:

- `GET /api/v1/tools`
- `GET /api/v1/skills`
- `POST /api/v1/tools/{tool_key}/execute`

Direct tool execution requires `tools.execute` plus the underlying permission.

| Tool | Mode | Required permissions |
| --- | --- | --- |
| `contacts.search` | read | `tools.execute`, `contacts.read` |
| `knowledge.search` | read | `tools.execute`, `knowledge.search` |
| `memory.list` | read | `tools.execute`, `memory.read` |
| `memory.write` | write | `tools.execute`, `memory.write` |
| `tasks.list` | read | `tools.execute`, `tasks.read` |
| `notifications.list` | read | `tools.execute`, `notifications.read` |
| `tasks.create` | write | `tools.execute`, `tasks.write` |

The owner can globally disable any built-in tool. HomeServer exposes no shell, PowerShell, arbitrary HTTP or unrestricted filesystem tool.

## Agent Tool Use

Agent Tool Use is controlled locally and disabled by default. The model only receives read functions that are globally enabled and authorized for the current paired app.

v0.13 read functions include:

- `homeserver_contacts_search`
- `homeserver_knowledge_search`
- `homeserver_memory_list`
- `homeserver_tasks_list`
- `homeserver_notifications_list`

HomeServer enforces a hard owner-selected 1–3 executed-tool-call limit per chat turn. Tool-result messages remain inside the local model exchange and are not stored as ordinary conversation messages.

## Approval-gated writes

The model is never offered direct `memory.write` or `tasks.create`.

When the owner enables Agent Tool Use and separately enables write proposals, HomeServer may expose:

- `homeserver_memory_write_request`
- `homeserver_task_create_request`

A proposal creates a pending local action request only. No memory/task mutation happens until the HomeServer owner explicitly approves it.

The originating app may check only its own request status:

`GET /api/v1/action-requests/{request_id}`

That app-visible status intentionally omits the proposed content/task payload and safe argument metadata. Other paired apps receive `404` for the request. VP3 has no approve/deny endpoint.

## Remote access

Remote VP3 uses the deployable relay plus the same HomeServer pairing credential. The relay session selects the HomeServer; the HomeServer bearer token determines permissions.

The trusted relay does not elevate permissions. Protected remote operations are forwarded back through canonical HomeServer APIs. Owner control, recovery, backups, Windows lifecycle and arbitrary localhost/HTTP access are excluded.

See `REMOTE.md` and `remote-client.js`.

## Owner-only surfaces

These remain outside the app permission model:

- `/system` Setup & Diagnostics
- Windows startup controls
- restart/shutdown
- owner secret management
- recovery mode
- Backup & Restore
- pairing approval

A valid VP3 bearer token cannot invoke owner `/api/v1/control/*` routes.

## Browser helpers

`connectors/vp3/client.js` provides local pairing/chat, conversation, contacts, knowledge/memory, tasks/notifications, tools, skills and action-status helpers.

`connectors/vp3/remote-client.js` provides the equivalent remote path through the relay.

Persistent relay/HomeServer credential storage remains VP3's responsibility.
