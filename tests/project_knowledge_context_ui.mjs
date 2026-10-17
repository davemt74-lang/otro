import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url),{chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const browser=await chromium.launch({headless:true,...(process.env.SCREENSHOT_CHROMIUM_EXECUTABLE?{executablePath:process.env.SCREENSHOT_CHROMIUM_EXECUTABLE,args:['--no-sandbox','--disable-dev-shm-usage']}:{} )});
const source=await fs.readFile(new URL('../ui/project-knowledge-r6.js',import.meta.url),'utf8');

try{
 for(const mode of ['cloud','native','chrome']){
  const page=await browser.newPage({viewport:{width:390,height:844}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('http://r6.test/**',r=>r.fulfill({contentType:'text/html',body:'<main style="max-width:370px"><form id="chatForm"><textarea id="chatInput">My typed question</textarea><button type="submit">Send</button></form></main><aside id="agentBrainDrawer"><div class="agent-brain-drawer-body"></div></aside>'}));await page.goto('http://r6.test/');
  await page.evaluate(mode=>{
   window.conversation=42;window.calls=[];window.submits=0;document.getElementById('chatForm').onsubmit=e=>{e.preventDefault();submits++;};
   window.STONEFELLOW_CHAT_CONTINUITY={conversationId:()=>conversation};window.STONEFELLOW_AGENT_IDENTITY_V236={agentId:0};
   window.HomeServerAgentRouting={getActiveConversationId:()=>String(conversation),getSelectedAgentId:()=>1};
   window.agentWorkspaceAgentId=0;window.agentConversationId=42;
   if(mode==='chrome'){document.getElementById('chatForm').remove();document.querySelector('main').innerHTML='<section id="agentView"><section class="agent-workspace-card"><textarea id="agentMessageInput">My typed question</textarea></section></section>';}
   else window.VP3_PROJECT_KNOWLEDGE={mode,endpoint:'http://r6.test/api/project-knowledge-r5.php',csrf:'fixture'};
   window.contextState={scope:{mode:'all',project_id:'',folder_id:0},title:'All authorized Knowledge',revision:'a'.repeat(64),available:true};
   window.annotation={kind:'annotation',id:'33333333-3333-4333-8333-333333333333',revision:'c'.repeat(64),title:'Supplier <img src=x onerror=evil()>',url:'https://supplier.example/prices',captured_at:'2026-10-13 05:00:00',excerpt:'Captured quote <script>evil()</script>',sync:{state:'received',confirmed_at:'2026-10-13 05:01:00'}};
   window.report={kind:'report',id:'44444444-4444-4444-8444-444444444444',revision:'d'.repeat(64),title:'Saved report',url:'/research-report.php?report=44444444-4444-4444-8444-444444444444',captured_at:'2026-10-12',excerpt:'Observed source value [C1]'};
   window.handle=async payload=>{
    calls.push(structuredClone(payload));if(window.revoked)throw Error('Knowledge access revoked.');
    if(window.defer){await new Promise(resolve=>window.release=resolve);if(window.failDeferred)throw Error('Old Knowledge request failed.');}
    if(payload.action==='set_context'){if(window.conflict)throw Error('Conversation context changed. Refresh before choosing another scope.');contextState={...contextState,scope:payload.scope,revision:'b'.repeat(64),title:'Suppliers'};}
    return {ok:true,contract:'vp3.project-knowledge.r6',state:contextState,cloud_origin:'http://r6.test',projects:[{id:'77777777-7777-4777-8777-777777777777',title:'Suppliers'}],folders:[{id:1,folder_name:'Purchasing'}],items:contextState.scope.mode==='off'?[]:[annotation],reports:contextState.scope.mode==='off'?[]:[report]};
   };
   window.msg=async(type,{payload})=>{assertType=type;if(type!=='project_knowledge_r6')throw Error('Wrong worker route');return handle(payload);};
   window.fetch=async(_url,options)=>{try{const data=await handle(JSON.parse(options.body));return {ok:true,json:async()=>data};}catch(e){return {ok:false,status:403,json:async()=>({error:e.message})};}};
  },mode);
  // Strict-mode host fixture avoids assigning an undeclared worker diagnostic.
  await page.evaluate(()=>window.assertType='');await page.addScriptTag({content:source});
  await page.getByText('Conversation Knowledge',{exact:true}).click();await page.getByRole('button',{name:'Attach to next message'}).first().waitFor();
  await page.getByLabel('Conversation Knowledge scope').selectOption('project:77777777-7777-4777-8777-777777777777');
  assert.equal(await page.evaluate(()=>calls.filter(c=>c.action==='set_context').length),0);
  await page.getByRole('button',{name:'Apply context'}).click();await page.waitForFunction(()=>calls.filter(c=>c.action==='options').length===2);
  await page.getByRole('button',{name:'Attach to next message'}).nth(0).click();await page.getByRole('button',{name:'Attach to next message'}).nth(1).click();
  const selection=await page.evaluate(()=>VP3ProjectKnowledgeR6.value());assert.equal(selection.attachments.length,2);assert.equal(selection.state_revision,'b'.repeat(64));
  assert.equal(await page.locator(mode==='chrome'?'#agentMessageInput':'#chatInput').inputValue(),'My typed question');assert.equal(await page.evaluate(()=>submits),0);
  assert.equal(await page.locator('.r6-results img,.r6-results script').count(),0);
  const reportHref=await page.locator('.r6-results a').nth(1).getAttribute('href');assert.match(reportHref,/http:\/\/r6.test\/research-report/);
  await page.evaluate(()=>VP3ProjectKnowledgeR6.consume({active:true,state:contextState,sources:[{...annotation,label:'P1'},{...report,label:'P2'}],cloud_origin:'http://r6.test'}));
  assert.equal(await page.locator('.r6-brain-sources a').count(),2);assert.equal(await page.evaluate(()=>VP3ProjectKnowledgeR6.value().attachments.length),0);
  await page.evaluate(()=>window.revoked=true);await page.getByRole('button',{name:'Find sources',exact:true}).click();await page.getByText('Knowledge access revoked.',{exact:true}).waitFor();assert.equal(await page.locator('.r6-results article').count(),0);assert.equal(await page.locator('.r6-brain-sources a').count(),0);
  for(const fault of ['success','failure','apply']){
   await page.evaluate(()=>{window.revoked=false;window.defer=false;window.failDeferred=false;});await page.evaluate(()=>VP3ProjectKnowledgeR6.refresh());
   if(fault==='apply')await page.getByLabel('Conversation Knowledge scope').selectOption('folder:1');
   await page.evaluate(()=>{window.defer=true;window.release=null;});
   await page.getByRole('button',{name:fault==='apply'?'Apply context':'Find sources',exact:true}).click();await page.waitForFunction(()=>typeof release==='function');
   assert.equal(await page.evaluate(()=>{try{VP3ProjectKnowledgeR6.value();return false;}catch(_){return true;}}),true,'Sending must wait for an explicit context operation');
   await page.evaluate(mode=>{window.oldRelease=release;conversation++;if(mode==='chrome')agentConversationId=conversation;window.dispatchEvent(new Event('stonefellow:chat-conversation-changed'));window.defer=false;},mode);
   await page.evaluate(()=>VP3ProjectKnowledgeR6.refresh());await page.getByRole('button',{name:'Attach to next message'}).first().click();
   const current=await page.evaluate(()=>VP3ProjectKnowledgeR6.value());
   await page.evaluate(fault=>{window.failDeferred=fault==='failure';window.oldRelease();},fault);await page.waitForTimeout(80);
   assert.deepEqual(await page.evaluate(()=>VP3ProjectKnowledgeR6.value()),current,'An old '+fault+' response must preserve the new conversation selection');
   assert.equal(await page.locator('.r6-selected button').count(),1);
   assert.equal(await page.locator(mode==='chrome'?'#agentMessageInput':'#chatInput').inputValue(),'My typed question');
  }
  await page.evaluate(()=>{window.failDeferred=false;});
  await page.evaluate(()=>{window.revoked=false;window.defer=true;});await page.getByRole('button',{name:'Find sources',exact:true}).click();await page.waitForFunction(()=>typeof release==='function');
  await page.evaluate(mode=>{conversation=43;if(mode==='chrome')agentConversationId=43;window.release();window.defer=false;},mode);
  await page.getByText('Conversation changed. Refresh its Knowledge context.',{exact:true}).waitFor();assert.equal(await page.locator('.r6-results article').count(),0);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);assert.deepEqual(errors,[]);await page.close();
 }
 console.log('PASS: R6 three actual mobile canvases, deliberate scope save, two-source attachments without replacing or sending drafts, safe source links/text, Brain receipts, revocation and conversation-race clearing.');
}finally{await browser.close();}
