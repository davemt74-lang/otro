# VP3 OS v1.0 — Production Release Hardening

VP3 OS v1.0 is the production release gate for the runtime built through v0.90. It adds **No new product features**. The purpose of v1.0 is to prove that the existing VP3 OS stack can be installed, upgraded, restarted, recovered, and operated repeatedly without weakening privacy or physical-action governance.

## Release contract

A v1.0 release candidate must pass all of the following before merge:

- fresh install against a new HomeServer data directory;
- schema migration and idempotent re-initialization through schema 26 or newer;
- restart persistence for Rooms, Devices, v0.70 routines, and v0.90 Room Modes;
- local backup creation and recovery validation;
- Windows executable and installer validation through the broad HomeServer CI;
- cross-platform VP3 OS validation on Ubuntu and Windows;
- a bounded soak of repeated Room Mode activation/approval/lifecycle cycles;
- database quick-check and foreign-key integrity after the bounded soak;
- owner credential availability and platform protection;
- safe degraded operation when optional hardware is unavailable;
- zero unapproved physical actions during the end-to-end release journey;
- post-merge validation on the exact merged main commit.

## Release readiness

The owner-only release-readiness endpoint returns a deterministic local report covering:

- database integrity and schema;
- data-directory writeability and free-space headroom;
- owner credential health;
- backup/recovery state;
- configured VP3 hardware readiness;
- physical-action governance invariants.

The result is one of:

- ready — all release checks are ready;
- degraded — the runtime is safe to operate but an optional or recommended condition is missing, such as no backup yet or expected hardware being offline;
- blocked — a release-critical condition failed, such as database corruption, an unavailable owner credential, an unwritable data directory, or a failed physical privacy invariant.

production_ready is false only when a blocking condition exists.

## Safety boundary

v1.0 does not create another execution path. Room Modes continue to call v0.70 routines, which create ordinary v0.60 devices.command approval requests. The v1.0 readiness service does not approve requests, create device command requests, or call provider/device execution primitives.

The release journey and bounded soak explicitly assert zero unapproved physical actions.

## CI discipline

Only two workflows are automatic for the v1.0 pull request:

1. HomeServer CI — broad Windows regression, packaging, installer, upgrade, backup/recovery, and distribution validation.
2. VP3 OS Production Release v1.0 — targeted Ubuntu/Windows VP3 OS release validation.

The v1.0 workflow uses concurrency cancellation so a newer release-candidate commit supersedes an older run. Historical VP3 OS phase workflows remain manual-only.

No new VP3 OS phase starts until v1.0 is green, merged, post-merge validated, and the deploy artifact from merged main is available.
