import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {chromium} from 'playwright';
const base=new URL('../ui/feed-app/',import.meta.url),html=await fs.readFile(new URL('index.html',base),'utf8'),script=await fs.readFile(new URL('app.js',base),'utf8'),css=await fs.readFile(new URL('app.css',base),'utf8');
const browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH}:{})});
try{for(const mode of ['cloud','native']){
 const page=await browser.newPage({viewport:{width:1440,height:900}}),errors=[],calls=[],posts=[],requests=new Map();let account=7,failPost=false,holdFeed=false,releaseFeed,holdPost=false,releasePost,mediaRequests=0;
 const profiles={7:{id:7,display_name:'Owner',username:'owner',bio:'My profile',is_public:false,is_self:true,following:false,counts:{followers:0,following:0},revision:'initial'},8:{id:8,display_name:'Other person',username:'other',bio:'Other profile',is_public:true,is_self:false,following:false,counts:{followers:0,following:0},revision:'other'},9:{id:9,display_name:'New account',username:'new',bio:'',is_public:true,is_self:true,following:false,counts:{followers:0,following:0},revision:'new'}};
 const endpoint=mode==='cloud'?'/api/social-feed-v1.php':'/api/v1/control/homeserver-apps/feed/request';
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('https://media.example/**',r=>{mediaRequests++;return r.fulfill({contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aY1sAAAAASUVORK5CYII=','base64')});});
 await page.route('https://feed.test/**',async route=>{
  const path=new URL(route.request().url()).pathname;
  if(path.endsWith('app.js'))return route.fulfill({contentType:'application/javascript',body:script});
  if(path.endsWith('app.css'))return route.fulfill({contentType:'text/css',body:css});
  if(path===endpoint){const p=route.request().postDataJSON();calls.push(p);const viewer=structuredClone(profiles[account]);let result={};
   if(p.action==='bootstrap')result={viewer};
   else if(p.action==='profile')result={profile:structuredClone(profiles[p.user_id||account])};
   else if(p.action==='profile.update'){profiles[account]={...profiles[account],display_name:p.display_name,username:p.username,bio:p.bio,is_public:p.is_public,revision:'updated'};result={profile:structuredClone(profiles[account])};}
   else if(p.action==='people')result={people:[structuredClone(profiles[8])]};
   else if(p.action==='follow'){profiles[p.user_id].following=p.following;profiles[p.user_id].counts.followers=Number(p.following);result={profile:structuredClone(profiles[p.user_id])};}
   else if(p.action==='post.create'){
    if(!requests.has(p.request_id)){requests.set(p.request_id,posts.length);posts.push({id:'post-'+posts.length,cursor:posts.length+1,body:p.body,media:p.media,visibility:p.visibility,created_at:'2026-10-19T12:00:00Z',author:viewer,can_delete:true});}
    result={post_id:'post-'+requests.get(p.request_id),replayed:true};
    if(failPost){failPost=false;return route.abort('failed');}if(holdPost){holdPost=false;await new Promise(r=>{releasePost=r;});}
   }else if(p.action==='post.delete'){const i=posts.findIndex(x=>x.id===p.id);if(i>=0)posts.splice(i,1);result={deleted:true};}
   else if(p.action==='feed'){result={feed:{items:structuredClone(p.mode==='profile'?posts.filter(x=>x.author.id===p.user_id):posts),has_more:false,next_cursor:0}};if(holdFeed){holdFeed=false;await new Promise(r=>{releaseFeed=r;});}}
   return route.fulfill({json:{ok:true,contract:'vp3.social-feed.v1',viewer_id:account,...result}});
  }
  const config=mode==='cloud'?JSON.stringify({endpoint,csrf:'fixture'}):'{}';return route.fulfill({contentType:'text/html',body:html.replace('<script id="feed-config" type="application/json">{}</script>','<script id="feed-config" type="application/json">'+config+'</script>')});
 });
 await page.goto('https://feed.test/?cloud=1');await page.getByRole('heading',{name:'Your feed',exact:true}).waitFor();
 await page.getByRole('link',{name:'Profile',exact:true}).click();await page.getByRole('button',{name:'Edit Profile',exact:true}).click();
 await page.getByLabel('Display name',{exact:true}).fill('Owner updated');await page.getByLabel('Bio',{exact:true}).fill('My updated bio');await page.getByLabel('Public profile — people can find and follow me',{exact:true}).check();await page.getByRole('button',{name:'Save Profile',exact:true}).click();await page.getByRole('heading',{name:'Owner updated',exact:true}).waitFor();
 assert.equal(profiles[7].is_public,true);assert.equal(await page.locator('.profile img').count(),0,'Missing avatar created a broken image');
 await page.getByRole('link',{name:'Feed',exact:true}).last().click();await page.getByRole('button',{name:'Find people',exact:true}).click();await page.getByRole('button',{name:'Follow Other person',exact:true}).click();await page.getByRole('button',{name:'Unfollow Other person',exact:true}).waitFor();assert.equal(profiles[8].following,true);
 await page.getByRole('link',{name:'Other person',exact:true}).click();await page.getByRole('heading',{name:'Other person',exact:true}).waitFor();await page.getByRole('button',{name:'Unfollow Other person',exact:true}).click();await page.getByRole('button',{name:'Follow Other person',exact:true}).waitFor();assert.equal(profiles[8].following,false);
 await page.evaluate(()=>{window.detachedFollow=[...document.querySelectorAll('button')].find(b=>b.textContent==='Follow');});
 await page.getByRole('link',{name:'Create Post',exact:true}).click();await page.getByLabel('What would you like to share?',{exact:true}).fill('Draft survives navigation <img src=x onerror="window.pwned=1">');
 await page.getByRole('combobox',{name:'Media type',exact:true}).selectOption('image');await page.getByRole('textbox',{name:'Media URL',exact:true}).fill('https://media.example/photo.png');await page.getByRole('button',{name:'Attach media',exact:true}).click();
 const before=calls.length;await page.evaluate(()=>detachedFollow.click());await page.waitForTimeout(30);assert.equal(calls.length,before,'Detached follow button dispatched after navigation');
 await page.reload();await page.getByRole('heading',{name:'Create Post',exact:true}).waitFor();assert.match(await page.getByLabel('What would you like to share?',{exact:true}).inputValue(),/Draft survives/);
 failPost=true;await page.getByRole('button',{name:'Publish Post',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#notice').textContent.length>0);assert.equal(posts.length,1,'Server write was not committed before lost response');
 holdPost=true;await page.getByRole('button',{name:'Publish Post',exact:true}).click();while(!releasePost)await page.waitForTimeout(10);assert.equal(await page.getByLabel('What would you like to share?',{exact:true}).isDisabled(),true,'Publication must freeze the sent draft while awaiting its receipt');releasePost();await page.getByRole('heading',{name:'Owner updated',exact:true}).waitFor();assert.equal(posts.length,1,'Exact retry duplicated post');
 assert.equal(await page.evaluate(()=>window.pwned),undefined);assert.match(await page.locator('article .body').innerText(),/<img/);assert.equal(mediaRequests,0,'External media loaded without clicking');if(process.env.FEED_SCREENSHOTS)await page.screenshot({path:process.env.FEED_SCREENSHOTS+'/feed-profile-'+mode+'.png',fullPage:true});
 await page.getByRole('button',{name:'Load image',exact:true}).click();await page.waitForFunction(()=>document.querySelector('article img')?.complete);assert.equal(mediaRequests,1);
 for(const width of [1440,390,320]){await page.setViewportSize({width,height:900});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true,'Profile/feed horizontal overflow at '+width);}
 await page.getByRole('link',{name:'Create Post',exact:true}).click();assert.equal(await page.getByLabel('What would you like to share?',{exact:true}).inputValue(),'','Published draft was restored');
 await page.getByLabel('What would you like to share?',{exact:true}).fill('Account 7 unsent text');account=9;await page.reload();await page.getByRole('heading',{name:'Create Post',exact:true}).waitFor();assert.equal(await page.getByLabel('What would you like to share?',{exact:true}).inputValue(),'','Another account inherited the draft');
 await page.evaluate(()=>{location.hash='#feed';});await page.getByRole('heading',{name:'Your feed',exact:true}).waitFor();holdFeed=true;await page.getByRole('button',{name:'Explore',exact:true}).click();while(!releaseFeed)await page.waitForTimeout(10);await page.getByRole('link',{name:'Profile',exact:true}).click();await page.getByRole('heading',{name:'New account',exact:true}).waitFor();releaseFeed();await page.waitForTimeout(50);assert.equal(await page.getByRole('heading',{name:'New account',exact:true}).count(),1,'Late feed replaced current profile');
 assert.deepEqual(errors,[]);assert.ok(calls.filter(p=>p.action==='post.create').every(p=>p.request_id===calls.find(p=>p.action==='post.create').request_id));if(mode==='cloud')assert.ok(calls.every(p=>p.csrf_token==='fixture'));
 await page.close();console.log('PASS '+mode+' Feed/profile/follow/post/retry/media/draft/identity/responsive acceptance');
}}finally{await browser.close();}
