import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync('ui/context-engine.js', 'utf8');
assert.match(source, /id="contextAgentEyesStatus" role="status"/);
assert.match(source, /type="button" data-view="tracky">Open Tracky/);
const ids = ['chatContextControls', 'contextUseMemory', 'contextUseKnowledge',
  'contextUseContacts', 'contextUseAgentEyes', 'contextCloudAllowed', 'contextBudget',
  'contextEngineStatus', 'contextSources', 'conversationList', 'chatMessages', 'view-chat',
  'contextAgentEyesStatus', 'contextAgentEyesEvidence', 'contextAgentEyesGuidance',
  'contextAgentEyesLimits', 'contextAgentEyesRefresh'];
const nodes = Object.fromEntries(ids.map(id => [id, {checked: false, disabled: false,
  value: '12000', textContent: '', innerHTML: '', classList: {toggle() {}, contains: () => true}}]));
let activeId = 'chat-a', now = 100000, timerId = 0, settings, status, handler;
const timers = new Map(), listeners = {}, requests = [];
const off = {include_agent_eyes: false, agent_eyes_local_only: false, cloud_allowed: true};
const on = {include_agent_eyes: true, agent_eyes_local_only: true, cloud_allowed: false};
const recent = age => ({state: 'recent_observation', reason: 'recent_observation',
  explanation: 'A recent permitted detector observation is available.',
  next_action: 'Refresh status to check availability again.', limitations: 'Checked snapshot, not a live view.',
  possible_face_regions: 'multiple', age_seconds: age, freshness_limit_seconds: 60});
const unavailable = reason => ({state: 'unavailable', reason, explanation: `Unavailable: ${reason}`,
  next_action: 'Review the existing supervised flow in Tracky.'});
const document = {readyState: 'complete', visibilityState: 'visible', getElementById: id => nodes[id],
  querySelector: selector => selector.includes('[data-brain-conversation]')
    ? (activeId ? {dataset: {brainConversation: activeId}} : null)
    : selector.startsWith('link') || selector.startsWith('script') ? {} : null,
  addEventListener: (event, callback) => {listeners[event] = callback;}};
const payload = () => ({context_settings: settings, agent_eyes_context: status, context_history: []});
const response = data => ({ok: true, json: async () => data});
const defer = () => {let resolve; const promise = new Promise(r => {resolve = r;}); return {promise, resolve};};
const sandbox = {document, window: {}, Date: {now: () => now}, MutationObserver: class {observe() {}},
  setTimeout: (callback, delay) => {timers.set(++timerId, {callback, delay}); return timerId;},
  clearTimeout: id => timers.delete(id),
  fetch: async (path, options = {}) => {
    requests.push({path, options});
    if (handler) return handler(path, options);
    if (options.method === 'PUT') {
      const body = JSON.parse(options.body);
      settings = {...body, agent_eyes_local_only: true, cloud_allowed: false};
      status = settings.include_agent_eyes ? recent(5) : unavailable('opted_out');
    }
    return response(payload());
  }};
settings = on; status = recent(59.5);
vm.runInNewContext(source, sandbox);
const flush = async () => {await new Promise(resolve => setImmediate(resolve));};
async function timer(delay, advance = delay) {
  const entry = [...timers].find(([, value]) => value.delay === delay);
  assert.ok(entry, `No ${delay}ms timer`);
  timers.delete(entry[0]); now += advance; entry[1].callback(); await flush();
}
const click = selector => listeners.click({target: {closest: query => query === selector ? {} : null}});
const change = () => listeners.change({target: {closest: query => query === '#chatContextControls' ? {} : null}});
await timer(0);
assert.match(nodes.contextAgentEyesEvidence.textContent, /Multiple possible face regions.*60s ago.*uncalibrated/);
assert.equal(nodes.contextCloudAllowed.disabled, true);
await timer(1000);
assert.equal(nodes.contextAgentEyesEvidence.textContent, '');
assert.match(nodes.contextAgentEyesStatus.textContent, /No current checked/);

// A delayed status response cannot extend the detector's 60-second deadline.
let pending = defer(); handler = () => pending.promise;
click('#contextAgentEyesRefresh'); await flush();
assert.equal(nodes.contextAgentEyesEvidence.textContent, '');
now += 12000;
pending.resolve(response({agent_eyes_context: recent(50)})); await flush();
assert.equal(nodes.contextAgentEyesEvidence.textContent, '');
assert.match(nodes.contextAgentEyesStatus.textContent, /No current checked/);
handler = null; status = unavailable('privacy_enabled');
click('#contextAgentEyesRefresh'); await flush();
assert.match(nodes.contextAgentEyesStatus.textContent, /privacy_enabled/);
assert.match(nodes.contextAgentEyesGuidance.textContent, /supervised flow/);
assert.equal(nodes.contextAgentEyesEvidence.textContent, '');

// Network errors immediately clear evidence and never display raw error text.
handler = async () => {throw Error('SECRET_PATH');};
click('#contextAgentEyesRefresh'); await flush();
assert.match(nodes.contextAgentEyesStatus.textContent, /could not be checked/);
assert.doesNotMatch(nodes.contextAgentEyesStatus.textContent, /SECRET_PATH/);
handler = null; status = recent(5);
click('#contextAgentEyesRefresh'); await flush();
assert.match(nodes.contextAgentEyesEvidence.textContent, /Multiple/);

// Off clears immediately, preserves the server's local history policy, and stops polling.
nodes.contextUseAgentEyes.checked = false;
change(); assert.equal(nodes.contextAgentEyesEvidence.textContent, ''); await flush();
assert.match(nodes.contextAgentEyesStatus.textContent, /opted_out/);
assert.equal(nodes.contextCloudAllowed.checked, false);
assert.equal(nodes.contextCloudAllowed.disabled, true);
assert.ok(![...timers.values()].some(item => item.delay === 5000));

