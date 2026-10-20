# KB4C — Food Monster connection management inside VP3 Agent Chat

The existing VP3 owner chat composer has a **+** button. KB4C adds a
minimal **Food Monster connections** section to the existing **Tools**
tab inside that modal, rather than introducing another dashboard,
sidebar or separate assistant.

## Pairing steps

1. In the owner's primary VP3 Agent Chat, open **+ → Tools → Food Monster
   connections → Connect a Food Monster**.
2. On the restaurant's own Food Monster website, sign in as its owner.
   For the current installation this is
   `https://gelato.vp3.me/vp3-connections.php`; an installation on any other
   public HTTPS subdomain uses the same `/vp3-connections.php` path.
3. Approve the desired read-only scopes and generate the 10-minute one-time
   code on Food Monster.
4. Enter the restaurant's **HTTPS origin**, a friendly connection label,
   and that one-time code in the chat Tools panel. HomeServer checks the
   origin, securely verifies the source instance identity, and redeems the
   code using its existing KB3 backend.
5. Connected restaurants appear separately with approved scopes and
   individual Disconnect controls.

The code is only sent in a JSON POST from the owner chat window to the
local HomeServer control API. The browser **never** sends it directly
to a user-supplied remote website, does not insert it into an Agent
Chat message and clears the code input whether pairing succeeds or fails.

## Disconnect safety

The Disconnect button requires explicit owner confirmation. It invokes
the certified KB4B HomeServer revoke API and displays whether remote
source-side token revocation was verified. In a network outage or an
unexpected remote response, the panel warns that the owner must revoke
the corresponding connection from Food Monster's own administration
page. HomeServer always clears its local credential.

## Permissions and limitations

- The UI only calls local `/api/v1/control/food-monster` endpoints.
- Each Food Monster instance requires separate Food Monster-owner approval.
- The panel is accessible through the **owner-local** VP3 Agent Chat
  canvas, not the paired-app API; paired applications receive no
  Food Monster credentials.
- HTML values from remote sources are inserted only as text, not markup.
- No external Slack service, web account directory, shared database,
  MCP server, or general-purpose remote agent execution is introduced.
- This stage provides the owner UI for menu/recipe knowledge already
  supported by KB1–KB4B. Rich knowledge synchronization, change
  tombstones and expanded source types remain separate future phases.
- A real deployed pairing between `gelato.vp3.me` and a running
  Windows HomeServer still requires field acceptance beyond CI.
