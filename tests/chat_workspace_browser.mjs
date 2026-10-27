// Real Chromium acceptance against the complete owner UI and an isolated HomeServer.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import net from 'node:net';
import {spawn} from 'node:child_process';
import {createRequire} from 'node:module';
const require = createRequire(import.meta.url);
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const root = process.cwd();
const dataDir = await fs.mkdtemp(path.join(os.tmpdir(), 'homeserver-chat-browser-'));
const probe = net.createServer();
await new Promise(resolve => probe.listen(0, '127.0.0.1', resolve));
const port = probe.address().port;
await new Promise(resolve => probe.close(resolve));
const base = `http://127.0.0.1:${port}`;
let browser, child;
let stderr = '';
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function assertCanvasLayout(page, regionId) {
  const layout = await page.evaluate(id => {
    const region = document.getElementById(id);
    const bounds = region.getBoundingClientRect();
    const composer = document.getElementById('chatForm').getBoundingClientRect();
    const panel = document.querySelector('#view-chat .chat-panel').getBoundingClientRect();
    return {regionBottom: bounds.bottom, composerTop: composer.top, composerBottom: composer.bottom,
      composerLeft: composer.left, composerRight: composer.right, panelLeft: panel.left, panelRight: panel.right,
      viewportHeight: innerHeight, viewportWidth: innerWidth, pageWidth: document.documentElement.scrollWidth,
      regionWidth: region.clientWidth, regionScrollWidth: region.scrollWidth,
      overflow: getComputedStyle(region).overflowY, composerPosition: getComputedStyle(document.getElementById('chatForm')).position};
  }, regionId);
  assert.equal(layout.overflow, 'visible', 'Chat/setup have no independent scrollbar');
  assert.equal(layout.composerPosition, 'sticky');
  if(layout.viewportWidth >= 1440) assert.ok(layout.composerRight-layout.composerLeft>1000, 'Wide desktop composer uses the right canvas');
  assert.ok(layout.composerBottom <= layout.viewportHeight && layout.composerLeft >= layout.panelLeft - 1 && layout.composerRight <= layout.panelRight + 1,
    `Composer stays fully inside the canvas: ${JSON.stringify(layout)}`);
  assert.ok(Math.abs((layout.composerLeft + layout.composerRight) - (layout.panelLeft + layout.panelRight)) <= 2,
    `Composer stays centered without legacy floating offsets: ${JSON.stringify(layout)}`);
  assert.ok(layout.pageWidth <= layout.viewportWidth + 1 && layout.regionScrollWidth <= layout.regionWidth + 1,
    `Canvas has no horizontal overflow: ${JSON.stringify(layout)}`);
}

