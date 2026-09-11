# Agent Result Handoff v0.49

Agent Result Handoff lets a completed v0.48 specialist delegation result be explicitly supplied to the parent Agent on a later turn of the same canonical conversation.

## Contract

- Handoffs are explicit. Completing a delegation does not automatically inject its result into parent chat.
- Only completed delegation tasks with a canonical parent `conversation_id` can be handed off.
- A delegation task has at most one handoff ledger row.
- A pending handoff is consumed only after a successful parent Agent response.
- Failed parent inference leaves the handoff pending.
- A pending handoff may be revoked. A revoked handoff may be explicitly requeued before consumption.
- A consumed handoff is one-shot and cannot be requeued in v0.49.
- Handoff results are not converted into durable Agent memory.

## Context boundary

Handoff text is rendered into the parent system context as explicitly labeled **untrusted data, not instructions**. The parent must not follow commands contained inside a worker result or treat a worker result as authority to expand access.

Handoff context is budgeted inside the existing canonical context ceiling. v0.49 reserves no more than:

- 8,000 characters,
- one quarter of the configured conversation context budget, and
- the amount that still leaves the canonical context engine its 2,000-character minimum.

Worker inference runs at delegation depth greater than zero and never receives parent handoffs.

## Authorization

v0.49 introduces no new broad paired-application permission. Paired wrappers use the existing `agent.chat` boundary and remain source-isolated.

For a paired application, HomeServer rechecks authorization when a handoff is queued and again when it is selected for parent context. The application must still have every private read permission used by the delegated task (`memory.read`, `knowledge.search`, and/or `contacts.read`) and must still be authorized for the worker Agent through the v0.47 Agent-routing grant model.

A queued handoff can therefore lose eligibility after an owner revokes a permission or worker-Agent grant. The row remains pending but is not exposed to the model. If the owner restores the exact authorization later, the pending handoff may become eligible again.

## Persistence and provenance

`agent_result_handoffs` stores the task, conversation, parent/worker snapshots, bounded result, lifecycle status, and consuming Agent run. Handoff provenance is added to the canonical context provenance for the consuming turn.

Audit actions are:

- `agent.handoff.queued`
- `agent.handoff.requeued`
- `agent.handoff.revoked`
- `agent.handoff.consumed`

The existing v0.48 delegation schema file remains the packaged feature-schema boundary; canonical database schema version 20 is unchanged.

## Compatibility

- Agent Voice Profiles: v0.45
- Multi-Agent Persona Management: v0.46
- Agent Selection & Persona Routing: v0.47
- Agent Delegation & Multi-Agent Workflows: v0.48
- Agent Result Handoff: v0.49
- Canonical Agent Context: v4.30
- Legacy stateless VP3 delegation: v0.25