nodes.contextUseAgentEyes.checked = true; change(); await flush();
assert.match(nodes.contextAgentEyesEvidence.textContent, /Multiple/);
const pollCount = requests.length;
await timer(5000);
assert.equal(requests.length, pollCount + 1);
assert.match(requests.at(-1).path, /\/agent-eyes-context$/);
assert.equal(requests.at(-1).options.cache, 'no-store');
assert.equal(requests.at(-1).options.method, undefined);

// A late refresh from A cannot leak into B or overwrite B's context policy.
pending = defer(); handler = () => pending.promise;
click('#contextAgentEyesRefresh'); await flush();
activeId = 'chat-b'; settings = off; status = unavailable('opted_out');
click('[data-brain-conversation]'); handler = null;
assert.equal(nodes.contextAgentEyesEvidence.textContent, '');
await timer(120); assert.equal(nodes.contextUseAgentEyes.checked, false);
pending.resolve(response({agent_eyes_context: recent(1)})); await flush();
assert.equal(nodes.contextAgentEyesEvidence.textContent, '');
assert.match(nodes.contextAgentEyesStatus.textContent, /opted_out/);
assert.equal(nodes.contextCloudAllowed.disabled, false);

// A late policy save from B cannot lock or repopulate C.
pending = defer(); handler = () => pending.promise;
nodes.contextUseAgentEyes.checked = true; change(); await flush();
activeId = 'chat-c'; click('[data-brain-conversation]'); handler = null;
await timer(120);
pending.resolve(response({context_settings: on, agent_eyes_context: recent(1)})); await flush();
assert.equal(nodes.contextUseAgentEyes.checked, false);
assert.equal(nodes.contextCloudAllowed.disabled, false);
assert.equal(nodes.contextAgentEyesEvidence.textContent, '');

// An in-flight conversation load cannot undo a more recent opt-out.
handler = () => (pending = defer()).promise;
click('[data-brain-conversation]'); await timer(120);
const pendingLoad = pending;
handler = null; nodes.contextUseAgentEyes.checked = false; change(); await flush();
pendingLoad.resolve(response({context_settings: on, agent_eyes_context: recent(1)})); await flush();
assert.equal(nodes.contextUseAgentEyes.checked, false);
assert.equal(nodes.contextAgentEyesEvidence.textContent, '');

// Rapid opt-in/opt-out writes are sent in order, so the server ends opted out.
pending = defer(); handler = () => pending.promise;
nodes.contextUseAgentEyes.checked = true; change(); await flush();
const pendingWrites = requests.length;
nodes.contextUseAgentEyes.checked = false; change(); await flush();
assert.equal(requests.length, pendingWrites);
handler = null;
pending.resolve(response({context_settings: on, agent_eyes_context: recent(1)})); await flush();
assert.equal(requests.length, pendingWrites + 1);
assert.equal(JSON.parse(requests.at(-1).options.body).include_agent_eyes, false);
assert.equal(settings.include_agent_eyes, false);
assert.equal(nodes.contextUseAgentEyes.checked, false);
assert.equal(nodes.contextAgentEyesEvidence.textContent, '');

nodes.contextUseAgentEyes.checked = true; change(); await flush();
assert.match(nodes.contextAgentEyesEvidence.textContent, /Multiple/);
document.visibilityState = 'hidden'; listeners.visibilitychange();
assert.equal(nodes.contextAgentEyesEvidence.textContent, '');
assert.ok(![...timers.values()].some(item => item.delay === 5000 || item.delay === 1000));
document.visibilityState = 'visible'; listeners.visibilitychange(); await timer(0);
assert.match(nodes.contextAgentEyesEvidence.textContent, /Multiple/);
const beforeNavigation = requests.length;
listeners.click({target: {closest: query => query === '[data-view]' ? {dataset: {view: 'tracky'}} : null}});
assert.equal(nodes.contextAgentEyesEvidence.textContent, '');
assert.equal(requests.length, beforeNavigation);
assert.ok(requests.every(item => !/capture|heartbeat|session\/start/.test(item.path)));

// UI fields use textContent, so even an unexpected payload cannot create markup.
status = {...unavailable('evidence_invalid'), explanation: '<img src=x onerror=alert(1)>'};
click('#contextAgentEyesRefresh'); await flush();
assert.equal(nodes.contextAgentEyesStatus.textContent, status.explanation);
assert.equal(nodes.contextAgentEyesStatus.innerHTML, '');
status = {...recent(1), possible_face_regions: 'toString'};
click('#contextAgentEyesRefresh'); await flush();
assert.equal(nodes.contextAgentEyesEvidence.textContent, '');
pending = defer(); handler = () => pending.promise;
click('#contextAgentEyesRefresh'); await flush();
now -= 5000; pending.resolve(response({agent_eyes_context: recent(1)})); await flush();
assert.equal(nodes.contextAgentEyesEvidence.textContent, '');
activeId = null; click('#newChat, #sidebarNewChat'); await timer(120);
assert.equal(nodes.contextUseAgentEyes.disabled, true);
assert.equal(nodes.contextAgentEyesEvidence.textContent, '');
assert.match(nodes.contextAgentEyesStatus.textContent, /Start a chat/);
console.log('TRACKY_AGENT_EYES_EVIDENCE_UI_V1G3B: age/latency expiry, privacy/errors, opt-out, polling, stale response isolation and navigation PASS');
