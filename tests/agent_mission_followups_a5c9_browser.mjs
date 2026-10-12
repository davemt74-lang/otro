import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {chromium} from 'playwright';
const native=true;
const script=await fs.readFile(new URL('../ui/agent-browser-workspaces.js',import.meta.url),'utf8');
const css=await fs.readFile(new URL('../ui/agent-browser-workspaces.css',import.meta.url),'utf8');
const browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{})});
const page=await browser.newPage({viewport:{width:1000,height:700}}),calls=[],errors=[];page.on('pageerror',e=>errors.push(e.message));
const rid='44444444-4444-4444-8444-444444444444',mid='11111111-1111-4111-8111-111111111111',second='22222222-2222-4222-8222-222222222222',tid='33333333-3333-4333-8333-333333333333';
const bad='<img src=x onerror=alert(1)>';
let review={request_id:rid,operation:'cancel',status:'choosing',choices:[{id:mid,objective:'Contact cleanup '+bad,status:'planned'},{id:second,objective:'Other contacts',status:'planned'}]},fail=false,revoked=false,gate=null,thread=77,lead=7;
let mission={id:mid,objective:'Contact cleanup '+bad,status:'waiting_review',authority_current:true,tools_enabled:false,tools_configured:false,tasks:[{id:tid,title:'Researcher',role:'investigator',status:'interrupted'}]};
const html='<div id="chatComposerShell"><form id="chatForm"><textarea id="chatInput"></textarea><button type="submit">Send</button></form></div><div id="chatMessages"></div><div id="conversationList"></div>';
await page.route('http://a5c9.test/',r=>r.fulfill({contentType:'text/html',body:html}));
await page.route('**/api/**',async r=>{
 const b=r.request().method()==='POST'?r.request().postDataJSON():{};calls.push(b);const ok=x=>r.fulfill({json:{ok:true,...x}});
 if(b.action==='task.route')return ok({route:'followup',followup:review,conversation_id:native?'a'.repeat(32):undefined});
 if(b.action==='task.followup.select'){
  assert.equal(b.mission_id,mid);review={...review,status:'proposed',mission_id:mid,expected_hash:'a'.repeat(64),mission_objective:mission.objective,completed_workers:1,interrupted_workers:1,expires_at:'2099-01-01 00:00:00',expired:false};return ok({followup:review});
 }
 if(b.action==='task.followup.decide'){
  if(gate)await gate;
  if(revoked)return r.fulfill({status:403,json:{ok:false,error:'Privacy revoked'}});
  if(fail){fail=false;return r.fulfill({status:503,json:{ok:false,error:'Lost command response'}});}
  assert.equal(b.request_id,rid);assert.equal(b.expected_hash,'a'.repeat(64));assert.equal(b.confirmed,true);
  review={...review,status:b.decision==='apply'?'applied':'dismissed',mission:b.decision==='apply'?mission:undefined,result_mission_id:mid};return ok({followup:review});
 }
 if(b.action==='task.followup.get')return ok({followup:review});
 if(b.action==='progress.feed')return ok({items:[],notifications:{items:[],unread:0}});
 if(b.action==='schedule.list')return ok({schedules:[]});
 if(b.action==='tools.get')return ok({tools:{revision:0,configured:false,active:false,capabilities:['contacts.search'],action_capabilities:[],max_calls_per_worker:3,max_actions_per_worker:3,assignments:{}}});
 if(b.action==='list'||b.action==='decisions')return ok({items:[]});
 return ok({items:[]});
});
async function boot(requestIds=true){
 await page.goto('http://a5c9.test/');await page.evaluate(requestIds=>{
  Object.defineProperty(window.crypto,'randomUUID',{configurable:true,value:requestIds?()=> '55555555-5555-4555-8555-'+String(Math.floor(Math.random()*1e12)).padStart(12,'0'):undefined});
  window.VP3_AGENT_TEAMS_A3={endpoint:'/api/missions',csrf:'fixture'};window.STONEFELLOW_CHAT_CONTINUITY={conversationId:()=>window.fixtureThread||77};window.HomeServerAgentRouting={getActiveConversationId:()=>window.fixtureCid||'a'.repeat(32),getSelectedAgentId:()=>window.fixtureLead||7};window.HomeServerChatTurn={isBusy:()=>false,usePreparedConversation:()=>true};window.confirm=()=>false;
 },requestIds);await page.addStyleTag({content:css});await page.addScriptTag({content:script});
}
async function submit(text){
 const before=calls.filter(c=>c.action==='task.route').length;
 await page.locator('#chatInput').fill(text);await page.locator('#chatForm button[type=submit]').click();
 // A previous review can already be visible. Wait for this composer turn to finish.
 await page.waitForFunction(()=>!document.querySelector('#chatInput').disabled&&document.querySelector('#chatInput').value===''&&document.querySelector('.vp3-teams-status').textContent==='Review the command on the chat canvas. No work changes until you confirm.');
 assert.equal(calls.filter(c=>c.action==='task.route').length,before+1,'This command must be routed exactly once');
 await page.locator('.vp3-mission-followup').waitFor({state:'visible'});
}
try{
 await boot(false);await page.locator('#chatInput').fill('Cancel contact cleanup');await page.locator('#chatForm button[type=submit]').click();
 await page.waitForFunction(()=>document.querySelector('.vp3-teams-status').textContent.includes('Secure request IDs are unavailable'));
 assert.equal(calls.filter(c=>c.action==='task.route'||c.action==='task.followup.decide').length,0,'Missing secure IDs must never issue commands');
 await boot();await submit('Cancel contact cleanup');
 assert.match(await page.locator('.vp3-mission-followup').textContent(),/Choose the mission/);
 assert.equal(calls.filter(c=>c.action==='task.followup.decide').length,0);
 assert.equal(await page.locator('.vp3-mission-followup img').count(),0);
 assert.equal(await page.locator('[data-agent-teams-a3]').getAttribute('open'),null,'Commands are reviewed on the canvas while teams remain collapsed');
 await page.locator('[data-followup-action=select]').first().click();await page.locator('[data-followup-action=apply]').waitFor({state:'visible'});
 fail=true;await page.locator('[data-followup-action=apply]').click();await page.waitForFunction(()=>document.querySelector('.vp3-teams-status').textContent.includes('Lost command response'));
 await page.locator('[data-followup-action=dismiss]').click();assert.equal(calls.filter(c=>c.action==='task.followup.decide').length,1,'Uncertain response cannot turn into another decision');
 await page.locator('[data-followup-action=apply]').click();await page.waitForFunction(()=>document.querySelector('.vp3-mission-followup').textContent.includes('Command applied'));
 const retries=calls.filter(c=>c.action==='task.followup.decide');assert.equal(retries.length,2);assert.deepEqual(retries[0],retries[1]);
 assert.equal(calls.filter(c=>['resume','start','tools.start','actions.review'].includes(c.action)).length,0);
 // A page reload restores the durable review without reissuing the decision.
 await boot();await page.waitForFunction(()=>document.querySelector('.vp3-mission-followup')?.textContent.includes('Command applied'));
 assert.equal(calls.filter(c=>c.action==='task.followup.decide').length,2);
 review={...review,operation:'resume',status:'proposed',expired:false,mission:undefined};await submit('Continue it');
 await page.locator('[data-followup-action=apply]').click();assert.equal(calls.filter(c=>c.action==='task.followup.decide').length,2);
 assert.match(await page.locator('.vp3-teams-status').textContent(),/authorize interrupted work/);
 await page.locator('[data-followup-replay]').check();await page.locator('[data-followup-action=apply]').click();await page.waitForFunction(()=>document.querySelector('.vp3-mission-followup').textContent.includes('Command applied'));
 assert.equal(calls.filter(c=>c.action==='task.followup.decide').at(-1).allow_reexecution,true);
 // Scope review shows exact assignments without configuring or launching them.
 const draft={max_parallel:1,assignments:[{task_id:tid,tools:['contacts.search'],max_calls:1,actions:[],max_actions:0,output:'analysis',browser_url:''}]};
 review={...review,operation:'scope',status:'proposed',objective:'Only missing email '+bad,plan:{tasks:[{title:'Remaining review',objective:'Inspect missing email',tools:['contacts.search'],actions:[]}]},mission:undefined};
 mission={...mission,id:second,status:'planned',chat_task:{draft,provider_key:'openai',model:'fixture'},continuity:{predecessor_id:mid}};
 await submit('Only missing email');assert.match(await page.locator('.vp3-mission-followup').textContent(),/fresh assignment review|fresh|linked plan needs/s);
 await page.setViewportSize({width:390,height:800});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1));
 assert.equal(await page.locator('.vp3-mission-followup img').count(),0);
 await page.locator('[data-followup-action=apply]').click();await page.waitForFunction(()=>document.querySelector('.vp3-teams-status').textContent.includes('Revised plan prepared'));
 assert.equal(calls.filter(c=>['tools.configure','tools.start','actions.review'].includes(c.action)).length,0);
 assert.match(await page.locator('.vp3-teams-detail').textContent(),/Continues a prior mission/);
 // Context changes fence an in-flight response and hide old review content.
 review={...review,operation:'cancel',status:'proposed',mission:undefined};await submit('Cancel it');
 let release;gate=new Promise(r=>release=r);await page.locator('[data-followup-action=apply]').click();
 await page.evaluate(()=>{window.fixtureCid='b'.repeat(32);window.fixtureThread=88;window.fixtureLead=8;});release();gate=null;
 await page.waitForFunction(()=>document.querySelector('.vp3-mission-followup').hidden);
 await page.evaluate(()=>{window.fixtureCid='a'.repeat(32);window.fixtureThread=77;window.fixtureLead=7;});
 review={...review,status:'proposed'};await submit('Cancel it');revoked=true;await page.locator('[data-followup-action=apply]').click();
 await page.waitForFunction(()=>document.querySelector('.vp3-mission-followup').hidden);
 assert.equal(await page.evaluate(key=>sessionStorage.getItem(key),'vp3.chat-followup.a5c9.'+(native?'owner':'fixture')),null);
 assert.deepEqual(errors,[]);
 console.log('A5C9 browser PASS: collapsed canvas commands, ambiguity, safe text/mobile width, exact uncertain retries, explicit replay review, refresh persistence, scope assignment review, conversation/lead and privacy fences');
}finally{await browser.close();}
