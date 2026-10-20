# VRSS V2C — authenticated following

Feed 1.4.0 adds authenticated Follow/Accept/Undo/Reject exchange between independent HomeServers. RSS remains the post transport. This is the bounded `vp3.vrss.follow.v1` protocol, not general ActivityPub interoperability. Cross-server likes, replies, reposts, private audiences and identity-address migration remain later sections.

## Owner experience

Enable public publishing at the existing Hosting/subdomain address, then opt into authenticated follows in Edit Profile. Existing installations remain opted out of authenticated following. Use Profile → Connections, Following → VRSS connections, or the governed Agent Chat actions. A follow remains pending until a signed acceptance arrives. Acceptance creates the RSS subscription; its initial import is quiet. Ordinary RSS subscriptions remain independent and do not fabricate follower identities.

Cloud Agent Chat supports “follow VRSS profile https://their-address/”, “show VRSS followers”, “show VRSS delivery status”, and “unfollow/block/unblock VRSS https://their-address/vrss/v1/actor”. “Remove follower VRSS <actor URL>” disconnects an inbound follower. Writes use existing conversation-bound confirmation codes and app-control permissions. Plain “follow https://website/” retains the RSS-reader behavior. The connection dialog exposes corresponding controls. Unblocking does not re-establish a removed relationship. Removing the accepted profile's RSS subscription also queues the authenticated unfollow.

Verified new-follower notices appear in the existing HomeServer Agent Chat canvas and, when existing workspace notification export is permitted, in Cloud Notifications and Agent Chat. They use durable event digests and read/dismiss receipts. Remote names are displayed as text; imported content is never treated as an instruction. Agent Brain shows follower/following counts, delivery attempts, pending update types and paused-route/key errors. Existing notification and voice preferences remain authoritative.

## Identity and publication

The existing immutable local UUID and canonical HTTPS base are preserved. Public actor and inbox addresses are `<base>/vrss/v1/actor` and `<base>/vrss/v1/inbox`. HTTPS address control plus possession of the advertised Ed25519 signing key authenticates an actor; this is not verification of a person's legal identity. The actor document's UUID, endpoints and HTTPS origin/path must agree. Social actor URLs are limited to 400 characters so existing bounded notification metadata can carry them intact.

Keys, peer pins, relationships, receipts, outbox and notice records stay in the isolated Feed SQLite database, covered by existing app-data backup/recovery. Private keys never appear in public documents, owner profile/status responses, Cloud snapshots or release packages. Restore the complete Feed database to recover the identity. Never clone a live identity to two publishers. A missing previously created key fails closed and requires backup recovery; it is never silently replaced.

Edit Profile offers signing-key rotation, retaining the UUID/address. Every transition is signed by the previous key. Peers accept a new key only through a chain from their existing pin. Up to 16 transitions are retained; arbitrary replacement and identity substitution fail closed. Backup rollback may leave sequence counters behind remote receipts, causing safe rejection; automatic address migration or lost-key replacement is not offered.

Existing Hosting still owns subdomains, DNS, TLS, route tokens, entitlements and bindings. The only new public write path is signed JSON POST `/vrss/v1/inbox`; it does not expose owner control routes or app assets. The hosted route validates its selected site/canonical base and existing ingress authorization. Direct manual HTTPS proxies must additionally forward the actor GET and exact inbox POST path. Requests and responses remain no-store. Disabling publication/following, stopping Feed, unbinding Hosting or losing route/TLS authority pauses exchange while retaining local data.

## Exchange and recovery rules

Every signed envelope includes the exact activity, intended recipient, actor/UUID, stable activity and follow IDs, ordered per-peer sequence and issued time, plus a transport timestamp valid for five minutes. Retries sign a new transport timestamp around the same activity. HTTPS fetches and POSTs validate global DNS addresses, pin the selected IP, verify the original TLS hostname, bypass ambient proxies/credentials and reject redirects, private addresses, compressed/non-JSON and oversized responses.

Only Follow, Accept, Undo and Reject are accepted. Signature/identity, audience, Follow ownership, key continuity and current publication authority are checked before commit. Exact receipts cannot execute twice; reused IDs with changed content and stale sequence numbers fail. Incoming Follow auto-acceptance is covered by the owner's explicit authenticated-following opt-in. Active repeated follows do not produce repeated new-follower announcements.

The durable outbox leases jobs, preserves per-peer order, retries with bounded exponential backoff and recovers expired crash leases. Local unfollow/block cancels pending sends and queues Undo/Reject. A cancelled in-flight delivery cannot mark itself successful locally; subsequent higher-sequence disconnect messages fence delayed older activity remotely. A network request already sent cannot be recalled, so remote disconnection becomes effective when its signed update arrives. Blocked actors are rejected locally immediately. Unblocking does not admit old Follow activities issued before the local removal cutoff.

Limits: 32 KiB envelopes/responses, four concurrent inbox verifications, 120 incoming envelopes/minute overall and 30/actor/minute, 1,000 pinned peers, 10,000 inbox receipts and 10,000 outbox history rows, plus existing Feed SQLite and subscription quotas. History is retained to preserve replay fences; a full history fails visibly rather than deleting receipts. Queue maintenance/archiving and general federation interoperability remain later work.

## Acceptance gates

| Area | Required evidence |
| --- | --- |
| Identity and origin | Independent UUID/keys, exact actor endpoints, real Ed25519 verification, pinned rotation, missing-key recovery failure |
| Following and revocation | Pending/Accept, exact retries, Undo, block/unblock cutoff, late Cloud/discovery revocation, stopped Feed and Hosting isolation |
| Delivery and restart | Durable leases/order/backoff, restart persistence and duplicate-free notices |
| Chat and Brain | Real owner API and governed app actions; Cloud MySQL confirmations/replay; committed SQLite/MySQL notice snapshots and read receipts |
| Browser | Production API/UI at 1440/390/320, explicit opt-in, follower notice/Brain state, block/unblock and no horizontal overflow |
| Release | Existing VRSS/Hosting/app-control/notification regressions; Windows executable/browser/restart/installer certification; green merged PRs and verified matching ZIPs |

Tests: `tests/vrss_v2c.py` runs two subprocesses with independent production SQLite databases and real signatures through a controlled network-document adapter, plus production owner/public API and Hosting ingress. `tests/vrss_v2c_tls.py` additionally runs two independent HTTPS application servers with a test CA and deterministic DNS/socket routing, exercising the unchanged production HTTPS transport, TLS host verification, signatures, Follow/Accept, RSS subscription, follower notices and delivered block/Reject. Separate transport tests cover redirect and private-network rejection. `tests/vrss_v2c_browser.mjs` drives the production UI and real signing/verification with controlled external peer fixtures. These are deterministic certification tests; they do not claim live public DNS or two geographically separate deployments.

Protocol references: https://www.w3.org/TR/activitypub/ (Follow/Accept/Undo semantics); https://cryptography.io/en/50.0.2/hazmat/primitives/asymmetric/ed25519/ (signing API).
