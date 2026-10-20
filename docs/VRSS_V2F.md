# VRSS V2F — signed reposts and undo

Feed 1.7.0 extends the existing HomeServer-owned RSS and signed inbox. The original author's HomeServer owns repost permissions, receipts and counts. The reposter's HomeServer owns its attributed link and durable undo intent. Cloud remains the optional governed Chat and notification bridge; existing Hosting subdomains, HTTPS, permissions and private data boundaries remain authoritative.

## Owner experience

Incoming repost permission is explicitly default-off in Feed → Edit Profile. An accepted VRSS follower may repost an original public post only after its author opts in. Feed cards show Repost/Undo repost and author-owned counts. Your profile and feed display who reposted and the original author's name and canonical source link. Reposts contain no copied body, attachments or media. Ordinary RSS items and nested repost pointers do not acquire invented social identities. Repost the canonical original, not another repost.

Publication waits for the exact source acknowledgement. Undo immediately removes local/RSS publication, cancels the earlier send lease and queues signed cleanup. Stable owner-scoped request IDs survive lost responses and browser reloads. A fresh repost after undo uses a new repost ID; old signed activities and owner request replays cannot restore an undone ID. Original content is unchanged by undo.

Native Agent Chat displays verified repost notices and inline source-owned counts; Agent Brain shows durable social delivery and source-check status. The native governed Feed action catalog and Cloud Agent Chat support `repost VRSS <original-post-URL>`, `undo repost VRSS <original-post-URL>` and `show VRSS reposts <original-post-URL>`. Writes retain exact arguments/nonces through existing confirmation, lease and replay checks. Notices retain account scope, informational/voice policies, deduplication and read/dismiss synchronization. Remote content cannot authorize writes.

## Protocol and current authority

The actor advertises `vp3.vrss.reposts.v1` with an explicit enabled flag. `Repost` and `UndoRepost` use the existing pinned Ed25519 identity, actor/recipient, immutable activity ID, ordered per-peer sequence, follow ID and transport-time envelope. They bind the canonical original post and a sender-owned `/vrss/v1/reposts/<uuid>` ID. The receiver checks exact origin, current accepted inbound follow, explicit consent and public undeleted original. Undo retains a permanent tombstone, including cleanup for known revoked follows. Reused IDs cannot change their object/actor/follow.

`/vrss/v1/posts/<uuid>/reposts` exposes the original author's bounded count, actor and identity. Its `/id-<sha256>` projection confirms one exact public repost receipt and current authority. Counts exclude blocked, removed or replaced follows. The reposter rechecks its current local/Cloud authority and follow after discovery and at commit. Revision and live-lease fences prevent stale checks or cancelled acknowledgements from restoring publication.

A public repost pointer rechecks the live original-author receipt before returning its attribution and original link. An unavailable/deleted/private original, disabled permission or removed follow fails closed. The periodic worker rechecks at most two sources per pass, with at least a minute between checks. RSS contains attribution and links only; these require an exact delivered receipt and a successful source check within 15 minutes. Local undo/unfollow/block suppresses publication immediately. A failed current source check suppresses subsequent RSS publication. Readers may retain previously downloaded links; the protocol does not claim to erase remote RSS caches or provide instantaneous global deletion. Opening the repost pointer checks current availability. Never copy private original text into a repost.

Existing Hosting ingress allowlists only public GET/HEAD repost pointers/counts/selected receipts and the signed inbox. Owner controls/app assets remain inaccessible there. Expired TLS, revoked/rotated route tokens, disabled publication, stopped Feed, Cloud revocation and app quotas retain their existing behavior.

## Bounds and upgrade

Repost records, outgoing object histories and owner request receipts each have a 10,000-row bound. Permanent inbox/outbox/revocation fences, per-actor/global inbox rate limits and four concurrent signature verifications remain in use. Counts fit the existing 1,000-peer bound. Public RSS retains 100 entries and 1 MiB total; pointers and receipts fit the 32 KiB JSON transport. Existing app SQLite quotas apply atomically. Full histories fail visibly without dropping replay fences.

The bundled upgrade recognizes Feed 1.0–1.6 and preserves identity, signing keys, address/Hosting binding, posts, subscriptions, likes, comments and deliberately stopped state. Deploy matching green merged Cloud and Windows packages, Cloud first, preserving private data. This bounded VRSS extension does not claim general ActivityPub compatibility, live geographically separate deployment certification or physical hardware certification. Quote text, nested repost chains, private audiences, copying media and address migration are outside V2F.

## Ten release acceptance gates

| Gate | Required evidence |
| --- | --- |
| Consent and upgrade | Default-off source permission and 1.6→1.7 upgrade preserving data/keys/stopped state |
| Exact identity | Real signatures; actor, original post, recipient, repost ID and follow binding |
| Replay and undo | Stable nonce across response loss/reload; permanent undo fences; fresh IDs for new intent |
| Attribution and RSS | Original author/source links, unique repost GUIDs, no copied body/media, bounded chronological output |
| Current visibility | Private/deleted/disabled originals, source receipt checks, stale-check and cancelled-ACK fencing |
| Revocation | Local and source follow/block/reconnect, Cloud discovery/commit revocation, cleanup for known follows |
| Recovery and bounds | Independent databases, real TLS, restart/retry, bounded histories/quotas and source refresh |
| Chat and Brain | Native inline counts/status; governed Cloud exact confirmation/replay; verified SQL notifications/read receipts |
| Browser | Production UI/API at 1440/390/320, literal untrusted names, reload/retry, undo and no overflow |
| Release | Existing Feed/Hosting/Chat regression suites, full Windows installer certification, green merged PRs and exact-source verified ZIP/checksums |

All ten gates are required before release delivery. Independent databases and signatures are exercised by `tests/vrss_v2f.py`; `tests/vrss_v2f_tls.py` runs two actual HTTPS apps using production transport with a test CA and deterministic DNS/socket mapping. Browser fixtures use production UI/API and controlled external responses.
