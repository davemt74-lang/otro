# Agent Delegation & Multi-Agent Workflows v0.48

v0.48 adds a durable, one-hop multi-Agent orchestration layer on top of v0.47 Agent Selection & Persona Routing.

## Runtime contract

- Every delegation records the source application, parent Agent, worker Agent, task, status, result, provider/model metadata, context budget, permission snapshot, and lifecycle timestamps.
- A worker must be a different Agent from its parent.
- Worker identity is resolved through the v0.47 routing boundary both when queued and when executed.
- Paired wrappers can use only secondary Agents explicitly granted to that wrapper. Revoking a grant before execution prevents the queued task from running.
- Paired-app permissions are snapshotted at queue time and intersected with current permissions at run time. Permissions can shrink but never expand through a queued task.
- Worker context is built independently for the worker Agent. Parent memory is not copied or inherited.
- Model-driven delegation is owner-controlled and disabled by default.
- v0.48 is intentionally one-hop: worker inference is never offered the delegation tool.
- First-turn delegations bind to the canonical conversation created for that turn.
- Historical delegation rows retain Agent-name snapshots and do not block secondary-Agent deletion.

## Compatibility

- Existing `/api/v1/chat` behavior remains compatible.
- Existing stateless VP3 delegation remains v0.25.
- Agent personas remain v0.46.
- Agent routing remains v0.47.
- Agent Voice Profiles remain v0.45.
- Canonical context remains v4.30.
- Canonical database schema version remains unchanged; v0.48 uses an idempotent feature schema.
