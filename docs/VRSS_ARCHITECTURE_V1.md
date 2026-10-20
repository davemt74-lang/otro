# VRSS — architecture and first delivery

VRSS is RSS with the social functions of a social network. Every HomeServer owns its identity, posts, subscription list and imported cache. A shared Cloud database is optional. This document defines the first delivery and the remaining sections; it does not claim that remote comments or likes already work.

## Build sections and gates

| Section | Result | Gate before proceeding |
| --- | --- | --- |
| V1 — foundation and reader | Local owner profile/posts, RSS/Atom subscriptions, media links, opt-in public profile/RSS | Service/API/browser acceptance; Windows package/restart/installer certification; merged source and verified ZIP |
| V2 — delivery and identity | Automatic HTTPS/subdomain routing, profile URL discovery, background polling with conditional requests, identity keys and recovery | Real two-server reachability, router/offline recovery, private-route isolation and key recovery |
| V3 — social exchange | Remote follows, replies, comments, likes, reposts, notifications, blocking and authenticated audiences | Authenticated and replay-safe exchange between two independent servers, revocation and abuse handling |
| V4 — media and portability | Upload/capture, local media storage/public delivery, export/import and address migration | Media quotas, exact restore, durable identity and migration tests |

Each section is implemented, tested and merged before the next begins. Scores describe the documented section's acceptance coverage, never completion of the whole social network.

## Ownership and compatibility

One local owner identity is supported in V1. Its random UUID/URN is stored in the Feed app's isolated `app-data/<app-id>/sqlite/vrss.db`, alongside local posts, subscriptions and imported entries. It survives application restarts and bundled app updates. Existing app-data backup/recovery includes this database. Never copy an active identity database to two independently publishing servers.

The existing F1 Cloud account, posts, profiles and follow relationships remain unchanged and separately accessible through Profile → Open Cloud Feed. Cloud posts are not silently migrated or mirrored into a public local feed. A HomeServer member account is not mapped to the owner's VRSS identity. Multi-member posting requires a later explicit identity/permission design.

VRSS publication uses RSS 2.0, stable item GUIDs and Media RSS content links. Ordinary RSS readers can read public posts. Incoming RSS 2.0 and Atom feeds are supported, with UTF-8 XML, plain text/HTML summaries and enclosure/media URLs. Atom XHTML bodies, RSS 1.0/RDF, OPML, feed discovery and non-UTF-8 encodings are not part of V1. No HTML or remote script is rendered from imported summaries. Media is fetched only after the reader clicks its link/load control; there is no automatic media download or autoplay.

RSS is the reading/publishing layer. It does not authenticate a remote comment or provide private follower delivery. V3 must use an authenticated actor/activity protocol, preferably compatible with ActivityPub, instead of treating a supplied feed author string as proof of identity. Signature verification, origin binding, durable activity IDs, per-source receipts, key rotation/recovery, audience checks, inbox limits and moderation are mandatory before enabling remote social writes. V1 advertises `social_writes: false` and exposes no inbox, follow-count claims or unsupported interaction buttons.

## Addresses and public access

The owner chooses a canonical HTTPS origin or path prefix in Edit Profile. It is locked after first enabling publication, because existing item links and remote subscriptions must not silently change identity. The internal UUID stays independent of display-name changes. Address migration is reserved for V4.

Only these read-only paths are publishable through a narrowly configured TLS reverse proxy:

| Path appended to the canonical base | Response |
| --- | --- |
| `/vrss/v1/profile` | Public profile HTML for browsers, JSON for API clients |
| `/vrss/v1/rss` | RSS 2.0 document |
| `/vrss/v1/posts/<uuid>` | Public post HTML/JSON |

For example, `https://dave.example` produces `https://dave.example/vrss/v1/rss`. A prefix such as `https://example.com/dave` is supported only when the external proxy strips `/dave` before forwarding the three path patterns. The external HTTPS certificate and DNS must already work.

V1 does not create DNS entries, register subdomains, configure TLS, open router ports or wire the existing VP3 Hosting ingress automatically. Private/local reader and posting work without any of those. Public read endpoints start disabled and return 404 until the owner explicitly enables publishing. Publishing all posts marked Public is an explicit checkbox in the profile editor. **Never forward the whole HomeServer listener or its owner/control/session routes to the public Internet.** V2 will integrate managed routing rather than ask ordinary users to configure a proxy.

Local post audiences are Public and Only me. Public posts stay local while publishing is off. Only-me posts never appear in any public document. Followers-only publishing is absent until remote authentication exists. Disable/delete removes future public reads and sends no-store headers; it cannot erase copies already downloaded or cached elsewhere. Source disappearances are not interpreted as authenticated delete activities by the V1 reader.

## Reader and storage rules

