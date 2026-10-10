# Runtime Reliability & Recovery

This hardening release adds process scheduler health and immediate review fences to the existing A5C1–A5C6 task flow. No new trigger type, tool authority, or automatic replay is introduced. Schema remains 75.

## Confirmed failures and repairs

- A committed scheduled launch could fail at dispatch and remain `running` until process restart. The exact persisted run is now blocked, the mission enters `waiting_review`, and live leases are revoked immediately.
- Executor submission and unexpected worker-future errors could be unobserved. Each future is observed; submission failures and current-lease worker failures enter review. A stale future cannot interrupt a newly resumed lease or reopen cancellation.
- Scheduler failures were silently swallowed or mislabeled as authority changes. Safe error categories separate database outages, changed authority, changed plans, preparation failures and dispatch failures. Raw exceptions and provider details never enter health projections.
- Chat and Brain could retain cached healthy state after relay failure. Both views mark status unavailable and update together after reconnect. Older servers without health support remain unknown.

## Ten-point acceptance rubric

The automated score is 10/10 only when all ten gates pass on the exact release candidate. CI is the browser and Windows execution authority; tests run locally where the environment supports them. Installed owner-device/model behavior remains separate acceptance.

| Gate | Required evidence |
| --- | --- |
| 1. Committed launch gap | Dispatch fault produces one durable blocked slot and a review-waiting mission; subsequent ticks never replay it |
| 2. Executor failure | Real dispatcher claims leases; submission fault revokes them and preserves run identity |
| 3. Worker failure | Real contact-read worker with commit fault enters review; no proposed or saved change leaks |
| 4. Stale completions | Explicit resume uses a new lease; old failing futures cannot interrupt it; cancellation remains terminal |
| 5. Database outage | Preparation rolls back; unlaunched slot remains due; heartbeat reports safe error and recovers after a successful tick |
| 6. Runtime health | Started/tick/success/failure timestamps, stopped/degraded/stalled states and consecutive failures; monotonic staleness detection |
| 7. Slow inference | Workers running over five minutes are visible; no automatic inference replay or change approval |
| 8. Current authority | Pairing revocation rejects Cloud operations; conversation privacy hides run failures and history; existing exact-edit/readback tests pass |
| 9. Real browser | Both production scripts show timestamps, review/slow states, inert text, disconnect invalidation, reconnect and old-server compatibility |
| 10. Release certification | A1–A5C6 regressions, migrations, full PR/post-merge CI, certified Windows/browser/installer upgrade and exact artifact provenance |

## Validation and installation

`python tests/agent_mission_schedules_a5c6.py` exercises the real database, scheduler, worker dispatch, approvals and receipt/readback plus injected fault boundaries. The existing focused A5C workflow runs this on Linux and both production browser scripts; Windows CI runs the same extended service test before packaging and verifies silent installer upgrade preserves private data. Other task regression suites remain required.

Deploy the matching Cloud package first, then run the certified HomeServer installer. Inspect scheduler health and a scheduled test-contact review. Verify one approved change, pause/cancel, disconnect/reconnect and restart. Slow-worker warnings are observational: use existing run controls to stop work or explicitly approve reexecution after reviewing evidence.
