# KB5C — Governed automatic Food Monster knowledge synchronization

KB5C adds **optional**, per-restaurant background synchronization to the
existing Food Monster ↔ VP3 HomeServer connection. This is part of the
HomeServer's existing Agent Brain/Chat experience, not an independent
Food Monster AI agent, SaaS directory, Slack integration or MCP service.

## Owner experience

In primary VP3 Agent Chat, open **+ → Tools → Food Monster connections**.
Each individually authorized restaurant retains its manual **Sync knowledge**
button and adds an **Automatic sync** checkbox, hourly / 6-hour / daily
cadence and **Save schedule**. Auto-sync defaults OFF for existing and
newly connected installations. No automatic external reads occur until
that restaurant's HomeServer owner explicitly enables it.

Owner-local API:

- `GET /api/v1/control/food-monster/connections/{id}/auto-sync` returns
  enabled, interval, due time, in-progress state, last attempt/success,
  consecutive failure count, and last outcome.
- `PUT /api/v1/control/food-monster/connections/{id}/auto-sync`, JSON
  `{"enabled":true,"interval_seconds":21600}` turns scheduling on or off.
  Intervals are exactly 3600, 21600 or 86400 seconds (1h, 6h, daily).

No scheduling endpoints are exposed to paired-app bearer permissions or
Remote Bridge. The restaurant admin continues to approve read scopes
using its own Food Monster administrator page on its chosen HTTPS domain.

## Durable scheduler

A dedicated SQLite feature schema `food_monster_sync_policy.sql` records
the per-connection owner preference, due timestamp, failure backoff,
last outcome and unique 1-hour renewable-by-reclaim lease. The scheduler
starts with HomeServer's normal FastAPI lifespan and shuts down with it.
It checks a maximum of four due restaurants per pass, once per minute,
and uses the existing KB5B bounded, read-only paginated manifest.

The database claim is atomic and includes a unique lease identifier,
so duplicate workers and restarts cannot claim the same still-live work.
After a process crash, an abandoned lease is reclaimable after its
expiration. An outdated run cannot clear a newer lease.

Disabling or reconfiguring an active schedule clears its lease and
invalidates that run. A scheduled sweep checks the lease between
manifest pages and again **inside the target SQLite document-write
transaction**, preventing a canceled run from committing cached data.
Existing manual sync and source revocation operations remain serialized
with their connection through the existing HomeServer connector lock.

On transient failures, the next attempt backs off exponentially starting
at five minutes and capped at the configured interval; the owner sees
the last result without dumping raw source exceptions into logs.
Authentication failure still uses KB5B's delete-local-cache/revoke behavior;
a disconnected restaurant is never scheduled. Disconnecting a
restaurant cascades deletion of its policy, private documents and FTS.

## Boundaries and limitations

- The cache remains separate from shared HomeServer Knowledge and Memory.
- All retrieved snapshots remain explicitly **unverified cached data**.
  Current recipe/menu answers in Agent Chat continue to use KB4A live reads.
- Background synchronization makes outgoing HTTPS requests to the specific
  previously administrator-approved Food Monster only; no automatic writes
  to restaurant records.
- The first version uses a single worker and the existing connector lock;
  concurrent restaurants do not generate unlimited parallel requests.
- A claim lease is 1 hour. If a very large or unresponsive scan exceeds
  that window, it must stop instead of writing after its lease has expired.
- The existing KB5A source manifest is forward-ID pagination rather
  than an atomic source snapshot; KB5B's two-complete-sweep removal safeguard
  remains necessary.
- Live deployment between `gelato.vp3.me` and a specific HomeServer is a
  separate field-certification task; CI exercises synthetic source data.

## Acceptance

Initial opt-out, independent restaurant schedules, valid cadence bounds,
duplicate-claim prevention, expired-lease restart recovery, manual disable
during an in-flight manifest, no post-cancel cached writes, failure
backoff, status persistence, source disconnect/cascade, and original
KB3–KB5B regressions. No new phase begins until all exact PR-head
workflows are green and the PR is confirmed merged into main.
