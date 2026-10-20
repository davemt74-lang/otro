# VRSS V2D — signed likes and unlikes

Feed 1.5.0 adds likes to the existing authenticated VRSS connections. RSS remains the post transport. Each post author's HomeServer owns its aggregate; no central social database is introduced. This is the bounded `vp3.vrss.interactions.v1` protocol, extending the existing VRSS signed inbox. It does not claim general ActivityPub interoperability. Comments/replies, private audiences, reposts and identity-address migration are later sections.

## Owner experience and upgrade

Existing installations remain opted out of receiving likes. In Feed → Edit Profile, enable **Allow signed VRSS likes on public posts** in addition to the existing publication/following settings. UUID, canonical HTTPS address, private keys, Hosting binding, posts, subscriptions and stopped-app state are preserved. Disabling incoming likes does not delete retained records.

An owner can like a public post from an accepted VRSS follow. The destination must advertise enabled reaction support. Ordinary RSS items do not acquire fabricated social identities. Own posts show received counts; self-liking is unavailable. Private and deleted posts cannot be discovered or liked.

Feed cards offer Like/Unlike and a count refresh. A pending action does not optimistically increment the author's count. Cached counts are explicitly the last report from the author; the control tooltip shows when they were checked. Delivery acknowledgements update that report. Agent Brain shows queued Like/Unlike updates and delivery errors in the existing drawer.

Cloud Agent Chat accepts `like VRSS <canonical-post-URL>`, `unlike VRSS <canonical-post-URL>`, and `show VRSS likes <canonical-post-URL>`. Writes use existing conversation-bound review/confirmation, execution leases, current permissions and exact confirmation replay. HomeServer native Agent actions expose the same operations through the existing Feed action catalog. A verified new-like notice can show its count directly on the HomeServer Chat canvas; no separate dashboard is required.

Like notices use existing informational notification and voice preferences. A stable actor/post digest prevents repeated notices for the same pair. Unlike dismisses that notice. When existing authenticated notification export is allowed, Cloud projects the committed notice and read/dismiss receipt once. Neither an RSS item nor arbitrary remote text can fabricate this event.

## Protocol and authority

The actor document advertises enabled `interactions.likes` and its contract. The sole public write surface remains signed JSON POST `/vrss/v1/inbox`. Like/Unlike envelopes retain the existing actor UUID, exact recipient, activity ID, per-peer sequence, follow ID, issued time, transport time and Ed25519 signature, plus canonical `object` and immutable sender-owned `reaction_id`. Follow/Accept/Undo/Reject retain their exact original schema.

The read-only `/vrss/v1/posts/<uuid>/interactions` endpoint returns a bounded projection: contract, canonical object, author actor/UUID and count. Discovery validates every field against the pinned actor and HTTPS origin. It never downloads a large post body to establish reaction support. Existing Hosting ingress explicitly allowlists this projection and retains route-token, hostname, TLS, binding and owner-API isolation.

A new Like requires the current accepted inbound follow and its exact follow ID, incoming-likes opt-in and a current public undeleted local post. Counts join that same current relationship, excluding blocked actors. Blocking, removing a follower or unfollowing cannot revive old counts upon reconnection. Unlike may clean up an existing or known revoked relationship, including after a post stops accepting likes. A removed reaction ID remains a permanent tombstone: a fresh activity ID, signature, timestamp or sequence cannot restore it.

Outgoing owner requests carry a durable nonce and payload fingerprint. Repeating one nonce cannot run another operation or toggle newer state. A changed payload under the same nonce fails. Unlike cancels a pending Like lease and queues its own ordered update. A request already sent cannot be recalled; the later higher-sequence revocation fences it remotely. Crash leases, retries and backoff reuse the existing durable outbox.

An obsolete read cannot overwrite a newer count acknowledgement or local mutation: backend cache revisions and UI view generations fence delayed responses. A cancelled in-flight delivery cannot apply its acknowledgement. Current Feed/publication/Hosting authority and Cloud scope are rechecked after discovery and at commit. Stopping Feed or losing publication/TLS authority pauses exchange while retaining data.

Existing 32 KiB envelopes, 4 concurrent verifications, 120 incoming envelopes/minute globally, 30/actor/minute, 1,000 peers, 10,000 inbox receipts/outbox history records and app SQLite quotas remain. Reaction records, selected-post caches and owner request receipts each have a 10,000-row limit. Histories fail visibly when full rather than discarding replay fences. History maintenance remains later work.

## Acceptance gates

The section is complete only when all ten gates pass; the release score describes this bounded feature, not a guarantee against every possible deployment failure.

| Gate | Evidence |
| --- | --- |
| Identity and object binding | Real Ed25519, pinned actor UUID, exact origin/recipient/post/reaction ownership |
| Consent and privacy | Default-off upgrade, explicit profile toggle, private/deleted exclusion |
| Count integrity | One current like per actor/post; duplicate receipt and request replay |
| Revocation | Permanent unlike IDs, current follow IDs, block/unblock and removal fences |
| Durable delivery | Restart, retry/lease ordering, real HTTPS acknowledgements |
| Race recovery | Stale read/mutation and cancelled in-flight ACK cannot overwrite newer state |
| Current authority | Cloud/follow revocation during discovery; existing stopped-app and Hosting boundaries |
| Chat and Brain | Governed confirmation/replay, inline count, pending delivery; actual MySQL notice/read sync |
| Browser | Production API/UI at 1440/390/320 pixels, likes/unlikes, pending/acknowledged counts, no overflow |
| Release | Existing VRSS/Hosting/Chat regressions, full Windows installer certification, green merged PRs and exact-source verified ZIPs |

`tests/vrss_v2d.py` uses independent subprocesses and production databases/signatures with a controlled document boundary. `tests/vrss_v2d_tls.py` uses two actual HTTPS application servers and unchanged production transport, with a test CA and deterministic DNS/socket mapping. Browser tests drive production APIs and UI with controlled signed external fixtures. These tests do not claim live geographically separate public deployments. Windows executable, browser, restart and installer gates remain required before release.
