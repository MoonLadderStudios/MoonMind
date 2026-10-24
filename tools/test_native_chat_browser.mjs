#!/usr/bin/env node
// Exercise the real compiled native app with MoonMind's production adapter.
// All HTTP data and actions are synthetic and confined to this loopback server.
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFileSync, existsSync } from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { chromium } from 'playwright';

const fixture = path.resolve(process.env.MOONMIND_NATIVE_CHAT_FIXTURE_DIR || 'var/chat-recovery/native-ui');
assert.ok(existsSync(path.join(fixture, 'index.html')), 'Set MOONMIND_NATIVE_CHAT_FIXTURE_DIR to the extracted native web-ui');
const binding = 'chatb_browser_fixture';
const uiBase = `/omnigent-ui/workflow-chat/${binding}`;
const apiBase = `/api/workflow-chat-bindings/${binding}/omnigent`;
const render = (mode) => {
  const rendered = spawnSync(process.env.MOONMIND_TEST_PYTHON || 'python3', ['-c', `
import sys
from pathlib import Path
from moonmind.omnigent.native_ui import build_chat_bootstrap,render_native_ui_document
b=build_chat_bootstrap(chat_binding_id="${binding}", mode=sys.argv[2], read_only=True,
 capabilities={"viewTranscript":True,"sendMessage":False}, state="ended")
print(render_native_ui_document(Path(sys.argv[1]).read_text(),bootstrap=b,scoped_base=b["uiBase"]))
`, path.join(fixture, 'index.html'), mode], { encoding: 'utf8', maxBuffer: 4 * 1024 * 1024 });
  assert.equal(rendered.status, 0, rendered.stderr);
  return rendered.stdout;
};
const documents = { embedded: render('embedded'), full_page: render('full_page') };
let scenario = {};
let apiReads = [];
let submissionKeys = [];
const acceptedSubmissions = new Set();
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const transcript = [{
  id: 'item-fixture-1', type: 'message', status: 'completed',
  response_id: 'turn-fixture', created_at: 1,
  role: 'assistant', content: [{ type: 'output_text', text: 'Captured fixture response survives cleanup.' }],
}];
const session = {
  id: binding, object: 'session', title: 'Synthetic recorded conversation', status: 'idle',
  kind: 'conversation', harness: 'codex', labels: {}, items: [], pending_inputs: [],
  pending_elicitations: [], todos: [], model_options: [], runner_online: false,
  host_online: false, host_resumable: false, inference_configured: true,
  readOnly: true, capabilities: { viewTranscript: true, sendMessage: false },
  created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
};
const server = createServer(async (req, res) => {
  const url = new URL(req.url, 'http://fixture.test');
  const reply = (value, status = 200) => {
    res.writeHead(status, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(value));
  };
  if (req.method === 'POST' && url.pathname === `${apiBase}/v1/sessions/${binding}/events`) {
    for await (const _chunk of req) { /* Consume only synthetic fixture input. */ }
    const key = req.headers['idempotency-key'];
    submissionKeys.push(key);
    if (!key) return reply({ error: 'Missing submission identity' }, 400);
    acceptedSubmissions.add(key);
    if (scenario.loseAck) {
      scenario.loseAck = false;
      res.writeHead(200, { 'Content-Type': 'application/json', 'Content-Length': '1000' });
      res.flushHeaders();
      res.write('{"queued":');
      setTimeout(() => res.destroy(), 10);
      return;
    }
    return reply({ queued: true, pending_id: 'synthetic-pending' });
  }
  if (req.method !== 'GET') return reply({ error: 'No live actions in this fixture' }, 405);
  if (url.pathname === '/outer') {
    res.writeHead(200, { 'Content-Type': 'text/html' });
    return res.end(`<output id="state">loading</output><iframe src="${uiBase}?embedded=1"></iframe>
      <script>addEventListener('message',e=>{if(e.origin===location.origin&&e.source===document.querySelector('iframe').contentWindow&&e.data.chatBindingId==='${binding}')document.querySelector('#state').textContent=e.data.type+':'+(e.data.reason||'')})</script>`);
  }
  if (url.pathname.startsWith(apiBase + '/')) {
    const route = url.pathname.slice(apiBase.length);
    apiReads.push(route);
    if (route === '/v1/info') return reply({ accounts_enabled: false, needs_setup: false, sharing_mode: 'off', dictation_available: false });
    if (route === '/v1/me') return reply({ user_id: 'workflow-owner', is_admin: false });
    if (route === `/v1/sessions/${binding}` || route === `/v1/sessions/${binding}/items`) {
      if (scenario.denied) return reply({ detail: { code: 'omnigent_chat_operation_denied' } }, 403);
      if (scenario.transient && !scenario.retried) { scenario.retried = true; return reply({}, 503); }
      if (route.endsWith('/items') && scenario.slow) await delay(scenario.slow);
      return reply(route.endsWith('/items') ? { object: 'list', data: transcript, has_more: false } : session);
    }
    if (route === `/v1/sessions/${binding}/children`) return reply({ data: [], has_more: false });
    if (route === '/v1/extensions') return reply({ extensions: [] });
    if (route === '/v1/projects/order' || route === '/v1/sessions/projects') return reply([]);
    if (route === '/v1/hosts') return reply({ hosts: [] });
    if (route === '/v1/harnesses') return reply({ harnesses: [] });
    return reply({ data: [], has_more: false });
  }
  if (url.pathname === uiBase || url.pathname.startsWith(uiBase + '/c/')) {
    res.writeHead(200, { 'Content-Type': 'text/html' });
    return res.end(documents[url.searchParams.get('embedded') === '1' ? 'embedded' : 'full_page']);
  }
  if (url.pathname.startsWith(uiBase + '/assets/')) {
    const file = path.resolve(fixture, url.pathname.slice(uiBase.length + 1));
    if (!file.startsWith(fixture + path.sep) || !existsSync(file)) return reply({}, 404);
    const type = file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : file.endsWith('.svg') ? 'image/svg+xml' : 'application/octet-stream';
    res.writeHead(200, { 'Content-Type': type });
    return res.end(readFileSync(file));
  }
  return reply({}, 404);
});
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const browser = await chromium.launch({ headless: true });
try {
  for (const test of [
    { name: 'embedded', path: '/outer', embedded: true },
    { name: 'full-page', path: uiBase },
    { name: 'deep-link', path: `${uiBase}/c/${binding}` },
    { name: 'slow-startup', path: '/outer', embedded: true, slow: 16000 },
    { name: 'transient-read', path: uiBase, transient: true },
  ]) {
    scenario = { ...test };
    apiReads = [];
    const context = await browser.newContext();
    context.setDefaultTimeout(30000);
    const page = await context.newPage();
    const requests = [];
    const errors = [];
    page.on('request', (request) => requests.push(new URL(request.url())));
    page.on('pageerror', (error) => errors.push(error.message));
    await page.goto(origin + test.path);
    const native = test.embedded ? page.frameLocator('iframe') : page;
    await native.getByRole('log').waitFor({ timeout: 55000 });
    await native.getByRole('log').getByText('Captured fixture response survives cleanup.', { exact: true }).waitFor();
    assert.equal(await native.getByText('Page not found', { exact: true }).count(), 0);
    assert.ok(apiReads.includes(`/v1/sessions/${binding}/items`), 'Transcript read must actually succeed');
    assert.ok(!apiReads.includes('/v1/sessions'), 'Hosted mode must suppress the global session catalog request');
    assert.ok(requests.some((url) => url.pathname.startsWith(uiBase + '/assets/')), 'Real compiled assets were fetched');
    assert.ok(requests.every((url) => url.origin === origin), 'No direct upstream or third-party traffic');
    assert.ok(!requests.some((url) => url.pathname.startsWith('/v1/')), 'No unscoped API traffic');
    assert.deepEqual(errors, []);
    if (test.embedded) assert.match(await page.locator('#state').innerText(), /chat.ready/);
    if (test.name === 'full-page') {
      const send = () => page.evaluate(async (endpoint) => {
        try {
          const response = await fetch(endpoint, { method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ type: 'message', data: { role: 'user', content: [{ type: 'input_text', text: 'Synthetic fixture input' }] } }) });
          return { status: response.status, body: await response.json() };
        } catch { return { failed: true }; }
      }, `${apiBase}/v1/sessions/${binding}/events`);
      scenario.loseAck = true;
      assert.equal((await send()).failed, true);
      await page.reload();
      await page.getByRole('log').waitFor();
      assert.equal((await send()).status, 200);
      assert.equal(submissionKeys[0], submissionKeys[1], 'Lost acknowledgement retains identity through reload');
      assert.equal(acceptedSubmissions.size, 1, 'Retry cannot execute a second turn');
      assert.equal((await send()).status, 200);
      assert.notEqual(submissionKeys[1], submissionKeys[2], 'A later confirmed identical send is a distinct intent');
      assert.equal(acceptedSubmissions.size, 2);
      scenario.denied = true;
      await page.reload();
      await page.getByText('Chat unavailable', { exact: true }).waitFor();
      assert.match(await page.locator('body').innerText(), /transcript_access_denied/);
      scenario.denied = false;
      await page.getByRole('button', { name: 'Retry', exact: true }).click();
      await page.getByRole('log').waitFor();
    }
    console.log(`native compiled app: ${test.name} passed`);
    await context.close();
  }
} finally {
  await browser.close();
  await new Promise((resolve) => server.close(resolve));
}
