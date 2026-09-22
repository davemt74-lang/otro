# VP3 OS v0.70 — Local Automation Rules & Routines

VP3 OS v0.70 adds a deterministic, local rules engine on top of the governed Room & Device Automation Runtime introduced in v0.60.

## Safety boundary

Rules never call provider drivers directly.

Every physical step compiles into one of two safe outputs:

1. **Suggest only** — creates a normal automation suggestion with zero execution authority.
2. **Ask every time** — creates a normal `devices.command` action request that still requires local HomeServer owner approval.

The existing v0.60 execution chain remains authoritative:

```
Rule / Routine
     ↓
Suggestion or devices.command request
     ↓
LOCAL HOMESERVER OWNER APPROVAL
     ↓
Exact request + arguments revalidated
     ↓
Registered local provider driver
     ↓
Physical device
```

There is no automatic physical execution mode in v0.70.

## Supported triggers

- Manual
- Daily UTC schedule
- Device-state edge trigger

Daily schedules intentionally use UTC in v0.70 to avoid ambiguous timezone/DST behavior inside the homeserver runtime.

## Conditions

Rules may include up to eight device-state conditions. Supported operators:

- `eq`
- `ne`
- `gt`
- `gte`
- `lt`
- `lte`

All conditions must pass before a rule can fire.

## Routines

A routine contains up to 16 ordered physical steps. Every step is validated through the same v0.60 device command normalizer used by manual and Agent requests.

Provider scenes remain discovery-only. A VP3 routine is explicit, inspectable, and composed of bounded VP3 device actions.

## Guardrails

- global automation runtime disable switch
- per-rule and per-routine enable state
- cooldowns
- rising-edge behavior for device-state triggers
- per-minute rule fire limit
- maximum actions per routine run
- persistent rule execution audit
- no arbitrary provider command passthrough
- no lock, garage, camera, security, appliance, or opaque scene execution

## Persistence

Schema migration 024 adds:

- `automation_runtime_settings`
- `automation_routines`
- `automation_routine_steps`
- `automation_rules`
- `automation_rule_executions`

Existing v0.60 room/device/action/suggestion tables remain unchanged.
