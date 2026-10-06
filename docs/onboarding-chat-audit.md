# HomeServer onboarding and chat audit

Initial score: 6/10. This is a ten-item release checklist, not a claim that all HomeServer features or physical devices have been independently certified. Final acceptance requires green PR checks, merge, and a verified Windows package.

| Criterion | Result and evidence |
| --- | --- |
| Short basic setup | One Cloud connection card; voice and camera choices collapsed; Chromium verifies zero visible consent controls in basic setup |
| Clear choices | Sign in and approve in Cloud, or explicitly choose local use; saved pairing is distinguished from a live heartbeat |
| Truthful completion | Connected finish rechecks server authority and returns 409 when the connection is unavailable; explicit local finish preserves existing pairing |
| Working primary controls | Connection refresh, finish error recovery and local completion exercise the real owner API in Chromium |
| Finite requests | 45-second abort, unreadable-response errors, pending status, busy release and timer cleanup; lifecycle harness |
| Current state | Summary/code revisions and operation generations reject late poll/start replies; duplicate starts blocked; lifecycle harness |
| Resource use | Basic status excludes optional package scans and camera diagnostics; API tests assert those functions are not called; hidden/inactive setup does no polling |
| Permission and Stop | Optional consent remains explicit; collapsing a camera section stops its activity through existing lifecycle hooks; real UI never requests microphone/camera during setup inspection |
| One page scrollbar | Chat and onboarding overflow remains visible; 500-message Chromium acceptance checks document scrolling and readable newest message at four widths |
| Wide canvas and stable footer | Responsive canvas/composer up to 1120px, sticky footer with measured scroll clearance, contained consent text, working modal/sidebar and no unhandled browser errors |

The onboarding controller is scripted and works before an AI provider is configured. Text responses still need an enabled provider. Real account sign-in/approval and installed Windows camera hardware remain manual acceptance steps.

Connection lifecycle fixes are retained from PR282. The Windows release gate exercises same-version replacement using the executable SHA256, saved HTTPS pairing after restart, remote permissions, recovery, restore, and private-data preservation.
