import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {chromium} from 'playwright';

const source=await fs.readFile(new URL('../ui/knowledge-actions-r8.js',import.meta.url),'utf8');
const browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{})});
try { for(const mode of ['cloud','native','chrome']) {
 const page=await browser.newPage({viewport:{width:390,height:844}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('https://actions.test/**',r=>r.fulfill({contentType:'text/html',body:'<main><div id="assistant"></div><form id="chatForm"><textarea id="chatInput">Unsent owner message</textarea></form></main><aside id="agentBrainDrawer"><div class="agent-brain-drawer-body"></div></aside>'}));
 await page.goto('https://actions.test/');
 await page.evaluate(mode=>{
  window.conversation=1;window.agentConversationId=1;window.agentWorkspaceAgentId=0;window.selectedAgent=1;
  window.calls=[];window.actions={};window.writes={};window.gates={};
  window.STONEFELLOW_CHAT_CONTINUITY={conversationId:()=>conversation};
  window.STONEFELLOW_AGENT_IDENTITY_V236={agentId:0};
  window.HomeServerAgentRouting={getActiveConversationId:()=>String(conversation),getSelectedAgentId:()=>selectedAgent};
  if(mode==='chrome')document.querySelector('main').innerHTML='<div id="agentView"><div class="agent-workspace-card"><div id="assistant"></div><textarea id="agentMessageInput">Unsent owner message</textarea></div></div>';
  else window.VP3_PROJECT_KNOWLEDGE={mode,endpoint:mode==='native'?'https://actions.test/api/v1/workspace-sync/project-knowledge':'https://actions.test/api/project-knowledge-r5.php',csrf:'fixture'};
  window.hold=(action,id,fail=false)=>{let release;const promise=new Promise(r=>{release=r;});gates[action+':'+id]={promise,release,fail,started:false};};
  window.release=(action,id)=>{const k=action+':'+id;gates[k].release();delete gates[k];};
  window.change=id=>{conversation=id;agentConversationId=id;agentWorkspaceAgentId=0;selectedAgent=1;STONEFELLOW_AGENT_IDENTITY_V236.agentId=0;window.dispatchEvent(new Event('vp3:project-knowledge-r6-context-changed'));};
  window.evidence=id=>({kind:'annotation',id:'33333333-3333-4333-8333-333333333333',revision:'b'.repeat(64),title:'Evidence '+id,excerpt:'Saved source',url:'https://supplier.example/source/'+id,captured_at:'2026-10-18',label:'P1'});
  window.draftFor=id=>({draft_id:'d'.repeat(64),operation:'task.create',target_key:'',target_revision:'',destination_title:'Task '+id,state_revision:'a'.repeat(64),draft_fingerprint:'e'.repeat(64),fields:{title:'Draft '+id,description:'Review source'},references:[{kind:'annotation',id:evidence(id).id,revision:evidence(id).revision}],evidence:{sources:[evidence(id)]}});
  window.handle=async p=>{
   calls.push(structuredClone(p));const id=Number(p.conversation_id||p.local_conversation),rows=actions[id]||(actions[id]={});
   if(p.action==='prepare')rows[p.request_id]={id:p.request_id,status:'pending',review_hash:'c'.repeat(64),preview:{operation:p.operation,destination_title:'Task '+id,fields:p.fields,before:Object.fromEntries(Object.keys(p.fields).map(k=>[k,null])),evidence:{sources:[evidence(id)]}}};
   if(p.action==='decide'){
    const row=rows[p.id];if(!row||!p.confirmed||row.review_hash!==p.expected_hash)throw Error('Wrong exact review');
    if(row.status==='pending'){row.status='applied';writes[id]=(writes[id]||0)+1;row.result={record_key:'task:'+id,source_url:'https://actions.test/record/'+id,applied_at:'2026-10-18T00:00:00Z'};row.sync={state:'received'};}
   }
   const response=structuredClone({ok:true,contract:'vp3.knowledge-actions.r8',context_key:'test:'+id,state:{title:'Context '+id,scope:{mode:'project'},revision:'a'.repeat(64)},sources:[evidence(id)],targets:{},target:{revision:'f'.repeat(64)},suggestion:{fields:{title:'Old suggestion '+id},notice:'Old draft'},actions:Object.values(rows),action:rows[p.id||p.request_id],draft:draftFor(id)});
   const gate=gates[p.action+':'+id];if(gate){gate.started=true;await gate.promise;if(gate.fail)throw Error('Delayed old request failed');}
   return response;
  };
  window.fetch=async(_,o)=>({ok:true,json:async()=>handle(JSON.parse(o.body))});
  window.msg=async(_,o)=>handle(o.payload);
 },mode);
 await page.addScriptTag({content:source});
 await page.getByText('Act on reviewed Knowledge',{exact:true}).click();
 await page.getByLabel('Use source Evidence 1',{exact:true}).waitFor();
 async function load(id){await page.evaluate(id=>{change(id);return VP3KnowledgeActionsR8.refresh();},id);await page.getByLabel('Use source Evidence '+id,{exact:true}).waitFor();}
 async function prepare(id){await page.getByLabel('Knowledge action',{exact:true}).selectOption('task.create');await page.getByLabel('Use source Evidence '+id,{exact:true}).check();await page.getByLabel('Proposed title',{exact:true}).fill('Review '+id);await page.getByRole('button',{name:'Prepare exact action review',exact:true}).click();await page.getByText('Action status: pending',{exact:true}).waitFor();}
 async function apply(id){await page.evaluate(id=>hold('decide',id),id);await page.getByRole('checkbox',{name:'Apply this exact change',exact:true}).check();await page.getByRole('button',{name:'Apply this exact change',exact:true}).click();await page.waitForFunction(id=>gates['decide:'+id]?.started,id);}
 const phases=['options','options-error','suggest','destination','prepare','decide','get-error','draft-prepare'];
 for(let i=0;i<phases.length;i++){
  const phase=phases[i],old=42+i*10,current=old+1;
  await load(old);
  let request='options';
  if(phase==='decide'||phase==='get-error'){
   await prepare(old);await apply(old);
   if(phase==='get-error'){
    await page.evaluate(id=>release('decide',id),old);await page.getByText('Action status: applied',{exact:true}).waitFor();
    request='get';await page.evaluate(id=>hold('get',id,true),old);await page.getByRole('button',{name:'Check action status',exact:true}).click();
   }else request='decide';
  }else if(['suggest','destination','prepare'].includes(phase)){
   await page.getByLabel('Knowledge action',{exact:true}).selectOption(phase==='destination'?'knowledge.create':'task.create');
   await page.getByLabel('Use source Evidence '+old,{exact:true}).check();await page.getByLabel('Proposed title',{exact:true}).fill('Old proposed task');
   request=phase;await page.evaluate(({request,id})=>hold(request,id),{request,id:old});
   await page.getByRole('button',{name:phase==='suggest'?'Draft from selected sources':'Prepare exact action review',exact:true}).click();
  }else if(phase==='draft-prepare'){
   await page.evaluate(id=>VP3KnowledgeActionsR9.show({contract:'vp3.knowledge-actions.r9',context_key:'test:'+id,drafts:[draftFor(id)]},document.getElementById('assistant')),old);
   await page.getByRole('textbox',{name:'Task draft Title',exact:true}).waitFor();
   request='prepare';await page.evaluate(id=>hold('prepare',id,true),old);await page.getByRole('button',{name:'Review exact change',exact:true}).click();
  }else{
   await page.evaluate(({id,fail})=>{hold('options',id,fail);void VP3KnowledgeActionsR8.refresh();},{id:old,fail:phase==='options-error'});
  }
  await page.waitForFunction(({request,id})=>gates[request+':'+id]?.started,{request,id:old});
  await load(current);await prepare(current);await apply(current);
  await page.evaluate(({request,id})=>release(request,id),{request,id:old});
  await page.waitForTimeout(50);
  assert.equal(await page.locator('[data-field=title]').count(),1,mode+' '+phase+': late work must not clear current fields');
  assert.equal(await page.getByLabel('Proposed title',{exact:true}).inputValue(),'Review '+current,mode+' '+phase+': late work must retain current fields');
  assert.equal(await page.getByLabel('Use source Evidence '+current,{exact:true}).isChecked(),true);
  assert.equal(await page.getByRole('button',{name:'Apply this exact change',exact:true}).isDisabled(),true,mode+' '+phase+': old finally must not unlock a current write');
  assert.equal(await page.getByText('Action status: pending',{exact:true}).count(),1,'Keep the current exact review');
  assert.doesNotMatch(await page.locator('.r8-brain-actions').innerText(),new RegExp('Task '+old+'\\b'),'Old receipt must not enter current Brain');
  await page.evaluate(id=>release('decide',id),current);await page.getByText('Action status: applied',{exact:true}).waitFor();
  assert.equal(await page.evaluate(id=>writes[id],current),1,'One confirmed write per review');
  if(phase==='destination')assert.equal(await page.evaluate(id=>calls.some(p=>p.action==='prepare'&&Number(p.conversation_id||p.local_conversation)===id),old),false,'An old destination response cannot dispatch the next prepare step');
  if(phase==='decide'){
   const decisions=await page.evaluate(()=>calls.filter(p=>p.action==='decide').length);
   await load(old);await page.getByText('Action status: applied',{exact:true}).waitFor();
   await page.getByRole('button',{name:'Check action status',exact:true}).click();await page.getByText('Action status: applied',{exact:true}).waitFor();
   assert.equal(await page.evaluate(id=>writes[id],old),1,'A committed old action is recovered by its receipt without a second write');
   assert.equal(await page.evaluate(()=>calls.filter(p=>p.action==='decide').length),decisions);
  }
  assert.equal(await page.locator(mode==='chrome'?'#agentMessageInput':'#chatInput').inputValue(),'Unsent owner message');
 }
 // A detached exact-review button must not submit its old ID in a new context.
 await load(1000);await prepare(1000);await page.getByRole('checkbox',{name:'Apply this exact change',exact:true}).check();
 await page.evaluate(()=>{window.oldApply=[...document.querySelectorAll('button')].find(b=>b.textContent==='Apply this exact change');});
 await load(1001);await prepare(1001);const before=await page.evaluate(()=>calls.length);
 await page.evaluate(()=>oldApply.click());await page.waitForTimeout(50);
 assert.equal(await page.evaluate(()=>calls.length),before,'A stale review button must not dispatch under a new conversation');
 assert.equal(await page.getByText('Action status: pending',{exact:true}).count(),1);
 await page.getByRole('checkbox',{name:'Apply this exact change',exact:true}).check();
 await page.evaluate(()=>{window.oldApply=[...document.querySelectorAll('button')].find(b=>b.textContent==='Apply this exact change');selectedAgent=7;agentWorkspaceAgentId=7;STONEFELLOW_AGENT_IDENTITY_V236.agentId=7;});
 await page.evaluate(()=>oldApply.click());await page.waitForTimeout(50);
 assert.equal(await page.evaluate(()=>calls.length),before,'An Agent change without an event cannot dispatch the old exact review');
 assert.equal(await page.locator('.knowledge-actions-r8 article').count(),0);
 assert.deepEqual(errors,[]);await page.close();
 console.log('PASS action-result ownership, request failures, exact-review binding, busy controls, Brain receipts and composer: '+mode);
 }} finally {await browser.close();}
