import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {chromium} from 'playwright';

const child=spawn(process.env.VP3_TEST_PYTHON||'python',['-u','tests/vrss_v1_server.py'],{stdio:['ignore','pipe','pipe']});
let stderr='';child.stderr.on('data',b=>stderr+=b.toString());
const lines=createInterface({input:child.stdout});
const config=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('VRSS server startup timed out')),60000);lines.on('line',line=>{try{const value=JSON.parse(line);if(value.url){clearTimeout(timer);resolve(value);}}catch{}});child.on('exit',code=>reject(Error(`VRSS server exited ${code}: ${stderr}`)));});
const browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{})});
try{
 for(const width of [1440,390,320]){
  const context=await browser.newContext({viewport:{width,height:960}}),page=await context.newPage();
  let mediaLoads=0;await page.route('https://media.example/**',r=>{mediaLoads++;return r.abort();});
  const origin=config.url,endpoint=origin+'/api/v1/control/homeserver-apps/vrss/request';
  const boot=await context.request.post(origin+'/__owner/session',{headers:{'X-HomeServer-Owner':config.owner}});assert.equal(boot.status(),200);
  await page.goto(origin+'/api/v1/control/homeserver-apps/vp3.feed/preview/');
  await page.getByRole('heading',{name:'Your feed',exact:true}).waitFor();
  assert.equal(await page.title(),'VRSS · HomeServer');
  await page.getByRole('button',{name:'Following',exact:true}).click();
  await page.getByLabel('RSS or Atom feed URL').fill('https://news.example/rss');await page.getByRole('button',{name:'Follow feed',exact:true}).click();
  await page.getByRole('button',{name:'Unfollow',exact:true}).waitFor();await page.getByRole('button',{name:'Close',exact:true}).click();
  await page.getByRole('heading',{name:'New album',exact:true}).waitFor();assert.equal(mediaLoads,0);assert.equal(await page.evaluate(()=>Boolean(window.injected)),false);
  if(process.env.VRSS_SCREENSHOT_DIR)await page.screenshot({path:`${process.env.VRSS_SCREENSHOT_DIR}/vrss-feed-${width}.png`,fullPage:true});
  await page.getByRole('link',{name:'Create Post',exact:true}).click();
  await page.getByLabel('What would you like to share?').fill('Local draft '+width);await page.reload();await page.getByLabel('What would you like to share?').waitFor();assert.equal(await page.getByLabel('What would you like to share?').inputValue(),'Local draft '+width);
  // Lose the response after the real write commits; retry uses the same receipt.
  let lost=true;await page.route(endpoint,async route=>{const p=route.request().postDataJSON();if(p.action==='post.create'&&lost){lost=false;await route.fetch();return route.abort('failed');}return route.continue();});
  await page.getByRole('button',{name:'Publish Post',exact:true}).click();await page.waitForFunction(()=>!document.querySelector('button.primary')?.disabled);await page.getByRole('button',{name:'Publish Post',exact:true}).click();
  await page.getByRole('heading',{name:'Your posts',exact:true}).waitFor();await page.locator('.card .body').filter({hasText:'Local draft '+width}).waitFor();assert.equal(await page.locator('.card .body').filter({hasText:'Local draft '+width}).count(),1);
  await page.unroute(endpoint);
  await page.getByRole('button',{name:'Edit Profile',exact:true}).click();await page.getByLabel('Display name',{exact:true}).fill('Dave '+width);await page.getByRole('button',{name:'Save Profile',exact:true}).click();await page.getByRole('heading',{name:'Dave '+width,exact:true}).waitFor();
  // A pending publication locks editable fields and media controls.
  await page.getByRole('link',{name:'Create Post',exact:true}).click();assert.equal(await page.getByLabel('What would you like to share?').inputValue(),'');await page.getByLabel('What would you like to share?').fill('Pending '+width);
  let release;const pending=new Promise(r=>release=r);let entered;const didEnter=new Promise(r=>entered=r);await page.route(endpoint,async route=>{if(route.request().postDataJSON().action==='post.create'){entered();await pending;}return route.continue();});
  await page.getByRole('button',{name:'Publish Post',exact:true}).click();await didEnter;assert.equal(await page.getByLabel('What would you like to share?').isDisabled(),true);assert.equal(await page.getByRole('button',{name:'Attach media',exact:true}).isDisabled(),true);release();await page.getByRole('heading',{name:'Your posts',exact:true}).waitFor();await page.unroute(endpoint);
  // A late feed response cannot overwrite the current profile.
  let releaseFeed;const waitFeed=new Promise(r=>releaseFeed=r);let enteredFeed;const didFeed=new Promise(r=>enteredFeed=r);await page.route(endpoint,async route=>{const p=route.request().postDataJSON();if(p.action==='feed'&&p.mode!=='profile'){const response=await route.fetch();enteredFeed();await waitFeed;return route.fulfill({response});}return route.continue();});
  await page.getByRole('link',{name:'Feed',exact:true}).click();await didFeed;await page.getByRole('link',{name:'Profile',exact:true}).click();await page.getByRole('heading',{name:'Dave '+width,exact:true}).waitFor();releaseFeed();await page.waitForTimeout(80);assert.equal(await page.getByRole('heading',{name:'Your feed',exact:true}).count(),0);await page.unroute(endpoint);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`overflow at ${width}`);
  await page.getByRole('link',{name:'Feed',exact:true}).click();await page.getByRole('button',{name:'Following',exact:true}).click();await page.getByRole('button',{name:'Unfollow',exact:true}).click();await page.getByRole('button',{name:'Unfollow',exact:true}).waitFor({state:'detached'});await page.getByRole('button',{name:'Close',exact:true}).click();await page.getByRole('heading',{name:'Your feed',exact:true}).waitFor();await page.getByRole('heading',{name:'New album',exact:true}).waitFor({state:'detached'});
  await context.close();
 }
 console.log('PASS real VRSS API/SQLite/browser at 1440/390/320: local drafts, RSS follow/unfollow, no passive media or XSS, exact lost-response retry, immutable pending form and stale response isolation');
}finally{await browser.close();child.kill('SIGTERM');lines.close();}
