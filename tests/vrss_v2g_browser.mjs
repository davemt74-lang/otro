import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {randomUUID} from 'node:crypto';
import {chromium} from 'playwright';
const child=spawn(process.env.VP3_TEST_PYTHON||'python',['-u','tests/vrss_v1_server.py'],{env:{...process.env,VRSS_SOCIAL_TEST:'1',VRSS_COMMENTS_TEST:'1',VRSS_CONVERSATIONS_TEST:'1'},stdio:['ignore','pipe','pipe']});
let errors='';child.stderr.on('data',b=>errors+=b.toString());
const lines=createInterface({input:child.stdout});
const config=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error(errors||'Fixture timeout')),60000);lines.on('line',line=>{try{const d=JSON.parse(line);if(d.url){clearTimeout(timer);resolve(d);}}catch{}});child.on('exit',code=>reject(Error(`Fixture exited ${code}: ${errors}`)));});
const browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{})});
try{
 for(const width of [1440,390,320]){
  const ctx=await browser.newContext({viewport:{width,height:960}}),page=await ctx.newPage(),base=config.url,ep=base+'/api/v1/control/homeserver-apps/vrss/request',preview=base+'/api/v1/control/homeserver-apps/vp3.feed/preview/',url='https://bob.example/vrss/v1/posts/11111111-1111-4111-8111-111111111111';
  const api=async(action,args={})=>{const r=await ctx.request.post(ep,{headers:{'X-VRSS-Request':'1'},data:{action,...args}});assert.equal(r.status(),200,await r.text());return r.json();};
  const reply=async data=>{const r=await ctx.request.post(base+'/api/v1/control/vrss-test-conversation-reply',{data});assert.equal(r.status(),200,await r.text());};
  await ctx.request.post(base+'/__owner/session',{headers:{'X-HomeServer-Owner':config.owner}});
  await page.goto(preview+'#profile');await page.getByRole('button',{name:'Edit Profile',exact:true}).click();
  if(width===1440)assert.equal(await page.locator('#vrss-mentions').isChecked(),false);
  const address=page.getByLabel('Publishing address',{exact:true});if(await address.isEnabled())await address.selectOption(config.hosting_site_id);
  for(const id of ['vrss-public','vrss-social','vrss-comments','vrss-mentions','vrss-thread-notifications'])await page.locator('#'+id).check();
  await page.getByRole('button',{name:'Save Profile',exact:true}).click();await page.getByRole('button',{name:'Edit Profile',exact:true}).waitFor();
  for(const n of (await api('notices')).notices)await api('notice.read',{notice_id:n.id});
  await reply({reset:true,body:'Baseline reply'});await page.goto(preview);await page.getByRole('button',{name:'Conversations',exact:true}).click();
  await page.getByLabel('Public VRSS post URL',{exact:true}).fill(url);await page.getByRole('button',{name:'Open conversation',exact:true}).click();
  let lost=false;const attempts=[];
  await page.route(ep,async route=>{const body=route.request().postDataJSON();if(body.action==='social.conversation'){attempts.push(body);if(!lost){lost=true;await route.fetch();await route.abort();return;}}await route.continue();});
  await page.getByRole('button',{name:'Follow conversation',exact:true}).click();await page.getByRole('button',{name:'Retry conversation change',exact:true}).waitFor();
  await page.reload();await page.getByRole('button',{name:'Conversations',exact:true}).click();await page.getByRole('button',{name:'Retry conversation change',exact:true}).click();
  await page.getByRole('button',{name:'Unfollow conversation',exact:true}).waitFor();assert.equal(attempts.length,2);assert.equal(attempts[0].request_id,attempts[1].request_id);await page.unroute(ep);
  assert.equal((await api('social.status')).social.following,0);assert.equal((await api('notices')).notices.length,0);
  await page.getByRole('button',{name:'Mute conversation',exact:true}).click();await page.getByRole('button',{name:'Unmute conversation',exact:true}).waitFor();
  await reply({body:'Muted reply'});assert.equal((await api('notices')).notices.length,0);
  await page.getByRole('button',{name:'Unmute conversation',exact:true}).click();await page.getByRole('button',{name:'Mute conversation',exact:true}).waitFor();
  const literal='Public <script>literal</script> reply '+width;await reply({body:literal});assert.equal((await api('notices')).notices.length,1);
  if(width===1440){const r=await ctx.request.post(base+'/api/v1/control/vrss-test-social-event',{data:{kind:'Mention'}});assert.equal(r.status(),200,await r.text());assert.equal((await ctx.request.post(base+'/api/v1/control/vrss-test-pump')).status(),200);}
  await ctx.request.post(base+'/api/v1/control/system/setup',{data:{complete:true}});await page.goto(base+'/#chat');
  const title=width===1440?'VRSS mention':'New reply in a followed conversation',updates=page.getByRole('region',{name:'VRSS updates',exact:true});const notice=updates.locator('article.vp3-mission-notice').filter({hasText:title}).first();
  await notice.getByRole('button',{name:'Open conversation',exact:true}).click();await notice.getByText(literal,{exact:true}).waitFor();assert.equal(await notice.getByRole('link',{name:'Original post',exact:true}).getAttribute('href'),url);assert.equal(await notice.locator('script').count(),0);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  await notice.getByRole('button',{name:'Mute conversation',exact:true}).click();await notice.getByText('Conversation settings saved.',{exact:true}).waitFor();assert.equal((await api('notices')).notices.length,0);
  const watch=(await api('social.conversations')).conversations.items.find(x=>x.object===url);await api('social.conversation',{url,enabled:false,muted:false,expected_revision:watch.revision,request_id:randomUUID()});await ctx.close();
 }
 console.log('PASS V2G browser 1440/390/320: default-off consent, thread-only subscription, lost-response/reload nonce, mute/no backlog, native Chat conversation/mention, literal text and no overflow');
}finally{await browser.close();child.kill();}
