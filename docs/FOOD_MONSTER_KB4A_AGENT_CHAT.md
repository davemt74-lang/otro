# KB4A — Food Monster live knowledge in the main VP3 Agent Chat

This is the first **owner-chat retrieval** stage built on merged Gelato KB2
(PR #378) and OTRO KB3 (PR #345). The current example Food Monster instance
is `https://gelato.vp3.me`. The connector accepts any independently
authorized public HTTPS installation; no code is tied to that hostname.

## Use

First pair a Food Monster instance using the protected Gelato owner approval
page `/vp3-connections.php` and OTRO's owner-local Food Monster connection
endpoint (KB3). The HomeServer owner may then ask in the **existing**
main VP3 chat:

- `What's our current dough recipe at Gelato?`
- `What are the allergens in our pepperoni pizza at Gelato?`
- `Compare dough recipes across all my restaurants` (two separately
  authorized connections)

The Agent Chat runtime uses a narrow, bounded, **live** read-only query
rather than synchronizing whole databases. It accepts a matching named
restaurant or defaults to the only active connection; ambiguous multi-site
requests prompt for a specific restaurant. Comparison is deliberately
limited to two sites per turn.

Each evidence excerpt and the assistant answer cite the source restaurant,
original record ID and current content revision where available. The
returned response also exposes `context.food_monster_sources`, containing
source IDs, origins and revisions but **no access credentials or raw
record bodies**.

## Authorization and privacy

- Only `source_app_key == "owner"` with `owner_tools` may use connected
  Food Monster sources. Paired applications do not inherit the owner's
  Food Monster credentials, datasets or consultation ability.
- The existing owner conversation settings must allow Knowledge. Selected
  Project Knowledge packets and Agent Eyes local-only sessions are explicitly
  excluded from this external context injection.
- Food Monster rechecks the owner role, org membership and the separately
  approved connection scopes for every read. Failed or revoked sources
  produce a specific unavailability notice instead of silently serving
  cached claims.
- A query is made only for an explicit food/recipe/menu-related request,
  never for unrelated chat messages.
- Retrieved content is treated as untrusted factual context, not as
  instructions; no automated write actions or memory import is added.
- This phase does **not** automatically copy data to the persistent
  HomeServer Knowledge index. A disconnect therefore leaves no synced
  KB4A food documents in the index. Previous chat replies containing
  source facts remain in conversation history until the user deletes
  those messages/conversations; source revocation does not retroactively
  erase assistant replies.
- If the HomeServer owner enables a cloud inference provider for their
  conversation, the retrieved excerpts may be sent to their selected
  inference provider as part of the prompt. Owners wanting local-only
  processing should select the existing Private/Ollama local mode.
- The source-side manual revocation requirement from KB3 still applies
  until a separately certified remote revocation workflow is available.

## Limitations

This stage is deliberately bounded to active menu and recipe records.
Employee data, payroll, CRM, sales, arbitrary connectors, filesystem and
write-capable agent tools remain outside the Food Monster connection grant.
A full incremental sync/tombstone protocol, better ranking and staff
knowledge beyond menus/recipes will follow in separate, gated phases.

`KB4A` acceptance: owner-only retrieval, multi-restaurant routing,
explicit authorization, provenance, bounded excerpts, no cross-app leakage,
no implicit sources for unrelated chat, failure-closed remote reads.
