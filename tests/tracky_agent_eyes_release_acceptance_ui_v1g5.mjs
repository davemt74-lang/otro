import assert from 'node:assert/strict';import fs from 'node:fs';import vm from 'node:vm';
const source=fs.readFileSync('ui/tracky/agent-eyes-release-acceptance.js','utf8');
let now=0,view='tracky',id='owner-a',handler,interval;const nodes={},listeners={},timers=[],requests=[];
const node=()=>({textContent:'',value:'',checked:false,disabled:false,children:[],replaceChildren(){this.children=[];},append(n){this.children.push(n);},addEventListener(e,fn){this[e]=fn;}});
for(const n of ['Panel','Stage','Record','Observed','Guide','Checks','Message','Refresh'])nodes[n]=node();nodes.Stage.value='local_chat';
const document={visibilityState:'visible',getElementById:key=>key==='view-tracky'?{classList:{contains:()=>view==='tracky'}}:nodes[key.replace('trackyReleaseAcceptance','')],querySelector:()=>({dataset:{brainConversation:id}}),createElement:node,addEventListener:(e,fn)=>listeners[e]=fn};
class Controller{constructor(){this.signal={aborted:false};}abort(){this.signal.aborted=true;}}
const state={inspection_token:'a'.repeat(64),expected_fingerprint:'b'.repeat(16),recordable_stages:['local_chat'],checks:[{key:'local_chat',label:'Local chat <img>',state:'pending',guidance:'Inspect current owner chat.'}]};
const response=data=>({ok:true,json:async()=>data});
vm.runInNewContext(source,{document,performance:{now:()=>now},AbortController:Controller,setTimeout:(fn,ms)=>{const t={fn,ms};timers.push(t);return t;},clearTimeout(){},setInterval:fn=>interval=fn,fetch:async(path,options)=>{requests.push({path,options});return handler?handler():response(state);}});
const flush=()=>new Promise(r=>setImmediate(r));await flush();assert.equal(nodes.Record.disabled,true);nodes.Observed.checked=true;nodes.Observed.change();assert.equal(nodes.Record.disabled,false);
handler=async()=>response({...state,inspection_token:'c'.repeat(64)});interval();await flush();assert.equal(nodes.Observed.checked,false,'New prerequisites retained inspection consent');
nodes.Observed.checked=true;nodes.Observed.change();let resolve;handler=()=>new Promise(r=>resolve=r);nodes.Record.click();await flush();const payload=JSON.parse(requests.at(-1).options.body);assert.equal(payload.conversation_id,'owner-a');assert.equal(payload.inspection_token,'c'.repeat(64));assert.equal(payload.owner_observed,true);
id='owner-b';resolve(response(state));await flush();assert.equal(nodes.Record.disabled,true,'Late response enabled different owner chat');
handler=async()=>response(state);nodes.Refresh.click();await flush();nodes.Observed.checked=true;now=6000;nodes.Observed.change();assert.equal(nodes.Record.disabled,true,'Expired prerequisite snapshot enabled recording');
document.visibilityState='hidden';listeners.visibilitychange();const count=requests.length;interval();await flush();assert.equal(requests.length,count);assert.equal(nodes.Record.disabled,true);
assert.ok(requests.every(r=>r.options.cache==='no-store'));assert.ok(requests.filter(r=>r.options.method==='POST').every(r=>r.path.endsWith('/record')));assert.doesNotMatch(source,/getUserMedia|innerHTML|localStorage|sessionStorage|\/start|\/stop|\/heartbeat|\/restart/);
console.log('TRACKY_AGENT_EYES_RELEASE_ACCEPTANCE_UI_V1G5: changed inspection, local conversation, deadline, hidden/late response, safe text and record-only POST PASS');
