import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {chromium} from 'playwright';
const native=true;
const source=await fs.readFile(new URL('../ui/agent-browser-workspaces.js',import.meta.url),'utf8');
const browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{})});
const page=await browser.newPage();const errors=[],calls=[];page.on('pageerror',e=>errors.push(e.message));
let failOnce=true,contract={revision:0,configured:false,active:false,capabilities:['contacts.search'],action_capabilities:['contacts.update'],max_calls_per_worker:3,max_actions_per_worker:3,assignments:{}};
const tid='22222222-2222-4222-8222-222222222222',mid='11111111-1111-4111-8111-111111111111';
const draft={max_parallel:1,assignments:[{task_id:tid,tools:['contacts.search'],max_calls:1,actions:['contacts.update'],max_actions:1,output:'analysis',browser_url:''}]};
let change={id:'33333333-3333-4333-8333-333333333333',task_id:tid,action_key:'contacts.update',arguments:{organization:'Example Studios'},payload_hash:'a'.repeat(64),status:'pending',can_approve:true,outcome_state:'awaiting_review',destination:'HomeServer',created_at:'2030-01-01 00:00:00'};
let mission={id:mid,objective:'Review my contacts <img src=x onerror=alert(1)>',status:'planned',tools_enabled:false,tools_configured:false,authority_current:true,tasks:[{id:tid,title:'Contact specialist',role:'editor',status:'queued'}],chat_task:{draft,provider_key:'openai',model:'configured-model',prepared_at:'2030-01-01 00:00:00'}};
const html='<div id="chatComposerShell"><form id="chatForm"><textarea id="chatInput"></textarea><button type="submit">Send</button></form></div>';
await page.route('http://a5c5.test/',r=>r.fulfill({contentType:'text/html',body:html}));
await page.route('**/api/**',async route=>{
 const body=route.request().postDataJSON();calls.push(body);assert.equal(body.csrf_token,native?'owner':'fixture');
 const ok=value=>route.fulfill({json:{ok:true,...value}});
 if(body.action==='task.prepare'){
  assert.equal(body.objective,mission.objective);assert.ok(body.request_id);
  if(native){assert.equal(body.conversation_id,'active-conversation');assert.equal(body.parent_agent_id,7);}else assert.equal(body.thread_id,77);
  if(failOnce){failOnce=false;return route.fulfill({status:503,json:{ok:false,error:'Lost preparation response'}});}
  return ok({mission});
 }
 if(body.action==='tools.get')return ok({tools:contract});
 if(body.action==='tools.configure'){assert.deepEqual(body.assignments,draft);assert.equal(body.confirmed,true);assert.equal(body.expected_revision,0);contract={...contract,revision:1,configured:true,active:true,assignments:draft};mission={...mission,tools_configured:true};return ok({tools:contract});}
 if(body.action==='tools.start'){assert.equal(body.expected_revision,1);assert.equal(body.confirmed,true);mission={...mission,status:'completed',tools_enabled:true,tasks:[{...mission.tasks[0],status:'completed'}],action_summaries:[change],completion_report:{state:'awaiting_review',summary:'0 of 1 changes verified',execution_verified:false}};return ok({mission});}
 if(body.action==='actions.review'){assert.equal(body.action_id,change.id);assert.equal(body.expected_hash,change.payload_hash);assert.equal(body.confirmed,true);change={...change,status:'executed',can_approve:false,outcome_state:'verified',verified_at:'2030-01-01 00:00:05',checked_at:'2030-01-01 00:00:05'};mission={...mission,action_summaries:[change],completion_report:{state:'complete',summary:'1 of 1 changes verified',execution_verified:true}};return ok({actions:[change]});}
 if(body.action==='actions.list')return ok({actions:[change]});
 if(body.action==='list')return ok({items:[mission]});
 if(body.action==='decisions')return ok({items:[]});
 return ok({mission});
});
async function boot(){
 await page.goto('http://a5c5.test/');
 await page.evaluate(native=>{
  window.VP3_AGENT_TEAMS_A3={endpoint:'/api/agent-missions-cloud-v1.php',csrf:'fixture'};window.STONEFELLOW_CHAT={initialConversationId:999};
  window.STONEFELLOW_CHAT_CONTINUITY={conversationId:()=>77};
  window.HomeServerAgentRouting={getActiveConversationId:()=> 'active-conversation',getSelectedAgentId:()=>7};
  window.confirm=()=>false;
  Object.defineProperty(window.crypto,'randomUUID',{value:()=> '44444444-4444-4444-8444-'+String(Math.floor(Math.random()*1e12)).padStart(12,'0')});
 },native);
 await page.addScriptTag({content:source});
 await page.evaluate(()=>{window.ordinary=0;document.querySelector('#chatForm').addEventListener('submit',e=>{e.preventDefault();window.ordinary++;});});
}
try{
 await boot();
 await page.locator('#chatInput').fill('Ordinary message');await page.getByRole('button',{name:'Send',exact:true}).click();assert.equal(await page.evaluate(()=>ordinary),1);assert.equal(calls.length,0);
 await page.locator('#vp3ChatTaskMode').check();await page.locator('#chatInput').fill(mission.objective);await page.getByRole('button',{name:'Send',exact:true}).click();
 await page.waitForFunction(()=>document.querySelector('.vp3-teams-status').textContent.includes('Lost preparation response'));
 const first=calls.find(c=>c.action==='task.prepare');assert.ok(first);assert.equal(await page.evaluate(()=>ordinary),1);
 assert.equal(await page.locator('#chatInput').inputValue(),mission.objective);
 await boot();await page.locator('#vp3ChatTaskMode').check();await page.locator('#chatInput').fill(mission.objective);await page.getByRole('button',{name:'Send',exact:true}).click();
 await page.waitForFunction(()=>document.querySelector('.vp3-teams-status').textContent.startsWith('Plan prepared.'));
 const prepared=calls.filter(c=>c.action==='task.prepare');assert.equal(prepared.length,2);assert.equal(prepared[0].request_id,prepared[1].request_id,'Reload retains the exact task intent');
 assert.equal(await page.locator('.vp3-teams-detail img').count(),0);
 assert.equal(calls.filter(c=>['start','tools.start','tools.configure'].includes(c.action)).length,0);
 assert.equal(await page.getByRole('button',{name:'Start',exact:true}).count(),0);
 assert.ok(await page.locator('input[data-tool-field="tool"]').isChecked());assert.ok(await page.locator('input[data-tool-field="action"]').isChecked());
 await page.getByRole('button',{name:'Approve assignments',exact:true}).click();assert.equal(calls.filter(c=>c.action==='tools.configure').length,0);
 await page.evaluate(()=>window.confirm=()=>true);await page.getByRole('button',{name:'Approve assignments',exact:true}).click();
 await page.getByRole('button',{name:'Start',exact:true}).waitFor();await page.getByRole('button',{name:'Start',exact:true}).click();
 await page.getByRole('button',{name:'Approve this change',exact:true}).waitFor();assert.match(await page.locator('.vp3-specialist-completion').textContent(),/awaiting_review/);
 await page.getByRole('button',{name:'Approve this change',exact:true}).click();
 await page.waitForFunction(()=>document.querySelector('.vp3-specialist-completion').textContent.includes('1 of 1 changes verified'));
 assert.match(await page.locator('.vp3-specialist-change').textContent(),/Last verified/);
 await page.evaluate(()=>{const panel=document.createElement('div');document.body.appendChild(panel);VP3_AGENT_TEAMS_A3_BRAIN(panel);});
 assert.match(await page.locator('[data-agent-teams-brain-a3]').textContent(),/1 of 1 changes verified/);
 assert.equal(calls.filter(c=>c.action==='start').length,0);assert.deepEqual(errors,[]);
 console.log('A5C5 browser PASS: '+(native?'HomeServer':'Cloud')+' ordinary composer, selected conversation, durable reload retries, safe plan, separate assignment/change approvals and verified Chat/Brain completion');
}finally{await browser.close();}
