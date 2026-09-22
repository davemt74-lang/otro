# VP3 OS v1.2 — Fleet & Remote Device Management

VP3 OS v1.2 adds a local-first fleet layer on top of the v1.1 appliance runtime. It does not add new cognitive authority, physical-action authority, or an alternate updater.

## Trust model

Each VP3 device remains authoritative for its own private data, hardware, approvals, and update lifecycle. Fleet access is disabled by default. The owner must select one active paired controller application that already has fleet.read, fleet.manage, and fleet.telemetry.

Fleet decommissioning disables fleet access and revokes only those fleet permissions from the enrolled controller. It does not delete private HomeServer data or unrelated paired-app permissions.

## Device health telemetry

The paired fleet status surface exposes only bounded operational metadata: opaque HomeServer device ID, owner-assigned label, VP3 hardware profile, VP3 OS version, release channel, rollout ring, commissioning/certification state, privacy-fault flag, update state, backup-state bucket, storage-state bucket, watchdog-failure count, and report timestamp.

Fleet telemetry explicitly excludes conversations, recordings, Memory content, Knowledge/document content, credentials, filesystem paths, host/network addresses, and arbitrary metadata.

Remote diagnostics and support summaries are separate owner-controlled policy toggles. A full v1.1 support ZIP remains owner-only/local; v1.2 does not upload private diagnostics to an arbitrary remote endpoint.

## Fleet inventory and alerts

A fleet controller registry can store sanitized check-ins from multiple VP3 devices and derives online/stale state from a configurable threshold. Alerts cover offline devices, privacy faults, blocked/degraded commissioning, failed/degraded hardware certification, missing/stale backups, low/critical storage, failed/rolled-back updates, watchdog recovery, and paused rollouts.

Removing a device from the registry changes only the fleet registry. It does not reach into the remote device or erase that device's private data.

## Rollout rings

Fleet rollouts track a release version, stable/beta/dev channel, pilot/staged/broad ring, state, failure threshold, and per-device outcomes.

Ring targeting is cumulative: pilot includes pilot devices; staged includes pilot plus staged; broad includes pilot, staged, and broad devices on the matching channel.

Device outcomes are pending, staged, approved, applying, healthy, failed, rolled_back, offline, or degraded. An active rollout automatically pauses when failed plus rolled_back outcomes reach its failure threshold. The owner can manually start/resume, pause, complete, or cancel a rollout.

## Remote update requests

Fleet control does not create another updater. A paired controller may request only a release whose exact SHA-256 and version already exist in the local v1.1 staged-package registry. If the package is absent, the request is recorded as unavailable.

A valid request becomes pending_owner. Only the local owner control surface can approve it. Approval reuses v1.1 pre-update backup and package approval. Applying the update remains a separate explicit local v1.1 action. Fleet control cannot download, approve, or apply an update automatically.

## Remote Bridge operations

v1.2 extends the existing outbound Remote Bridge allowlist with fleet.device.status, fleet.device.diagnostics, fleet.device.support_summary, fleet.device.update_request, fleet.checkin, fleet.inventory, fleet.rollouts, and fleet.rollout.outcome.

Every protected operation is forwarded only to loopback HomeServer APIs, which re-authenticate the paired bearer token and enforce fleet permissions plus exact enrolled-controller identity. There is no generic HTTP proxy and no remote owner-control route.

## Fleet dashboard

Setup & Diagnostics includes a Fleet Management workspace for local enrollment policy, controller selection, privacy toggles, fleet inventory, alerts, rollout creation/lifecycle, and owner review of pending fleet update requests.

## Pilot acceptance

The v1.2 release gate simulates five devices across pilot/staged/broad rings: one healthy device, one rollback, one failed installer, one degraded hardware device, and one offline device. The broad rollout must pause automatically at the configured failure threshold while retaining all device outcomes and alerts.

## Release gates

v1.2 must pass schema 28 upgrade/idempotence, v1.0 and v1.1 historical regression, fleet runtime/API/privacy tests, five-device pilot acceptance, Remote Bridge fleet routing, JavaScript syntax, backup/security/system reliability, Windows installer contract, broad HomeServer packaging, post-merge Ubuntu/Windows fleet validation, post-merge HomeServer CI, and final merged-main Windows artifact verification.

No next VP3 OS phase begins until v1.2 is green on the exact PR head, merged, green on the exact merged main commit, and the final deploy ZIP is verified.