async function assertOnboardingLayout(page) {
  await assertCanvasLayout(page, 'chatOnboardingCanvas');
  assert.equal(await page.locator('#chatOnboardingCanvas .onboard-visual-consent:visible').count(), 0, 'Basic setup asks no camera/biometric questions');
  await page.locator('#onboardOptional').evaluate(node => {node.open=true;});
  await page.locator('.onboard-feature').evaluateAll(nodes=>nodes.forEach(node=>{node.open=true;}));
  const consents = page.locator('#chatOnboardingCanvas .onboard-visual-consent:visible');
  assert.ok(await consents.count() >= 6, 'The real onboarding consent controls are present');
  for (const consent of await consents.all()) {
    await consent.scrollIntoViewIfNeeded();
    const bounds = await consent.evaluate(label => {
      const card = label.closest('.onboard-step').getBoundingClientRect();
      const input = label.querySelector('input').getBoundingClientRect();
      const text = label.querySelector('span').getBoundingClientRect();
      return {cardLeft: card.left, cardRight: card.right, inputLeft: input.left, inputRight: input.right,
        inputWidth: input.width, textLeft: text.left, textRight: text.right, textWidth: text.width};
    });
    assert.ok(bounds.inputWidth <= 24 && bounds.inputLeft >= bounds.cardLeft && bounds.textLeft >= bounds.inputRight && bounds.textWidth > 0 && bounds.textRight <= bounds.cardRight,
      `Checkbox and consent text remain readable inside their card: ${JSON.stringify(bounds)}`);
  }
  await assertCanvasLayout(page, 'chatOnboardingCanvas');
  await page.locator('.onboard-feature').evaluateAll(nodes=>nodes.forEach(node=>{node.open=false;}));
  await page.locator('#onboardOptional').evaluate(node=>{node.open=false;});
}
try {
  browser = await chromium.launch({headless: true, ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH ? {executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH} : {})});

  // Cap the pre-fix runaway observer so this regression fails clearly rather
  // than hanging the entire test runner. Normal production code has no cap.
  const freezePage = await browser.newPage();
  await freezePage.setContent('<select id="voiceTtsVoice"><option value="test">Test</option></select><script data-agent-voice-profile></script>');
  await freezePage.evaluate(() => {
    const NativeObserver = window.MutationObserver;
    window.catalogMutations = 0;
    window.catalogTextWrites = 0;
    const text = Object.getOwnPropertyDescriptor(Node.prototype, 'textContent');
    Object.defineProperty(Node.prototype, 'textContent', {...text, set(value) {
      if (this.id?.startsWith('voicePack')) window.catalogTextWrites++;
      text.set.call(this, value);
    }});
    window.MutationObserver = class extends NativeObserver {
      constructor(callback) { super((records, observer) => {
        if (++window.catalogMutations > 500) { observer.disconnect(); return; }
        callback(records, observer);
      }); }
    };
    window.fetch = async () => ({ok: true, json: async () => ({voices: [{key: 'test', label: 'Test', available: true, can_preview: true}]})});
  });
  const catalogSource = await fs.readFile(process.env.VOICE_CATALOG_SOURCE || path.join(root, 'ui/voice-catalog.js'), 'utf8');
  await freezePage.addScriptTag({content: catalogSource});
  await freezePage.evaluate(() => window.dispatchEvent(new Event('homeserver:voice-settings-loaded')));
  await delay(150);
  const mutations = await freezePage.evaluate(() => window.catalogMutations);
  assert.ok(mutations < 10, `Voice status must become idle; observed ${mutations} feedback callbacks`);
  assert.equal(await freezePage.locator('#voicePackBadge').textContent(), 'Ready');
  const writes = await freezePage.evaluate(() => window.catalogTextWrites);
  await freezePage.evaluate(() => {
    const message = document.createElement('p'); message.textContent = 'An unrelated chat update'; document.body.appendChild(message);
  });
  await delay(100);
  assert.equal(await freezePage.evaluate(() => window.catalogTextWrites), writes, 'Chat updates must not rerender the voice catalog');
  await freezePage.close();
  console.log('PASS: voice catalog settles without feedback callbacks');

  child = spawn(process.env.HOMESERVER_TEST_PYTHON || 'python', ['-u', '-c', `
from app.runtime import app
from app.security import OWNER_CONTROL_TOKEN
from app.services import providers
import uvicorn, json
def fixture_generate(messages, model_override=None):
    routing = messages[0]['content'].startswith('You are the lead agent routing')
    content = json.dumps({'route':'chat','reason':'The fixture can answer directly','plan':None}) if routing else 'Browser acceptance reply.'
    return {'provider':'ollama', 'model':model_override or 'test-model', 'content':content}
providers.generate_ollama = fixture_generate
from app.services import brain
original_conversation = brain.get_conversation
def fixture_conversation(*args, **kwargs):
    result = original_conversation(*args, **kwargs)
    for message in result['messages']:
        if message['role'] == 'assistant':
            message['research_reports'] = [
                {'id':'11111111-1111-4111-8111-111111111111','title':'Captured studio findings <img src=unsafe> ' + 'LongTitle' * 25,'url':'https://one.example/research-report.php?report=11111111-1111-4111-8111-111111111111'},
                {'id':'invalid','title':'Unsafe reference','url':'javascript:alert(1)'}]
    return result
brain.get_conversation = fixture_conversation
print('BROWSER_BOOTSTRAP ' + OWNER_CONTROL_TOKEN, flush=True)
uvicorn.run(app, host='127.0.0.1', port=${port}, log_level='error')
`], {cwd: root, env: {...process.env, HOMESERVER_DATA_DIR: dataDir, NO_PROXY: 'localhost,127.0.0.1'}});
  child.stderr.on('data', chunk => { stderr = (stderr + chunk).slice(-5000); });
  const token = await new Promise((resolve, reject) => {
    let output = '';
    const timer = setTimeout(() => reject(new Error('HomeServer bootstrap timed out: ' + stderr)), 30000);
    child.stdout.on('data', chunk => {
      output += chunk;
      const match = output.match(/BROWSER_BOOTSTRAP ([^\r\n]+)/);
      if (match) { clearTimeout(timer); resolve(match[1]); }
    });
    child.once('exit', code => {clearTimeout(timer); reject(new Error(`HomeServer exited ${code}: ${stderr}`));});
  });
  const context = await browser.newContext({viewport: {width: 1440, height: 900}});
  if (process.env.AGENT_VOICE_PROFILE_SOURCE) {
    const source = await fs.readFile(process.env.AGENT_VOICE_PROFILE_SOURCE, 'utf8');
    await context.route('**/assets/agent-voice-profile.js*', route => route.fulfill({contentType: 'text/javascript', body: source}));
  }
  for (let attempt = 0; ; attempt++) {
    try { if ((await context.request.get(base + '/api/v1/health')).ok()) break; } catch (_) {}
    if (attempt >= 100) throw new Error('HomeServer did not listen: ' + stderr);
    await delay(100);
  }
  assert.ok((await context.request.post(base + '/__owner/session', {headers: {'x-homeserver-owner': token}})).ok());
  assert.ok((await context.request.post(base + '/api/v1/control/system/setup', {data: {complete: true}})).ok());
  assert.ok((await context.request.put(base + '/api/v1/control/provider', {data: {base_url: 'http://127.0.0.1:9', model: 'test-model', enabled: true}})).ok());
  console.log('PASS: isolated owner server and model fixture ready');
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.stack || error.message));
  let microphoneCalls = 0;
  await page.exposeFunction('countMicrophoneCall', () => {microphoneCalls++;});
  await page.addInitScript(() => {
    window.voiceFixture = {recognizers: [], spoken: [], inputWrites: [], captureIntents: []};
    const value=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value');
    Object.defineProperty(HTMLTextAreaElement.prototype,'value',{...value,set(text){
      if(this.id==='chatInput'){window.voiceFixture.inputWrites.push({text,stack:new Error().stack});window.voiceFixture.inputWrites.splice(0,Math.max(0,window.voiceFixture.inputWrites.length-12));}
      value.set.call(this,text);
    }});
    window.SpeechRecognition = class {
      constructor() { window.voiceFixture.recognizers.push(this); }
      start() { this.active = true; queueMicrotask(() => this.onstart?.()); }
      stop() { this.active = false; queueMicrotask(() => this.onend?.()); }
      abort() { this.stop(); }
    };
    window.SpeechSynthesisUtterance = class { constructor(text) { this.text = text; } };
    Object.defineProperty(window, 'speechSynthesis', {value: {
      getVoices: () => [], addEventListener() {}, removeEventListener() {}, cancel() {},
      speak(utterance) { window.voiceFixture.spoken.push(utterance.text); queueMicrotask(() => {utterance.onstart?.(); utterance.onend?.();}); },
    }});
    const original = navigator.mediaDevices?.getUserMedia?.bind(navigator.mediaDevices);
    if (original) navigator.mediaDevices.getUserMedia = (...args) => {window.voiceFixture.captureIntents.push({voiceActive:document.getElementById('voiceInputButton')?.getAttribute('aria-pressed')==='true',audio:args[0]?.audio,video:args[0]?.video===true});window.countMicrophoneCall(); return original(...args);};
  });
  await page.goto(base + '/#chat');
  await page.waitForFunction(() => window.HomeServerChatOptions && document.querySelector('#chatOptionsVoice #dictateInputButton')).catch(error => {throw new Error(error.message + '\nBrowser errors: ' + JSON.stringify(errors));});
  console.log('PASS: complete owner page loaded');
  assert.equal(await page.locator('#view-chat .section-intro').count(), 0);
  assert.equal(await page.locator('.topbar').isVisible(), false);
  assert.equal(await page.locator('#chatContextControls').isVisible(), false);
  assert.equal(await page.locator('#chatForm button:not([data-orb-chat]):visible').count(), 2);
  assert.equal(await page.locator('#chatForm button[data-orb-chat]:visible').count(), 1, 'The requested chatbar button is present once');
  await page.waitForFunction(() => document.querySelectorAll('#chatKnowledgeControls>details').length === 4 && document.querySelector('#chatToolControls .vp3-agent-teams-a3'));
  assert.equal(await page.locator('#view-chat .chat-panel>.project-knowledge-r6,#view-chat .chat-panel>.knowledge-actions-r8,#view-chat .chat-panel>.vp3-chat-task-mode,#view-chat .chat-panel>.vp3-agent-teams-a3').count(), 0);
  assert.equal(await page.locator('.project-knowledge-r6').isVisible(), false);
  // A late module insertion must be moved without cloning or resetting edits.
  await page.evaluate(() => {
    const panel = document.querySelector('.project-knowledge-r5');
    panel.querySelector('input').value = 'Retained source search';
    window.retainedKnowledgePanel = panel;
    document.getElementById('chatForm').before(panel);
  });
  await page.waitForFunction(() => document.querySelector('#chatKnowledgeControls .project-knowledge-r5') === window.retainedKnowledgePanel);
  const plusRect = await page.locator('#chatOptionsButton').boundingBox();
  const inputRect = await page.locator('#chatInput').boundingBox();
  assert.ok(plusRect.x > inputRect.x + inputRect.width / 2, '+ belongs on the right');
  await page.locator('#chatOptionsButton').click();
  assert.equal(await page.locator('#chatOptionsDialog').evaluate(node => node.open), true);
  assert.equal(await page.locator('#chatContextControls').isVisible(), true);
  assert.equal(await page.locator('.project-knowledge-r5 input').first().inputValue(), 'Retained source search');
  await page.keyboard.press('ArrowRight');
  assert.equal(await page.locator('#chatOptionsTabVoice').getAttribute('aria-selected'), 'true');
  await page.waitForFunction(() => document.querySelector('#voiceTtsVoice')?.options.length > 0);
  assert.equal(await page.locator('#voiceSettingsForm').count(), 1);
  assert.equal(await page.locator('#chatOptionsVoice #voiceSettingsForm').isVisible(), true);
  await page.waitForFunction(() => document.querySelector('#chatAgentVoicePanel #agentVoiceChoice')?.options.length > 1);
  assert.equal(microphoneCalls, 0, 'Opening preferences must never start microphone capture');
  for (let i = 0; i < 12; i++) {
    await page.keyboard.press('Tab');
    assert.equal(await page.locator('#chatOptionsDialog').evaluate(node => node.contains(document.activeElement)), true, 'Dialog traps keyboard focus');
  }
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('#chatOptionsButton').evaluate(node => node === document.activeElement), true);
  await page.locator('#chatOptionsButton').click();
  await page.locator('#chatOptionsTabMore').click();
  await page.getByLabel('Work mode', {exact: true}).selectOption('chat');
  await page.locator('#chatOptionsClose').click();
  await page.locator('#chatOptionsButton').click();
  await page.locator('#chatOptionsTabMore').click();
  assert.equal(await page.getByLabel('Work mode', {exact: true}).inputValue(), 'chat');
  await page.getByLabel('Work mode', {exact: true}).selectOption('automatic');
  await page.locator('#chatOptionsClose').click();

  assert.equal(await page.locator('#vp3ChatTaskMode').inputValue(), 'automatic');
  await page.locator('#chatInput').fill('Browser acceptance message');
  await page.locator('#chatForm [type="submit"]').click();
  await page.waitForFunction(() => document.querySelector('#chatMessages')?.textContent.includes('Browser acceptance reply.'));
  await page.waitForFunction(() => !document.querySelector('#chatForm [type="submit"]').disabled);
  assert.equal(await page.locator('#chatForm [type="submit"]').isEnabled(), true);
  await page.locator('.hs-chat-research-source').first().waitFor();
  assert.equal(await page.locator('a.hs-chat-research-source[href^="javascript:"]').count(),0);
  assert.equal(await page.locator('img[src=unsafe]').count(),0);
  await page.locator('.hs-chat-research-source').first().scrollIntoViewIfNeeded();
  assert.match(await page.locator('.hs-chat-research-source').first().innerText(),/Captured studio findings/);
  await page.locator('#chatOptionsButton').click();
  await page.waitForFunction(() => !document.querySelector('#contextUseMemory').disabled);
  assert.equal(await page.locator('#contextUseMemory').isEnabled(), true);
  await page.locator('#contextUseMemory').uncheck();
  await page.waitForFunction(() => document.querySelector('#contextEngineStatus')?.textContent.includes('saved'));
  await page.locator('#chatOptionsClose').click();
  await page.reload();
  await page.waitForFunction(() => window.HomeServerChatOptions && document.querySelector('[data-brain-conversation].active'));
  await page.locator('#chatOptionsButton').click();
  await page.waitForFunction(() => !document.querySelector('#contextUseMemory')?.checked && !document.querySelector('#contextUseMemory')?.disabled);
  await page.locator('#chatOptionsTabVoice').click();
  await page.waitForFunction(() => document.querySelector('#voiceTtsVoice')?.options.length > 0);
  await page.locator('#voiceSpeakingRate').fill('1.15');
  await page.locator('#voiceSettingsForm [type="submit"]').click();
  await page.waitForFunction(() => !document.querySelector('#chatOptionsDialog').open);
  const savedVoice = await (await context.request.get(base + '/api/v1/control/voice/settings')).json();
  assert.equal(savedVoice.preferences.speaking_rate, 1.15);
  await page.locator('#chatOptionsButton').click();
  await page.locator('#chatOptionsTabVoice').click();
  let releaseProfile, profileRequested = false;
  const profileGate = new Promise(resolve => {releaseProfile = resolve;});
  await page.route('**/api/v1/control/voice/agents/1/profile', async route => {
    if (route.request().method() !== 'GET') {await route.continue(); return;}
    const response = await route.fetch();
    profileRequested = true;
    await profileGate;
    await route.fulfill({response});
  });
  await page.evaluate(() => {window.pendingProfileRefresh = window.HomeServerAgentVoiceProfile.load(true);});
  for (let i = 0; !profileRequested; i++) {if (i > 100) throw Error('Profile refresh was not requested'); await delay(50);}
  await page.locator('#agentVoiceRateEnabled').check();
  await page.locator('#agentVoiceRate').fill('1.2');
  releaseProfile();
  await page.evaluate(() => window.pendingProfileRefresh);
  assert.equal(await page.locator('#agentVoiceRateEnabled').isChecked(), true, 'Late refresh preserves the draft');
  assert.equal(await page.locator('#agentVoiceRate').inputValue(), '1.2', 'Late refresh preserves edited speed');
  await page.unroute('**/api/v1/control/voice/agents/1/profile');
  let releaseStaleProfile, staleProfileRequested = false;
  const staleProfileGate = new Promise(resolve => {releaseStaleProfile = resolve;});
  await page.route('**/api/v1/control/voice/agents/1/profile', async route => {
    if (route.request().method() !== 'GET') {await route.continue(); return;}
    const response = await route.fetch();
    staleProfileRequested = true;
    await staleProfileGate;
    await route.fulfill({response});
  });
  await page.evaluate(() => {window.staleProfileRefresh = window.HomeServerAgentVoiceProfile.load(true);});
  for (let i = 0; !staleProfileRequested; i++) {if (i > 100) throw Error('Stale profile refresh was not requested'); await delay(50);}
  await page.locator('#agentVoiceProfileForm [type="submit"]').click();
  await page.waitForFunction(() => document.querySelector('#agentVoiceSaved')?.textContent.includes('Saved locally'));
  releaseStaleProfile();
  await page.evaluate(() => window.staleProfileRefresh);
  assert.equal(await page.locator('#agentVoiceRateEnabled').isChecked(), true, 'Late pre-save GET cannot reset the acknowledged profile');
  assert.equal(await page.locator('#agentVoiceRate').inputValue(), '1.2');
  await page.unroute('**/api/v1/control/voice/agents/1/profile');
  await page.locator('#chatOptionsClose').click();
  const profile = await (await context.request.get(base + '/api/v1/control/voice/agents/1/profile')).json();
  assert.equal(profile.overrides.speaking_rate, 1.2);
  await page.locator('#chatOptionsButton').click();
  await page.locator('#chatOptionsTabMore').click();
  await page.waitForFunction(() => document.querySelector('#chatMoreActions #chatAgentSelect'));
  assert.equal(await page.locator('#chatMoreActions #agentWorkflowToggle').isVisible(), true);
  await page.locator('#agentWorkflowToggle').click();
  assert.equal(await page.locator('#chatOptionsDialog').evaluate(node => node.open), false);
  assert.equal(await page.locator('#agentWorkflowPanel').isVisible(), true);
  await page.locator('#chatOptionsButton').click();
  await page.locator('#chatOptionsTabMore').click();
  await page.locator('#agentWorkflowToggle').click();
  assert.equal(await page.locator('#agentWorkflowPanel').isVisible(), false);
  await page.locator('#chatOptionsButton').click();
  await page.locator('#chatOptionsTabMore').click();
  await page.locator('#chatOnboardingToggle').click();
  assert.equal(await page.locator('#chatOptionsDialog').evaluate(node => node.open), false);
  assert.equal(await page.locator('#chatOnboardingCanvas').isVisible(), true);
  for (const viewport of [{width: 1440, height: 900}, {width: 1024, height: 768}, {width: 390, height: 844}, {width: 320, height: 640}]) {
    await page.setViewportSize(viewport);
    await assertOnboardingLayout(page);
  }
  assert.equal(microphoneCalls, 0, 'Inspecting consent controls never starts capture');
  await page.setViewportSize({width: 1440, height: 900});
  let setupState = {setup:{complete:true},cloud:{paired:true,connected:false},pairing:{state:'paired'},provision:{phase:'ready'}};
  await page.route('**/api/v1/control/onboarding/summary*', route => route.fulfill({json: setupState}));
  await page.locator('#onboardCheckConnection').click();
  await page.waitForFunction(()=>document.querySelector('#onboardFeedback').textContent.includes('still retrying'));
  assert.equal(await page.locator('#onboardCloud').getAttribute('data-complete'),'false');
  assert.equal(await page.locator('#onboardFinish').isVisible(),false,'Saved pairing is not live completion');
  setupState={...setupState,cloud:{paired:true,connected:true}};
  await page.locator('#onboardCheckConnection').click();
  await page.waitForFunction(()=>document.querySelector('#onboardFinish').hidden===false);
  assert.equal(await page.locator('#onboardCloud').getAttribute('data-complete'),'true');
  // The real server has no paired connection: a late disconnect must reject finish.
  await page.locator('#onboardFinish').click();
  await page.waitForFunction(()=>document.querySelector('#onboardFeedback').textContent.includes('not connected yet'));
  assert.equal(await page.locator('#chatOnboardingCanvas').isVisible(),true);
  setupState={...setupState,cloud:{paired:false,connected:false}};
  await page.locator('#onboardCheckConnection').click();
  await page.waitForFunction(()=>!document.querySelector('#onboardLater').hidden);
  await page.locator('#onboardLater').click();
  await page.locator('#chatOnboardingCanvas').waitFor({state:'hidden'});
  await page.unroute('**/api/v1/control/onboarding/summary*');
  await page.locator('#chatOptionsButton').click();
  await page.locator('#chatOptionsTabMore').click();
  await page.locator('#chatBrainToggle').click();
  assert.equal(await page.locator('#chatBrainDrawer').getAttribute('aria-hidden'), 'false');
  await page.locator('#closeChatBrain').click();
  await page.locator('#chatOptionsButton').click();
  await page.locator('#chatOptionsTabVoice').click();
  await page.locator('#hsTranscriptOpen').click();
  assert.equal(await page.locator('#chatOptionsDialog').evaluate(node => node.open), false);
  assert.equal(await page.locator('#hsTranscriptionCanvas').isVisible(), true);
  await page.locator('#hsTranscriptClose').click();

  // Exercise actual native Chat + planner + conversation voice with only the
  // browser speech services mocked. A delayed route must not restart capture.
  let releaseVoiceRoute, voiceRouteGate = null, failVoiceRoute = false;
  let voiceSends = 0;
  page.on('request', request => {if (new URL(request.url()).pathname === '/api/v1/control/chat' && request.method() === 'POST') voiceSends++;});
  const missionRoute = '**/api/v1/control/agent-browser-workspaces';
  await page.route(missionRoute, async route => {
    if (route.request().postDataJSON()?.action !== 'task.route') return route.continue();
    if (voiceRouteGate) await voiceRouteGate;
    if (failVoiceRoute) return route.fulfill({status:503,json:{ok:false,error:'Routing is unavailable'}});
    return route.continue();
  });
  async function speak(text, final = true) {
    await page.evaluate(({text,final}) => {
      const recognizer = window.voiceFixture.recognizers.findLast(item => item.active);
      if (!recognizer) throw Error('No active conversation recognizer');
      const result = [{transcript:text}]; result.isFinal = final;
      recognizer.onresult({resultIndex:0,results:[result]});
    }, {text,final});
  }
  async function listening() { await page.waitForFunction(() => document.querySelector('#voiceInputButton.listening') && window.voiceFixture.recognizers.some(item => item.active)).catch(async error=>{throw new Error(error.message+' '+JSON.stringify(await page.evaluate(()=>({input:document.querySelector('#chatInput').value,voice:document.querySelector('#voiceInputButton').outerHTML,flash:document.querySelector('#flash').textContent,active:window.voiceFixture.recognizers.map(x=>x.active),writes:window.voiceFixture.inputWrites}))));}); }
  await page.locator('#chatOptionsButton').click();
  await page.locator('#chatOptionsTabVoice').click();
  await page.locator('#voiceInputButton').click();
  await listening();
  assert.equal(await page.locator('#chatOptionsDialog').evaluate(node => node.open), false);
  await speak('A native voice', false);
  assert.equal(await page.locator('#chatInput').inputValue(), 'A native voice');
  voiceRouteGate = new Promise(resolve => {releaseVoiceRoute = resolve;});
  await speak('A native voice question');
  await page.waitForFunction(() => document.querySelector('#chatInput').disabled);
  const recognitionCount = await page.evaluate(() => window.voiceFixture.recognizers.length);
  await delay(1000);
  assert.equal(await page.evaluate(() => window.voiceFixture.recognizers.length), recognitionCount, 'Routing must keep capture paused');
  assert.equal(await page.locator('#chatOptionsDialog').evaluate(node => node.open), false, 'Ordinary routing keeps tools closed');
  releaseVoiceRoute(); voiceRouteGate = null;
  await page.waitForFunction(() => window.voiceFixture.spoken.length === 1);
  await listening();
  assert.equal(voiceSends, 1, 'One spoken input sends one canonical Chat request');
  assert.match(await page.evaluate(() => window.voiceFixture.spoken[0]), /Browser acceptance reply/);
  failVoiceRoute = true;
  await speak('A failed native route');
  await page.waitForFunction(() => document.querySelector('#chatOptionsDialog').open);
  assert.match(await page.locator('.vp3-teams-status').innerText(), /Routing is unavailable/);
  assert.equal(voiceSends, 1, 'Uncertain routing must not dispatch Chat');
  await page.locator('#chatOptionsClose').click();
  await page.waitForFunction(() => !window.HomeServerConversationVoice.isEnabled());
  assert.equal(await page.locator('#chatInput').inputValue(), 'A failed native route', 'Recovery never starts capture over a failed draft');
  await page.locator('#chatInput').evaluate(input=>{input.value='';input.dispatchEvent(new Event('input',{bubbles:true}));});
  assert.equal(await page.locator('#chatInput').inputValue(),'','Clearing a failed draft must finish before enabling capture');
  await page.locator('#chatOptionsButton').click();
  await page.locator('#chatOptionsTabVoice').click();
  await page.locator('#voiceInputButton').click();
  await listening();
  failVoiceRoute = false;
  voiceRouteGate = new Promise(resolve => {releaseVoiceRoute = resolve;});
  await speak('Stop this delayed native request');
  await page.waitForFunction(() => document.querySelector('#chatInput').disabled);
  await page.locator('#chatCaptureStop').click();
  releaseVoiceRoute(); voiceRouteGate = null;
  await page.waitForFunction(() => !document.querySelector('#chatInput').disabled);
  assert.equal(voiceSends, 1, 'Stop rejects a late route instead of submitting it');
  assert.equal(await page.locator('#chatInput').inputValue(), 'Stop this delayed native request');
  assert.equal(await page.locator('#voiceInputButton').getAttribute('aria-pressed'), 'false');
  assert.equal(await page.evaluate(() => window.HomeServerConversationVoice.isEnabled()), false);
  await page.unroute(missionRoute);
  // Explicit research keeps its consent review available in the options dialog.
  await page.locator('#chatOptionsButton').click();
  await page.locator('#chatOptionsTabMore').click();
  await page.getByLabel('Work mode', {exact:true}).selectOption('chrome');
  await page.locator('#chatOptionsClose').click();
  await page.locator('#chatInput').fill('Research a source');
  await page.locator('#chatForm [type="submit"]').click();
  await page.waitForFunction(() => document.querySelector('#chatOptionsDialog').open);
  assert.equal(await page.locator('.vp3-chat-research').isVisible(), true);
  assert.equal(voiceSends, 1);
  await page.getByLabel('Work mode', {exact:true}).selectOption('automatic');
  await page.locator('#chatOptionsClose').click();
  console.log('PASS: native voice interim input, delayed routing, one spoken reply, listening resume, failed routes, Stop cancellation, and visible Chrome research consent');

  // Recovery uses the real owner session, persisted conversations and provider
  // boundary. Transport failures never trigger a replay or clear the draft.
  const recoveryPage = await context.newPage();
  recoveryPage.on('pageerror', error => errors.push(error.stack || error.message));
  let recoverySends = 0, failInference = true, releaseInference, inferenceGate = null;
  recoveryPage.on('request', request => {if(new URL(request.url()).pathname === '/api/v1/control/chat' && request.method()==='POST')recoverySends++;});
  await recoveryPage.route('**/api/v1/control/chat', async route => {
    if(inferenceGate)await inferenceGate;
    if(failInference)return route.fulfill({status:503,json:{detail:'Provider unavailable'}});
    return route.continue();
  });
  await recoveryPage.goto(base + '/#chat');
  await recoveryPage.waitForFunction(() => window.HomeServerChatTurn?.conversationId() && window.HomeServerAgentRouting?.getSelectedAgentId());
  const selectedConversation=await recoveryPage.evaluate(() => window.HomeServerChatTurn.conversationId());
  await recoveryPage.locator('#chatInput').fill('Keep this failed provider message');
  await recoveryPage.locator('#chatForm [type="submit"]').click();
  await recoveryPage.waitForFunction(() => !document.querySelector('#chatInput').disabled && document.querySelector('#chatSessionStatus')?.hidden===false);
  assert.equal(await recoveryPage.locator('#chatInput').inputValue(),'Keep this failed provider message');
  assert.equal(recoverySends,1);
  // A newer server conversation cannot replace the explicitly selected one.
  const secondary=await (await context.request.post(base+'/api/v1/control/agents',{data:{name:'Recovery specialist',instructions:'Help with recovery.',model:'test-model'}})).json();
  const newer=await (await context.request.post(base+'/api/v1/control/chat',{data:{message:'A newer specialist conversation',agent_id:secondary.agent.id}})).json();
  await recoveryPage.reload();
  await recoveryPage.waitForFunction(id => window.HomeServerChatTurn?.conversationId()===id && document.querySelector('#chatInput').value==='Keep this failed provider message',selectedConversation);
  assert.equal(recoverySends,1,'Refresh restores context and draft without replaying a failed turn');
  // Opening the specialist conversation and reloading restores its bound Agent.
  await recoveryPage.locator(`[data-brain-conversation="${newer.conversation_id}"]`).click();
  await recoveryPage.waitForFunction(id => window.HomeServerChatTurn?.conversationId()===id,newer.conversation_id);
  await recoveryPage.locator('#chatInput').fill('Specialist draft');
  await recoveryPage.reload();
  await recoveryPage.waitForFunction(({id,agent}) => window.HomeServerChatTurn?.conversationId()===id && window.HomeServerAgentRouting?.getSelectedAgentId()===agent,{id:newer.conversation_id,agent:secondary.agent.id});
  assert.equal(await recoveryPage.locator('#chatInput').inputValue(),'Specialist draft');
  // An older binding response must not override a newer conversation's Agent.
  let releaseBinding,oldBindingRequested=false;
  const bindingGate=new Promise(resolve=>{releaseBinding=resolve;});
  const oldBindingPattern=`**/api/v1/control/conversations/${selectedConversation}/routing`;
  await recoveryPage.route(oldBindingPattern,async route=>{oldBindingRequested=true;const response=await route.fetch();await bindingGate;await route.fulfill({response});});
  await recoveryPage.evaluate(id=>{window.oldBinding=window.HomeServerAgentRouting.syncConversationBinding(id);},selectedConversation);
  for(let attempt=0;!oldBindingRequested;attempt++){if(attempt>100)throw Error('Old binding not requested');await delay(25);}
  await recoveryPage.evaluate(id=>window.HomeServerAgentRouting.syncConversationBinding(id),newer.conversation_id);
  releaseBinding();await recoveryPage.evaluate(()=>window.oldBinding);await recoveryPage.unroute(oldBindingPattern);
  assert.equal(await recoveryPage.evaluate(()=>window.HomeServerAgentRouting.getSelectedAgentId()),secondary.agent.id,'A late binding cannot change the selected Agent');
  // Restart the actual runtime using the same data directory and owner token.
  // No alternate conversation database or recovery-only server is involved.
  const restartArgs=child.spawnargs.slice(1);
  const stopped=new Promise(resolve=>child.once('exit',resolve));child.kill();await stopped;
  child=spawn(process.env.HOMESERVER_TEST_PYTHON || 'python',restartArgs,{cwd:root,env:{...process.env,HOMESERVER_DATA_DIR:dataDir,NO_PROXY:'localhost,127.0.0.1'}});
  child.stdout.on('data',()=>{});child.stderr.on('data',chunk=>{stderr=(stderr+chunk).slice(-5000);});
  for(let attempt=0;;attempt++){
    try{if((await context.request.get(base+'/api/v1/health')).ok())break;}catch(_){}
    if(attempt>=100)throw Error('Restart did not listen: '+stderr);await delay(100);
  }
  assert.ok((await context.request.post(base+'/__owner/session',{headers:{'x-homeserver-owner':token}})).ok());
  await recoveryPage.reload();
  await recoveryPage.waitForFunction(({id,agent})=>window.HomeServerChatTurn?.conversationId()===id && window.HomeServerAgentRouting?.getSelectedAgentId()===agent,{id:newer.conversation_id,agent:secondary.agent.id});
  assert.equal(await recoveryPage.locator('#chatInput').inputValue(),'Specialist draft','Runtime restart preserves the selected conversation, bound Agent and unsent draft');
  assert.equal(recoverySends,1,'Runtime restart never replays the draft');
  await recoveryPage.locator(`[data-brain-conversation="${selectedConversation}"]`).click();
  await recoveryPage.waitForFunction(id => window.HomeServerChatTurn.conversationId()===id,selectedConversation);
  assert.equal(await recoveryPage.locator('#chatInput').inputValue(),'Keep this failed provider message','Drafts belong to their conversation');
  // Offline submit is stopped before automatic routing/tool interception.
  await context.setOffline(true);
  await recoveryPage.locator('#chatInput').fill('An offline draft');
  await recoveryPage.locator('#chatForm [type="submit"]').click();
  assert.equal(recoverySends,1);
  assert.match(await recoveryPage.locator('#chatSessionStatus').innerText(),/offline/);
  await context.setOffline(false);
  assert.equal(recoverySends,1,'Reconnection does not automatically resend');
  failInference=false;
  inferenceGate=new Promise(resolve => {releaseInference=resolve;});
  await recoveryPage.locator('#chatInput').fill('Stop an in-flight provider request');
  await recoveryPage.locator('#chatForm [type="submit"]').click();
  await recoveryPage.waitForFunction(() => window.HomeServerChatTurn.isBusy());
  await recoveryPage.locator('#chatCaptureStop').click();
  releaseInference(); inferenceGate=null;
  await recoveryPage.waitForFunction(() => !window.HomeServerChatTurn.isBusy());
  assert.equal(await recoveryPage.locator('#chatInput').inputValue(),'Stop an in-flight provider request');
  assert.equal(await recoveryPage.locator('#chatInput').isEnabled(),true);
  await recoveryPage.locator('#chatCheckHistory').click();
  await recoveryPage.waitForFunction(id => window.HomeServerChatTurn.conversationId()===id,selectedConversation);
  assert.equal(recoverySends,2,'Checking history never resends a stopped request');
  // New unsent specialist drafts restore only an Agent returned by the owner API.
  let releaseNewChatRefresh;
  const newChatRefreshGate=new Promise(resolve=>{releaseNewChatRefresh=resolve;});
  const pendingRefreshes=[];
  await recoveryPage.route('**/api/v1/control/agent-routing',route=>{
    const pending=(async()=>{const response=await route.fetch();await newChatRefreshGate;await route.fulfill({response});})();
    pendingRefreshes.push(pending);return pending;
  });
  await recoveryPage.locator('#sidebarNewChat').click();
  await recoveryPage.locator('#chatOptionsButton').click();await recoveryPage.locator('#chatOptionsTabMore').click();
  await recoveryPage.locator('#chatAgentSelect').selectOption(String(secondary.agent.id));
  await recoveryPage.locator('#chatOptionsClose').click();
  await recoveryPage.locator('#chatInput').fill('An unsent specialist draft');
  releaseNewChatRefresh();await Promise.all(pendingRefreshes);
  await recoveryPage.evaluate(()=>window.HomeServerAgentRouting.refresh());
  assert.equal(await recoveryPage.evaluate(()=>window.HomeServerAgentRouting.getSelectedAgentId()),secondary.agent.id,'A delayed sidebar refresh cannot reset an explicit specialist choice');
  await recoveryPage.unroute('**/api/v1/control/agent-routing');
  await recoveryPage.reload();
  await recoveryPage.waitForFunction(id=>window.HomeServerAgentRouting?.getSelectedAgentId()===id && document.querySelector('#chatInput').value==='An unsent specialist draft',secondary.agent.id).catch(async error=>{throw Error(error.message+' '+JSON.stringify(await recoveryPage.evaluate(()=>({agent:window.HomeServerAgentRouting?.getSelectedAgentId(),draft:document.querySelector('#chatInput').value,active:localStorage.getItem('homeserver:chat:active-v1'),drafts:sessionStorage.getItem('homeserver:chat:drafts-v1')}))));});
  assert.equal(await recoveryPage.evaluate(()=>window.HomeServerChatTurn.conversationId()),null);
  assert.equal(recoverySends,2,'Restoring an unsent specialist draft does not create a conversation');
  await recoveryPage.locator('#chatOptionsButton').click();await recoveryPage.locator('#chatOptionsTabMore').click();
  await recoveryPage.locator('#chatAgentSelect').selectOption('1');
  assert.equal(await recoveryPage.locator('#chatInput').inputValue(),'','New drafts are separated by selected Agent');
  await recoveryPage.locator('#chatAgentSelect').selectOption(String(secondary.agent.id));
  assert.equal(await recoveryPage.locator('#chatInput').inputValue(),'An unsent specialist draft');
  await recoveryPage.locator('#chatOptionsClose').click();
  // Session loss clears private recovery state and stops all capture.
  await recoveryPage.route('**/api/v1/control/conversations?*',route => route.fulfill({status:401,json:{detail:'Owner session expired'}}));
  await recoveryPage.reload();
  await recoveryPage.waitForFunction(() => document.querySelector('#chatSessionStatusText')?.textContent.includes('Sign in') || document.querySelector('#chatSessionStatusText')?.textContent.includes('history is unavailable'));
  assert.equal(await recoveryPage.evaluate(() => sessionStorage.getItem('homeserver:chat:drafts-v1')),null);
  assert.equal(await recoveryPage.locator('#chatInput').inputValue(),'');
  assert.equal(recoverySends,2);
  await recoveryPage.close();
  console.log('PASS: provider failure, scoped draft/Agent restoration, offline recovery, explicit history check, in-flight Stop, and session-loss cleanup without replay');

  // Capture state comes from the existing ownership controls, and Stop delegates
  // to those controls instead of introducing a second recording implementation.
  await page.evaluate(() => {
    const talk = document.getElementById('voiceInputButton');
    talk.addEventListener('click', event => {event.stopImmediatePropagation(); talk.setAttribute('aria-pressed', 'false');});
    talk.setAttribute('aria-pressed', 'true');
  });
  await page.locator('#chatCaptureStop').waitFor({state: 'visible'});
  await page.locator('#chatCaptureStop').click();
  assert.equal(await page.locator('#chatCaptureStop').isVisible(), false);
  assert.ok(microphoneCalls > 0, 'Explicit listening opens the processed voice filter monitor');
  assert.ok(await page.evaluate(()=>voiceFixture.captureIntents.every(c=>c.voiceActive&&c.audio.echoCancellation&&c.audio.noiseSuppression&&!c.video)), 'Microphone monitoring requires active listening and noise suppression');
  const stoppedMicrophoneCalls=microphoneCalls;await delay(100);assert.equal(microphoneCalls,stoppedMicrophoneCalls,'Stop cannot reopen the microphone');

  for (const view of ['agent', 'tools', 'contacts', 'members', 'ambient', 'automation', 'tracky', 'federation', 'physical-world', 'apps', 'homeserver-apps', 'backups', 'storage', 'health', 'activity']) {
    const nav = page.locator(`.primary-sidebar-nav [data-view="${view}"]`);
    assert.equal(await nav.count(), 1, `${view} has a main navigation link`);
    await nav.click();
    assert.equal(await page.locator(`#view-${view}`).evaluate(node => node.classList.contains('active')), true);
  }
  // Workspace browser stays bounded and exposes complete details only on request.
  let syncRequests = 0;
  const fullText = 'Full original text 💡 '.repeat(2500);
  const syncRoute = /\/api\/v1\/control\/workspace-sync(?:\/|\?|$)/;
  await page.route(syncRoute, async route => {
    syncRequests++;
    const url = new URL(route.request().url());
    if (url.pathname.includes('/records/')) {
      const detail = url.searchParams.has('key'), offset = Number(url.searchParams.get('offset') || 0);
      await route.fulfill({json: detail ? {items:[{data:{title:'Cloud record',content_text:fullText,sku:'SKU-7'}}], attachments:[{name:'own.pdf',sha256:'a'.repeat(64)}]} : {items:Array.from({length:50},(_,i)=>({table:'knowledge_items',source_id:String(offset+i+1),title:'Cloud record '+(offset+i+1),preview:'Safe preview',updated_at:'2026-10-06 19:48:12'})),count:500}});
    } else await route.fulfill({json:{enabled:true,paired:true,last_success_at:'2026-10-06T19:49:00Z',last_error:''}});
  });
  await page.locator('.primary-sidebar-nav [data-view="cloud-data"]').click();
  await page.waitForFunction(() => document.querySelectorAll('.workspace-sync-record').length === 50).catch(async error => {
    const state=await page.evaluate(()=>({active:document.querySelector('#view-cloud-data').className,hidden:document.hidden,options:document.querySelector('#workspaceSyncDataset').options.length,status:document.querySelector('#workspaceSyncStatus').textContent}));
    throw new Error(error.message+'\nCloud data state: '+JSON.stringify(state)+'\nBrowser errors: '+JSON.stringify(errors)+'\nRequests: '+syncRequests);
  });
  assert.equal(await page.locator('#workspaceSyncDataset option').count(),16);
  await page.locator('#workspaceSyncNext').click();
  await page.waitForFunction(() => document.querySelector('#workspaceSyncPage').textContent === '51–100 of 500');
  await page.locator('.workspace-sync-record').first().click();
  await page.locator('#workspaceSyncDetail[open]').waitFor();
  assert.equal(await page.locator('#workspaceSyncAttachments a').getAttribute('href'),'/api/v1/control/workspace-sync/assets/'+'a'.repeat(64));
  await page.getByRole('button',{name:'Show full text',exact:true}).click();
  assert.ok((await page.locator('#workspaceSyncDetailText').textContent()).includes(fullText));
  for(const width of [1440,320]){
    await page.setViewportSize({width,height:900});
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    assert.ok(await page.locator('#workspaceSyncDetail').evaluate(node => node.scrollWidth <= node.clientWidth + 1));
  }
  await page.setViewportSize({width:1440,height:900});
  await page.locator('#workspaceSyncDetail button').first().click();
  await page.locator('.primary-sidebar-nav [data-view="contacts"]').click();
  const awayRequests=syncRequests;
  await page.evaluate(()=>document.querySelector('#view-cloud-data').classList.add('inactive-fixture'));
  await delay(100);
  assert.equal(syncRequests,awayRequests,'Inactive workspace does no background fetch');
  await page.unroute(syncRoute);

  // Unsaved Ambient settings survive polling, and testing saves before speech.
  const ambientCalls=[];
  const ambientRoute = /\/api\/v1\/control\/vp3-os\/ambient(?:\/|\?|$)/;
  await page.route(ambientRoute,async route=>{
    const request=route.request(),path=new URL(request.url()).pathname;
    ambientCalls.push({path,method:request.method(),body:request.postDataJSON()});
    if(path.endsWith('/announce-test'))return route.fulfill({json:{spoken:true}});
    const settings=request.method()==='PUT'?request.postDataJSON():{enabled:false,wake_enabled:true,proactive_voice:true,presence_policy:'sensor_required',announcement_levels:['warning']};
    const status={settings,state:'armed',presence:'unknown',hardware:{}};
    await route.fulfill({json:request.method()==='PUT'?{status,settings}:status});
  });
  await page.locator('.primary-sidebar-nav [data-view="ambient"]').click();
  await page.waitForFunction(()=>document.querySelector('#ambientPresencePolicy').value==='sensor_required');
  await page.locator('#ambientEnabled').check();
  await page.locator('#ambientPresencePolicy').selectOption('assume_present');
  await delay(5200);
  assert.equal(await page.locator('#ambientPresencePolicy').inputValue(),'assume_present','Polling preserves unsaved policy');
  await page.locator('#ambientTestVoice').click();
  await page.waitForFunction(()=>document.querySelector('#ambientFeedback').textContent.includes('voice played'));
  const saved=ambientCalls.findIndex(call=>call.method==='PUT'),spoken=ambientCalls.findIndex(call=>call.path.endsWith('/announce-test'));
  assert.ok(saved>=0&&spoken>saved);
  assert.equal(ambientCalls[saved].body.presence_policy,'assume_present');
  assert.equal(ambientCalls[saved].body.enabled,true);
  await page.unroute(ambientRoute);
  console.log('PASS: workspace pagination, originals, responsive full-text details and saved Ambient speech choice');
  await page.goto(base + '/#contacts');
  await page.waitForFunction(() => window.HomeServerChatOptions);
  assert.equal(await page.locator('#view-contacts').evaluate(node => node.classList.contains('active')), true, 'Startup preserves deep links');
  await page.locator('.primary-sidebar-nav [data-view="chat"]').click();
  await page.waitForFunction(() => document.querySelector('#view-chat.active'));
  // Isolate the shell poller from the full page's independent Cloud status poller.
  // Keep the exact one-request assertion without counting unrelated timer traffic.
  const cloudPage = await browser.newPage();
  let cloudRequests = 0;
  await cloudPage.route(base + '/shell-poll-fixture', route => route.fulfill({contentType:'text/html',body:'<!doctype html><div id="hsV220CloudPill"></div>'}));
  await cloudPage.route('**/api/v1/control/cloud-connection', async route => {
    cloudRequests++; await delay(150);
    await route.fulfill({json: {cloud: {state: 'offline'}, service: {version: '2.4'}}});
  });
  await cloudPage.goto(base + '/shell-poll-fixture');
  await cloudPage.addScriptTag({content:await fs.readFile(path.join(root,'ui/universal-shell-v220.js'),'utf8')});
  await cloudPage.waitForFunction(() => document.querySelector('#hsV220CloudPill').dataset.state === 'offline');
  cloudRequests = 0;
  await cloudPage.evaluate(() => Promise.all(Array.from({length: 20}, () => window.HOMESERVER_UNIVERSAL_SHELL_V220.refreshCloud())));
  assert.equal(cloudRequests, 1, 'Repeated cloud refreshes cannot overlap');
  await cloudPage.evaluate(() => {
    Object.defineProperty(document, 'hidden', {configurable: true, value: true});
    return window.HOMESERVER_UNIVERSAL_SHELL_V220.refreshCloud();
  });
  assert.equal(cloudRequests, 1, 'Hidden-page refresh does no background work');
  await cloudPage.close();
  await page.evaluate(() => {
    const messages = document.getElementById('chatMessages');
    messages.style.scrollBehavior = 'auto';
    for (let i = 0; i < 500; i++) {
      const row = document.createElement('div'); row.className = 'chat-message assistant'; row.textContent = `Long chat ${i}: ` + 'Example content. '.repeat(20); messages.appendChild(row);
    }
    messages.lastElementChild.scrollIntoView({block:'end'});
  });
  await delay(400);
  const geometry = await page.evaluate(() => {
    const messages = document.getElementById('chatMessages'); const form = document.getElementById('chatForm').getBoundingClientRect();
    return {scroll: getComputedStyle(messages).overflowY, bottom: form.bottom, height: innerHeight, pageHeight: document.documentElement.scrollHeight};
  });
  assert.ok(geometry.scroll==='visible' && geometry.bottom <= geometry.height && geometry.pageHeight > geometry.height * 5, JSON.stringify(geometry));
  for (const viewport of [{width: 1440, height: 900}, {width: 1024, height: 768}, {width: 390, height: 844}, {width: 320, height: 640}]) {
    await page.setViewportSize(viewport);
    await assertCanvasLayout(page, 'chatMessages');
    await page.locator('#chatMessages .chat-message').last().evaluate(node=>node.scrollIntoView({block:'end'}));
    const last = await page.locator('#chatMessages .chat-message').last().boundingBox();
    const composer = await page.locator('#chatForm').boundingBox();
    assert.ok(last.y + last.height <= composer.y, 'The newest message can be read above the composer');
    await page.locator('#chatInput').evaluate(node => {node.style.height = '120px';});
    await assertCanvasLayout(page, 'chatMessages');
    await page.locator('#chatInput').evaluate(node => {node.style.height = '';});
  }
  await page.setViewportSize({width: 1440, height: 900});
  await page.locator('#chatOptionsButton').click();
  await page.locator('#chatOptionsClose').click();
  await page.setViewportSize({width: 390, height: 844});
  await page.locator('#hsV220IndexMenu').click();
  await page.locator('.primary-sidebar-nav [data-view="contacts"]').click();
  assert.equal(await page.locator('body').evaluate(node => node.classList.contains('hs-v220-nav-open')), false);
  await page.locator('#hsV220IndexMenu').click();
  await page.locator('.primary-sidebar-nav [data-view="chat"]').click();
  await page.locator('#chatOptionsButton').click();
  const mobileDialog = await page.locator('#chatOptionsDialog').boundingBox();
  assert.ok(mobileDialog.x >= 0 && mobileDialog.x + mobileDialog.width <= 390);
  await page.screenshot({path: process.env.CHAT_SCREENSHOT_PATH || path.join(dataDir, 'chat-mobile.png')});
  assert.deepEqual(errors, [], 'Complete owner UI has no unhandled browser errors');
  console.log('PASS: voice observer becomes idle; clean composer; dialog tabs/focus; no automatic capture; chat send; persisted context/voice; Stop; full sidebar/deep links; 500-message scroll; readable consent and footer separation at four viewport sizes; mobile navigation.');
} finally {
  if (browser) await browser.close();
  if (child && child.exitCode === null) {child.kill('SIGTERM'); await Promise.race([new Promise(resolve => child.once('exit', resolve)), delay(5000)]); if (child.exitCode === null) child.kill('SIGKILL');}
  await fs.rm(dataDir, {recursive: true, force: true});
}