Following accepts a direct public HTTP/HTTPS RSS/Atom URL. The subscription list is private. Following initially fetches the source; Refresh checks it again, with a one-minute per-source throttle. V1 polling is manual. Failed sources remain listed with an error and can be refreshed or unfollowed. Unfollowing removes the source and its imported cache atomically. A refresh finishing after an unfollow cannot recreate it. Import keys are scoped to a subscription and deduplicated across refreshes; source article edits update the cached record.

The timeline combines local posts and subscribed entries by local arrival time, with source publication time shown on each imported card. It is paged in groups of 20. Local posts and subscriptions never need Cloud pairing. Limits are 100 subscriptions, 100 cached items per subscription, 10,000 local post receipts, 60 new posts per hour, four media URLs per post, 10,000 text characters, a 64 KB control request and a 1 MB XML fetch/output. The existing Feed app SQLite quota also applies. Post receipts include deleted posts, so a retry cannot resurrect them. Receipt pruning/archive is a later maintenance feature.

Network fetches pin a validated global IP before connecting, retain the original TLS hostname/certificate check, bypass ambient proxies, send no owner/Cloud cookies or tokens, and repeat validation for each redirect. Private/local/multicast destinations, credentials, nonstandard ports, insecure HTTPS downgrades, excessive redirects, oversized and compressed XML responses, DTDs and entity declarations are rejected. Source media remains a user-initiated external link rather than a server-side fetch.

## UI and upgrades

The default app remains `vp3.feed`, now version 1.1.0. The canvas shows a feed, Profile and Create Post. Following opens a dialog; address and publishing settings live in Edit Profile. There are no permanent diagnostics panels. Drafts live in sessionStorage under the local identity, pending writes freeze the form, retries reuse an exact request ID/content fingerprint, and late view results cannot replace the current screen.

On startup a known bundled F1 1.0.0 System App advances to V1 without changing user-app collisions or deliberately stopped state. Local VRSS data is separate from release files. The old Cloud view remains available and retains its F1 server-side pairing and security rules.

## Acceptance and standards

`tests/vrss_v1.py` exercises actual isolated SQLite, default upgrade and owner/public API routes, with controlled remote responses only. `tests/vrss_v1_browser.mjs` drives the packaged app against the production API at desktop, 390px and 320px. Remote feeds are deterministic fixtures; they do not replace the production parser, owner gateway, write receipts or storage. Windows CI also runs service acceptance before the existing executable/browser/restart/recovery/installer certification. Candidate and main checks plus artifact source-tree/provenance verification gate deployment delivery.

