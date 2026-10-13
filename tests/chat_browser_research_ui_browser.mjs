import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {chromium} from 'playwright';
const native=true;
const script=await fs.readFile(new URL('../ui/chat-browser-research-v1.js',import.meta.url),'utf8');
const css=await fs.readFile(new URL('../ui/chat-browser-research-v1.css',import.meta.url),'utf8');
const browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{})});
const page=await browser.newPage({viewport:{width:1100,height:800}});const calls=[],errors=[];page.on('pageerror',e=>errors.push(e.message));
let ordinary=0,blocked=false,gate=null,confirmed=true;
let r={id:'review',question:'Compare https://one.example/about and https://two.example/services',runtime_id:'runtime',review_hash:'a'.repeat(64),status:'prepared',expires_at:'2099-01-01',budgets:{sources:2,pages:2,claims:60},sources:[{url:'https://one.example/about'},{url:'https://two.example/services'}]};
await page.route('http://research.test/',route=>route.fulfill({contentType:'text/html',body:'<main><form id="chatForm"><textarea id="chatInput"></textarea><button>Send</button></form><select id="vp3ChatTaskMode"><option value="chat">Chat</option></select></main>'}));
await page.route('**/api/research',async route=>{
 const body=route.request().postDataJSON();calls.push(body);if(gate)await gate;
 if(blocked)return route.fulfill({status:403,json:{ok:false,error:'Permission revoked'}});
 const response={ok:true,contract:'vp3.chat-browser-research.v1'};
 if(body.action==='list')Object.assign(response,{requests:[],runtimes:[{id:'runtime',title:'Approved research',domains:['one.example','two.example']}],projects:[{id:'project',title:'Reviewed research'}]});
 else if(body.action==='prepare')response.request={...r,question:body.question,sources:body.discovery_confirmed?r.sources.map(x=>({...x,origin:'web_search',discovery_domains:body.discovery_domains,title:'Candidate <img src=x>',reason:'Not yet read or verified'})):r.sources};
 else if(body.action==='start'){assert.equal(body.confirmed,true);assert.equal(body.expected_hash,r.review_hash);r={...r,status:'waiting_chrome'};response.request=r;}
 else if(body.action==='get')response.request=r;
 else if(body.action==='save'){assert.equal(body.project_id,'project');response.request={...r,mission:{...r.mission,report:{url:'/research-report.php?id=draft'}}};}
 else throw Error('Unexpected action');
 return route.fulfill({json:response});
});
try{
 await page.goto('http://research.test/');await page.evaluate(native=>{
  window.VP3_CHAT_RESEARCH={endpoint:'/api/research',csrf:'fixture',native};window.currentConversation=native?'local-conversation':71;
  window.STONEFELLOW_CHAT_CONTINUITY={conversationId:()=>window.currentConversation};window.STONEFELLOW_AGENT_IDENTITY_V236={agentId:9};
  window.HomeServerAgentRouting={getActiveConversationId:()=>window.currentConversation,getSelectedAgentId:()=>4};
  window.confirm=()=>window.researchConfirm!==false;Object.defineProperty(window.crypto,'randomUUID',{configurable:true,value:()=> '77777777-7777-4777-8777-777777777777'});
  document.getElementById('chatForm').addEventListener('submit',e=>{e.preventDefault();window.ordinary=(window.ordinary||0)+1;});
 },native);
 await page.addStyleTag({content:css});await page.addScriptTag({content:script});
 await page.locator('#vp3ChatTaskMode').selectOption('chrome');
 await page.locator('select[aria-label="Approved Chrome research runtime"]').selectOption('runtime');
 await page.locator('#chatInput').fill('Compare available services and missing evidence');await page.locator('#chatForm button').click();
 await page.getByRole('button',{name:'Approve search and propose sources'}).waitFor();
 assert.equal(calls.filter(x=>x.action==='prepare').length,0,'no search before canvas consent');
 assert.match(await page.locator('.vp3-chat-research pre').innerText(),/site:one.example OR site:two.example/);
 await page.getByRole('button',{name:'Cancel search review'}).click();assert.equal(calls.filter(x=>x.action==='prepare').length,0);
 await page.locator('#chatForm button').click();await page.getByRole('button',{name:'Approve search and propose sources'}).click();
 await page.getByRole('button',{name:'Approve plan and start Chrome research'}).waitFor();
 const discovery=calls.find(x=>x.action==='prepare');assert.equal(discovery.discovery_confirmed,true);assert.deepEqual(discovery.discovery_domains,['one.example','two.example']);
 assert.equal(calls.filter(x=>x.action==='start').length,0,'search cannot start Chrome');
 await page.getByText('Search candidates are suggestions, not evidence.',{exact:false}).waitFor();assert.equal(await page.locator('img[src=x]').count(),0);
 await page.locator('#chatInput').fill(r.question);await page.locator('#chatForm button').click();
 await page.getByRole('button',{name:'Approve plan and start Chrome research'}).waitFor();
 assert.equal(calls.filter(x=>x.action==='start').length,0);assert.equal(await page.evaluate(()=>window.ordinary||0),0);
 await page.evaluate(()=>window.researchConfirm=false);await page.getByRole('button',{name:'Approve plan and start Chrome research'}).click();assert.equal(calls.filter(x=>x.action==='start').length,0);
 await page.evaluate(()=>window.researchConfirm=true);await page.getByRole('button',{name:'Approve plan and start Chrome research'}).click();await page.getByText('Waiting for the connected Chrome extension.',{exact:false}).waitFor();
 r={...r,status:'complete',mission:{progress:{pages:2,corroborated:1,conflicted:1},memo_preview:'Supported brief <img src=x>',claims:[{state:'conflicted',statement:'Sources disagree <script>',evidence:[{domain:'one.example',excerpt:'Primary evidence'}]}],pages:[{domain:'one.example',fingerprint:'a'.repeat(64),source_version_id:'version'}],gaps:['A missing date']}};
 // The reload list restores server-owned runs and safe source-linked results.
 await page.unroute('**/api/research');
 await page.route('**/api/research',async route=>{const b=route.request().postDataJSON();calls.push(b);if(blocked)return route.fulfill({status:403,json:{ok:false,error:'Permission revoked'}});return route.fulfill({json:{ok:true,contract:'vp3.chat-browser-research.v1',requests:[r],runtimes:[{id:'runtime',title:'Approved research',domains:[]}],projects:[{id:'project',title:'Reviewed research'}],request:r}});});
 await page.getByRole('button',{name:'Refresh research'}).click();await page.getByText('Supported brief <img src=x>',{exact:true}).waitFor();
 assert.equal(await page.locator('img[src=x]').count(),0);assert.equal(await page.locator('.vp3-chat-research a').count(),2);
 await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+2));
 const beforeFollowup=calls.filter(x=>x.action==='prepare').length;await page.getByRole('button',{name:'Research this gap'}).click();assert.equal(await page.locator('#chatInput').inputValue(),'Investigate: A missing date');assert.equal(calls.filter(x=>x.action==='prepare').length,beforeFollowup,'gap cannot automatically search or restart');
 blocked=true;await page.getByRole('button',{name:'Refresh research'}).click();await page.waitForFunction(()=>!document.querySelector('.vp3-chat-research pre'));
 assert.deepEqual(errors,[]);console.log('PASS: '+(native?'HomeServer':'Cloud')+' R2 canvas consent reviews exact external query, separates unverified candidates from evidence, prepares gap follow-ups without execution, intercepts ordinary Chat, requires exact approval, restores cited/conflicting findings, renders safe text/mobile and clears revoked results.');
}finally{await browser.close();}
