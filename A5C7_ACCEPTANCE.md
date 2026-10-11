# A5C7 — Automatic Specialist Delegation

Agent Chat defaults to Automatic. The selected lead chooses direct chat for greetings, questions and discussion, or prepares a bounded specialist plan for concrete investigations and multi-step work. Chat only and Specialist plan remain available. Requests beyond the 4,000-character planning limit keep normal chat.

Preparation starts no workers and approves no tools. Review assignments, capability budgets and dependencies before starting. Every proposed edit still requires exact approval. Chat and Brain display the lead reason and decision time. Existing completion reporting verifies saved changes.

## Ten release gates

| Gate | Acceptance |
| --- | --- |
| Routing | Strict chat/specialists decisions; malformed responses do not start work or silently fall back |
| Context | Selected lead and bounded history from the validated source conversation |
| Provider | Current provider/privacy routing; local-only conversations use Ollama |
| Plan | Registered capabilities, 1–4 workers, dependency and read/proposal budgets |
| Review | No tools before reviewed assignments; separate exact approval for each edit |
| Completion | Real SQLite investigation, reviewed mutation, receipt/readback and duplicate prevention |
| Retry | Durable source-scoped decisions; conflicting concurrent responses return one choice |
| Recovery | Interrupted preparation resumes saved plan without re-inference |
| Presentation | Native/Cloud Chromium: one normal send, safe reasons, timestamps, conversation fences and pending-submit isolation |
| Deployment | Schema 76, full current-head CI, packaged Windows runtime/browser/media and silent upgrade preserving private data |

Run HomeServer service acceptance and both production-script Chromium suites. All prior specialist regressions remain required. Merge only green candidates; verify post-merge source/provenance and distribution hashes.

Routing adds a bounded lead model call before ordinary chat generation; specialist choices reuse its validated plan. Controlled inference validates routing behavior, not live model quality. Installed acceptance remains an owner-device exercise.

## Installed acceptance

Deploy matching Cloud first and upgrade HomeServer. Send a greeting, then request a review of recorded contact details. Confirm direct chat, reviewed specialist assignments, exact edit approval, verified completion and timestamped reasons. Retry after reconnect, switch conversations during routing, revoke permissions and restart interrupted tasks; verify no duplicate changes.