References: [RSS 2.0](https://www.rssboard.org/rss-specification), [Atom (RFC 4287)](https://www.rfc-editor.org/rfc/rfc4287), [Media RSS](https://www.rssboard.org/media-rss), [ActivityPub](https://www.w3.org/TR/activitypub/).

## V2A — existing Hosting/subdomain integration

Feed 1.2.0 reuses the existing VP3 Cloud Hosting and System App binding. Cloud remains authoritative for the assigned subdomain, DNS provisioning, hostname verification, edge TLS, package limits and route credentials. HomeServer keeps the same local VRSS identity, posts and subscriptions. No additional domain registry or public listener is introduced.

On Cloud, the included **VRSS Feed** System App can be acquired/reconciled and assigned to an existing Hosting site using the current Apps/Hosting controls or the governed Agent Chat flow (for example, “host VRSS at my existing site name”). Existing Hosting activation, DNS and TLS setup still apply. The catalog adds Feed to existing installations when opened; `upgrade.php` also refreshes the catalog. Install/reconcile Feed on the paired HomeServer before assigning Hosting.

On HomeServer, open Feed → Profile → Edit Profile. **Publishing address** lists the existing sites bound to Feed. Selecting a site fills its HTTPS address; publication can be enabled only once that assigned route is ready. Selection alone does not publish. Saving the explicit publishing choice locks the canonical address and site binding. A V1 manually configured address can adopt the existing Hosting binding only at the identical canonical address. Manual HTTPS publication remains supported for existing installations.

For a bound Feed site, the existing authenticated Hosting ingress serves `/` as the public profile, plus `/vrss/v1/profile`, `/vrss/v1/rss` and `/vrss/v1/posts/<uuid>`. Only GET and HEAD are accepted. JSON profile/post reads remain available through content negotiation. The owner canvas, application assets, subscription list and control APIs are not served at that hostname, even when the incoming browser has an owner cookie. Ordinary hosted apps keep their existing runtime behavior.

The existing route token, hostname, TLS, package and runtime checks run before VRSS routing. VRSS additionally checks the persisted selected site and exact canonical address on every public read. Disabled publication, stopped Feed, unbinding, reassignment, inactive routes and expired TLS block publication; local data remains intact. Public documents and routing errors use `Cache-Control: no-store`. Previously downloaded copies cannot be recalled.

Acceptance uses real Cloud MySQL catalog/binding operations, real HomeServer SQLite and Hosting ingress, and production browser UI at 1440, 390 and 320 pixels. Controlled Cloud desired-state/TLS metadata represents the existing edge contract; live DNS-provider credentials and a public two-machine deployment are not exercised by these deterministic checks. Windows certification covers the executable, packaged browser, restart/recovery and installer upgrade. This section connects the existing subdomain system; automatic feed discovery, background refresh, identity keys/recovery and remote social exchanges remain later work.

## V2B — discovery, scheduled reading and Agent integration

Feed 1.3.0 accepts direct RSS/Atom feeds, public VRSS profiles and website URLs declaring an RSS/Atom alternate link. Discovery shares a 25-second request budget, follows at most three declared candidates and uses the existing validated, pinned public transport for every hop. It never guesses feed paths or downloads media. Public profile HTML now declares its feed with `rel="alternate"`.

A HomeServer lifecycle worker checks up to four due subscriptions each pass, about every 15 seconds. Each successful source is next due in 15 minutes. Per-source revision fences and a persisted three-minute lease prevent overlapping refreshes and fence late responses after an unfollow. ETag and Last-Modified validators are sent only to the exact document URL that supplied them. Valid 304 responses preserve cached posts. Retries back off from 15 minutes to six hours; due times, validators, failure counts, last success and leases survive restarts. Manual refresh retains the one-minute throttle. Agent “refresh my feed” queues existing sources and respects that throttle. Offline recovery catches up incrementally rather than starting parallel fetches.

The cache retains 100 entries per source. Persistent item receipts, capped at 10,000 per source and covered by the existing app SQLite quota, prevent old entries reappearing as new notices after cache eviction. Reaching the receipt cap stops that source with an actionable error; unfollow/refollow explicitly resets its history. A first import or first upgraded-source check establishes a quiet baseline. Later new IDs create a batch notification in a transactional outbox; edits, 304 responses and repeated documents do not announce new posts. Pending outbox entries are capped at 200 and delivered history at approximately 200; a delivery backlog retries without discarding the imported-item transaction. Unfollow deletes imported cache/receipts/outbox and dismisses its existing notices.

HomeServer uses the existing Activity Center notification preference and dedupe controls. Unread new-post summaries appear in the Agent Chat canvas with Open VRSS and Mark read; subscription health and last successful update appear in the existing Agent Brain drawer. Existing ambient-voice policy continues to decide whether informational notifications are spoken. No new voice consent or automatic playback is introduced.

The bundled Feed Agent action manifest exposes read-only status/timeline actions and governed follow, refresh, queue-refresh and unfollow actions. Cloud Agent Chat can “follow https://example.com/”, “refresh my feed”, “show new VRSS posts” and “show VRSS subscription health”. Cloud follow and refresh prepare the existing conversation-bound confirmation flow; HomeServer remains the execution authority. Feed content/control exports also require the paired app's current Cloud-export scope. Existing permission revocation is checked on every request.

When existing workspace synchronization is enabled and the paired app permits notification export, committed HomeServer new-post notices project into the account's existing Cloud Notifications/Agent Chat informational pipeline. Projection uses stable per-event keys, current authenticated session/account locks and read receipts; retries cannot create duplicate native notification rows. Operational status remains in Agent Brain; new posts do not force an approval prompt. Stopped Feed never polls or serves its owner control. Local reading remains available without Cloud pairing.

RSS requests reveal no authenticated follower identity. V2B therefore exposes no new-follower event or follower-count claim. Verified identities, signed/replay-safe remote follows and genuine new-follower notifications remain the next social-exchange work, after the identity/recovery gate.

Acceptance: `tests/vrss_v2b.py` exercises real SQLite, parser, conditional HTTP construction, lease contention, restart/backoff, durable notices, cache-eviction receipts, unsubscribe fences and paired Cloud scope/confirmation. `tests/vrss_v1_browser.mjs` covers real owner API/SQLite/UI at 1440, 390 and 320 pixels, including Chat notices and Agent Brain health. Cloud MySQL tests exercise reviewed chat preparation, confirmation/replay and read status/posts; workspace tests exercise actual committed notification upload, dedupe, read receipts, ignored unsupported follower events and revoked sessions on SQLite/MySQL. Release still requires the full Windows executable/installer certification and exact-tree provenance checks.

## V2C — authenticated following

Feed 1.4.0 implements the bounded HomeServer-to-HomeServer follow section described in [VRSS_V2C.md](VRSS_V2C.md). The historical V1/V2A limitations above describe those releases. Public opt-in, Ed25519 identity continuity, Follow/Accept/Undo/Reject, durable delivery, blocks and verified follower notices now have explicit acceptance coverage. General ActivityPub interoperability, remote reactions/comments and address migration remain outside this section.
