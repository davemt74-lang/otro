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
  window.mission={id:'11111111-1111-4111-8111-111111111111',objective:'Prepare a document',status:'completed',tools_enabled:true,authority_current:true,tasks:[{id:'22222222-2222-4222-8222-222222222222',title:'Document specialist',role:'editor',status:'completed'}]};
  window.change={id:'33333333-3333-4333-8333-333333333333',task_id:mission.tasks[0].id,action_key:'knowledge.create',arguments:{title:'Review <img src=x onerror=alert(1)>',content:'Exact document content'},payload_hash:'a'.repeat(64),status:'pending',can_approve:true,destination:'HomeServer',created_at:'2030-01-01 00:00:00'};
  window.failOnce=true;
  window.fetch=async(url,opts)=>{
   const request=JSON.parse(opts.body);calls.push(request);
   assertCsrf(request);const ok=payload=>({ok:true,json:async()=>({ok:true,...payload})});
   if(request.action==='list')return ok({items:[mission]});
   if(request.action==='decisions')return ok({items:[]});
   if(request.action==='actions.list')return ok({actions:[change]});
   if(request.action==='actions.review'){
    if(request.expected_hash!==change.payload_hash||request.action_id!==change.id||request.decision!=='approve'||request.confirmed!==true)throw Error('Review was not bound to the exact preview');
    if(failOnce){failOnce=false;return {ok:false,status:503,json:async()=>({ok:false,error:'Review interrupted'})};}
    change={...change,status:'executed',can_approve:false,execution_tool_run_id:42,executed_at:'2030-01-01 00:00:02'};
    mission.action_summaries=[{id:change.id,task_id:change.task_id,action_key:change.action_key,status:'executed',created_at:change.created_at,executed_at:change.executed_at}];
    return ok({actions:[change]});
   }
   return ok({mission});
  };
  function assertCsrf(request){if(request.csrf_token!==(native?'owner':'fixture'))throw Error('Missing source CSRF');}
 },native);
 await page.addScriptTag({content:source});
 await page.locator('[data-agent-teams-a3]>summary').click();
 await page.getByRole('button',{name:'View',exact:true}).click();
 const panel=page.locator('.vp3-specialist-changes');await panel.waitFor();
 assert.match(await panel.textContent(),/Exact document content/);
 assert.equal(await panel.locator('img').count(),0,'Model content must remain inert text');
 await page.getByRole('button',{name:'Approve this change',exact:true}).click();
 assert.equal(await page.evaluate(()=>calls.filter(c=>c.action==='actions.review').length),0,'Cancelled review must not submit');
 await page.evaluate(()=>window.confirm=()=>true);
 await page.getByRole('button',{name:'Approve this change',exact:true}).click();
 await page.waitForFunction(()=>document.querySelector('.vp3-teams-status').textContent==='Review interrupted');
 await page.getByRole('button',{name:'Approve this change',exact:true}).click();
 await page.waitForFunction(()=>change.status==='executed');
 await page.waitForFunction(()=>document.querySelector('.vp3-specialist-changes').textContent.includes('Execution receipt 42'));
 const reviews=await page.evaluate(()=>calls.filter(c=>c.action==='actions.review'));
 assert.equal(reviews.length,2);assert.equal(reviews[0].request_id,reviews[1].request_id,'A retry must retain its durable review ID');
 assert.equal(await page.getByRole('button',{name:'Approve this change',exact:true}).count(),0);
 await page.evaluate(native=>{
  const drawer=document.createElement('aside');drawer.id=native?'chatBrainDrawer':'chatNotificationDrawer';drawer.innerHTML='<button data-notification-tab="brain" class="active">Brain</button><div data-notification-drawer-body></div>';document.body.appendChild(drawer);
  VP3_AGENT_TEAMS_A3_BRAIN(drawer.querySelector('[data-notification-drawer-body]'));
 },native);
 assert.match(await page.locator('[data-agent-teams-brain-a3]').textContent(),/knowledge.create · executed/);
 assert.deepEqual(errors,[]);
 console.log('A5C3 browser PASS: '+(native?'HomeServer':'Cloud')+' exact change review, cancelled consent, safe text, durable retries, execution receipt and Brain timestamps');
}finally{await browser.close();}
