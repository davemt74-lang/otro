import assert from 'node:assert/strict';import fs from 'node:fs';import vm from 'node:vm';
const source=fs.readFileSync('ui/tracky/agent-eyes-shared-scene.js','utf8');
let id='chat-a',view='tracky',now=1000,handler,interval,confirm=true;const nodes={},timers=[],requests=[],listeners={};
function node(){return {hidden:false,value:'',checked:false,textContent:'',children:[],addEventListener:(e,fn)=>{},setAttribute(){},append(...children){for(const child of children){this.children.push(child);if(child.id)nodes[child.id]=child;}},insertAdjacentElement:(_,child)=>{nodes[child.id]=child;}};}
for(const key of ['trackySceneSharePanel','trackySceneShareEnable','trackySceneShareDisable','trackySceneShareStatus','contextUseAgentEyes','contextAgentEyesEvidence']){nodes[key]=node();nodes[key].addEventListener=(e,fn)=>nodes[key][e]=fn;}
nodes.contextUseAgentEyes.checked=true;
const document={readyState:'complete',visibilityState:'visible',getElementById:key=>key.startsWith('view-')?{classList:{contains:()=>key==='view-'+view}}:nodes[key],querySelector:()=>({dataset:{brainConversation:id}}),createElement:()=>{const n=node();n.addEventListener=(e,fn)=>n[e]=fn;return n;},addEventListener:(e,fn)=>listeners[e]=fn};
const response=data=>({ok:true,json:async()=>data}),status={enabled:false,delivery:'not_requested'};
const sandbox={document,window:{confirm:()=>confirm},Date:{now:()=>now},setInterval:fn=>interval=fn,setTimeout:(fn,ms)=>{timers.push({fn,ms});return timers.length;},clearTimeout(){},fetch:async(path,options)=>{requests.push({path,options});return handler?handler(path,options):response(status);}};
vm.runInNewContext(source,sandbox);const flush=()=>new Promise(r=>setImmediate(r));await flush();
assert.match(nodes.trackySceneShareStatus.textContent,/Sharing is off/);assert.ok(requests.every(r=>r.options.method==='GET'));
confirm=false;nodes.trackySceneShareEnable.click();await flush();assert.ok(requests.every(r=>r.options.method==='GET'));
confirm=true;handler=async(path,options)=>{assert.deepEqual(JSON.parse(options.body),{enabled:true,consent:true});return response({enabled:true,delivery:'pending'});};nodes.trackySceneShareEnable.click();await flush();assert.match(nodes.trackySceneShareStatus.textContent,/waiting for Cloud/);
handler=async()=>response({enabled:false,delivery:'pending',experience:{title:'Sharing off locally',guidance:'Cloud revocation is pending.'}});await interval();await flush();assert.match(nodes.trackySceneShareStatus.textContent,/Sharing off locally.*revocation is pending/);
handler=null;view='chat';const recent={state:'recent_observation',age_seconds:59,request_fingerprint:'a'.repeat(16),scene:{objects:['chair'],owner_corrections:[{object:'chair',present:false,conflicts_with_camera:true}]}};
sandbox.window.TrackySceneCorrections.render(recent,{conversationId:id,requestStarted:now});assert.equal(nodes.contextSceneCorrection.hidden,false);assert.match(nodes.contextSceneReportStatus.textContent,/differs from camera/);timers.at(-1).fn();assert.equal(nodes.contextSceneCorrection.hidden,true);
sandbox.window.TrackySceneCorrections.render(recent,{conversationId:id,requestStarted:now-2000});assert.equal(nodes.contextSceneCorrection.hidden,true);
sandbox.window.TrackySceneCorrections.render({...recent,age_seconds:1},{conversationId:id,requestStarted:now});nodes.contextSceneObject.value='chair';nodes.contextScenePresent.value='false';let resolve;
handler=(path,options)=>{assert.match(path,/chat-a\/agent-eyes-correction$/);assert.deepEqual(JSON.parse(options.body),{object_label:'chair',present:false,expected_fingerprint:'a'.repeat(16)});return new Promise(r=>resolve=r);};nodes.contextSceneReport.click();await flush();assert.equal(nodes.contextSceneCorrection.hidden,true);
id='chat-b';resolve(response({agent_eyes_context:recent}));await flush();assert.equal(nodes.contextSceneCorrection.hidden,true,'Response reached different conversation');
id='chat-a';handler=null;sandbox.window.TrackySceneCorrections.render({...recent,age_seconds:1},{conversationId:id,requestStarted:now});document.visibilityState='hidden';listeners.visibilitychange();assert.equal(nodes.contextSceneCorrection.hidden,true);
assert.ok(requests.every(r=>r.options.cache==='no-store'));assert.doesNotMatch(source,/innerHTML|getUserMedia|localStorage|sessionStorage/);
console.log('TRACKY_SHARED_SCENE_UI_V1G3D: off default, explicit sharing, observation-bound reports, expiry/transit, hidden view, late conversation response and safe text PASS');
