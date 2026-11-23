# HomeServer reliability audit — 2026-11-23

This audit covers the Windows owner interface, conversation and voice lifecycle,
provider cancellation, Ambient speech, outbound Cloud relay, paired-app data
authority, continuity snapshots, installed-app scheduling, and release checks.
It extends the existing application and protocol rather than introducing another
chat engine or pairing path.

## Findings and corrections

| Finding | Correction | Regression evidence |
| --- | --- | --- |
| Completed continuity responses waited behind supplementary network sync and polling. | Upload each result promptly; preserve accepted batches and retry receipts without replaying actions. Run supplementary sync in one worker. | `remote_bridge_receipt_latency.py`, `remote_bridge_sqlite_recovery.py` |
| Superseded background responses could alter a replacement pairing's sync state. | Fence response application and failure reporting to the current session; never hold the pairing lock across HTTP. | `tracky_sync_session_fence.py`, `vp3_https_session_race.py` |
| Overview's floating footer submitted before the canonical conversation was initialized. | Share preparation, acknowledge actual turn acceptance, and retain rejected drafts for explicit retry. Optional settings do not hold readiness. | `chat_workspace_browser.mjs`, `listening-section2.mjs` |
| Prepared conversation binding could leave the canonical submission guard unready. | Establish readiness for a prepared conversation, invalidate stale history, and refuse rebinding during restoration or an active turn. | `listening-section2.mjs`, `agent_mission_chat_routes_a5c7_browser.mjs` |
| First Listen could cancel its own saved-conversation restoration. | Preserve only initial voice setup during restoration; manual switching, navigation and Stop still cancel. | `listening-section2.mjs`, `chat_workspace_browser.mjs` |
| Windows footer hid Listen, added a separate Chat button, and differed from Cloud. | Match the white Cloud footer, show Listen and Send, preserve options and the floating orb. | Desktop/mobile browser screenshots and geometry assertions |
| The floating orb covered popup Close, and moving the canonical panel lost its modern message styling. | Reserve visible space for the orb and share the existing message styles with the popup. | Normal pointer-hit, text contrast, content geometry, scrolling and composer assertions on desktop/mobile |
| Aborting a native owner chat left model/tool work able to commit a late answer. | Native UI opts into request-disconnect cancellation using the existing cancellation token and cleanup path. Legacy and paired request semantics remain explicit. | `owner_chat_disconnect_cancellation.py`, `inference_cancellation_v040.py` |
| Ambient voice tests treated an absent external speaker report as an LLM busy/rate-limit error. | Probe the configured/default native desktop output without opening a stream or microphone; preserve explicit managed-device failures and report the actual blocker. | `ambient_desktop_readiness.py`, `ambient_agent_v050.py` |
| Concurrent or obsolete speech could play after privacy/Stop or close replacement audio. | Reserve Ambient speech atomically, recheck permission before playback, and clean up only the owning stream. Presence remains ephemeral. | `ambient_desktop_readiness.py`, `device_audio_v030.py`, `device_audio_v040.py` |
| A transient scheduler database error permanently stopped installed-app jobs; Stop/Start could duplicate a surviving worker. | Retry safely on the existing interruptible interval and retain live stopping workers until they exit. | `homeserver_app_scheduler_recovery.py` |
| Shared continuity exported records outside an application's narrower scopes or accepted a non-VP3 Cloud mirror. | Preserve paired identity and canonical scope checks; authorize the VP3 mirror source before applying it. | `shared_context_scope_privacy.py` |
| Bounded snapshots claimed complete coverage and could tombstone omitted live records. | Exclude incomplete datasets from authoritative cleanup coverage, preserve explicit limited/unavailable reasons, and retain normal complete-snapshot deletion semantics. | `shared_context_snapshot_coverage.py` and companion Cloud snapshot/reconciliation checks |
| Python Playwright installation failed after the moving Linux runner changed. | Pin the affected browser workflows to the supported Ubuntu 24.04 runner. | Hosted browser acceptance |
| Transient media-supplier download failures interrupted installer certification. | Retry transient failures with bounded backoff, clear partial downloads, and retain checksum verification. | `stage_ffmpeg_download_retry.ps1` |

## Release verification

Release requires the final source tree's PR gates, full Windows executable and
installer certification, complete browser acceptance, and the companion Cloud
continuity checks. The installer must preserve existing private data and pairing.
Deployment archives must be checked against their release metadata and checksums.

The audio/device tests use controlled hardware interfaces. Actual microphone,
speaker and device-driver behavior still needs a check on the installed machine.
