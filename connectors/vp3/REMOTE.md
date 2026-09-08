# VP3 Remote HomeServer Connector

`remote-client.js` is the browser-side counterpart for the deployable HomeServer Remote Relay.

It does **not** replace HomeServer pairing. Remote access has two independent layers:

```text
Relay claim token        -> selects which HomeServer VP3 can reach
HomeServer claim token   -> selects which capabilities VP3 may use
```

## Initial claim

The local Windows HomeServer owner:

1. configures **Remote Bridge** with the production relay WSS URL
2. enables Remote Bridge
3. reads the one-time relay claim code shown by HomeServer

VP3 then claims that HomeServer:

```js
const remote = new VP3HomeServerRemoteConnector({
  relayBaseUrl: 'https://relay.example.com',
  appKey: 'vp3',
  appName: 'VP3',
});

const relay = await remote.claimHomeServer('ABCD-EFGH-JKLM');
// Persist relay.relay_token securely in VP3's authenticated user context.
```

The relay session is now associated with that HomeServer, but it still grants no private HomeServer capability.

## Pair VP3 with HomeServer

Use the normal HomeServer claim-token pairing flow through the relay:

```js
const result = await remote.pair(undefined, {
  onCode(code) {
    // Tell the user to approve this code in the local HomeServer Control Center.
    console.log(code);
  },
});

// remote.homeServerToken is now the scoped HomeServer bearer credential.
```

The v0.17 VP3 default permission set is:

- `agent.chat`
- `awareness.read`
- `contacts.read`
- `events.read`
- `events.write`
- `knowledge.search`
- `memory.read`
- `memory.write`
- `notifications.read`
- `plugins.read`
- `tasks.read`
- `tasks.write`
- `tools.execute`
- `usage.read`
- `usage.write`

The relay can see the scoped HomeServer bearer token because the current protocol is a trusted relay. HomeServer stores only its hash and remains the final permission authority.

## Use capabilities

```js
await remote.chat('What do I know about this customer?');
await remote.contacts('Example Organization');
await remote.searchKnowledge('renewal notes');
await remote.memory();
await remote.inferenceStatus();
await remote.awareness(50);
await remote.events({limit: 100});
await remote.plugins();
await remote.usage(200);
```

The remote connector also exposes event emission and VP3 cloud-usage reporting when the paired app has `events.write` or `usage.write` respectively.

Remote relay operations in v0.17 are:

- `capabilities`
- `pair.request`
- `pair.status`
- `chat`
- `conversations.list`
- `conversation.get`
- `contacts.search`
- `knowledge.search`
- `memory.read`
- `memory.write`
- `inference.status`
- `events.emit`
- `events.list`
- `awareness.list`
- `plugins.list`
- `usage.cloud`
- `usage.read`
- `tools.list`
- `skills.list`
- `tool.execute`
- `action.status`

Opaque conversation identifiers are preserved end-to-end; VP3 must not coerce HomeServer conversation IDs to numbers.

If VP3 lacks the corresponding HomeServer permission, the relay returns the HomeServer denial status. The relay does not elevate or translate permissions.

## Rotate relay session

```js
const rotated = await remote.rotateRelaySession();
// Replace the previously stored relay token with rotated.relay_token.
```

Rotation revokes the prior relay session token immediately.

## Storage guidance

VP3 must treat both tokens as credentials:

- relay token: access path to the claimed HomeServer
- HomeServer token: scoped app authorization inside that HomeServer

Do not place either token in URLs, query strings, analytics events or client logs.

## Trust boundary

The current Remote Relay terminates HTTPS/WSS and can see relayed application payloads and HomeServer app bearer credentials. It is therefore a trusted service component, not an end-to-end encrypted transport.

Future application-payload encryption can be layered on top without changing HomeServer's outbound-only network model or its canonical app-permission checks.
