# Shared participant conversation memory — v1345

Participant names and explicitly saved conversation notes now use the existing Cloud/HomeServer pairing and workspace worker. This extends v1344 participant continuity without changing microphone owners, AI credentials, compute routing, conversation databases, or tool authority.

## Identity and linking

The Cloud profile key is the stable shared reference; each HomeServer browser retains its native Tracky participant ID. Names are labels, never merge keys. Cloud-only names import as text-only local participants. After the first complete Cloud scan, unique local participants automatically receive separate Cloud records. A local name that already exists in Cloud waits for an explicit link or a separate-record choice. Duplicate Cloud names are distinguished by their Cloud record numbers.

Linking preserves the local participant and its local enrollment. If the saved name or note differs, the participant panel shows both versions before the owner chooses Use Cloud details or Keep local details. Linking does not merge accounts, transcripts, contacts, or biometric enrollments. Cloud profiles already linked to another local participant cannot be silently reassigned.

## Offline changes and conflicts

The existing HomeServer SQLite database contains an account-bound participant text cache and durable edit queue (migration 082). The existing workspace worker transfers one bounded page per cycle; participant failures do not fail the rest of workspace synchronization. A browser transfers Tracky's changes into that queue while open; an already queued change continues retrying after the browser closes. A change made while the browser cannot reach HomeServer remains in IndexedDB until the browser reconnects.

Changes carry the last observed content revision. Cloud serializes them with its existing live account/session lock and a participant row lock. A stale change returns the current Cloud text and leaves the local version intact. The UI offers explicit resolution. Late receipts cannot remove a newer queued edit; a stale Use Cloud action cannot discard an unreviewed edit. Response validation precedes an atomic local cache/receipt commit.

Notes can be cleared independently. Participant removal follows the existing local deletion cleanup, including local samples and dialogue references. Cloud retains a text-free inactive profile tombstone, including when deletion arrives before an earlier creation. An old update cannot resurrect it. Cloud's account-self participant is protected from removal. Keeping a local participant after a Cloud deletion creates a new separate reference rather than reviving the removed record. Concurrent local edits are checked again before a remote deletion removes local data.

## Scope and context

Only profile references, names, saved conversation notes, deletion flags and content revisions cross this new exchange. Ordinary Tracky notes, photos, embeddings, enrollment audio, recognition/cloning consents, provider bindings, credentials and local authentication do not. Imported text does not enroll anyone for recognition. Existing memory and contact read permissions, memory namespace scope, current pairing and the workspace pause switch remain authoritative. The queue and cache are qualified by Cloud origin and account ID. A replacement/unverified pairing cannot use the previous account's participant context.

The existing v1344 typed selection and current voice-match context attach the selected participant's current saved note to Chat. Sync changes invalidate pending participant context. Returning-user greetings continue through the existing Eyes/voice owner; private notes are not spoken merely because a face appears. Recognition remains provisional and grants no authentication or tool permission.

## Deployment and checks

Deploy matching Cloud first, then HomeServer. No new runtime, model download or Cloud database migration is introduced. Existing participant and Brain tables must already be available. HomeServer applies SQLite migration 082 and upgrades the existing participant IndexedDB from version 2 to 3, retaining all old stores and samples. Close older HomeServer browser tabs if they block that upgrade.

Coverage includes real SQLite and IndexedDB transactions, prior-version data preservation, PHP session-scoped writes, bounded Python transport, cross-runtime HTTP, offline recovery, conflicting edits, receipt races, deletions before creations, account replacement, permission/scope revocation, local enrollment preservation and the existing Chat/listening/participant continuity regressions. Automated camera and acoustic inputs are fixtures; physical webcam/microphone accuracy still requires installed hardware testing.
