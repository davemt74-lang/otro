# VP3 Connector Contract

HomeServer is application-neutral. VP3 is an authorized client that connects through HomeServer APIs rather than reading SQLite or local files directly.

## Local endpoint

Default: `http://127.0.0.1:4377`

VP3 should call `GET /api/v1/capabilities` first. HomeServer v0.17 reports claim-v1 pairing, Agent Brain conversations, private context, awareness/events, contacts, knowledge, memory, tasks/reminders, notifications, plugins, skills, direct tools, inference routing and usage history/sync.

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

The v0.17 VP3 connector defaults are:

- `agent.chat`
- `awareness.read`
- `contacts.read`
- `events.read`
- `events.write`
- `knowledge.search`
- `memory.read`
- `memory.write`
- `notifications.read`
- `plugins.read`
- `tasks.read`
- `tasks.write`
- `tools.execute`
- `usage.read`
- `usage.write`

Permissions are independent. Read permissions do not grant writes, and direct Agent Tools still require `tools.execute` plus the underlying capability permission.

## Agent Brain

With `agent.chat`, VP3 can use the same private HomeServer agent as the local Control Center. Each paired app has an isolated conversation namespace.

Memory and knowledge are injected into ordinary chat context only when the app separately has `memory.read` and `knowledge.search`. Other private capabilities remain permissioned APIs/tools. HomeServer conversation IDs are opaque identifiers and must be preserved as strings.

Conversation APIs:

- `POST /api/v1/chat`
- `GET /api/v1/conversations`
- `GET /api/v1/conversations/{conversation_id}`

## v0.17 cognition and cloud integration

VP3 can use the v0.17 cognition surface through either the local connector or the Remote Relay:

- `GET /api/v1/inference/status`
- `POST /api/v1/events`
- `GET /api/v1/events`
- `GET /api/v1/awareness`
- `GET /api/v1/plugins`
- `POST /api/v1/usage/cloud`
- `GET /api/v1/usage`

This supports the core routing contract: HomeServer-local or user-provider inference is not billed as VP3 cloud usage; VP3 can record paid cloud fallback usage back to HomeServer when a cloud fallback is actually used.

## Contacts

`GET /api/v1/contacts?q=<query>` requires `contacts.read`. Contact create/update/delete remains owner-controlled.

## Tasks, reminders and notifications

Direct app APIs:

- `GET /api/v1/tasks` — `tasks.read`
- `POST /api/v1/tasks` — `tasks.write`
- `PATCH /api/v1/tasks/{task_id}` — `tasks.write`
- `GET /api/v1/notifications` — `notifications.read`

Tasks may contain due/reminder times, priority, optional contact linkage and daily/weekly/monthly recurrence. The local HomeServer scheduler turns due reminders into canonical notification rows; it does not execute arbitrary actions.

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

Agent Tool Use is controlled locally and disabled by default. The model only receives functions that are globally enabled and authorized for the current paired app. Approval-gated write proposals remain owner-controlled; VP3 cannot approve its own action requests.

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

`connectors/vp3/client.js` provides local pairing plus v0.17 chat, conversation, contacts, knowledge/memory, awareness/events, plugins, inference status, usage, tasks/notifications, tools, skills and action-status helpers.

`connectors/vp3/remote-client.js` provides the equivalent remote path through the relay with the same default permission set.

Persistent relay/HomeServer credential storage remains VP3's responsibility.
