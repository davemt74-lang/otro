# Section 16: Apps, Plugins and Agent Control

Audit baseline: **7/10**. Six areas supported the normal path but failed a recovery or authority boundary. Each area below receives 1 point when its acceptance checks pass, 0.5 when only the normal path works, and 0 when unavailable. The score applies to this release's defined acceptance scope, not every possible behavior of the platform.

| Area | Baseline | Release acceptance |
| --- | ---: | --- |
| Settings persistence | 0.5 | Validate every field first; restore the vault after database failure; preserve concurrent changes |
| Dispatch authority | 0.5 | Enforce confirmation and read-only policy inside the dispatcher |
| Approval binding | 0.5 | Reject approval replay after the selected release or action changes |
| Plugin governance | 0.5 | Preserve pause/revocation on registration; reject invalid tool arguments before handler entry |
| Release recovery | 0.5 | Restore the prior release on failed activation; restore action, settings and permission metadata on promotion |
| Cloud acknowledgements | 0.5 | Reject failed, empty, mismatched and unredacted control replies |
| Protected system targets | 1 | Retain protected app lifecycle and builtin provider identity checks |
| Scoped tool authority | 1 | Retain current permission and app scope checks |
| Secret projection | 1 | Keep values out of returned settings, metadata and audit events |
| Existing app contracts | 1 | Retain catalog, lifecycle, Agent context, distribution and app regressions |

The candidate reaches 10/10 only after these checks and the release workflows pass. Local tests run `tests/apps_plugins_section16.py`, the retained HomeServer Apps workflow's test list, cognition, and Section 14 authority checks. PR Core and Windows release CI run the new regression plus universal app control, Agent runtime and cognition checks.

Changes serialize app control and release modifications within the HomeServer process. Release-bound approvals require a fresh request after an app update; older unbound generic action approvals fail closed. Settings compensate for reported filesystem/database write failures. This is not a claim of a single crash-atomic transaction across SQLite and the separate DPAPI vault file.

Windows deployment must use the merged main build's `HomeServerSetup.exe` and preserve the existing HomeServer data directory. The Cloud deployment archive must match its merged main commit and retain live configuration, private storage and uploads.
