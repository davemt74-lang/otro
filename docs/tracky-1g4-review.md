# Agent Eyes 1G4 — Unified Cloud experience

The scene transport, local camera runtime, model review and recovery controls already exist. This section adds one read-only presentation over their canonical state, shared by Cloud Tracky, HomeServer device settings and Agent responses.

| Acceptance criterion | Evidence |
| --- | --- |
| One status explanation | Canonical current-context/Agent Brain field, direct Agent answer, shared card and lightweight authenticated GET |
| Truthful consent | Cloud explicitly knows only last received state; never asserts current local consent or a pending revocation; HomeServer distinguishes local off, delivery pending and acknowledgment |
| Freshness | Original 60-second observation ceiling; expired labels removed; original last observation timestamp retained; elapsed HTTP time deducted |
| Connection | Account/site/device-bound existing heartbeat; 300-second recent-contact ceiling; future/invalid contact remains unknown; stale contact does not certify disconnection/recovery |
| Setup/privacy/recovery | Static guidance from existing normalized scene reasons; actions remain in local Tracky; status refresh cannot capture, renew consent or acknowledge recovery |
| Browser boundaries | Text-only DOM, bounded cards, abort timeout, no hidden polling, clear on hidden/error, isolate late responses and request controllers |
| Integration/isolation | Existing HTTPS and ordered scene ledger retained; paired site binding and cross-account/site reads verified with actual InnoDB |
| Regression/package | Existing PR Core/Recovery/release gates; new section files required by production packaging; no new workflow or runtime |

Code review target is 10/10. Final software acceptance is 10/10 only after exact-head CI, merge and merged-main archive verification. Installed-device camera acceptance remains separate.

No schema migration or scene protocol change is required. Cloud upgrade.php remains the standard upgrade path. Install the companion HomeServer v2.4 build for enhanced local delivery explanations. Optional sharing remains off by default and resets off on restart.
