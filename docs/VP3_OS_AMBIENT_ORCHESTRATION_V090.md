# VP3 OS v0.90 — Ambient Orchestration & Room Modes

VP3 OS v0.90 coordinates the governed automation layers introduced in v0.60, v0.70, and v0.80.

A Room Mode is not a second device-control system. It references an existing v0.70 routine and adds context, simulation, priority, conflicts, and lifecycle state around that routine.

## Execution boundary

    Presence / meeting / time context
                  ↓
         Ambient Orchestrator
                  ↓
          Room Mode suggestion
                  ↓
          Owner activates mode
                  ↓
        Existing v0.70 routine
                  ↓
      devices.command requests
                  ↓
       Local owner approvals
                  ↓
       v0.60 device execution

Ambient context never activates a mode automatically.

## Room Modes

A mode includes:

- name and stable key
- one existing v0.70 routine
- one or more room scopes
- priority from 0 to 100
- optional suggestion trigger
- enabled state

Room Mode routines must use the v0.70 ask_every_time approval mode.

## Suggestion context

v0.90 supports deterministic local suggestion conditions:

- live Ambient Agent coarse presence: present / absent
- physical meeting: active / inactive
- weekday
- UTC time window

All configured conditions must match before a suggestion is surfaced. Presence orchestration reads the live Ambient Agent state and does not depend on v0.80 learning being enabled; the v0.80 presence ledger is retained only as bounded historical context.

Context creates a suggestion only. The owner must accept it before any approval requests are created.

## Session lifecycle

    suggested
       ↓ owner accepts
    requested
       ↓ all device requests execute
    active
       ↓ owner/manual override/conflict
    suspended
       ↓ owner ends
    ended

A denied, expired, missing, or failed governed device request moves the session to failed.

A manual change to a device participating in an active mode suspends the mode so VP3 does not claim the room is still being orchestrated as configured.

## Conflict handling

Modes conflict when requested or active sessions target at least one shared device.

By default a conflicting activation is blocked.

The owner may explicitly request supersession only when the new mode has a strictly higher priority than every conflicting mode. Lower or equal priority modes remain blocked.

Supersession changes orchestration state only. It does not bypass device approvals.

## Simulation

Before activation, simulation shows:

- room scopes
- affected devices
- current device states
- exact routine commands
- number of approval requests
- active/requested conflicts
- whether priority supersession is available
- zero physical actions without owner approval

## Ending a mode

Ending or suspending a Room Mode never silently reverses physical device state.

A future mode/routine can explicitly request a different state through the same governed v0.70/v0.60 path.

## Agent and Cognitive Runtime

Mode suggestions and lifecycle changes are emitted as private Cognitive Runtime awareness events. They are not memory candidates and do not grant Agent execution authority.

## Schema 26

Migration 026 adds:

- orchestration_settings
- orchestration_modes
- orchestration_mode_sessions
- orchestration_mode_conflicts
- orchestration_mode_transitions
