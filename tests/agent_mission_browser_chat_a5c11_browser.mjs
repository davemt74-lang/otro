import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {chromium} from 'playwright';
const native=true;
const script=await fs.readFile(new URL('../ui/agent-browser-workspaces.js',import.meta.url),'utf8');
const css=await fs.readFile(new URL('../ui/agent-browser-workspaces.css',import.meta.url),'utf8');
const browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{})});
const page=await browser.newPage({viewport:{width:1100,height:800}});page.setDefaultTimeout(10000);
const errors=[],calls=[];page.on('pageerror',e=>errors.push(e.message));
const mid='11111111-1111-4111-8111-111111111111',tid='22222222-2222-4222-8222-222222222222',cid='33333333-3333-4333-8333-333333333333';
const url='https://example.com/company',expires='2099-01-01 00:00:00';let blocked=false,starts=0;
const draft={max_parallel:1,assignments:[{task_id:tid,tools:['browser.read'],max_calls:1,actions:[],max_actions:0,output:'sources',browser_url:url}]};
let mission={id:mid,objective:'Research the approved company site',status:'planned',tools_enabled:false,tools_configured:false,authority_current:true,chat_task:{draft},progress:{state:'awaiting_review'},tasks:[{id:tid,title:'Website research',role:'researcher',status:'queued'}]};
let contract={configured:false,revision:0,active:false,capabilities:['browser.read'],action_capabilities:[],assignments:{},browser_ready:false,browser_requirements:[{task_id:tid,url,origin:'https://example.com',ready:false,status:'approval_required',expires_at:null}]};
const html='<div id="chatComposerShell"><form id="chatForm"><textarea id="chatInput"></textarea><button type="submit">Send</button></form></div><div id="chatMessages"></div><div id="conversationList"></div>';
await page.route('http://a5c11.test/',r=>r.fulfill({contentType:'text/html',body:html}));
await page.route('**/api/**',async r=>{
 const b=r.request().postDataJSON();calls.push(b);const ok=x=>r.fulfill({json:{ok:true,...x}});
 if(blocked)return r.fulfill({status:403,json:{ok:false,error:'Current permission revoked'}});
 if(b.action==='progress.feed')return ok({items:[mission],notifications:{items:[],unread:0}});
 if(b.action==='list')return ok({items:[mission]});
 if(b.action==='get')return ok({mission});
 if(b.action==='schedule.list')return ok({schedules:[]});
 if(b.action==='tools.get')return ok({tools:contract});
 if(b.action==='tools.configure'){assert.equal(b.assignments.assignments[0].browser_url,url);assert.equal(b.confirmed,true);mission.tools_configured=true;contract={...contract,configured:true,active:true,revision:1,assignments:draft};return ok({tools:contract});}
 if(b.action==='browser.grant'){assert.equal(b.url,url);contract={...contract,browser_ready:true,browser_requirements:[{...contract.browser_requirements[0],ready:true,status:'approved',expires_at:expires}]};return ok({browser:{status:'approved',approved_origin:'https://example.com',current_url:url,expires_at:expires}});}
 if(b.action==='tools.start'){assert.equal(contract.browser_ready,true);starts++;mission={...mission,status:'completed',tools_enabled:true,progress:{state:'completed'},completion_report:{state:'complete',summary:'Research completed.',next_action:'No owner action is required.'},tasks:[{...mission.tasks[0],status:'completed',result:JSON.stringify({kind:'sources',title:'Company facts',body:'Supplied company evidence.',citations:[cid]}),browser_sources:[{citation_id:cid,url,title:'Company <img src=x onerror=alert(1)>',captured_at:'2030-01-01 12:00:00'},{citation_id:'unsafe',url:'javascript:alert(1)',title:'Unsafe'}]}]};return ok({mission});}
 if(b.action==='diagnostics.get')return ok({diagnostics:{ready:false,checked_at:'2030-01-01T12:00:00Z',checks:[{key:'workspace_sync',status:'blocked',detail:'Data is waiting.',next_action:'Review sync status.'}]}});
 return ok({items:[],actions:[]});
});
try{
 await page.goto('http://a5c11.test/');await page.evaluate(()=>{window.VP3_AGENT_TEAMS_A3={endpoint:'/api/missions',csrf:'fixture'};window.STONEFELLOW_CHAT_CONTINUITY={conversationId:()=>91};window.HomeServerAgentRouting={getActiveConversationId:()=> 'a'.repeat(32),getSelectedAgentId:()=>7};Object.defineProperty(window.crypto,'randomUUID',{configurable:true,value:()=> '44444444-4444-4444-8444-444444444444'});window.confirm=()=>true;});
 await page.addStyleTag({content:css});await page.addScriptTag({content:script});
 await page.locator('[data-agent-teams-a3] summary').first().click();
 await page.locator('[data-action="get"]').first().click();
 await page.waitForFunction(()=>document.querySelector('[data-tool-field="browser_url"]')?.value==='https://example.com/company');
 assert.equal(await page.locator('input[data-browser-task]').inputValue(),url);
 await page.locator('[data-action="tools.configure"]').click();
 await page.waitForFunction(()=>document.querySelector('[data-action="tools.start"]')?.disabled===true);
 assert.match(await page.locator('.vp3-browser-requirement').textContent(),/approval required/);
 assert.equal(calls.filter(c=>c.action==='browser.grant').length,0);
 await page.waitForFunction(()=>document.querySelector('[data-action="get"]')?.disabled===false);
 await page.locator('details[data-browser-worker]>summary').click();
 await page.locator('[data-action="browser.grant"]').click();
 await page.waitForFunction(()=>document.querySelector('[data-action="tools.start"]')?.disabled===false);
 await page.locator('[data-action="tools.start"]').click();
 await page.waitForFunction(()=>document.querySelector('.vp3-browser-source a'));
 assert.equal(starts,1);assert.equal(await page.locator('.vp3-browser-source a').count(),1);assert.equal(await page.locator('.vp3-browser-source a').getAttribute('href'),url);
 assert.match(await page.locator('.vp3-browser-source').textContent(),new RegExp(cid));assert.equal(await page.locator('img[src="x"]').count(),0);
 await page.locator('[data-action="diagnostics.get"]').click();await page.waitForFunction(()=>document.querySelector('.vp3-integration-diagnostic')?.hidden===false);
 assert.match(await page.locator('.vp3-integration-diagnostic').textContent(),/Review sync status/);
 await page.setViewportSize({width:390,height:844});
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+2));
 blocked=true;await page.locator('[data-action="get"]').first().click();await page.waitForFunction(()=>!document.querySelector('.vp3-browser-source'));
 assert.equal(await page.locator('.vp3-integration-diagnostic').getAttribute('hidden'),'');assert.deepEqual(errors,[]);
 console.log('PASS: Chat browser proposal prefill, separate origin grant, blocked Start until approved, source citations/safe links, mobile and permission-cleared diagnostics.');
}finally{await browser.close();}
