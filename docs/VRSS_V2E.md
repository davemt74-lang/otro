# VRSS V2E — signed comments, replies and owner moderation

Feed 1.6.0 extends the existing HomeServer-owned RSS and signed-inbox system. Each post author's HomeServer holds its authoritative thread. The comment sender retains durable delivery requests. Cloud remains the optional governed Chat/notification bridge and existing Hosting/subdomain authority; there is no central social database.

## Owner experience

Existing profiles remain opted out. In Feed → Edit Profile, explicitly enable signed comments/replies alongside public publishing and authenticated follows. Incoming remote comments start **pending** and are private to the post owner. The owner can approve or hide the exact current revision. Owner-authored comments/replies are approved locally. A sender can permanently retract their own comment; post owners hide other people's comments. Hidden parents suppress the entire public reply chain. Approval of a reply requires its parent to be currently published.

Feed cards provide inline Comments, a composer, Reply, author Retract and owner Approve/Hide controls. Text is rendered as text, never remote HTML. Owner-scoped session drafts retain the request nonce through lost responses and reloads. A pending send is distinct from delivery to the owner and public approval. A retraction can remain queued while the destination is unavailable; public copies already downloaded cannot be recalled.

Verified incoming-comment notices appear in existing Agent Chat. HomeServer Chat opens the thread inline for approval/hiding without another dashboard. Agent Brain shows pending moderation and the existing durable social delivery queue. Polling preserves an open review. Notification projection uses the existing informational, voice, read/dismiss and account-synchronization policies; remote comment text cannot fabricate a notice or authorize a write.

Cloud Agent Chat supports:

- `comment VRSS <canonical-post-URL>: <text>`
- `reply VRSS <canonical-post-URL> to <published-comment-ID>: <text>`
- `show VRSS comments <canonical-post-URL>`; returned cursors support subsequent pages.
- `approve VRSS comment <comment-ID> on <canonical-post-URL>`
- `hide VRSS comment <comment-ID> on <canonical-post-URL>`
- `retract VRSS comment <comment-ID> on <canonical-post-URL>`

Writes retain existing conversation-bound confirmation, execution leases, replay protection and current permissions. Moderation first reads the exact owner-only comment and includes its text, author and revision in the approval preview. Confirmation cannot silently approve a newer state. The native governed Feed action catalog exposes the same operations. Ordinary RSS items do not gain invented social identities.

## Protocol and visibility

The actor advertises `vp3.vrss.comments.v1` and an explicit enabled flag. The existing signed JSON inbox remains the sole public write surface. `Comment` and `DeleteComment` envelopes retain pinned Ed25519 identity, actor/recipient, activity ID, ordered per-peer sequence, exact follow ID and issue/transport times. They bind a canonical post URL and immutable sender-owned comment ID; Comment also binds plain text and an optional parent ID. IDs, bodies and parents cannot be changed under a reused comment or request ID.

New comments require the currently accepted inbound follow, explicit recipient consent and a current public undeleted post. A reply must reference a published comment in that same post, with at most four reply levels. A sender verifies the post author's bounded public thread projection and exact parent projection before enqueueing a reply. The receiver rechecks all state inside its transaction. Deletion retains a permanent tombstone, accepts cleanup for known revoked follows, clears retained comment text and cannot be reversed by fresh signatures, sequences, owner approval or retry.

Read-only `/vrss/v1/posts/<uuid>/comments`, cursor pages and SHA-256-addressed single-comment projections expose only approved comments with current accepted follows and visible ancestors. Pending/hidden content and moderation revisions are never public. Private/deleted posts, stopped Feed, disabled publication, revoked Hosting routes and unavailable TLS retain their existing boundaries. Existing Hosting ingress explicitly allowlists these GET/HEAD projections and the signed inbox; it never routes owner controls or app assets. Owner-only review APIs recheck the exact post and comment.

Outgoing requests, ordered delivery, retry/backoff and leases reuse the existing outbox. Retraction cancels a pending send lease; a late acknowledgement cannot overwrite the newer operation. Current local/Cloud authority and follow state are rechecked after remote discovery and at commit. A blocked/removed follow cannot restore visibility upon a later reconnection. Visibility is always computed from the current follow ID.

## Bounds and upgrades

Comment text is limited to 2,000 UTF-8 bytes; canonical IDs/addresses to 400 characters. Public pages scan at most five candidates, return at most five comments and remain within the existing 32 KiB transport budget. Cursor and selected-comment projections cannot reveal pending/hidden text. Comments, outgoing comment records and owner request receipts each have a 10,000-row limit. Existing app quotas, inbox/outbox history bounds, four concurrent verifications and global/per-actor inbox rate limits remain. Full histories fail visibly instead of dropping replay fences.

The bundled upgrade recognizes Feed 1.0 through 1.5 and preserves deliberately stopped state, profile identity/address, signing keys, Hosting binding, posts and subscriptions. Comments stay off until explicitly enabled. Deploy the matching green merged Cloud and Windows packages together, Cloud first; retain existing private HomeServer data. The signed protocol is a bounded VRSS extension, not a claim of general ActivityPub interoperability. Editing comments, attachments and distributing replies to every reader's database are outside this section.

## Ten release acceptance gates

| Gate | Required evidence |
| --- | --- |
| Consent and upgrade | Default-off migration; real bundled 1.5 → 1.6 upgrade preserves identity/data/stopped state |
| Identity/object binding | Real signatures, pinned origin/UUID, exact recipient/post/comment/follow/parent |
| Idempotency | Stable owner nonces, immutable bodies, exact inbox receipts and permanent deletion fences |
| Private moderation | Pending text absent publicly; owner-only exact revision review, approval and hiding |
| Reply visibility | Same-post parent, depth limit, current follow and hidden/deleted ancestor suppression |
| Recovery and ordering | Restart, retry, lost-response replay, cancelled delivery acknowledgement and ordered retraction |
| Current authority | Follow/Cloud revocation during discovery, block/reconnect, private posts and Hosting/TLS isolation |
| Chat and Brain | Inline native review, governed Cloud confirmation/replay, real SQL notice/read synchronization |
| Browser | Production API/UI at 1440/390/320 pixels, literal untrusted text, drafts/retry and no overflow |
| Release | Existing Feed/Hosting/Chat regressions, complete Windows executable/installer certification, green merged PRs and exact-source verified deployment ZIPs |

`tests/vrss_v2e.py` uses independent production databases and real signatures. `tests/vrss_v2e_tls.py` runs two independent HTTPS applications with the unchanged production transport, a test CA and deterministic DNS/socket mapping. Browser tests use production APIs/UI and signed controlled external fixtures. These checks do not claim live geographically separate deployments or real DNS-provider credential certification. All ten gates, including Windows packaging and merge, are required before the section is release-ready.
