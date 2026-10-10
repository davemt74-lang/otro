import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {chromium} from 'playwright';
const native=true;
const source=await fs.readFile(new URL(native?'../ui/agent-browser-workspaces.js':'../chat-agent-teams-a3.js',import.meta.url),'utf8');
const browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{})});
const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
try{
 await page.setContent('<div id="chatComposerShell"><form id="chatForm"></form></div>');
 await page.evaluate(native=>{
  window.VP3_AGENT_TEAMS_A3={endpoint:'/api/agent-missions-cloud-v1.php',csrf:'fixture'};
  window.confirm=()=>false;window.calls=[];let serial=0;
  Object.defineProperty(window,'crypto',{value:{randomUUID:()=> '00000000-0000-4000-8000-'+String(++serial).padStart(12,'0')}});
  window.change={id:'33333333-3333-4333-8333-333333333333',task_id:'22222222-2222-4222-8222-222222222222',action_key:'workspace.update',arguments:{fields:{display_name:'Reviewed contact'}},payload_hash:'a'.repeat(64),status:'executed',outcome_state:'queued',outcome_message:'Waiting for Cloud delivery',can_recover:native,destination:'Cloud',execution_tool_run_id:42,checked_at:'2030-01-01 00:00:02'};
  window.mission={id:'11111111-1111-4111-8111-111111111111',objective:'Verify saved changes',status:'completed',tools_enabled:true,authority_current:true,tasks:[{id:change.task_id,title:'Contact specialist',role:'editor',status:'completed'}],action_summaries:[change],completion_report:{state:'awaiting_delivery',summary:'0 of 1 changes verified; 1 waiting; <img src=x onerror=alert(1)>',execution_verified:false}};
  window.failOnce=true;
  window.fetch=async(url,opts)=>{
   const request=JSON.parse(opts.body);calls.push(request);
   if(request.csrf_token!==(native?'owner':'fixture'))throw Error('Missing source CSRF');
   const ok=payload=>({ok:true,json:async()=>({ok:true,...payload})});
   if(request.action==='list')return ok({items:[mission]});
   if(request.action==='decisions')return ok({items:[]});
   if(request.action==='actions.list')return ok({actions:[change]});
   if(request.action==='actions.recover'){
    if(!native||request.expected_hash!==change.payload_hash||request.action_id!==change.id||request.confirmed!==true)throw Error('Recovery must match exact approved change');
    if(failOnce){failOnce=false;return {ok:false,status:503,json:async()=>({ok:false,error:'Recovery interrupted'})};}
    return ok({actions:[change]});
   }
   return ok({mission});
  };
 },native);
 await page.addScriptTag({content:source});
 await page.locator('[data-agent-teams-a3]>summary').click();
 await page.getByRole('button',{name:'View',exact:true}).click();
 const report=page.locator('.vp3-teams-detail .vp3-specialist-completion');await report.waitFor();
 assert.match(await report.textContent(),/awaiting_delivery.*0 of 1 changes verified/);
 assert.match(await report.textContent(),/separately from model-generated findings/);
 assert.equal(await report.locator('img').count(),0,'Completion report must remain inert text');
 if(native){
  await page.getByRole('button',{name:'Retry Cloud delivery',exact:true}).click();
  assert.equal(await page.evaluate(()=>calls.filter(c=>c.action==='actions.recover').length),0);
  await page.evaluate(()=>window.confirm=()=>true);
  await page.getByRole('button',{name:'Retry Cloud delivery',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('.vp3-teams-status').textContent==='Recovery interrupted');
  await page.getByRole('button',{name:'Retry Cloud delivery',exact:true}).click();
  await page.waitForFunction(()=>calls.filter(c=>c.action==='actions.recover').length===2);
  const calls=await page.evaluate(()=>window.calls.filter(c=>c.action==='actions.recover'));
  assert.equal(calls[0].request_id,calls[1].request_id,'Lost responses retain one durable recovery intent');
 }else assert.equal(await page.getByRole('button',{name:'Retry Cloud delivery',exact:true}).count(),0);
 for(const state of ['applied','verified','superseded','missing','blocked','failed']){
  await page.evaluate(state=>{
   change={...change,outcome_state:state,can_recover:false,verified_at:state==='verified'?'2030-01-01 00:00:04':null};
   mission.action_summaries=[change];mission.completion_report={state:state==='verified'?'complete':state==='applied'?'awaiting_delivery':'attention',summary:state==='verified'?'1 of 1 changes verified':'0 of 1 changes verified',execution_verified:state==='verified'};
  },state);
  await page.getByRole('button',{name:'Refresh',exact:true}).click();
  await page.waitForFunction(state=>document.querySelector('.vp3-specialist-change small')?.textContent.startsWith(state+' ·'),state);
  assert.equal(await page.getByRole('button',{name:'Retry Cloud delivery',exact:true}).count(),0);
  assert.match(await report.textContent(),state==='verified'?/1 of 1 changes verified/:/0 of 1 changes verified/);
 }
 await page.evaluate(native=>{
  const drawer=document.createElement('aside');drawer.id=native?'chatBrainDrawer':'chatNotificationDrawer';drawer.innerHTML='<button data-notification-tab="brain" class="active">Brain</button><div data-notification-drawer-body></div>';document.body.appendChild(drawer);
  VP3_AGENT_TEAMS_A3_BRAIN(drawer.querySelector('[data-notification-drawer-body]'));
 },native);
 const brain=page.locator('[data-agent-teams-brain-a3]');
 assert.match(await brain.textContent(),/workspace.update · failed/);
 assert.match(await brain.textContent(),/Lead agent completion report/);
 assert.equal(await brain.locator('.vp3-brain-worker .vp3-teams-meta').filter({hasText:'workspace.update'}).count(),1);
 await page.evaluate(()=>{mission.authority_current=false;mission.completion_report=null;mission.action_summaries=[];});
 await page.getByRole('button',{name:'Refresh',exact:true}).click();
 await page.waitForFunction(()=>!document.querySelector('.vp3-teams-detail .vp3-specialist-completion'));
 assert.equal(await page.locator('.vp3-specialist-changes').count(),0);
 assert.deepEqual(errors,[]);
 console.log('A5C4 browser PASS: '+(native?'HomeServer':'Cloud')+' truthful completion states, safe report text, Chat/Brain timestamps, recovery consent and retained request IDs, authority hiding');
}finally{await browser.close();}
