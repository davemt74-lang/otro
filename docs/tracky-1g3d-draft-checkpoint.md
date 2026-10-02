# Section 1G3D draft and recovery checkpoint

Section C is complete at c6c875e7d697bfeb62b2fd5d381d48c2c6cc0eb4 (tree d7382f6c99ab22ae069a5635925bf47dd1b657f8).
PRs #251 and #252 are merged. All four corrected merged-main workflows passed, including Windows CI run 37054592468.
Its HomeServer-Windows artifact is 11247773732, size 241330922, SHA256 4c18674301396013ff8fc0cce3e2920adf2dae6493814567a4430e4af7d63344.
The installed owner camera/scene tests remain required; hardware is not certified.

This D branch is an UNWIRED, UNTESTED checkpoint, not a completed section or deployable feature.
The execution workspace disconnected with environment_offline while drafting D.
The app/services module is preserved here. Owner UI controls, transport integration, Cloud integration,
focused tests and final deploy ZIP verification remain required. Do not merge this checkpoint.

Pending HomeServer integration, drafted locally before the disconnect:
- Agent Eyes scene projection calls shared.decorate(scene, request fingerprint), with separate owner reports and room-device sources.
- Compact prompt keeps camera object enums, setting/lighting, owner-report map, conflicts and at most two typed device summaries.
- Native managed worker calls shared.record_current after a completed non-test observation, outside its lock.
- Existing current_context world_state uses shared.local_world; MUST gate this on scene configuration/review and avoid new opt-out camera reads.
- Existing Cloud payload excludes every shared.reserved relation from generic world_state; sends only revision and filtered summary in agent_scene_share.
- Existing sync_due considers shared.pending while retaining retry backoff.
- Existing sync_cloud uses an exact site/revision/fingerprint/state receipt to acknowledge CURRENT consent, not an older in-flight snapshot.
- Owner onboarding GET/POST /visual/agent-eyes/scene/share, strict booleans and UI header.
- Owner conversation POST /agent-eyes-correction: validate owner conversation, include_agent_eyes, sticky local-only and cloud_allowed false; typed object/present/fingerprint.
- Add chat correction controls with stale-response/visibility/TTL isolation, and Tracky sharing controls default off.
- Fix status UI to display selected model and expired review clearly.
- Add tests/tracky_agent_eyes_shared_scene_v1g3d.py and UI suite to existing PR Core and Windows CI ONLY after test files exist.

Cloud draft design (repository davemt74-lang/software base 122a2ebb21c9b5b2d5cf587c04118932b801f560):
- New typed helper includes/tracky-agent-scene-v1g3d.php, strict exact schema, enums, timestamp, 8 object/report limits, fixed protocol.
- Canonical JSON SHA256 matches Python fingerprint; arrays sorted, associative keys recursively sorted.
- Ordered per-account/per-site consent metadata, in existing Tracky sync transaction, after existing session/plugin/device/site checks.
- New metadata table tracky_cloud_scene_share_order: user_id/site_id PK, accepted_revision, fingerprint, summary_json. Not another physical world authority.
- SELECT FOR UPDATE, newer revision wins, equal identical is idempotent, equal different conflicts, old request never overrides revocation.
- Receipt accepted/revision/fingerprint/state/site_id; observation time never renewed by heartbeat or retry.
- Projection hides objects/reports unless original observation age is 0..60s; future/stale/unavailable/revoked never expose labels.
- Attach filtered scene to existing current_context/cognitive-context; derive inferred class graph rows, never identity or verified inventory.
- Agent physical queries and Tracky page explain possible objects, age, uncalibrated uncertainty, separate owner reports and conflicts.
- Include helper via tracky-cloud-v270.php and schema bootstrap; production package file/metadata checks.
- Add PHP normalization/order/account isolation/freshness/malformed/replay tests plus golden Python/PHP fixtures to existing recovery baseline.
- No camera capture authority, media, model/provider prose, session IDs, chat history or messages in Cloud summary.
- Process consent resets off on restart. Revision repair after backup must retain current revoked or freshly owner-reapproved desired state.

Local Cloud baseline before D passed every case after correcting only the scratch PHP session directory.
PHP 8.3 CLI was extracted under /workspace/scratch/e410f85fac76/php-runtime; no production dependency changes.
Scratch checkpoints: 1g3c-state.json, 1g3d-design-notes.txt, integrate_1g3d_hs.py, verify-homeserver-artifact.py.
Current Library helper pointer: 1g3cd-helper-path.json; fresh helper copies were prepared for this request.
Actual D code and final ZIPs still require a connected execution workspace. No D score or release has been claimed.
