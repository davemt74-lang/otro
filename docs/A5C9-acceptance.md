# A5C9 — Follow-up Commands & Mission Continuity

Ordinary Automatic Chat can propose pause, cancel, resume, or a change to remaining mission scope. The command review appears beside the composer while Agent Teams stays collapsed. Ambiguous references require an explicit mission choice. Commands never approve specialist edits.

## Review and execution

Reviews bind the source, conversation, lead agent, request UUID, exact command, current mission/task/action/assignment state, and a fifteen-minute expiry. Confirmation, control changes, lifecycle events, notices, and command receipts commit atomically. Repeated decisions read the same durable receipt. A lost response cannot become another command; there is no automatic replay.

Pause revokes active leases and retains completed results. Resume requires explicit authorization to rerun interrupted work and the current assignments and permissions. Cancellation fences late results. Scope confirmation prepares a linked, unstarted successor, retains predecessor history and saved changes, and retires pending predecessor edit proposals. The successor requires fresh assignment review. Completed findings are reused as untrusted data under current permissions, and saved or queued edits cannot be proposed again. Superseded missions cannot restart, retry, or add workers.

## Acceptance gates

- `agent_mission_followups_a5c9.py`: real SQLite and governed contact save, pause rollback, stale worker fencing, exact retries and restart persistence, explicit interrupted-work replay, retained results, successor assignments and notices, duplicate-edit protection, ambiguous selection, source/conversation/privacy boundaries, expiry, and an authentic schema-77 upgrade to 78 preserving existing receipts.
- Native and Cloud A5C9 browser tests: actual production scripts and CSS, collapsed composer reviews, safe text and mobile width, mission selection, uncertain-response intent, reload readback, reexecution checkbox, fresh scope assignments, conversation/lead changes, and permission revocation.
- Cloud A5C9 HTTP test: real PHP relay, authentication/CSRF, strict review fields, source-safe payloads, durable reads, and disconnected or uncertain responses.
- Existing A5C1–A5C8 service and browser suites remain in their CI gates. Windows certification runs the new backend acceptance and requires release schema 78. Packaging checks certified source-tree identity and includes the minimum follow-up schema in provenance.

The local backend, migration, PHP relay, syntax and source checks pass. The local environment restricts browser startup sockets, so browser results are required from the PR workflows. Both current-head PR checks and Windows certification must pass before merge and deployment ZIP delivery.

## Acceptance evidence and scoring

The A5C8 transport corrections were independently reviewed, tested through the real authenticated dispatcher and production PHP receipt readback, merged in Cloud PR #569 and HomeServer PR #312, and certified before A5C9 resumed. This section preserves those fixes.

| Section | Required evidence for 10/10 |
| --- | --- |
| Pause and cancel | Exact current review, transactional rollback, lease revocation, retained completed findings, durable receipt retries, and rejection of late output. |
| Remaining scope | Linked unstarted successor, fresh assignment review, immutable prior history, retired pending changes, and prevention of previously saved or queued proposals. |
| Reviewed continuation | Explicit interrupted-work authorization, current permission/assignment checks, preserved completed work, and bounded replay. |
| Mission ambiguity | Explicit selection when multiple eligible missions exist, checked through native service and the real paired dispatcher; conversation/source/lead fences. |
| Synchronized surfaces | Events and notifications share the command transaction; native and Cloud canvas reviews, status readback, Brain continuity links, and permission cache clearing. |
| Release integrity | Authentic schema-77 upgrade, all A5C1–A5C9 regressions, fresh browser suites, certified Windows installer/upgrade, green merges and source/provenance/checksum verification. |

Scores are assigned only against the completed evidence. Backend and HTTP gates run locally; browser and Windows gates run in CI because this local environment blocks Chromium startup sockets. No release is considered 10/10 while a required gate is pending or failing.
