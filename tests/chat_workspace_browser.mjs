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
      regionWidth: region.clientWidth, regionScrollWidth: region.scrollWidth};
  }, regionId);
  assert.ok(layout.regionBottom <= layout.composerTop + 1, `Content scrolls above the composer: ${JSON.stringify(layout)}`);
  assert.ok(layout.composerBottom <= layout.viewportHeight && layout.composerLeft >= layout.panelLeft - 1 && layout.composerRight <= layout.panelRight + 1,
    `Composer stays fully inside the canvas: ${JSON.stringify(layout)}`);
  assert.ok(Math.abs((layout.composerLeft + layout.composerRight) - (layout.panelLeft + layout.panelRight)) <= 2,
    `Composer stays centered without legacy floating offsets: ${JSON.stringify(layout)}`);
  assert.ok(layout.pageWidth <= layout.viewportWidth + 1 && layout.regionScrollWidth <= layout.regionWidth + 1,
    `Canvas has no horizontal overflow: ${JSON.stringify(layout)}`);
}

async function assertOnboardingLayout(page) {
  await assertCanvasLayout(page, 'chatOnboardingCanvas');
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
import uvicorn
providers.generate_ollama = lambda messages, model_override=None: {'provider':'ollama', 'model':model_override or 'test-model', 'content':'Browser acceptance reply.'}
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
    const original = navigator.mediaDevices?.getUserMedia?.bind(navigator.mediaDevices);
    if (original) navigator.mediaDevices.getUserMedia = (...args) => {window.countMicrophoneCall(); return original(...args);};
  });
  await page.goto(base + '/#chat');
  await page.waitForFunction(() => window.HomeServerChatOptions && document.querySelector('#chatOptionsVoice #dictateInputButton')).catch(error => {throw new Error(error.message + '\nBrowser errors: ' + JSON.stringify(errors));});
  console.log('PASS: complete owner page loaded');
  assert.equal(await page.locator('#view-chat .section-intro').count(), 0);
  assert.equal(await page.locator('.topbar').isVisible(), false);
  assert.equal(await page.locator('#chatContextControls').isVisible(), false);
  assert.equal(await page.locator('#chatForm button:visible').count(), 2);
  const plusRect = await page.locator('#chatOptionsButton').boundingBox();
  const inputRect = await page.locator('#chatInput').boundingBox();
  assert.ok(plusRect.x > inputRect.x + inputRect.width / 2, '+ belongs on the right');
  await page.locator('#chatOptionsButton').click();
  assert.equal(await page.locator('#chatOptionsDialog').evaluate(node => node.open), true);
  assert.equal(await page.locator('#chatContextControls').isVisible(), true);
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

  await page.locator('#chatInput').fill('Browser acceptance message');
  await page.locator('#chatForm [type="submit"]').click();
  await page.waitForFunction(() => document.querySelector('#chatMessages')?.textContent.includes('Browser acceptance reply.'));
  await page.waitForFunction(() => !document.querySelector('#chatForm [type="submit"]').disabled);
  assert.equal(await page.locator('#chatForm [type="submit"]').isEnabled(), true);
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
  await page.locator('#onboardLater').click();
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
  assert.equal(microphoneCalls, 0);

  for (const view of ['agent', 'tools', 'contacts', 'members', 'ambient', 'automation', 'tracky', 'federation', 'physical-world', 'apps', 'homeserver-apps', 'backups', 'storage', 'health', 'activity']) {
    const nav = page.locator(`.primary-sidebar-nav [data-view="${view}"]`);
    assert.equal(await nav.count(), 1, `${view} has a main navigation link`);
    await nav.click();
    assert.equal(await page.locator(`#view-${view}`).evaluate(node => node.classList.contains('active')), true);
  }
  await page.goto(base + '/#contacts');
  await page.waitForFunction(() => window.HomeServerChatOptions);
  assert.equal(await page.locator('#view-contacts').evaluate(node => node.classList.contains('active')), true, 'Startup preserves deep links');
  await page.locator('.primary-sidebar-nav [data-view="chat"]').click();
  await page.waitForFunction(() => document.querySelector('#view-chat.active'));
  await page.evaluate(() => window.HOMESERVER_UNIVERSAL_SHELL_V220.refreshCloud());
  let cloudRequests = 0;
  await page.route('**/api/v1/control/cloud-connection', async route => {
    cloudRequests++; await delay(150);
    await route.fulfill({json: {cloud: {state: 'offline'}, service: {version: '2.4'}}});
  });
  await page.evaluate(() => Promise.all(Array.from({length: 20}, () => window.HOMESERVER_UNIVERSAL_SHELL_V220.refreshCloud())));
  assert.equal(cloudRequests, 1, 'Repeated cloud refreshes cannot overlap');
  await page.evaluate(() => {
    Object.defineProperty(document, 'hidden', {configurable: true, value: true});
    return window.HOMESERVER_UNIVERSAL_SHELL_V220.refreshCloud();
  });
  assert.equal(cloudRequests, 1, 'Hidden-page refresh does no background work');
  await page.evaluate(() => {delete document.hidden;});
  await page.unroute('**/api/v1/control/cloud-connection');
  await page.evaluate(() => {
    const messages = document.getElementById('chatMessages');
    messages.style.scrollBehavior = 'auto';
    for (let i = 0; i < 500; i++) {
      const row = document.createElement('div'); row.className = 'chat-message assistant'; row.textContent = `Long chat ${i}: ` + 'Example content. '.repeat(20); messages.appendChild(row);
    }
    messages.scrollTop = messages.scrollHeight;
  });
  await delay(400);
  const geometry = await page.evaluate(() => {
    const messages = document.getElementById('chatMessages'); const form = document.getElementById('chatForm').getBoundingClientRect();
    return {scroll: messages.scrollHeight > messages.clientHeight, bottom: form.bottom, height: innerHeight, pageHeight: document.documentElement.scrollHeight};
  });
  assert.ok(geometry.scroll && geometry.bottom <= geometry.height && geometry.pageHeight <= geometry.height + 2, JSON.stringify(geometry));
  for (const viewport of [{width: 1440, height: 900}, {width: 1024, height: 768}, {width: 390, height: 844}, {width: 320, height: 640}]) {
    await page.setViewportSize(viewport);
    await assertCanvasLayout(page, 'chatMessages');
    await page.locator('#chatMessages .chat-message').last().scrollIntoViewIfNeeded();
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
