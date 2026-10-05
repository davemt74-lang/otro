# Section 11 — Onboarding, accounts, membership and permissions

This section reviews the existing main Cloud (`software`) and Windows HomeServer (`otro`) flow. It extends the current chat canvas onboarding and canonical account/member services. Installed microphone/camera certification remains the owner's Section 10 exercise; it is independent of these software changes.

## Inventory and review scope

| Area | Existing authority | Review/validation |
| --- | --- | --- |
| Cloud required setup | chat-onboarding-v241, user_agents, user_profiles, onboarding intelligence | Required Agent name/profile address retained; finish serialized on account before resolving Agent. |
| Optional activation and Agent context | workspace state, activation state, onboarding skill, Cognitive Feed | Configured services with revoked permission remain locked in progress/milestones/Chat context. |
| Account token generation | homeserver-account-pairing-v1210 | Account lock covers existing connection check and token replacement. |
| HTTPS pairing | homeserver-https-relay-v1300 | Account/token/session/connection commit atomic; cross-account device collision rejected. |
| HomeServer setup and recovery | onboarding_chat, cloud_pairing, HTTPS session, existing local paired-app permissions | Durable protected pending journal; same approved credentials reused after lost response/restart; existing saved connection protected. |
| Owner boundaries | existing OwnerGateway and local onboarding UI gate | Existing owner-only setup/member management retained; member admin role is not owner. |
| Member auth and private context | members, member_api, migration 064 | Current password/status/lockout rechecked under SQLite write lock; context writes/deletes recheck current role/status; bounded concurrent session/context behavior. |
| Installer and release | existing Windows CI and production Cloud workflow | New service regressions join retained owner/onboarding/pairing/member and packaged-launch checks. |

## Pairing recovery contract

HomeServer persists a pending journal before sending the HTTPS request. The journal contains the account token's hash, endpoint/device bindings, the already approved local credential, and a high-entropy recovery session secret. The actual account token is not persisted. The journal uses existing Windows DPAPI protection and atomic writes (restricted file on non-Windows).

Cloud stores only the session-secret hash and encrypts the local credential with existing credential storage. A retry may return the original committed session only while the original account token is unexpired and paired, the same device/local credential/session proof all match, and the canonical connection and session are still active. Retry does not rotate credentials, reset permissions, update metadata, revive revoked connections or emit a duplicate event. Legacy callers without a recovery proof keep first-use pairing compatibility.

A lost HTTP response does not revoke the original local approval. A partial local save can finish using the journal and saved HTTPS session. Already saved connections must be explicitly disconnected before new pairing. Owner reset clears unfinished authorization; disconnect shares the device-poll lock and removes pending device proof, so a late background poll cannot restore the connection. Setup polling avoids concurrent deliberate owner actions and ignores older summary responses.

Deploy Cloud before HomeServer to enable the full lost-response replay contract. Against older Cloud versions, normal pairing remains compatible but a lost response after Cloud commits can still require explicit Cloud removal and a new pairing. Recovery is bounded by the original 15-minute authorization; it is not a permanent bearer-token recovery endpoint.

## Validation and acceptance gates

- `tests/onboarding_accounts_section11.php`: production SQL/crypto over a dialect-adapted SQLite fixture; atomic rollback, retry/revocation/expiry/bindings, cross-account collisions, event projection failures, optional permission-state propagation.
- `tests/onboarding_accounts_section11.py`: production pairing/crypto/member services with controlled HTTP transport and real SQLite; response loss/restart/partial save, existing connection protection, cancellation, same-origin relay URL, current authority during authentication, simultaneous lockout and bounded sessions/context.
- Retained onboarding, first-run, membership, account-pairing, HTTPS relay, owner gateway, migration, privacy and integration checks remain merge gates.
- All PR checks must finish successfully at the final revisions before merge; merged production artifacts must match those merged sources.

## Owner installed checks

1. Upgrade Cloud and HomeServer, keeping existing private data. Existing pairing should continue.
2. With a fresh account/device, complete Agent name/profile address and pair from the HomeServer chat canvas using the visible code.
3. Interrupt connectivity during pairing and retry the same code within 15 minutes; verify a single approved connection after restart.
4. Reset an unfinished code; verify the old local proof no longer completes setup. Disconnect; verify background polling does not reconnect automatically.
5. Remove/restore access to a previously configured optional workflow; verify locked/complete status follows current permission.
6. Use distinct member/guest accounts; check private context isolation, guest write rejection, owner-only management, password reset and disabling existing sessions.

These checks supplement CI; no hardware or installed-device certification is claimed by this section.
