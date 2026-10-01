/* Runtime interaction acceptance without browser dependencies. */
'use strict';
const fs=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const script=fs.readFileSync('ui/agent-brain-drawer.js','utf8');
const css=fs.readFileSync('ui/agent-brain-drawer.css','utf8');
const registry=new Map();
const handlers=new Map();
let submitCalls=0,chatNavClicks=0,healthNavClicks=0,intervals=0;
class Element {
  constructor(name='element') {
    this.name=name;this.children=[];this.listeners=new Map();this.dataset={};
    this.attributes={};this.focused=false;this.value='';this.textContent='';
    const classes=new Set();
    this.classList={
      add:x=>classes.add(x),remove:x=>classes.delete(x),contains:x=>classes.has(x),
      toggle:(x,force)=>{if(force===undefined)force=!classes.has(x);if(force)classes.add(x);else classes.delete(x);return force;}
    };
  }
  set id(value) { this._id=value;registry.set(value,this); }
  get id() { return this._id; }
  set innerHTML(value) {
    this.html=value;
    for(const match of value.matchAll(/id="([^"]+)"/g)) registry.set(match[1],new Element(match[1]));
    if(value.includes('agent-brain-close'))this.closeButton=new Element('close');
  }
  get innerHTML(){return this.html||'';}
  setAttribute(k,v){this.attributes[k]=v;}
  removeAttribute(k){delete this.attributes[k];}
  appendChild(el){this.children.push(el);return el;}
  append(...els){this.children.push(...els);}
  prepend(el){this.children.unshift(el);}
  replaceChildren(){this.children=[];}
  addEventListener(k,fn){this.listeners.set(k,fn);}
  fire(k,event={}){const fn=this.listeners.get(k);assert.ok(fn,'handler '+this.name+' '+k);return fn(event);}
  querySelector(selector){return selector==='.agent-brain-close'?this.closeButton:null;}
  focus(){this.focused=true;}
  click(){return this.fire('click');}
  contains(target){return this===target||this.children.includes(target);}
  dispatchEvent(){}
}
const top=new Element('top');
const body=new Element('body');
const nav=new Element('chat-nav');
nav.addEventListener('click',()=>chatNavClicks++);
const health=new Element('health-nav');
health.addEventListener('click',()=>healthNavClicks++);
const activity=new Element('activity-nav');
const input=new Element('chatInput');
registry.set('chatInput',input);
const document={
  readyState:'complete',body,hidden:false,head:new Element('head'),
  createElement:type=>new Element(type),
  getElementById:id=>registry.get(id)||null,
  querySelector:sel=>({
    '.topbar .top-actions':top,
    '.nav [data-view="chat"]':nav,
    '[data-view="health"]':health,
    '[data-view="activity"]':activity,
  })[sel]||null,
  addEventListener:(event,fn)=>handlers.set(event,fn)
};
let healthRequests=0,activityRequests=0,manualSyncs=0;
const fetch=(url,options)=>{
  assert.equal(options.credentials,'same-origin');
  if(url==='/api/v1/control/activity-center/sync') {
    assert.equal(options.method,'POST');
    manualSyncs++;
    return Promise.resolve({ok:true,json:async()=>({health:{created:0}})});
  }
  assert.equal(options.cache,'no-store');
  assert.equal(options.method,undefined);
  if(url==='/api/v1/control/health') {
    healthRequests++;
    return Promise.resolve({ok:true,json:async()=>({
      overall:'attention',issues:[{key:'storage:disk-pressure',title:'Disk pressure',
        severity:'warning',repair:{action_key:null,agent_can_execute:false}}]
    })});
  }
  if(url.includes('/activity-center/brain-context')) {
    activityRequests++;
    return Promise.resolve({ok:true,json:async()=>({
      attention:[{event_id:'notification:42',title:'Maintenance warning',level:'warning'}]
    })});
  }
  throw new Error('Unexpected fetch '+url);
};
vm.runInNewContext(script,{document,fetch,setInterval:()=>++intervals,clearInterval:()=>{},
  Event:class{constructor(type){this.type=type;}},console});
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  const toggle=registry.get('agentBrainDrawerToggle');
  const drawer=registry.get('agentBrainDrawer');
  assert.ok(toggle&&drawer,'Drawer and toggle mount in existing shell');
  toggle.fire('click');
  await flush();
  assert.equal(drawer.attributes['aria-hidden'],'false');
  assert.equal(toggle.attributes['aria-expanded'],'true');
  assert.ok(drawer.classList.contains('is-open'));
  assert.equal(registry.get('agentBrainHealthState').textContent,'Attention');
  const issues=registry.get('agentBrainIssues');
  assert.equal(issues.children.length,1);
  issues.fire('click',{target:{closest:()=>issues.children[0]}});
  assert.equal(chatNavClicks,1);
  assert.ok(input.value.includes('storage:disk-pressure'));
  assert.ok(!input.value.includes('Disk pressure'),'Raw user/app title must not become instruction');
  assert.ok(input.focused);
  assert.equal(drawer.attributes['aria-hidden'],'true');
  assert.equal(submitCalls,0);
  toggle.fire('click');
  await flush();
  const alerts=registry.get('agentBrainAlerts');
  assert.equal(alerts.children.length,1,'Warning maintenance event visible');
  registry.get('agentBrainRefresh').fire('click');
  await flush();
  assert.equal(manualSyncs,1,'Explicit refresh synchronizes maintenance');
  alerts.fire('click',{target:{closest:()=>alerts.children[0]}});
  assert.equal(chatNavClicks,2);
  assert.ok(input.value.includes('notification:42'));
  assert.ok(!input.value.includes('Maintenance warning'));
  toggle.fire('click');
  handlers.get('keydown')({key:'Escape'});
  assert.equal(drawer.attributes['aria-hidden'],'true');
  assert.ok(Object.hasOwn(drawer.attributes,'inert'),'Closed sidebar must be inert');
  toggle.fire('click');
  registry.get('agentBrainHealth').fire('click');
  assert.equal(healthNavClicks,1,'Health action uses existing workspace');
  assert.ok(healthRequests>=2&&activityRequests>=2);
  assert.match(css,/position:fixed/);
  assert.match(css,/max-width:100vw/);
  console.log('Section 31: real drawer interactions, warning handoff and governance PASS');
})().catch(error=>{console.error(error);process.exitCode=1;});
