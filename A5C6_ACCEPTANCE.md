# A5C6 — Scheduled Specialist Tasks

Prepare a task in Agent Chat, review and approve its specialist assignments, then choose a daily or weekly local time and IANA timezone. Confirm the exact recurring plan. Current provider/privacy settings, source permissions and separate per-worker read/proposal budgets apply to each run. Every proposed edit still requires exact per-change approval.

Schedules appear in Agent Chat and Brain with next-run time, last-run time, status and up to ten recent run records. View run opens the ordinary specialist mission and its completion/readback report. Pause and Cancel future runs stop future scheduling; current runs and prepared changes remain available under their existing separate controls. Resume skips missed slots and schedules the next future occurrence. Blocked schedules require a newly reviewed plan.

## Documented automated gates

| Gate | Required evidence |
| --- | --- |
| Reviewed plan | New Chat task, current contract revision and explicit recurring consent; frozen assignments and dependencies |
| Schedule time | Daily/weekly wall times, IANA timezone, DST gap skipped and fold executed once |
| Actual execution | Production scheduler dispatches specialists using real SQLite contact reads and separate call/proposal budgets |
| Consequential changes | Native write only after exact approval; receipt/readback verification and retry prevention |
| Durable identity | One run per schedule/slot; concurrent requests and lost responses/reload reuse their identities |
| Overlap | Unfinished workers and pending/queued/uncertain changes prevent another scheduled run |
| Current authority | Policy/pairing/privacy changes block before preparation; source-scoped Cloud relay; no recurring browser grants |
| Lifecycle | Pause/resume/cancel, no catch-up burst, no automatic replay of interrupted worker leases |
| Atomic data | Schema 75, database restart, rollback of mission/contracts/run slot after failure; Windows timezone data bundled |
| Chat and Brain | Real Chromium: confirmation, timezone entry, persistent retry, run links, inert text and timestamps |

## Validation

Run `python tests/agent_mission_schedules_a5c6.py`, the existing A5C1–A5C5 service/migration regressions, and both production-script Chromium suites. Full PR and post-merge CI plus Windows installer/browser/upgrade certification must pass before delivery. Schema 75 and timezone data are included in the deploy installer.

Inference and transport fixtures exercise real scheduler, approval, SQLite and browser contracts. Live model quality and an owner's installed device/account remain installed acceptance: schedule a test-contact review, inspect run evidence and proposed edits, approve one exact change, then verify the source record and timestamps. Check pause/cancel and restart without a duplicate run. Missed slots coalesce to at most one current run; recurring browser operations are excluded because browser grants require per-run review. At most 20 uncancelled schedules per source are allowed; processing is limited to 20 due schedules per tick.
