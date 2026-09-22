# VP3 OS v1.1 — Controlled Production Rollout & Hardware Certification

VP3 OS v1.1 moves the v1.0 production release onto an appliance-style operating model. It adds no new cognitive or physical-action authority. The phase focuses on commissioning, hardware certification, controlled release channels, staged updates, runtime recovery, privacy-safe diagnostics, and pilot rollout discipline.

## Default production posture

A fresh v1.1 installation starts with:

- release channel: stable
- rollout ring: pilot
- automatic update apply: disabled
- supervised runtime watchdog: enabled
- maximum failed-start policy metadata: 3
- remote unattended update feed: disabled

Updates are always owner staged and owner approved. Selecting beta/dev or moving from pilot to staged/broad is an explicit local-owner choice.

## Commissioning

The commissioning report combines:

- VP3 hardware profile and normalized hardware inventory
- hardware adapter/controller state
- physical privacy-switch verification
- database integrity/schema state
- owner credential availability
- backup readiness
- v1.0 production release-readiness gates
- first-run setup state
- current release channel/ring

Commissioning is ready when release readiness, database/security, backup, and hardware are ready; degraded when operation is safe but a recommended or expected component is missing; and blocked when a production-critical safety/integrity condition fails.

## Hardware certification

v1.1 can persist a certification snapshot for VP3 Node, VP3 Desk, VP3 Studio, VP3 Team Node, VP3 Pocket, and custom hardware. Certification records VP3 OS version, hardware/firmware revision when available, expected component inventory, missing/not-ready components, controller metadata, privacy status, and result.

A privacy-switch fault is a failed certification. Missing expected runtime hardware is degraded. A complete safe profile passes.

## Controlled release package

The v1.1 updater consumes an owner-supplied ZIP with RELEASE.json, HomeServerSetup.exe, HomeServer.exe (recommended), and SHA256SUMS.txt at the archive root.

RELEASE.json uses format vp3-os-release-v1 and declares version, release channel, minimum supported database schema, SHA-256 hashes for executable/installer, and optional release notes.

Before a package can be staged, VP3 OS verifies archive paths, entry count/size limits, channel compatibility, schema compatibility, embedded file hashes, and SHA256SUMS.txt.

v1.1 does not add an unattended internet update feed. Package authenticity remains an explicit owner/distribution responsibility; the local runtime verifies the package integrity it is given.

## Apply and rollback lifecycle

A staged update must be explicitly approved. Approval creates a validated HomeServer backup before the apply control becomes available.

When the owner applies an approved update on installed Windows HomeServer:

1. the staged installer is re-hashed;
2. the current executable is copied as a binary rollback point;
3. a pending-update manifest is written under the private runtime directory;
4. HomeServer shuts down through the supervised runtime controller;
5. an external Windows helper installs the update silently;
6. the updated HomeServer is launched;
7. the helper checks the local /api/v1/health endpoint;
8. if health validation fails, the helper stops the unhealthy executable and restores the previous executable;
9. rollout state reconciles locally as applied, rolled_back, or failed.

The pre-update private-data backup is preserved independently. v1.1 does not silently restore private data during binary rollback, because database rollback can destroy legitimate post-backup changes and must remain an explicit recovery operation.

## Runtime watchdog

The installed tray runtime watches the supervised API server thread. If the server exits unexpectedly after startup, the launcher treats it as a supervised restart condition. Explicit restart, shutdown, and update transitions are not considered crashes.

This watchdog does not bypass restricted recovery mode and does not create an infinite update/restart authority.

## Sanitized support bundle

The owner can create a local ZIP containing only bounded system metadata: release/support summary, sanitized diagnostics, commissioning/hardware state, and recent rollout events.

The support bundle explicitly excludes conversations, recordings/audio, Memory content, Knowledge/document content, provider/owner credentials, and absolute HomeServer filesystem paths. Support bundle creation is owner-only.

## v1.1 acceptance gates

The release candidate must prove schema 27 fresh install and upgrade/idempotence; stable/pilot safe defaults; hardware commissioning and certification; release-package path/hash/schema/channel validation; tampered package rejection; pre-update backup requirement; installed-Windows-only apply boundary; external update helper and binary rollback contract; update result reconciliation; sanitized support-bundle privacy; v1.0 production regression remains green; Ubuntu + Windows v1.1 gate; broad HomeServer Windows packaging/installer/upgrade gate; exact-head merge discipline and post-merge validation; and a final merged-main deploy artifact that includes RELEASE.json.

No next VP3 OS phase begins until all of those are green, merged, post-merge verified, and packaged.
