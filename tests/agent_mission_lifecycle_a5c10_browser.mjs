import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {chromium} from 'playwright';

const native=true;
const script=await fs.readFile(new URL('../ui/agent-browser-workspaces.js',import.meta.url),'utf8');
const css=await fs.readFile(new URL('../ui/agent-browser-workspaces.css',import.meta.url),'utf8');
const browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{})});
const page=await browser.newPage({viewport:{width:1100,height:800}}),calls=[],errors=[];
page.on('pageerror',e=>errors.push(e.message));page.setDefaultTimeout(10000);
const mid='11111111-1111-4111-8111-111111111111',tid='22222222-2222-4222-8222-222222222222',rid='33333333-3333-4333-8333-333333333333';
const secret='Private lifecycle <img src=x onerror=alert(1)>';
let mission={id:mid,objective:secret,status:'completed',tools_enabled:false,authority_current:true,
 progress:{state:'awaiting_delivery',completed_workers:1,total_workers:1},
 completion_report:{state:'awaiting_delivery',summary:'1 change waiting for delivery.',next_action:'Wait for delivery and refresh its receipt.'},
 tasks:[{id:tid,title:'Recorded review',status:'completed',role:'reviewer'}]};
let notices={items:[{id:4,mission_id:mid,title:secret,created_at:'2030-01-01 00:00:00'}],unread:1};
let gate=null,release,feeds=0,denyRead=false;
const html='<div id="chatComposerShell"><form id="chatForm"><textarea id="chatInput"></textarea><button type="submit">Send</button></form></div><div id="chatMessages"></div><div id="conversationList"></div>';
await page.route('http://a5c10.test/',r=>r.fulfill({contentType:'text/html',body:html}));
await page.route('**/api/**',async r=>{
 const b=r.request().method()==='POST'?r.request().postDataJSON():{};calls.push(b);
 const ok=x=>r.fulfill({json:{ok:true,...x}});
 if(b.action==='progress.feed'){
  const response={items:[structuredClone(mission)],notifications:structuredClone(notices)};
  feeds++;if(gate)await gate;return ok(response);
 }
 if(b.action==='notifications.read')return denyRead?r.fulfill({status:403,json:{ok:false,error:'Current permission revoked'}}):ok({notifications:{items:[],unread:0}});
 if(b.action==='list')return ok({items:[mission]});
 if(b.action==='get')return ok({mission});
 if(b.action==='schedule.list')return ok({schedules:[{id:rid,objective:secret,status:'paused',frequency:'daily',hour:9,minute:0,timezone:'UTC'}]});
 if(b.action==='task.route')return ok({route:'followup',conversation_id:native?'a'.repeat(32):undefined,followup:{request_id:rid,operation:'cancel',status:'proposed',mission_id:mid,mission_objective:secret,expected_hash:'a'.repeat(64),expires_at:'2099-01-01',completed_workers:1}});
 if(b.action==='decisions')return ok({items:[]});
 return ok({items:[]});
});
async function poll(state){
 mission={...mission,progress:{...mission.progress,state},completion_report:{...mission.completion_report,state,next_action:state==='attention'?'Review changes that could not be verified.':'Wait for delivery and refresh its receipt.'}};
 const before=feeds;await page.evaluate(()=>window.dispatchEvent(new Event('online')));
 await page.waitForFunction(expected=>document.querySelector('.vp3-mission-live-card')?.dataset.progressState===expected,state);
 assert.equal(feeds,before+1);
}
try{
 await page.goto('http://a5c10.test/');
 await page.evaluate(()=>{
  window.VP3_AGENT_TEAMS_A3={endpoint:'/api/missions',csrf:'lifecycle-fixture'};
  Object.defineProperty(window.crypto,'randomUUID',{configurable:true,value:()=> '44444444-4444-4444-8444-444444444444'});
  window.STONEFELLOW_CHAT_CONTINUITY={conversationId:()=>91};
  window.HomeServerAgentRouting={getActiveConversationId:()=> 'a'.repeat(32),getSelectedAgentId:()=>7};
  window.confirm=()=>false;
 });
 await page.addStyleTag({content:css});await page.addScriptTag({content:script});
 await page.waitForFunction(()=>document.querySelector('.vp3-mission-live-card')?.dataset.progressState==='awaiting_delivery');
 assert.match(await page.locator('.vp3-mission-live-summary').textContent(),/Awaiting saved-change delivery/);
 assert.equal(await page.locator('[data-agent-teams-a3]').getAttribute('open'),null);
 await poll('attention');assert.match(await page.locator('.vp3-mission-live-summary').textContent(),/Results need attention/);
 await page.locator('.vp3-mission-live-card').getByRole('button',{name:'Open mission'}).click();
 await page.waitForFunction(()=>document.querySelector('.vp3-completion-next-action')?.textContent.includes('Review changes'));
 assert.equal(await page.locator('.vp3-teams-detail img').count(),0);
 // Opened detail, schedule, command and persisted intent all contain the same private context.
 await page.waitForFunction(()=>!document.querySelector('.vp3-teams-create button').disabled);
 await page.locator('#chatInput').fill('Cancel the lifecycle review');
 const routeResponse=page.waitForResponse(r=>r.request().postDataJSON()?.action==='task.route');
 await page.locator('#chatForm button[type=submit]').click();await routeResponse;
 await page.locator('.vp3-mission-followup').waitFor({state:'visible'});
 await page.evaluate(native=>{
  const csrf=native?'owner':'lifecycle-fixture';
  sessionStorage.setItem('vp3.chat-task.pending.a5c7.'+csrf+'.automatic.test',JSON.stringify({objective:'Private lifecycle'}));
  sessionStorage.setItem('vp3.schedule.pending.a5c6.'+csrf+'.test',JSON.stringify({objective:'Private lifecycle'}));
  sessionStorage.setItem('unrelated.preference','keep');
 },native);
 // A successful response started before revocation cannot restore cleared state.
 await page.waitForFunction(()=>!document.querySelector('.vp3-teams-create button').disabled);
 gate=new Promise(resolve=>release=resolve);const before=feeds;
 await page.evaluate(()=>window.dispatchEvent(new Event('online')));
 // Route receipt is observable outside page JS; wait without depending on arbitrary test delays.
 const limit=Date.now()+10000;while(feeds===before){assert.ok(Date.now()<limit,'Progress request did not start');await new Promise(resolve=>setTimeout(resolve,10));}
 denyRead=true;
 await page.locator('.vp3-mission-notifications').getByRole('button',{name:'Mark read'}).click();
 await page.waitForFunction(()=>document.querySelector('.vp3-mission-followup').hidden&&!document.querySelector('.vp3-mission-live-card'));
 const response=page.waitForResponse(r=>r.request().postDataJSON()?.action==='progress.feed');release();gate=null;await response;
 await page.waitForFunction(()=>!document.body.textContent.includes('Private lifecycle'));
 assert.equal(await page.locator('.vp3-completion-next-action').count(),0);
 assert.equal(await page.evaluate(()=>Object.keys(sessionStorage).some(k=>k.startsWith('vp3.chat-task.pending.')||k.startsWith('vp3.schedule.pending.')||k.startsWith('vp3.chat-followup.'))),false);
 assert.equal(await page.evaluate(()=>sessionStorage.getItem('unrelated.preference')),'keep');
 assert.equal(calls.filter(b=>['start','resume','retry','tools.start','actions.review','task.followup.decide'].includes(b.action)).length,0,'Read/visibility recovery cannot run work');
 await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 assert.deepEqual(errors,[]);
 console.log('A5C10 browser PASS: '+(native?'HomeServer':'Cloud')+' completed research with pending delivery/attention remains on canvas, owner next action, safe text/mobile width, all-surface revocation and stale response fencing');
}finally{if(release)release();await browser.close();}
