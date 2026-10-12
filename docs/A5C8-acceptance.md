# A5C8 — Live Mission Progress and Notifications

A5C8 shows real specialist activity in Chat while Agent Teams is collapsed, and integrates mission notices into the existing notification and Brain drawers.

## Acceptance audit

| Area | Score | Evidence |
| --- | --- | --- |
| Observable progress | 10/10 | Stages persist from actual worker transitions; completed workers, last activity and review states appear in the live feed. |
| Stall and restart recovery | 10/10 | Five minutes without observable progress fences the exact overdue lease for review. Fresh leases and late results are tested; replay requires explicit reviewed authorization. |
| Durable notifications | 10/10 | Notices commit with lifecycle events; exact notification reads persist across initialization. Research completion and verified saved changes have separate notices. |
| Source and privacy controls | 10/10 | Paired-source, permission and conversation privacy checks apply to feeds and reads. Revocation clears browser caches. |
| Chat, Brain and existing bell | 10/10 | Production scripts and CSS are exercised in Chromium, including collapsed Chat, existing unread counts, safe text, mobile width and disconnect/reconnect. |
| Regression coverage | 10/10 | A5C1–A5C7 service suites and A5C2–A5C8 specialist browser suites passed locally. |

Scores cover implemented behavior and local acceptance checks. GitHub CI and Windows packaged installer certification remain independent release gates.

## Behavior and limits

The foreground feed refreshes every five seconds and labels disconnected data as stale. It reports stages and elapsed activity, without fabricated percentage completion. Notifications are bounded and unread-first; large unread counts are marked as a lower bound. Local notices also appear in HomeServer Activity Center. Cancelled or failed missions retire obsolete approval prompts while preserving their history.

Recovery never automatically replays interrupted work. Owners review and authorize reexecution through existing controls. Cloud views remain subject to paired-app authority and conversation export settings.

## Validation

Real SQLite lifecycle, transaction rollback, read persistence, cancellation/restart idempotency, stale lease fencing, current lease protection, late-result rejection, source isolation, privacy revocation and watchdog lifecycle passed. Real Chromium tests passed for both deployed scripts and existing drawers; PHP HTTP relay tests passed for authentication, CSRF, exact notice IDs and disconnected responses.

Release requires current-head CI, Windows executable/browser/bridge/recovery/restore/media/installer acceptance, green post-merge checks, and source/provenance/checksum verification of both deployment ZIPs.

## Independent A5C8 transport review — 2026-10-12

The original release passed its service and browser suites, but omitted `progress.feed`, `notifications.list`, and `notifications.read` from the authenticated remote dispatcher. This prevented Cloud live updates. The original transport acceptance score was unsupported by a production dispatcher test.

The correction registers all three operations under the existing canonical paired VP3 identity and current `agent.chat` grant. Mission authorization failures return HTTP 403. Cloud receipt readback preserves upstream rejection codes, allowing existing browser permission handling to clear cached mission data rather than label revoked authority as a temporary disconnect. Exact missing notice IDs retain HTTP 404.

| Area | Acceptance evidence | Local score |
| --- | --- | --- |
| Authenticated live relay | The new SQLite test reproduced the omitted allowlist failure before the fix; all three production dispatcher operations now pass. | 10/10 |
| Source and privacy isolation | Forged owner payloads, other-app/missing/invalid tokens, private conversation projection and revoked permissions are covered through the actual dispatcher. | 10/10 |
| Durable read identity | Exact notice read, foreign/missing IDs and malformed IDs are tested through the dispatcher. | 10/10 |
| Cloud response status | Real PHP HTTP endpoint and production receipt readback preserve 401, 403, 404, 409, 422 and 503. | 10/10 |

Release acceptance still requires fresh current-head CI, Windows installer certification, green merge checks and verified replacement deployment ZIPs. Local Chromium startup is blocked by environment socket restrictions; browser acceptance is verified by GitHub CI.
