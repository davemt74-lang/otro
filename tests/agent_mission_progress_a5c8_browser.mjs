import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {chromium} from 'playwright';
const native=true;
const script=await fs.readFile(new URL('../ui/agent-browser-workspaces.js',import.meta.url),'utf8');
const browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{})});
const page=await browser.newPage({viewport:{width:1280,height:800}}),calls=[],errors=[];page.on('pageerror',e=>errors.push(e.message));
const mid='11111111-1111-4111-8111-111111111111',tid='22222222-2222-4222-8222-222222222222';
const bad='<img src=x onerror=alert(1)>';
let mission={id:mid,objective:'Review '+bad,status:'running',tools_enabled:false,authority_current:true,progress:{state:'running',checked_at:'2030-01-01T00:00:00Z',completed_workers:0,total_workers:1},tasks:[{id:tid,title:'Researcher '+bad,role:'researcher',status:'running',activity:{stage:'Choosing a read',last_activity_at:'2030-01-01 00:00:00'}}]};
let notices={items:[{id:4,mission_id:mid,title:'Review required '+bad,level:'action_required',created_at:'2030-01-01 00:00:00',read_at:null}],unread:1};
let disconnected=false,blocked=false,release=null,gate=null,feeds=0;
const html='<div class="topbar"><div class="top-actions"></div></div><div class="chat-topbar-actions"><div id="chatNotificationMenu"><button id="chatNotificationButton">Notifications</button></div><div id="chatProfileMenu"></div></div><div id="chatComposerShell"><form id="chatForm"><textarea id="chatInput"></textarea><button type="submit">Send</button></form></div><div id="chatMessages"></div><div id="conversationList"></div>';
await page.route('http://a5c8.test/',r=>r.fulfill({contentType:'text/html',body:html}));
await page.route('**/api/**',async r=>{
 const body=r.request().method()==='POST'?r.request().postDataJSON():{};calls.push(body);const ok=x=>r.fulfill({json:{ok:true,...x}});
 if(r.request().url().includes('notifications-fixture'))return ok({notifications:{unread:2,items:[]},brain:{},history:{items:[]}});
 if(body.action==='progress.feed'){
  feeds++;if(gate)await gate;
  if(blocked)return r.fulfill({status:403,json:{ok:false,error:'Permission revoked'}});
  if(disconnected)return r.fulfill({status:503,json:{ok:false,error:'Offline'}});
  return ok({items:[mission],notifications:notices});
 }
 if(body.action==='notifications.read'){assert.equal(body.notice_id,4);notices={items:[{...notices.items[0],read_at:'2030-01-01 00:01:00'}],unread:0};return ok({notifications:notices});}
 if(body.action==='get')return ok({mission});
 if(body.action==='list')return ok({items:[mission]});
 if(body.action==='schedule.list')return ok({schedules:[]});
 if(body.action==='decisions')return ok({items:[]});
 return ok({items:[]});
});
try{
 await page.goto('http://a5c8.test/');
 await page.evaluate(()=>{window.VP3_AGENT_TEAMS_A3={endpoint:'/api/missions',csrf:'fixture'};window.STONEFELLOW_CHAT_CONTINUITY={conversationId:()=>77};window.HomeServerAgentRouting={getActiveConversationId:()=> 'a'.repeat(32),getSelectedAgentId:()=>7};window.STONEFELLOW_NOTIFICATION_DRAWER={endpoint:'/api/notifications-fixture',csrf:'fixture'};window.VP3_COGNITIVE_PRESENTATION_V510={ownsAttention:true,ownsBrainPresentation:true};window.confirm=()=>false;});
 await page.addStyleTag({content:await fs.readFile(new URL('../ui/agent-browser-workspaces.css',import.meta.url),'utf8')});
 await page.addScriptTag({content:script});
 await page.waitForFunction(()=>document.querySelector('.vp3-mission-live-card')?.dataset.progressState==='running');
 assert.match(await page.locator('.vp3-mission-live-summary').textContent(),/Working.*Last activity/s);
 assert.equal(await page.locator('.vp3-mission-live-summary img').count(),0);
 assert.equal(await page.locator('[data-agent-teams-a3]').getAttribute('open'),null,'Updates work with missions collapsed');
 const drawer=await fs.readFile(new URL(native?'../ui/agent-brain-drawer.js':'../chat-notifications-drawer-v240.js',import.meta.url),'utf8');await page.addScriptTag({content:drawer});
 await page.evaluate(()=>window.dispatchEvent(new Event('online')));
 if(!native){
  await page.waitForFunction(()=>document.querySelector('#chatNotificationButton > span')?.textContent==='3');
  await page.locator('#chatNotificationButton').click();
  assert.match(await page.locator('#chatNotificationDrawer').textContent(),/Mission notifications/);
  await page.locator('#chatNotificationDrawer').getByRole('button',{name:'Mark read',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('#chatNotificationButton > span')?.textContent==='2');
 }else{
  await page.waitForFunction(()=>document.querySelector('#agentBrainMissionCount')?.textContent==='1 mission updates');
  await page.locator('#agentBrainDrawerToggle').click();
  await page.locator('#agentBrainMissionNotifications').getByRole('button',{name:'Mark read',exact:true}).click();
  await page.waitForFunction(()=>!document.querySelector('#agentBrainMissionCount')?.textContent);
 }
 assert.equal(calls.filter(c=>c.action==='notifications.read').length,1,'One exact read, even with root click handlers');
 assert.equal(calls.filter(c=>['start','resume','retry','tools.start','actions.review'].includes(c.action)).length,0,'Progress and read controls cannot execute work');
 disconnected=true;await page.evaluate(()=>window.dispatchEvent(new Event('online')));
 await page.waitForFunction(()=>document.querySelector('.vp3-mission-live-card')?.dataset.progressState==='disconnected');
 assert.match(await page.locator('.vp3-mission-live-summary').textContent(),/last known status/i);
 disconnected=false;mission={...mission,status:'waiting_review',progress:{...mission.progress,state:'stalled'}};
 await page.evaluate(()=>window.dispatchEvent(new Event('online')));await page.waitForFunction(()=>document.querySelector('.vp3-mission-live-card')?.dataset.progressState==='stalled');
 // Overlapping foreground refreshes share one request, and never restart work.
 gate=new Promise(resolve=>release=resolve);const before=feeds;await page.evaluate(()=>{window.dispatchEvent(new Event('online'));window.dispatchEvent(new Event('online'));});await page.waitForTimeout(50);assert.equal(feeds,before+1);release();gate=null;await page.waitForTimeout(100);
 await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth),'Mobile canvas must fit');
 blocked=true;await page.evaluate(()=>window.dispatchEvent(new Event('online')));await page.waitForFunction(()=>!document.querySelector('.vp3-mission-live-card'));
 assert.deepEqual(errors,[]);console.log('A5C8 browser PASS: '+(native?'HomeServer':'Cloud')+' collapsed-canvas progress, existing drawer/badge, exact read, safe text, no dispatch, disconnect/reconnect/stall, overlapping polls, revocation and mobile fit');
}finally{await browser.close();}
