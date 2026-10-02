# Tracky 1G1: integrated runtime audit (Cloud, HomeServer, development)

## Existing canonical production integrations verified on merged main
- **HomeServer** owns the canonical Tracky world state, semantic observations and governed active-perception request ledger in `app/services/tracky_physical_context.py`. All local providers use its single provider registry.
- **HomeServer owner-approved native camera** uses `tracky_native_camera.py`, the reviewed local model-integrity gate and the already-existing certification evidence ledger. `tracky_native_managed_session.py` reuses the same camera driver lock and canonical provider registry. The browser one-shot provider is implemented by `tracky_owner_perception.py`.
- **Owner visual identity** already uses the integrated HomeServer onboarding, local participant store, three-angle browser enrollment, revocable local Contacts association and revision-ordered opt-in to Cloud. No automatic identity claims or biometric payloads go to Cloud.
- **Agent Chat** already exposes onboarding, native diagnosis, owner review and supervised-session statuses. **Agent Brain** already owns federated contextual state through `tracky_federated_agent_context.py`.
- **Cloud** receives authenticated Tracky site semantic health/events through `includes/tracky-cloud-v270.php`. Owner association state is separately revocable and ordered; Cloud does not accept local signed receipts as verified biometric authority.
- **Standalone `tracky` repo** has the development-side enrollment, world-state reconciliation, model experimentation and simulator test modules. There is no requirement for the production user to deploy it or run another Tracky process.

## 1G1 integration defect fixed here
Prior `public_capability()` exposes any registered callback as `provider.available`, and `_cloud_payload()` translated this directly into `capabilities.active_perception=true` and site health, even for an *owner-only* temporary browser/supervised/native test provider. Callbacks reject unapproved requests correctly, but Cloud/Agent-facing availability was misleading.

**Correction:** retain `provider.available` as the local registration contract, add an explicit canonical `remote_requestable` capability and expose it consistently to Agent Chat and Cloud. A registered provider is never advertised to Cloud merely because it exists. An owner-bound provider remains local-only even if a buggy caller sets `remote_requestable=true`; an unknown future remote provider must explicitly declare `remote_requestable=true`, and nontracking providers cannot be advertised as unattended services. This makes the advertised capability fail closed without implementing a second runtime or changing owner capture flows.

## Next gating work; not yet implemented by 1G1
1. **1G2 controlled Agent Eyes:** build bounded, audited, owner-approved native perception using *this* provider registry, existing native capture lock, the existing model lifecycle and correction/replay pipeline. Camera runtime remains off by default; continuous unattended recognition is not enabled by this audit.
2. **1G3 Agent Brain:** expose normalized live capability, participant confidence/unknown state, policy and correction provenance through the existing Agent cognitive loop without inventing recognition proof or linking unknown faces.
3. **1G4 Cloud UI:** show effective remote readiness, consent/revocation and recovery in existing Cloud device/Agent views, without Cloud biometric storage.
4. **1G5 installed hardware:** ship signed release artifacts, then complete real camera/privacy/restart/driver tests on the owner's actual HomeServer. Green synthetic CI is not physical certification.

No existing Tracky enrollment, world-state, federation, model or camera service should be reimplemented. All optional visual recognition remains governed by separate opt-in, device capability, owner acceptance and retention controls.
