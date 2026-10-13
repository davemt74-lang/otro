import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {chromium} from 'playwright';
const native=process.argv.includes('--native');
const root=new URL('../',import.meta.url);
const script=await fs.readFile(new URL(native?'ui/chat-browser-research-v1.js':'chat-browser-research-v1.js',root),'utf8');
const css=await fs.readFile(new URL(native?'ui/chat-browser-research-v1.css':'chat-browser-research-v1.css',root),'utf8');
const browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{})});
const page=await browser.newPage({viewport:{width:390,height:844}}),calls=[],errors=[];
page.on('pageerror',e=>errors.push(e.message));let blocked=false,available=true,release=null,gate=null;
const citation={label:'C1',domain:'one.example',url:'https://one.example/about',fingerprint:'a'.repeat(64),source_version_id:'version-one',as_of:'2026-10-01'};
const reports=[1,2].map(i=>({id:'run'+i,question:'Studio hours <img src=x> '+i,saved_at:'2026-10-0'+i,report:{id:'report'+i,url:'/research-report.php?report=report'+i},answer:{text:'Observed studio hours [C1]. Captured evidence, not live freshness. '+('long '.repeat(100)),citations:[citation,{...citation,label:'C2',url:'javascript:alert(1)'}]}}));
await page.route('http://r3.test/',r=>r.fulfill({contentType:'text/html',body:'<form id="chatForm"><textarea id="chatInput"></textarea><button>Send</button></form><select id="vp3ChatTaskMode"><option value="chat">Chat</option></select>'}));
await page.route('**/api/research',async route=>{
 const body=route.request().postDataJSON();calls.push(body);if(gate)await gate;
 if(blocked)return route.fulfill({status:403,json:{ok:false,error:'Research access revoked'}});
 const base={ok:true,contract:'vp3.chat-browser-research.v1'};
 if(body.action==='list')Object.assign(base,{requests:[],projects:[],runtimes:[]});
 else if(body.action==='recall'){assert.equal(body.question,'studio hours');Object.assign(base,{reports:available?reports:[]});}
 else if(body.action==='compare'){assert.equal(body.before_id,'run1');assert.equal(body.after_id,'run2');Object.assign(base,{comparison:{before:{question:'Prior studio hours',citations:[citation]},after:{question:'Later studio hours',citations:[citation]},notice:'Different observations do not prove a real-world change. No research was restarted.',changes:[{key:'hours',state:'different_observed_values',before:[{value:'9 am',kind:'observed',citations:['C1']}],after:[{value:'10 am <script>',kind:'conflict',citations:['C1']}]},{key:'weekends',state:'not_reobserved',before:[{value:'Unknown',kind:'inference',citations:['C1']}],after:[]}]}});}
 else throw Error('Unexpected executing operation '+body.action);
 return route.fulfill({json:base});
});
try{
 await page.goto('http://r3.test/');await page.evaluate(native=>{
  window.VP3_CHAT_RESEARCH={native,endpoint:'/api/research',csrf:'fixture'};window.currentConversation=native?'local-r3':41;
  window.STONEFELLOW_CHAT_CONTINUITY={conversationId:()=>window.currentConversation};window.STONEFELLOW_AGENT_IDENTITY_V236={agentId:9};
  window.HomeServerAgentRouting={getActiveConversationId:()=>window.currentConversation,getSelectedAgentId:()=>4};
 },native);
 await page.addStyleTag({content:css});await page.addScriptTag({content:script});await page.locator('#vp3ChatTaskMode').selectOption('chrome');
 await page.locator('#chatInput').fill('studio hours');await page.getByRole('button',{name:'Recall saved research',exact:true}).click();
 await page.getByRole('button',{name:'Compare saved research'}).waitFor();
 assert.equal(await page.locator('img[src=x]').count(),0);assert.equal(await page.locator('a[href^="javascript:"]').count(),0);
 await page.locator('.vp3-chat-research details summary').first().click();await page.getByText('Pinned source version version-one',{exact:true}).first().waitFor({state:'attached'});
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+2));
 gate=new Promise(r=>release=r);await page.getByRole('button',{name:'Compare saved research'}).click();
 assert.equal(await page.getByLabel('Earlier saved research').isDisabled(),true);release();gate=null;
 await page.getByText('weekends · not reobserved').waitFor();await page.getByText('Different observations do not prove a real-world change.',{exact:false}).waitFor();
 assert.deepEqual([...new Set(calls.map(c=>c.action))],['list','recall','compare']);
 available=false;await page.getByRole('button',{name:'Refresh research'}).click();await page.getByText('No matching saved research is available under current permissions.').waitFor();assert.equal(await page.getByRole('button',{name:'Compare saved research'}).count(),0);
 available=true;await page.getByRole('button',{name:'Recall saved research',exact:true}).click();await page.getByRole('button',{name:'Compare saved research'}).waitFor();
 blocked=true;await page.getByRole('button',{name:'Recall saved research',exact:true}).click();await page.getByText('Research access revoked').waitFor();assert.equal(await page.locator('.vp3-chat-research details').count(),0);
 assert.deepEqual(errors,[]);console.log('PASS: '+(native?'native':'Cloud')+' R3 real mobile canvas, exact source versions, safe candidate text/links, scoped recall, locked two-report comparison, no search/read/save execution and revoked-result removal.');
}finally{await browser.close();}
