import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {chromium} from 'playwright';
const native=true;
const source=await fs.readFile(new URL('../ui/agent-browser-workspaces.js',import.meta.url),'utf8');
const browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{})});
const page=await browser.newPage(),calls=[],errors=[];page.on('pageerror',e=>errors.push(e.message));
const mid='11111111-1111-4111-8111-111111111111',tid='22222222-2222-4222-8222-222222222222',cid='a'.repeat(32);
const draft={max_parallel:1,assignments:[{task_id:tid,tools:['contacts.search'],max_calls:1,actions:[],max_actions:0,output:'analysis',browser_url:''}]};
const reason='Investigation <img src=x onerror=alert(1)> benefits from specialists';
const mission={id:mid,objective:'Review contacts',status:'planned',tools_enabled:false,tools_configured:false,authority_current:true,tasks:[{id:tid,title:'Contact specialist',role:'investigator',status:'queued'}],chat_task:{draft,provider_key:'openai',model:'fixture',prepared_at:'2030-01-01 00:00:00',automatic_reason:reason,decided_at:'2030-01-01 00:00:01'}};
let failure=false,invalid=false,routeGate=null,toolsGate=null;
const html='<div id="chatComposerShell"><form id="chatForm"><textarea id="chatInput"></textarea><button type="submit">Send</button></form></div><div id="chatMessages"></div><div id="conversationList"></div>';
await page.route('http://a5c7.test/',r=>r.fulfill({contentType:'text/html',body:html}));
await page.route('**/api/**',async r=>{
 const b=r.request().postDataJSON()||{};calls.push(b);const ok=value=>r.fulfill({json:{ok:true,...value}});
 if(b.action==='task.route'){
  if(routeGate)await routeGate;
  if(failure){failure=false;return r.fulfill({status:503,json:{ok:false,error:'Lost routing response'}});}
  if(invalid)return ok({route:'bad'});
  return b.objective==='Review contacts'?ok({route:'specialists',reason,mission,conversation_id:native?cid:undefined}):ok({route:'chat',reason:'Direct answer',conversation_id:native?cid:undefined});
 }
 if(b.action==='task.prepare')return ok({mission});
 if(b.action==='tools.get'){if(toolsGate)await toolsGate;return ok({tools:{revision:0,configured:false,active:false,capabilities:['contacts.search'],action_capabilities:[],max_calls_per_worker:3,max_actions_per_worker:3,assignments:{}}});}
 if(b.action==='list')return ok({items:[]});
 if(b.action==='schedule.list')return ok({schedules:[]});
 if(b.action==='decisions')return ok({items:[]});
 return ok({items:[]});
});
async function boot(realBrain=false){
 await page.goto('http://a5c7.test/');
 await page.evaluate(({native,cid})=>{
  window.VP3_AGENT_TEAMS_A3={endpoint:'/api/agent-missions-cloud-v1.php',csrf:'fixture'};
  window.currentContext=native?cid:77;window.currentAgent=7;
  window.STONEFELLOW_CHAT_CONTINUITY={conversationId:()=>window.currentContext};
  window.HomeServerAgentRouting={getActiveConversationId:()=>window.currentContext,getSelectedAgentId:()=>window.currentAgent};
  Object.defineProperty(window.crypto,'randomUUID',{value:()=> '44444444-4444-4444-8444-'+String(Math.floor(Math.random()*1e12)).padStart(12,'0')});window.confirm=()=>false;window.turns=[];window.addEventListener('homeserver:chat-turn',e=>window.turns.push(e.detail));
 },{native,cid});
 await page.addScriptTag({content:source});
 if(realBrain){
  const brain=await fs.readFile(new URL('../ui/brain.js',import.meta.url),'utf8');await page.addScriptTag({content:brain});
 }else await page.evaluate(()=>{window.ordinary=0;document.querySelector('#chatForm').addEventListener('submit',e=>{e.preventDefault();window.ordinary++;});});
}
function commandCalls(){return calls.filter(c=>['task.route','task.prepare','tools.configure','tools.start','actions.review','task.followup.decide','start','resume','retry'].includes(c.action)).length;}
async function send(text){await page.locator('#chatInput').fill(text);await page.getByRole('button',{name:'Send',exact:true}).click();}
async function settled(){await page.waitForFunction(()=>!document.querySelector('#chatInput').disabled);}
try{
 await boot();assert.equal(await page.locator('#vp3ChatTaskMode').inputValue(),'automatic');
 await send('Hello');await page.waitForFunction(()=>ordinary===1);assert.equal(calls.filter(c=>c.action==='task.route').length,1);
 if(native)assert.equal(await page.evaluate(()=>turns.length),0,'Normal chat retains real chat event ownership');
 await send('Review contacts');await settled();assert.equal(await page.evaluate(()=>ordinary),1);
 assert.equal(calls.filter(c=>['start','tools.start','tools.configure','actions.review'].includes(c.action)).length,0);
 assert.equal(await page.locator('.vp3-teams-detail img').count(),0);assert.match(await page.locator('.vp3-teams-detail').textContent(),/Lead chose specialists/);
 await page.evaluate(()=>{const brain=document.createElement('div');document.body.appendChild(brain);VP3_AGENT_TEAMS_A3_BRAIN(brain);});
 assert.match(await page.locator('[data-agent-teams-brain-a3]').textContent(),/Investigation <img/);
 if(native)assert.deepEqual(await page.evaluate(()=>turns.map(t=>t.status)),['started','completed']);
 await page.locator('#vp3ChatTaskMode').selectOption('chat');const before=commandCalls();await send('Chat only');assert.equal(commandCalls(),before);assert.equal(await page.evaluate(()=>ordinary),2);
 await page.locator('#vp3ChatTaskMode').selectOption('automatic');await send('x'.repeat(4001));assert.equal(commandCalls(),before);assert.equal(await page.evaluate(()=>ordinary),3);
 failure=true;await send('Retry request');await settled();const failed=calls.filter(c=>c.action==='task.route').at(-1);assert.match(await page.locator('.vp3-teams-status').textContent(),/Lost routing response/);
 await boot();await send('Retry request');await settled();assert.equal(calls.filter(c=>c.action==='task.route').at(-1).request_id,failed.request_id,'Reload repeats persisted intent');
 invalid=true;await send('Invalid');await settled();assert.equal(await page.evaluate(()=>ordinary),1);invalid=false;
 // Pending routing locks the composer, and a context switch cannot dispatch chat elsewhere.
 let release;routeGate=new Promise(resolve=>release=resolve);await send('Context switch');assert.ok(await page.locator('#chatInput').isDisabled());assert.ok(await page.locator('#vp3ChatTaskMode').isDisabled());
 await page.evaluate(()=>window.currentContext=window.currentContext===77?78:'b'.repeat(32));release();await settled();routeGate=null;assert.equal(await page.evaluate(()=>ordinary),1);assert.match(await page.locator('.vp3-teams-status').textContent(),/Conversation or lead changed/);
 // A second context fence follows the capability read before painting a plan.
 await boot();toolsGate=new Promise(resolve=>release=resolve);await send('Review contacts');await page.waitForTimeout(100);await page.evaluate(()=>window.currentAgent=8);release();await settled();toolsGate=null;assert.equal(await page.locator('.vp3-teams-detail:visible').count(),0);
 await boot();await page.evaluate(()=>{Storage.prototype.setItem=()=>{throw new Error('blocked');};});const storedCalls=commandCalls();await send('Storage blocked');assert.equal(commandCalls(),storedCalls);assert.match(await page.locator('.vp3-teams-status').textContent(),/storage is unavailable/);
 if(native){
  // Production brain adopts the prepared new-chat ID and makes exactly one real send.
  await page.route('**/api/v1/control/chat',async r=>{const b=r.request().postDataJSON();calls.push({realChat:b});assert.equal(b.conversation_id,cid);return r.fulfill({json:{conversation_id:cid,reply:'Hello from real brain'}});});
  await boot(true);await page.evaluate(()=>window.currentContext=null);const count=calls.length;await send('Hello');await settled();
  assert.equal(calls.slice(count).filter(c=>c.realChat).length,1);assert.deepEqual(await page.evaluate(()=>turns.map(t=>t.status)),['started','completed']);
 }
 assert.deepEqual(errors,[]);console.log('A5C7 browser PASS: '+(native?'HomeServer':'Cloud')+' automatic/direct/specialist routing, unchanged approval gates, safe reasons, durable retry, context fences, storage and turn ownership');
}finally{await browser.close();}
