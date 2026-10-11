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

The foreground feed refreshes every five seconds and labels disconnected data as stale. It reports stages and elapsed activity, without fabricated percentage completion. Notifications are bounded and unread-first; large unread counts are marked as a lower bound. Local notices also appear in HomeServer Activity Center.

Recovery never automatically replays interrupted work. Owners review and authorize reexecution through existing controls. Cloud views remain subject to paired-app authority and conversation export settings.

## Validation

Real SQLite lifecycle, transaction rollback, read persistence, cancellation/restart idempotency, stale lease fencing, current lease protection, late-result rejection, source isolation, privacy revocation and watchdog lifecycle passed. Real Chromium tests passed for both deployed scripts and existing drawers; PHP HTTP relay tests passed for authentication, CSRF, exact notice IDs and disconnected responses.

Release requires current-head CI, Windows executable/browser/bridge/recovery/restore/media/installer acceptance, green post-merge checks, and source/provenance/checksum verification of both deployment ZIPs.
