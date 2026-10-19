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
