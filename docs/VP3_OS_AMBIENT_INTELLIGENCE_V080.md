# VP3 OS v0.80 — Ambient Intelligence & Automation Learning

VP3 OS v0.80 adds a local learning layer above the governed v0.60 device runtime and v0.70 rules engine.

## Core boundary

v0.80 can notice, explain, simulate, and draft. It cannot execute a physical device command, approve an action request, or silently activate a learned automation.

    Completed local device actions
            + bounded context
                  ↓
          Pattern detector
                  ↓
        Automation proposal
                  ↓
       Owner reviews evidence
                  ↓
      Create disabled v0.70 draft
                  ↓
          Owner explicitly enables
                  ↓
           v0.70 rule engine
                  ↓
    Suggestion / approval request
                  ↓
     Local owner approval remains required
                  ↓
         v0.60 physical execution

## Learning source

The v0.80 detector learns from completed physical device actions already present in the v0.60 audit ledger.

It explicitly excludes any action whose source begins with "automation:". VP3 therefore does not learn from its own rules and cannot amplify an automation feedback loop.

The Ambient Agent may also record bounded presence state transitions as context. These records contain state and a small allowlist of identifiers only. They never contain raw audio, transcripts, selected text, browser content, or opaque provider payloads.

## Pattern types

### Time action

A bounded device command that repeatedly occurs in the same time bucket.

### Action sequence

Two to eight distinct device commands that repeatedly occur in the same local-learning session/time bucket.

All learned steps are revalidated against the current v0.60 device command contract before they can become evidence.

## Automation proposals

Each proposal contains confidence, occurrence count, first/last evidence timestamps, bounded evidence IDs, nearby context summary when available, a disabled v0.70 routine draft, a disabled v0.70 daily rule draft, and a historical simulation.

The cognitive runtime receives a private automation.opportunity awareness event when a proposal is surfaced, allowing Agent/Now to mention the opportunity without giving the Agent activation authority.

## Simulation

Simulation reports the observed historical occurrences, how many rule triggers the pattern represents, the number of steps per trigger, and how many approval requests would have been created.

Simulation always reports zero physical actions without owner approval.

## Proposal lifecycle

    proposed
       ↓ Create disabled draft
    materialized
       ↓ Explicit owner enable
    active

    proposed
       ↓ Dismiss
    suppressed

Dismissal applies a configurable suppression window so the same pattern is not repeatedly surfaced.

## Local-first behavior

The detector, evidence, context state, proposal drafts, feedback, and simulation history are stored on the HomeServer.

No LLM or cloud provider is required to detect a pattern or create a deterministic proposal.

## Schema 25

Migration 025 adds:

- automation_intelligence_settings
- automation_context_events
- automation_learning_patterns
- automation_proposals
- automation_proposal_feedback
- automation_simulations

v0.80 does not modify the v0.60 action-request execution contract or the v0.70 rule execution contract.
