# VRSS V2G — mentions and conversation subscriptions

Feed 1.8 extends the existing HomeServer identity, Hosting subdomain, public RSS and signed inbox. There is no shared social database. Existing follows, reactions, moderated comments and attributed reposts retain their boundaries.

## Owner experience

Incoming mentions are explicitly off by default. Enable them in Feed → Edit Profile. To mention an account in a public post or comment, write its canonical actor address as a whitespace-separated token: `@https://their-host/vrss/v1/actor`. There is no guessed handle directory. At most five distinct accounts may be mentioned per source. Private posts send nothing. Incoming comments send mentions only when their post owner publishes them; hidden, retracted, blocked or unavailable sources cannot be sent.

Use Conversations in Feed to open a public post URL and follow its published discussion without following its author's profile. This grants no ability to comment: existing comment consent and accepted-follow requirements still apply. Follow, unfollow, mute and unmute are local owner choices. Unmute and refollow establish a fresh baseline to avoid a muted historical backlog. Profile also controls thread notifications globally. Re-enabling them initializes a new baseline. Muting withdraws existing conversation notices; old dismissed notices never return automatically.

Agent Chat receives informational mention/reply notices, opens the current public thread inline, and offers follow/mute controls. Brain shows conversation counts, mute/initialization state and unavailable source checks. The governed action catalog and Cloud Agent support `show VRSS conversations`, `show VRSS conversation <post-URL> [after <cursor>]`, and `follow`, `unfollow`, `mute` or `unmute VRSS conversation <post-URL>`. Writes retain the exact URL, enabled/muted choices, revision and request nonce through existing confirmation and replay checks. Remote text cannot authorize actions.

## Identity and visibility

A `vp3.vrss.conversations.v1` Mention is an Ed25519-signed pointer from the original thread publisher. It binds its immutable activity and mention IDs, sequence, sender identity, recipient, original public post and optional comment ID. It contains no copied body or media and does not invent a follow relationship. For a moderated comment, the publisher's signature attests to its current public projection; it is not a new cryptographic claim about the comment author's signature.

The recipient verifies its opt-in, current Hosting/local authority, pinned source identity and signing-key continuity, block state, original-post origin and exact current public source. The published source must contain the recipient's canonical mention token. Comment proof uses the existing exact SHA-256 selector; hidden ancestors and deleted/private sources fail closed. Delivery requires an exact ID/object/recipient acknowledgement. Replay is bounded and idempotent, and mentions issued before a block cannot resume after unblock. Existing inbox size/rate/signature-concurrency limits apply.

Thread subscriptions use existing HTTPS public moderated-comment pages and pinned publisher identities, independent of profile follows. Initial scans suppress historical alerts. Subsequent scans retain seen IDs, revisit pages to catch late approvals, and coalesce a reply and mention for the same public comment into one notice. Revision, background-check generation and cursor checks reject stale responses after mute/unfollow, concurrent scans and permission changes. Background checks do not change the owner's settings revision.

Notice delivery rechecks the current public source. Chat fetches current published text when the owner opens a thread; notification bodies contain no copied source text. Previously delivered link-only notifications or downloaded RSS may remain cached; source deletion does not claim instantaneous remote erasure. Opening an unavailable thread does not disclose private text.

## Bounds, recovery and upgrade

Conversation records are capped at 50; seen-comment history, notice history, mention intents and existing owner request receipts each retain their 10,000-row bounds and app SQLite quotas. Histories fail visibly rather than silently discarding replay fences. The worker prepares at most two mention destinations and checks at most two conversation pages per pass, with at least a minute between checks of a conversation. Each public page has at most five comments. Long threads or many subscriptions require multiple passes. Notification source checks are also bounded and retry separately so one unavailable source does not block the queue. Ordinary Chat notice reads perform no remote source requests.

Subscriptions, seen IDs, request receipts and signed outbox state survive restart. Existing identities, keys, posts, subscriptions, likes, comments, reposts, Hosting bindings and deliberately stopped Feed state survive the 1.7→1.8 upgrade. Mention consent starts off. Deploy the matching green merged Cloud package first, run its upgrade, then run the Windows installer while preserving private data.

## Release acceptance gates

| Gate | Required evidence |
| --- | --- |
| Consent and upgrade | Default-off mentions; retained identity, data and stopped state on Feed upgrade |
| Signed source | Real Ed25519 sender/publisher/recipient/object binding; exact current public source |
| Delivery | Stable signed IDs, exact ACK, response-loss nonce replay and restart recovery |
| Moderation | No pre-approval send; late approval; hidden/deleted/private source and ancestor checks |
| Subscriptions | Independent of profile follows; historical baseline; public pagination |
| Notification choices | Mute/unmute/global settings, no muted backlog, channel coalescing and read/dismiss sync |
| Current authority | Block/unblock replay fences; stale fetch and concurrent settings checks |
| Bounds | Five targets, 50 conversation records, bounded histories, quotas and network passes |
| Canvas integration | Production Feed/Chat/Brain at 1440/390/320, literal untrusted text, no overflow |
| Release | Retained suites, real HTTPS, Cloud SQL/governed actions, full Windows installer certification, green merged PRs and verified matching ZIPs/checksums |

All gates are required before release delivery. Tests use independent production databases and real signatures. HTTPS tests use two actual applications and production transport with a test CA and deterministic socket/DNS mapping. These are software acceptance tests, not physical-device or geographically separate live-installation certification. V2G does not claim general ActivityPub compatibility.
