import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source=fs.readFileSync(new URL('../ui/chat-onboarding.js',import.meta.url),'utf8');
class Element {
  hidden=false;disabled=false;textContent='';value='';dataset={};listeners={};
  classList={toggle(){}};
  addEventListener(name,fn){this.listeners[name]=fn;}
  append(node){elements.set(node.id,node);}
  focus(){}
}
const elements=new Map();
const el=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
const requests=[];const timers=[];
const document={readyState:'complete',visibilityState:'visible',getElementById:el,querySelector:()=>el('head'),createElement:()=>new Element(),addEventListener(){}};
function fetch(path){return new Promise(resolve=>requests.push({path,resolve:body=>resolve({ok:true,json:async()=>body})}));}
const window={dispatchEvent(){},open:()=>({opener:null,location:{replace(){}},close(){}})};
vm.runInNewContext(source,{document,window,fetch,Event:class{},setInterval:fn=>timers.push(fn),navigator:{clipboard:{writeText:async()=>{}}},console});
const flush=async()=>{for(let i=0;i<20;i++)await Promise.resolve();};
const state=code=>({setup:{complete:true},cloud:{paired:false,connected:false},pairing:{state:code?'pending':'not_started',code,expires_at:'2026-10-05T06:00:00Z'},provision:{packages:[],supported_count:0}});
assert.equal(requests[0].path,'/api/v1/control/onboarding/summary');
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
console.log('ONBOARDING_LIFECYCLE_SECTION11=PASS: current summary/code and owner action supersede stale polling');
