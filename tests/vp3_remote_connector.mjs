import assert from 'node:assert/strict';

await import('../connectors/vp3/remote-client.js');
const Connector = globalThis.VP3HomeServerRemoteConnector;
assert.equal(typeof Connector, 'function');

const expectedPermissions = [
  'agent.chat',
  'awareness.read',
  'contacts.read',
  'events.read',
  'events.write',
  'knowledge.search',
  'memory.read',
  'memory.write',
  'notifications.read',
  'plugins.read',
  'tasks.read',
  'tasks.write',
  'tools.execute',
  'usage.read',
  'usage.write',
];
assert.deepEqual(Connector.DEFAULT_PERMISSIONS, expectedPermissions);

const requests = [];
const originalFetch = globalThis.fetch;
globalThis.fetch = async (url, options = {}) => {
  const body = options.body ? JSON.parse(options.body) : null;
  requests.push({url:String(url), options, body});
  return {
    ok:true,
    status:200,
    async json() {
      return {ok:true,status:200,payload:{accepted:true}};
    },
  };
};

try {
  const connector = new Connector({
    relayBaseUrl:'https://relay.example.test',
    relayToken:'relay-token-synthetic-1234567890',
    homeServerToken:'homeserver-token-synthetic-1234567890',
  });

  const uuid = '550e8400-e29b-41d4-a716-446655440000';
  await connector.conversation(uuid);
  assert.equal(requests.at(-1).body.operation, 'conversation.get');
  assert.equal(requests.at(-1).body.payload.conversation_id, uuid);

  await connector.inferenceStatus();
  assert.equal(requests.at(-1).body.operation, 'inference.status');

  await connector.emitEvent({event_id:'evt-1',event_type:'campaign.claimed'});
  assert.equal(requests.at(-1).body.operation, 'events.emit');

  await connector.events({limit:25,eventType:'campaign.claimed'});
  assert.equal(requests.at(-1).body.operation, 'events.list');
  assert.deepEqual(requests.at(-1).body.payload, {limit:25,event_type:'campaign.claimed'});

  await connector.awareness(12);
  assert.equal(requests.at(-1).body.operation, 'awareness.list');
  assert.equal(requests.at(-1).body.payload.limit, 12);

  await connector.plugins();
  assert.equal(requests.at(-1).body.operation, 'plugins.list');

  await connector.recordCloudUsage({event_id:'charge-1',billable_tokens:20});
  assert.equal(requests.at(-1).body.operation, 'usage.cloud');

  await connector.usage(40);
  assert.equal(requests.at(-1).body.operation, 'usage.read');
  assert.equal(requests.at(-1).body.payload.limit, 40);

  for (const request of requests) {
    assert.equal(request.body.bearer_token, 'homeserver-token-synthetic-1234567890');
    assert.match(request.url, /^https:\/\/relay\.example\.test\/v1\/request$/);
  }
} finally {
  globalThis.fetch = originalFetch;
}

console.log('VP3 remote connector protocol regression passed');
