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
  window.confirm=()=>true;window.calls=[];
  let serial=0;Object.defineProperty(window,'crypto',{value:{randomUUID:()=> '00000000-0000-4000-8000-'+String(++serial).padStart(12,'0')}});
  window.mission={id:'11111111-1111-4111-8111-111111111111',objective:'Read research',status:'planned',tasks:[{id:'22222222-2222-4222-8222-222222222222',title:'Research',role:'research',status:'queued',read_calls_used:0}]};
  window.contract={revision:0,configured:false,active:false,capabilities:['knowledge.search','browser.read'],max_calls_per_worker:3,assignments:{}};
  window.failOnce=true;
  window.fetch=async(url,opts)=>{
   const request=JSON.parse(opts.body);calls.push(request);
   if(request.csrf_token!==(native?'owner':'fixture'))throw Error('Missing source CSRF');
   const ok=payload=>({ok:true,json:async()=>({ok:true,...payload})});
   if(request.action==='list')return ok({items:[mission]});
   if(request.action==='decisions')return ok({items:[]});
   if(request.action==='tools.get')return ok({tools:contract});
   if(request.action==='tools.configure'){
    if(!request.confirmed||request.expected_revision!==0)throw Error('Missing reviewed revision');
    if(failOnce){failOnce=false;return {ok:false,status:503,json:async()=>({ok:false,error:'Temporary failure'})};}
    contract={...contract,revision:1,configured:true,active:true,assignments:request.assignments,expires_at:'2030-01-01 00:00:00'};mission.tools_configured=true;return ok({tools:contract});
   }
   if(request.action==='tools.start'){
    if(!request.confirmed||request.expected_revision!==1||!request.request_id)throw Error('Missing start approval');
    mission.status='running';mission.tools_enabled=true;return ok({mission});
   }
   return ok({mission});
  };
 },native);
 await page.addScriptTag({content:source});
 await page.locator('[data-agent-teams-a3]>summary').click();
 await page.getByRole('button',{name:'View',exact:true}).click();
 await page.getByRole('button',{name:'Review specialist assignments'}).click();
 const panel=page.locator('.vp3-specialist-assignments');await panel.waitFor();
 await panel.locator('[data-tool-field="tool"][value="knowledge.search"]').check();
 await panel.locator('[data-tool-field="max_calls"]').fill('2');
 await panel.locator('[data-tool-field="output"]').selectOption('sources');
 await page.getByRole('button',{name:'Refresh',exact:true}).click();
 await page.waitForFunction(()=>calls.filter(c=>c.action==='get').length>=2);
 assert.equal(await panel.locator('[data-tool-field="max_calls"]').inputValue(),'2');
 assert.equal(await panel.locator('[data-tool-field="tool"][value="knowledge.search"]').isChecked(),true);
 await page.getByRole('button',{name:'Approve assignments'}).click();
 await page.waitForFunction(()=>document.querySelector('.vp3-teams-status').textContent==='Temporary failure');
 await page.getByRole('button',{name:'Approve assignments'}).click();
 await page.waitForFunction(()=>contract.revision===1);
 const configured=await page.evaluate(()=>calls.filter(c=>c.action==='tools.configure'));
 assert.equal(configured.length,2);assert.equal(configured[0].request_id,configured[1].request_id,'Retries must use the same durable request ID');
 assert.deepEqual(configured[1].assignments.assignments[0],{task_id:'22222222-2222-4222-8222-222222222222',tools:['knowledge.search'],max_calls:2,output:'sources',browser_url:''});
 await page.getByRole('button',{name:'Start',exact:true}).click();
 await page.waitForFunction(()=>mission.status==='running');
 assert.equal(await page.evaluate(()=>calls.filter(c=>c.action==='start').length),0,'Assigned missions must use the coordinated start path');
 await page.evaluate(()=>{
  const drawer=document.createElement('aside');drawer.id='chatBrainDrawer';drawer.innerHTML='<button data-notification-tab="brain" class="active">Brain</button><div data-notification-drawer-body></div>';document.body.appendChild(drawer);
  VP3_AGENT_TEAMS_A3_BRAIN(drawer.querySelector('[data-notification-drawer-body]'));
 });
 assert.match(await page.locator('[data-agent-teams-brain-a3]').textContent(),/Read research/);
 assert.deepEqual(errors,[]);
 console.log('A5C2 browser PASS: '+(native?'native HomeServer':'Cloud Chat')+' reviewed assignments, refresh drafts, durable retries, revision-bound start and Brain projection');
}finally{await browser.close();}
