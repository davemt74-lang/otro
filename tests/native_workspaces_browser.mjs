import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import net from 'node:net';
import {spawn} from 'node:child_process';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const directory=await fs.mkdtemp(path.join(os.tmpdir(),'native-browser-'));
const probe=net.createServer();await new Promise(resolve=>probe.listen(0,'127.0.0.1',resolve));
const port=probe.address().port;await new Promise(resolve=>probe.close(resolve));
const base=`http://127.0.0.1:${port}`;
let browser,child,log='';
try{
  child=spawn(process.env.HOMESERVER_TEST_PYTHON||'python',['-u','-c',`
import hashlib,json
from app.database import initialize_database,db
initialize_database()
from app.services import workspace_sync as sync
from app.services.https_bridge_session import save_https_session
save_https_session('http://127.0.0.1:9/api/homeserver-https-poll-v1300.php','a'*64)
peer='http://127.0.0.1:9|1'
with db() as c:
 c.execute("INSERT INTO paired_apps(app_key,name,token_hash,status) VALUES('vp3','VP3',?,'active')",('f'*64,))
 c.execute('UPDATE workspace_sync_settings SET enabled=0,peer_id=?,session_hash=? WHERE id=1',(peer,hashlib.sha256(('a'*64).encode()).hexdigest()))
 c.execute("INSERT INTO contacts(display_name,notes) VALUES('Local contact','Local original')")
for dataset,table,count in [('contacts','crm_contacts',501),('knowledge','knowledge_items',251),('calendar','user_calendar_events',75),('crm','crm_leads',1),('products','agent_commerce_products_v800',1),('transcriptions','artist_transcript_sessions_v172',1),('meetings','video_meetings',1),('schedules','agent_scheduling_schedules',1)]:
 rows=[{'table':table,'source_id':str(i),'data':{'title':dataset+' item '+str(i),'name':dataset+' item '+str(i),'content_text':('Full Unicode 中文 evidence '*10000+' tailneedle <img src=x onerror=alert(1)>') if dataset=='knowledge' and i in (1,251) else 'Complete source data','email':'cloud'+str(i)+'@example.invalid'}} for i in range(1,count+1)]
 raw=json.dumps({'contract':sync.CONTRACT,'source':'cloud','dataset':dataset,'records':rows,'files':[]},ensure_ascii=False).encode()
 sync.apply_snapshot(peer,dataset,raw,hashlib.sha256(raw).hexdigest())
from app.runtime import app
from app.security import OWNER_CONTROL_TOKEN
import uvicorn
print('OWNER_TOKEN '+OWNER_CONTROL_TOKEN,flush=True)
uvicorn.run(app,host='127.0.0.1',port=${port},log_level='error')
`],{cwd:process.cwd(),env:{...process.env,HOMESERVER_DATA_DIR:directory,NO_PROXY:'localhost,127.0.0.1'}});
  child.stderr.on('data',chunk=>{log=(log+chunk).slice(-5000);});
  const token=await new Promise((resolve,reject)=>{let output='';const timeout=setTimeout(()=>reject(Error(log||'Startup timed out')),30000);child.stdout.on('data',chunk=>{output+=chunk;const match=output.match(/OWNER_TOKEN ([^\r\n]+)/);if(match){clearTimeout(timeout);resolve(match[1]);}});child.once('exit',code=>{clearTimeout(timeout);reject(Error('HomeServer exited '+code+': '+log));});});
  browser=await chromium.launch({headless:true,args:['--no-sandbox']});
  const context=await browser.newContext({viewport:{width:1440,height:900}});
  for(let attempt=0;;attempt++){try{if((await context.request.get(base+'/api/v1/health')).ok())break;}catch{}if(attempt>100)throw Error('No server: '+log);await new Promise(resolve=>setTimeout(resolve,100));}
  assert.ok((await context.request.post(base+'/__owner/session',{headers:{'X-HomeServer-Owner':token}})).ok());
  const page=await context.newPage();let dialogs=0;page.on('dialog',async dialog=>{dialogs++;await dialog.dismiss();});
  await page.goto(base+'/#native-calendar');
  await page.locator('#view-native-calendar.active article').first().waitFor();
  assert.equal(await page.locator('#pageTitle').textContent(),'Calendar');
  assert.equal(await page.locator('#view-native-calendar article').count(),50);
  await page.locator('#view-native-calendar button').filter({hasText:/^Next$/}).click();
  await page.locator('#view-native-calendar h3').filter({hasText:'calendar item 51'}).waitFor();
  assert.equal(await page.locator('#view-native-calendar article').count(),25);
  await page.locator('#view-native-calendar input').fill('item 75');
  await page.waitForFunction(()=>document.querySelectorAll('#view-native-calendar article').length===1);
  await page.locator('#view-native-calendar button').filter({hasText:'Details & original files'}).click();
  await page.locator('#nativeWorkspaceDetail dd').first().waitFor();
  assert.equal(await page.locator('#nativeWorkspaceDetail a').first().getAttribute('href'),'/api/v1/control/workspace-sync/source/calendar?key=user_calendar_events%3A75');
  await page.locator('#nativeWorkspaceDetail button').filter({hasText:'Close'}).click();
  await page.locator('[data-view="contacts"]').first().click();
  await page.locator('#contactsList .contact-card').first().waitFor();
  assert.equal(await page.locator('#contactsList [data-edit-contact]').count(),1);
  assert.equal(await page.locator('#contactsList [data-workspace-detail]').count(),500);
  await page.locator('#contactsCloudPages button').filter({hasText:'Next Cloud records'}).click();
  await page.locator('#contactsList h3').filter({hasText:'contacts item 501'}).waitFor();
  assert.equal(await page.locator('#contactsList [data-workspace-detail]').count(),1);
  await page.locator('[data-view="knowledge"]').first().click();
  await page.locator('#knowledgeCloudPages button').filter({hasText:'Next Cloud records'}).waitFor();
  await page.locator('#knowledgeCloudPages button').filter({hasText:'Next Cloud records'}).click();
  await page.locator('#knowledgeList h3').filter({hasText:'knowledge item 251'}).waitFor();
  await page.locator('#knowledgeList button').filter({hasText:'Details & source'}).first().click();
  await page.waitForFunction(()=>document.querySelector('#nativeWorkspaceDetail').textContent.includes('tailneedle'));
  assert.equal(await page.locator('#nativeWorkspaceDetail img').count(),0);
  assert.equal(dialogs,0);
  await page.setViewportSize({width:390,height:844});
  const dimensions=await page.locator('#nativeWorkspaceDetail').evaluate(el=>({width:el.getBoundingClientRect().width,scroll:el.scrollWidth,client:el.clientWidth,viewport:innerWidth}));
  assert.ok(dimensions.width<=dimensions.viewport&&dimensions.scroll<=dimensions.client+1,JSON.stringify(dimensions));
  await page.locator('#nativeWorkspaceDetail button').filter({hasText:'Close'}).click();
  for(const dataset of ['crm','products','transcriptions','meetings','schedules']){
    await page.evaluate(name=>window.openHomeServerView('native-'+name),dataset);
    await page.locator('#view-native-'+dataset+' article').first().waitFor();
  }
  assert.ok((await page.locator('#view-native-schedules').textContent()).includes('execute only on their owning system'));
  console.log('Native browser deep links, pagination, full Unicode details, source links, local edit isolation, escaped content and mobile layout PASS');
}finally{
  await browser?.close();
  if(child&&child.exitCode===null&&!child.killed){child.kill('SIGTERM');await new Promise(resolve=>{child.once('exit',resolve);setTimeout(()=>{child.kill('SIGKILL');resolve();},5000);});}
  await fs.rm(directory,{recursive:true,force:true});
}
