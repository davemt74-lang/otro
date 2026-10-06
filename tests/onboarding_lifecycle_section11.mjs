import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source=fs.readFileSync(new URL('../ui/chat-onboarding.js',import.meta.url),'utf8');
class Element {
  hidden=false;disabled=false;textContent='';value='';dataset={};listeners={};
  classList={toggle(){},contains(){return true;}};
  addEventListener(name,fn){this.listeners[name]=fn;}
  append(node){elements.set(node.id,node);}
  focus(){}
  setAttribute(){}
  querySelector(){return null;}
  querySelectorAll(){return [];}
}
const elements=new Map();
const el=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
const requests=[];const timers=[];
const deadlines=new Map();let deadlineId=0;
const document={readyState:'complete',visibilityState:'visible',getElementById:el,querySelector:()=>el('head'),createElement:()=>new Element(),addEventListener(){}};
function fetch(path,options){return new Promise((resolve,reject)=>{
  options.signal.addEventListener('abort',()=>reject(new Error('aborted')),{once:true});
  requests.push({path,resolve:body=>resolve({ok:true,json:async()=>body})});
});}
const window={addEventListener(){},dispatchEvent(){},open:()=>({opener:null,location:{replace(){}},close(){}})};
vm.runInNewContext(source,{document,window,fetch,Event:class{},setInterval:fn=>timers.push(fn),clearInterval(){},AbortController,
  setTimeout:fn=>{deadlines.set(++deadlineId,fn);return deadlineId;},clearTimeout:id=>deadlines.delete(id),
  navigator:{clipboard:{writeText:async()=>{}}},console});
const flush=async()=>{for(let i=0;i<20;i++)await Promise.resolve();};
const state=code=>({setup:{complete:true},cloud:{paired:false,connected:false},pairing:{state:code?'pending':'not_started',code,expires_at:'2026-10-05T06:00:00Z'},provision:{packages:[],supported_count:0}});
assert.equal(requests[0].path,'/api/v1/control/onboarding/summary?optional=false');
requests.shift().resolve(state(null));await flush();
el('chatOnboardingToggle').listeners.click();
const old=requests.shift();
const start=el('onboardStartCloud').listeners.click();
assert.equal(requests[0].path,'/api/v1/control/onboarding/device/start');
requests.shift().resolve({});await flush();
requests.shift().resolve(state('NEW2-NEW2-NEW2'));await flush();await start;
old.resolve(state('OLD2-OLD2-OLD2'));await flush();
assert.equal(el('onboardCodeText').textContent,'NEW2-NEW2-NEW2','late summary cannot replace newer code');
// A poll began before Reset; its old completion must not update the canvas.
const polling=timers.at(-1)();const poll=requests.shift();
assert.equal(poll.path,'/api/v1/control/onboarding/device/poll');
const reset=el('onboardResetCode').listeners.click();
requests.shift().resolve({});await flush();
requests.shift().resolve({});await flush();
requests.shift().resolve(state('FRESH-FRESH-FRESH'));await flush();await reset;
poll.resolve({cloud:{paired:true},pairing:{state:'paired'}});await flush();await polling;
assert.equal(requests.length,0,'old poll must not fetch a summary after Reset');
assert.equal(el('onboardCodeText').textContent,'FRESH-FRESH-FRESH');
// While an owner action is busy, automatic polling cannot start.
const voice=el('onboardInstallVoice').listeners.click();
const count=requests.length;await timers.at(-1)();assert.equal(requests.length,count);
requests.shift().resolve({});await flush();requests.shift().resolve(state('FRESH-FRESH-FRESH'));await flush();await voice;
el('chatOnboardingToggle').listeners.click();el('chatOnboardingToggle').listeners.click();
requests.shift().resolve({...state(null),pairing_recovery_pending:true});await flush();
assert.equal(el('onboardResetCode').hidden,false,'advanced pairing recovery has an explicit reset action');
assert.equal(el('onboardStartCloud').hidden,true);
// A saved pairing is not a verified live connection, and cannot finish as connected.
el('chatOnboardingToggle').listeners.click();el('chatOnboardingToggle').listeners.click();
requests.shift().resolve({...state(null),cloud:{paired:true,connected:false}});await flush();
assert.equal(el('onboardCloud').dataset.complete,'false');
assert.equal(el('onboardFinish').hidden,true);
await el('onboardFinish').listeners.click();assert.equal(requests.length,0);
// A stalled status read aborts and releases the busy controls without claiming success.
const check=el('onboardCheckConnection').listeners.click();await flush();
assert.equal(el('onboardCheckConnection').disabled,true);
assert.equal(requests.shift().path,'/api/v1/control/onboarding/summary?optional=false');
assert.equal(deadlines.size,1);
deadlines.values().next().value();await flush();await check;
assert.match(el('onboardFeedback').textContent,/took too long/);
assert.equal(el('onboardCheckConnection').disabled,false);
assert.equal(deadlines.size,0,'Completed/aborted requests release their timeout');
document.visibilityState='hidden';await timers.at(-1)();assert.equal(requests.length,0);
document.visibilityState='visible';
// Leaving setup while a start is in flight suppresses its late refresh/tab navigation.
const starting=el('onboardStartCloud').listeners.click();
assert.equal(requests.length,1);
await el('onboardStartCloud').listeners.click();assert.equal(requests.length,1,'Duplicate start does not make a second request');
el('chatOnboardingToggle').listeners.click();requests.shift().resolve({});await flush();await starting;
assert.equal(requests.length,0);assert.equal(el('chatOnboardingCanvas').hidden,true);
console.log('ONBOARDING_LIFECYCLE_SECTION11=PASS: current summary/code and owner action supersede stale polling');
