import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url),{chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const browser=await chromium.launch({headless:true,...(process.env.SCREENSHOT_CHROMIUM_EXECUTABLE?{executablePath:process.env.SCREENSHOT_CHROMIUM_EXECUTABLE,args:['--no-sandbox','--disable-dev-shm-usage','--use-gl=angle','--use-angle=swiftshader','--disable-gpu-sandbox']}:{} )});
const page=await browser.newPage({viewport:{width:390,height:844}});let revoked=false,calls=0;const errors=[];page.on('pageerror',e=>errors.push(e.message));
await page.route('http://native.test/',route=>route.fulfill({contentType:'text/html',body:'<main style="max-width:360px"><form id="chatForm"><textarea id="chatInput"></textarea></form></main>'}));
await page.route('**/api/v1/control/workspace-sync/browser-captures?*',route=>{calls++;assert.match(route.request().url(),/local_conversation=chat-r4/);return revoked?route.fulfill({status:403,json:{detail:'Shared capture permission revoked'}}):route.fulfill({json:{contract:'vp3.browser-capture.r4',synced_at:'2026-10-13',items:[{id:'capture',title:'Supplier <img src=x>',selection:'Flour <script>evil()</script>',note:'Compare costs',tags:['supplier'],source_url:'javascript:evil()',captured_at:'2026-10-13',knowledge_id:1,project_id:'project'}]}});});
try{
 await page.goto('http://native.test/');await page.evaluate(()=>{window.HomeServerAgentRouting={getActiveConversationId:()=> 'chat-r4',getSelectedAgentId:()=>4};window.sent=0;document.getElementById('chatForm').onsubmit=e=>{e.preventDefault();sent++;};});
 await page.addStyleTag({content:'.browser-captures-r4{overflow-wrap:anywhere}.browser-captures-r4 blockquote{white-space:pre-wrap;max-height:160px;overflow:auto}'});
 await page.addScriptTag({content:await fs.readFile(new URL('../ui/browser-captures-r4.js',import.meta.url),'utf8')});
 await page.getByText('Browser annotations',{exact:true}).click();await page.getByRole('button',{name:'Use annotation in Chat'}).waitFor();
 assert.equal(await page.locator('img[src=x]').count(),0);assert.equal(await page.locator('a[href^="javascript:"]').count(),0);
 await page.getByRole('button',{name:'Use annotation in Chat'}).click();await page.waitForFunction(()=>document.getElementById('chatInput').value.includes('Compare costs'));
 assert.equal(calls,2,'Use did not recheck current permissions');assert.equal(await page.evaluate(()=>sent),0,'Capture picker auto-sent a model request');
 await page.locator('#chatInput').fill('');const reloaded=page.waitForResponse(response=>response.url().includes('/browser-captures?'));await page.getByText('Browser annotations',{exact:true}).click();await reloaded;await page.waitForFunction(()=>!document.querySelector('.browser-captures-r4 > button').disabled);await page.getByRole('button',{name:'Use annotation in Chat'}).waitFor();revoked=true;
 await page.getByRole('button',{name:'Use annotation in Chat'}).click();await page.getByText('Shared capture permission revoked').waitFor();assert.equal(await page.locator('#chatInput').inputValue(),'');assert.equal(await page.locator('article').count(),0);
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+2));assert.deepEqual(errors,[]);
 console.log('PASS: native mobile capture picker, source text/link safety, explicit composer insertion without model/send/save, fresh authority before use and revoked-result removal.');
}finally{await browser.close();}
