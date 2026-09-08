import assert from 'node:assert/strict';

await import('../connectors/vp3/remote-client.js');
await import('../connectors/vp3/client.js');
const RemoteConnector = globalThis.VP3HomeServerRemoteConnector;
const LocalConnector = globalThis.VP3HomeServerConnector;
assert.equal(typeof RemoteConnector, 'function');
assert.equal(typeof LocalConnector, 'function');

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
assert.deepEqual(RemoteConnector.DEFAULT_PERMISSIONS, expectedPermissions);
assert.deepEqual(LocalConnector.DEFAULT_PERMISSIONS, expectedPermissions);

const requests = [];
const originalFetch = globalThis.fetch;
globalThis.fetch = async (url, options = {}) => {
  let body = null;
  if (options.body && typeof options.body === 'string') body = JSON.parse(options.body);
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
  const remote = new RemoteConnector({
    relayBaseUrl:'https://relay.example.test',
    relayToken:'relay-token-synthetic-1234567890',
    homeServerToken:'homeserver-token-synthetic-1234567890',
  });

  const uuid = '550e8400-e29b-41d4-a716-446655440000';
  await remote.conversation(uuid);
  assert.equal(requests.at(-1).body.operation, 'conversation.get');
  assert.equal(requests.at(-1).body.payload.conversation_id, uuid);

  await remote.inferenceStatus();
  assert.equal(requests.at(-1).body.operation, 'inference.status');

  await remote.emitEvent({event_id:'evt-1',event_type:'campaign.claimed'});
  assert.equal(requests.at(-1).body.operation, 'events.emit');

  await remote.events({limit:25,eventType:'campaign.claimed'});
  assert.equal(requests.at(-1).body.operation, 'events.list');
  assert.deepEqual(requests.at(-1).body.payload, {limit:25,event_type:'campaign.claimed'});

  await remote.awareness(12);
  assert.equal(requests.at(-1).body.operation, 'awareness.list');
  assert.equal(requests.at(-1).body.payload.limit, 12);

  await remote.plugins();
  assert.equal(requests.at(-1).body.operation, 'plugins.list');

  await remote.recordCloudUsage({event_id:'charge-1',billable_tokens:20});
  assert.equal(requests.at(-1).body.operation, 'usage.cloud');

  await remote.usage(40);
  assert.equal(requests.at(-1).body.operation, 'usage.read');
  assert.equal(requests.at(-1).body.payload.limit, 40);

  for (const request of requests.splice(0)) {
    assert.equal(request.body.bearer_token, 'homeserver-token-synthetic-1234567890');
    assert.match(request.url, /^https:\/\/relay\.example\.test\/v1\/request$/);
  }

  const local = new LocalConnector({
    baseUrl:'http://127.0.0.1:4377',
    token:'local-token-synthetic-1234567890',
  });

  await local.conversation(uuid);
  assert.equal(requests.at(-1).url, `http://127.0.0.1:4377/api/v1/conversations/${uuid}`);

  await local.inferenceStatus();
  assert.equal(requests.at(-1).url, 'http://127.0.0.1:4377/api/v1/inference/status');

  await local.emitEvent({event_id:'evt-2',event_type:'campaign.claimed'});
  assert.equal(requests.at(-1).url, 'http://127.0.0.1:4377/api/v1/events');
  assert.equal(requests.at(-1).options.method, 'POST');

  await local.events({limit:25,eventType:'campaign.claimed'});
  assert.match(requests.at(-1).url, /\/api\/v1\/events\?/);
  assert.match(requests.at(-1).url, /limit=25/);
  assert.match(requests.at(-1).url, /event_type=campaign\.claimed/);

  await local.awareness(12);
  assert.equal(requests.at(-1).url, 'http://127.0.0.1:4377/api/v1/awareness?limit=12');

  await local.plugins();
  assert.equal(requests.at(-1).url, 'http://127.0.0.1:4377/api/v1/plugins');

  await local.recordCloudUsage({event_id:'charge-2',billable_tokens:30});
  assert.equal(requests.at(-1).url, 'http://127.0.0.1:4377/api/v1/usage/cloud');
  assert.equal(requests.at(-1).options.method, 'POST');

  await local.usage(40);
  assert.equal(requests.at(-1).url, 'http://127.0.0.1:4377/api/v1/usage?limit=40');

  for (const request of requests) {
    assert.equal(request.options.headers.Authorization, 'Bearer local-token-synthetic-1234567890');
  }
} finally {
  globalThis.fetch = originalFetch;
}

console.log('VP3 local/remote connector protocol regression passed');
