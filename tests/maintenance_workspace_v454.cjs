/* Section 31D: real maintenance workspace state, escaping and chat handoff. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const script=fs.readFileSync('ui/maintenance-workspace.js','utf8');
const page=fs.readFileSync('ui/index.html','utf8');
const app=fs.readFileSync('ui/app.js','utf8');
assert.ok(page.includes('/assets/maintenance-workspace.css'));
assert.ok(page.includes('/assets/maintenance-workspace.js'));
assert.ok(page.indexOf('/assets/app.js')<page.indexOf('/assets/maintenance-workspace.js'));
assert.ok(app.includes('window.HomeServerMaintenanceWorkspace.load(status)'));
assert.ok(!app.includes("api('/api/v1/control/health/repair-plan')"),'Health must use one canonical snapshot');
assert.ok(app.includes("api('/api/v1/control/activity-center/sync',{method:'POST'})"));
assert.ok(!script.includes('requestSubmit(')&&!script.includes('chatForm.submit('));
const nodes=new Map(), handlers=new Map();
class Element {
  constructor(id=''){this.id=id;this.dataset={};this.innerHTML='';this.textContent='';this.isConnected=true;this.value='';this.focused=false;this.listeners={};this.children=[];}
  setAttribute(k,v){this[k]=v;}
  appendChild(el){this.children.push(el);}
  insertBefore(el){this.children.push(el);nodes.set(el.id,el);for(const id of el.innerHTML.matchAll(/id="([^"]+)"/g)){nodes.set(id[1],new Element(id[1]));}}
  querySelector(sel){if(sel==='.health-governance')return governance;if(sel==='.maintenance-heading p:not(.eyebrow)')return headingDescription;return null;}
  dispatchEvent(){}
  focus(){this.focused=true;}
}
const governance=new Element('governance'),health=new Element('view-health'),headingDescription=new Element('desc'),input=new Element('chatInput');
nodes.set('view-health',health);nodes.set('chatInput',input);
let chatClicks=0,approvalsClicks=0,activityClicks=0;
const nav={chat:{click(){chatClicks++;}},approvals:{click(){approvalsClicks++;}},activity:{click(){activityClicks++;}}};
const document={getElementById:id=>nodes.get(id)||null,createElement:()=>new Element(),addEventListener:(kind,fn)=>handlers.set(kind,fn),
 querySelector:sel=>{const m=sel.match(/data-view="([^"]+)"/);return m?nav[m[1]]||null:null;}};
const requests=[];
const fetch=(url,opts)=>{
 requests.push(url);
 assert.equal(opts.credentials,'same-origin');
 assert.equal(opts.cache,'no-store');
 const data=url.startsWith('/api/v1/control/activity?')?{items:[
  {source_key:'homeserver-health',notification_id:42,title:'<img src=x>',level:'warning',read:false,dismissed:false,archived:false},
  {source_key:'other',notification_id:80,title:'ignore',level:'error'},
  {source_key:'homeserver-health',notification_id:43,title:'dismissed',dismissed:true}
 ]}:{items:[
  {id:'x',status:'pending',action_key:'apps.recover',arguments_meta:{maintenance_issue_key:'app:vp3.notes:degraded'}},
  {id:'y',status:'executed',action_key:'apps.recover',arguments_meta:{maintenance_issue_key:'app:vp3.notes:degraded'}},
  {id:'z',status:'pending',action_key:'apps.stop',arguments_meta:{}}
 ]};
 return Promise.resolve({ok:true,json:async()=>data});
};
const window={};
vm.runInNewContext(script,{document,fetch,window,Event:class{constructor(type){this.type=type;}},console});
assert.equal(typeof window.HomeServerMaintenanceWorkspace.load,'function');
const fire=(button)=>handlers.get('click')({target:{closest:()=>button}});
(async()=>{
 await window.HomeServerMaintenanceWorkspace.load({snapshot_complete:false});
 assert.ok(nodes.get('maintenanceWorkspace'));
 assert.equal(nodes.get('maintenanceWorkspace').dataset.snapshot,'incomplete');
 assert.match(headingDescription.textContent,/incomplete/);
 assert.equal(nodes.get('maintenanceNotifications').dataset.notificationCount,'1');
 assert.ok(!nodes.get('maintenanceNotifications').innerHTML.includes('<img src=x>'),'Untrusted notification is escaped');
 assert.ok(nodes.get('maintenanceNotifications').innerHTML.includes('&lt;img src=x&gt;'));
 assert.equal(nodes.get('maintenancePending').dataset.count,'1');
 assert.equal(nodes.get('maintenanceHistory').dataset.count,'1');
 assert.equal(requests.length,2,'No writes or full health re-probe on workspace reads');
 const incident={dataset:{maintenanceChat:'app:vp3.notes:degraded'},hasAttribute:name=>name==='data-maintenance-chat'};
 fire(incident);
 assert.equal(chatClicks,1);
 assert.ok(input.value.includes('app:vp3.notes:degraded'));
 assert.ok(input.focused);
 const notification={dataset:{maintenanceNotification:'42'},hasAttribute:name=>name==='data-maintenance-notification'};
 fire(notification);
 assert.equal(chatClicks,2);
 assert.ok(input.value.includes('notification:42'));
 const approvalsBtn={dataset:{maintenanceGo:'approvals'}};
 fire(approvalsBtn);assert.equal(approvalsClicks,1);
 const activityBtn={dataset:{maintenanceGo:'activity'}};
 fire(activityBtn);assert.equal(activityClicks,1);
 await window.HomeServerMaintenanceWorkspace.load({snapshot_complete:true});
 assert.equal(nodes.get('maintenanceWorkspace').dataset.snapshot,'complete');
 console.log('Section 31D workspace, escaping, snapshot, pending/history and canonical Chat handoff PASS');
})().catch(err=>{console.error(err);process.exitCode=1;});
