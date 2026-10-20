import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {dirname,resolve} from 'node:path';
import vm from 'node:vm';

const base = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const source = readFileSync(resolve(base, 'ui/food-monster-connections.js'),'utf8');
const html = readFileSync(resolve(base,'ui/index.html'),'utf8');

assert.match(html, /id="chatOptionsMore"[^>]+role="tabpanel"/);
assert.match(html, /id="fmLinkForm" method="post" action="\/api\/v1\/control\/food-monster\/connections"/,
  'Without JavaScript, the pairing code must never fall back to a GET URL');
for (const required of ['foodMonsterLinks','fmLinksList','fmLinkSetup','fmLinkForm','fmLinkOrigin',
  'fmLinkLabel','fmLinkCode','fmLinkSubmit','fmLinksStatus']) {
  assert.ok(html.includes('id="' + required + '"'), required + ' must be in chat modal');
}
assert.match(html, /\/assets\/food-monster-connections\.js/);
assert.match(html, /\/assets\/food-monster-connections\.css/);
assert.doesNotMatch(source, /\.innerHTML\s*=/);
assert.doesNotMatch(source, /console\.(log|debug|info|warn|error)\(/);
assert.doesNotMatch(source, /(?:localStorage|sessionStorage)\./);
assert.ok(source.includes('/api/v1/control/food-monster'), 'Owner-local API boundary');

class FakeElement {
  constructor(tag) {
    this.tag = tag; this.children = []; this.listeners = {}; this.dataset = {};
    this.value = ''; this.disabled = false; this.open = true; this.attrs = {};
  }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  setAttribute(key, value) { this.attrs[key] = value; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = [...items]; }
}
const ids = ['fmLinkForm','fmLinkOrigin','fmLinkLabel','fmLinkCode','fmLinkSubmit',
  'fmLinksRefresh','fmLinksList','fmLinksStatus','fmLinkSetup','chatOptionsTabMore'];
const entries = Object.fromEntries(ids.map(id => [id,new FakeElement(id)]));
const doc = {
  readyState: 'complete',
  createElement: name => new FakeElement(name),
  getElementById: id => entries[id] ?? null,
  addEventListener() { throw new Error('Unexpected document event'); },
};
const requests = [];
let remoteList = [];
const fetch = async (url, init={}) => {
  requests.push({url, init});
  assert.ok(url.startsWith('/api/v1/control/food-monster/'),'No outbound browser request');
  const path = url.replace('/api/v1/control/food-monster','');
  if (path === '/connections' && (!init.method || init.method === 'GET')) {
    return {ok:true,json:async()=>({items:remoteList})};
  }
  if (path === '/connections' && init.method === 'POST') {
    const body = JSON.parse(init.body);
    assert.deepEqual(Object.keys(body).sort(), ['label','origin','pairing_code'].sort());
    assert.equal(body.origin, 'https://gelato.vp3.me');
    assert.equal(body.pairing_code,'fmlink_' + 'A'.repeat(43));
    remoteList = [{
      id: 'a'.repeat(32), label:body.label, origin:body.origin,
      status:'active',scopes:['menu','recipe']
    }];
    return {ok:true,json:async()=>({connection:{id:'a'.repeat(32)}})};
  }
  if (path === '/connections/' + 'a'.repeat(32) && init.method === 'DELETE') {
    remoteList = [];
    return {ok:true,json:async()=>({
      disconnected:true,remoteRevoked:false,remoteRevocationRequired:true,
      message:'Local connection removed. Revoke at Food Monster owner page.'
    })};
  }
  throw new Error('Unexpected local API call ' + path + ' ' + init.method);
};
const win = {confirm:()=>true};
vm.runInNewContext(source,{document:doc,window:win,URL,fetch,console});
const normalize = win.HomeServerFoodMonsterOrigin;
assert.equal(normalize('  HTTPS://GELATO.VP3.ME/  '),'https://gelato.vp3.me');
const forbidden=['http://gelato.vp3.me','https://127.0.0.1','https://localhost',
  'https://gelato.vp3.me/api','https://gelato.vp3.me?code=ABC',
  'https://guest:secret@gelato.vp3.me','https://gelato.vp3.me:8443',
  'https://router.local','javascript:alert(1)'];
for (const value of forbidden) {
  assert.throws(()=>normalize(value),/HTTPS|URL|domain|path|credentials|port/i,'Rejected origin '+value);
}
assert.ok(entries.fmLinkForm.listeners.submit, 'Form was wired');
entries.fmLinkOrigin.value='https://gelato.vp3.me';
entries.fmLinkLabel.value='Gelato';
entries.fmLinkCode.value='fmlink_' + 'A'.repeat(43);
await entries.fmLinkForm.listeners.submit({preventDefault(){}});
assert.equal(entries.fmLinkCode.value,'','One-time code must not remain in DOM');
assert.equal(entries.fmLinkOrigin.value,'');
assert.equal(entries.fmLinkSetup.open,false);
assert.equal(entries.fmLinksList.children.length,1);
assert.ok(requests.some(x=>x.init.method==='POST'), 'Paired via owner-local API');
assert.equal(entries.fmLinksStatus.dataset.level,'info');

const row = entries.fmLinksList.children[0];
const disconnect = row.children.find(x=>x.tag==='button');
assert.ok(disconnect?.listeners.click, 'Disconnect button wired');
await disconnect.listeners.click();
assert.ok(requests.some(x=>x.init.method==='DELETE'),'Disconnected via owner-local API');
assert.equal(entries.fmLinksStatus.dataset.level,'error','Unverified source revocation is a warning');
assert.match(entries.fmLinksStatus.textContent,/Revoke at Food Monster owner page/);
assert.ok(!source.includes('slack'), 'No Slack connector introduced');
console.log('KB4C private Food Monster chat UI PASS (origin validation, pairing, secret erasure, source revocation warning, isolation)');
