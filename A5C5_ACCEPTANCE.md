# A5C5 — Chat-to-Task Completion

Use the ordinary chat composer with **Use specialists** selected. The lead prepares a bounded plan using the active conversation and current model/privacy settings. Review and approve specialist assignments, then start the team. Review each proposed edit separately; the existing source-scoped approval executor and receipt readback verify saved changes.

## Automated acceptance gates

| Gate | Required evidence |
| --- | --- |
| Composer | Ordinary messages retain their normal route; task mode uses the current conversation and selected lead |
| Inference | Lead and worker routing uses the current configured provider; local-only tasks use Ollama |
| Plan | Strict registered capabilities, separate budgets, earlier-task dependencies and required investigation reads for record updates |
| Assignment review | Preparation approves no tools; model-only Start cannot bypass assignment review |
| Real task | Two specialists read actual SQLite contacts, propose a supported revision-bound edit, then save and independently verify only after exact approval |
| Retry identity | Lost responses and reload retain the request ID; concurrent first preparation persists one whole mission and draft |
| Permissions | Current policy and pairing gates apply at preparation, assignment approval, execution and result visibility |
| Lifecycle | Cancellation performs no writes; interrupted leases leave no actionable proposals; restart requires explicit reexecution |
| Durable data | Schema 74 retains the draft and provider provenance; provider/privacy setup preserves caller-owned transactions and rollback |
| Presentation | Real Chromium verifies separate plan/edit consent, inert text, current timestamps and verified completion in Chat and Brain |

## Validation

`python tests/agent_mission_chat_tasks_a5c5.py` exercises production routing, scheduler, SQLite reads/writes, approval receipts and restart fences with controlled inference. Existing A5C1–A5C4 and A1–A5 service and migration regressions remain required. Native and Cloud Chromium checks exercise the production scripts with controlled transport. Windows CI runs the new task service check, installer/browser certification and schema 74 packaging. All current CI must pass before merge.

This certifies automated contracts, not live model quality or an owner's installed device/account. Record coverage is bounded by displayed call/result budgets; findings are model generated and saved-change verification reflects current readback. Browser permissions remain separately reviewed.

## Installed acceptance

After deploying Cloud and upgrading HomeServer, use a test contact with a blank organization and a supported organization in its notes. Select Use specialists and request a review. Inspect assignments, start, inspect the exact edit and approve it. Verify the updated source record and timestamped lead report. Repeat with cancellation, permission revocation and restart/reconnect; confirm no duplicate writes. Export redacted observations for any installed-device issue.
