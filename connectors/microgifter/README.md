# Microgifter → HomeServer Remote Bridge

This connector is the server-side reference integration for pairing **Microgifter** as an independent HomeServer wrapper alongside VP3.

## Architecture

HomeServer is the private capability authority. VP3 and Microgifter pair as different application identities:

- `app_key: vp3`
- `app_key: microgifter`

They may request similar coarse permissions while receiving completely different owner-controlled application scopes. A Microgifter credential cannot use VP3's Memory prefixes, Knowledge kinds, Tools, Plugins, or cloud policy unless the HomeServer owner explicitly grants those resources to Microgifter.

`HomeServerRemoteClient.php` deliberately does **not** persist tokens. The host application must store the relay token and HomeServer bearer token using its own encrypted server-side credential storage. Never put either token in browser JavaScript, HTML, URLs, logs, or client-visible JSON.

## Pairing flow

1. The user claims a Remote Bridge session using the claim code shown by HomeServer.
2. Microgifter calls `requestPairing()`. The request is always identified as `microgifter` / `Microgifter`.
3. HomeServer shows the owner the requested permissions.
4. After the owner approves the pairing code, Microgifter calls `pairingStatus()`.
5. Once `ready=true`, the pairing claim token becomes the protected HomeServer bearer token.
6. Store both credentials encrypted, keyed to the Microgifter user/HomeServer pairing.

The HomeServer owner can subsequently narrow Microgifter without re-pairing using Connected Apps → Microgifter → scope controls.

## Effective scope

Call `appScope()` after pairing. It reads the authenticated `app_scope` returned by `tools.list`. Empty resource lists mean “all resources already allowed by the coarse permission”; non-empty lists narrow access.

Current scope fields:

- `cloud_allowed`
- `memory_key_prefixes`
- `knowledge_kinds`
- `tool_names`
- `plugin_keys`

HomeServer enforces the scope. Microgifter should also use it to improve UX—for example, show “local only” when `cloud_allowed=false`—but must never treat its own UI as the security boundary.

## Recommended Microgifter defaults

Use `MicrogifterHomeServerRemoteClient::DEFAULT_PERMISSIONS` for the initial request, then let the HomeServer owner narrow resources. The defaults cover Agent chat, awareness, contacts, events, Knowledge, Memory, notifications, plugins, tasks, tools, and usage telemetry.

For a hospitality/merchant deployment, a sensible owner scope could look conceptually like:

- Memory prefixes: `microgifter:`
- Knowledge kinds: only the kinds intended for merchant/customer workflows
- Tools: only Microgifter-relevant tool keys
- Plugins: only explicitly approved plugin keys
- Cloud: owner choice

Do not share VP3-specific prefixes or tool/plugin grants merely because both wrappers use the same HomeServer.

## Minimal PHP example

```php
require_once __DIR__.'/HomeServerRemoteClient.php';

$client = new MicrogifterHomeServerRemoteClient(
    $relayBaseUrl,
    decryptStoredRelayToken(),
    decryptStoredHomeServerToken()
);

$scope = $client->appScope();
$response = $client->chat('Summarize the active merchant opportunities.');
```

The connector also exposes `capabilities()`, `inferenceStatus()`, `contacts()`, `searchKnowledge()`, `memory()`, `tools()`, `skills()`, `plugins()`, and `executeTool()`.

## Multi-wrapper acceptance

`tests/multi_wrapper_v028.py` pairs real `vp3` and `microgifter` identities against the same temporary HomeServer and proves that their private scopes remain independent even when the coarse permission set overlaps.
