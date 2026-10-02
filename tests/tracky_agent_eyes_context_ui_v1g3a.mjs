import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync('ui/context-engine.js', 'utf8');
assert.match(source, /id="contextUseAgentEyes" type="checkbox">/);
assert.match(source, /conversation stays local and read-only/);
const ids = ['chatContextControls', 'contextUseMemory', 'contextUseKnowledge',
  'contextUseContacts', 'contextUseAgentEyes', 'contextCloudAllowed', 'contextBudget',
  'contextEngineStatus', 'contextSources', 'conversationList', 'chatMessages'];
const nodes = Object.fromEntries(ids.map(id => [id, {
  checked: !['contextUseAgentEyes'].includes(id), disabled: false, value: '12000',
  classList: {toggle() {}}, textContent: '', innerHTML: '',
}]));
let activeId = 'owner-chat', timers = [], listeners = {}, requests = [];
let settings = {include_memory: true, include_knowledge: true, include_contacts: true,
  cloud_allowed: true, max_context_chars: 12000, include_agent_eyes: false, agent_eyes_local_only: false};
const document = {
  readyState: 'complete', getElementById: id => nodes[id],
  querySelector: selector => selector.includes('[data-brain-conversation]')
    ? (activeId ? {dataset: {brainConversation: activeId}} : null)
    : selector.startsWith('link') || selector.startsWith('script') ? {} : null,
  addEventListener: (event, callback) => { listeners[event] = callback; },
};
const sandbox = {document, window: {}, MutationObserver: class {observe() {}},
  setTimeout: callback => {timers.push(callback); return timers.length;}, clearTimeout() {},
  fetch: async (path, options) => {
    if (options.method === 'PUT') {
      const body = JSON.parse(options.body); requests.push(body);
      const locked = settings.agent_eyes_local_only || body.include_agent_eyes;
      settings = {...body, agent_eyes_local_only: locked, cloud_allowed: locked ? false : body.cloud_allowed};
    }
    return {ok: true, json: async () => ({context_settings: settings, context_history: [
      {sources: [{kind: 'agent_eyes', title: 'Recent permitted context'}]},
    ]})};
  },
};
vm.runInNewContext(source, sandbox);
const flush = async () => { await new Promise(resolve => setImmediate(resolve)); };
await timers.pop()(); await flush();
assert.equal(nodes.contextUseAgentEyes.checked, false);
assert.equal(nodes.contextUseAgentEyes.disabled, false);
assert.equal(nodes.contextCloudAllowed.disabled, false);
assert.match(nodes.contextSources.innerHTML, /Agent Eyes/);
nodes.contextUseAgentEyes.checked = true;
listeners.change({target: {closest: () => true}}); await flush();
assert.equal(requests.at(-1).include_agent_eyes, true);
assert.equal(nodes.contextCloudAllowed.checked, false);
assert.equal(nodes.contextCloudAllowed.disabled, true);
assert.match(nodes.contextEngineStatus.textContent, /local model required.*read-only/);
nodes.contextUseAgentEyes.checked = false;
listeners.change({target: {closest: () => true}}); await flush();
assert.equal(requests.at(-1).include_agent_eyes, false);
assert.equal(nodes.contextCloudAllowed.disabled, true);
activeId = null;
listeners.click({target: {closest: () => true}});
await timers.pop()(); await flush();
assert.equal(nodes.contextUseAgentEyes.checked, false);
assert.equal(nodes.contextUseAgentEyes.disabled, true);
assert.equal(nodes.contextCloudAllowed.disabled, true);
console.log('TRACKY_AGENT_EYES_CONTEXT_UI_V1G3A: default off, owner opt-in, server local policy, opt-out, new-chat reset PASS');
