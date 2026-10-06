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
  await page.locator('#agentVoiceRateEnabled').check();
  await page.locator('#agentVoiceRate').fill('1.2');
  await page.locator('#agentVoiceProfileForm [type="submit"]').click();
  await page.waitForFunction(() => document.querySelector('#agentVoiceSaved')?.textContent.includes('Saved locally'));
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
  console.log('PASS: voice observer becomes idle; clean composer; dialog tabs/focus; no automatic capture; chat send; persisted context/voice; Stop; full sidebar/deep links; 500-message scroll; mobile navigation.');
} finally {
  if (browser) await browser.close();
  if (child && child.exitCode === null) {child.kill('SIGTERM'); await Promise.race([new Promise(resolve => child.once('exit', resolve)), delay(5000)]); if (child.exitCode === null) child.kill('SIGKILL');}
  await fs.rm(dataDir, {recursive: true, force: true});
}
