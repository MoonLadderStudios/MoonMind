// Verify retry identity on HTTP origins without SubtleCrypto. No network.
const assert = require('node:assert/strict');
const { createHash, randomFillSync } = require('node:crypto');
const fs = require('node:fs');
const vm = require('node:vm');
const adapter = fs.readFileSync(process.argv[2], 'utf8');
const stored = new Map();
const requests = [];
let loseAcknowledgement = true;
let responseStatus = 200;
function boot() {
  const context = {
    URL, Request, Response, Headers, TextEncoder, Uint8Array, Uint32Array, DataView,
    crypto: { getRandomValues: randomFillSync },
    __MOONMIND_OMNIGENT_CHAT__: { chatBindingId: 'cb-1', apiBase: '/api/workflow-chat-bindings/cb-1/omnigent' },
    location: { href: 'http://moonmind.test/omnigent-ui/workflow-chat/cb-1', origin: 'http://moonmind.test', search: '', hash: '' },
    history: { state: null, replaceState() {} },
    sessionStorage: { getItem: (key) => stored.get(key), setItem: (key, value) => stored.set(key, value) },
    document: { readyState: 'complete', getElementById: () => null },
    parent: { postMessage() {} },
    addEventListener() {},
    XMLHttpRequest: class { open() {} },
    fetch: async (_url, init) => {
      requests.push(new Headers(init.headers).get('Idempotency-Key'));
      if (loseAcknowledgement) throw new Error('Synthetic lost acknowledgement');
      return new Response('{"queued":true}', { status: responseStatus, headers: { 'Content-Type': 'application/json' } });
    },
  };
  context.window = context;
  vm.runInNewContext(adapter, context);
  return context;
}
const send = (context, body) => context.fetch('/v1/sessions/cb-1/events', { method: 'POST', body });
(async () => {
  for (const body of ['', 'a'.repeat(55), 'b'.repeat(56), 'c'.repeat(64), 'κ🙂'.repeat(200)]) {
    stored.clear(); requests.length = 0; loseAcknowledgement = true;
    await assert.rejects(send(boot(), body));
    const entry = JSON.parse([...stored.values()][0])[0];
    assert.equal(entry[0], createHash('sha256').update(body).digest('hex'));
    assert.match(entry[1], /^mm-[a-f0-9]{32}$/);
    loseAcknowledgement = false;
    const reloaded = boot();
    await send(reloaded, body);
    assert.equal(requests[0], requests[1], 'Reload retries the same unacknowledged submission');
    assert.deepEqual(JSON.parse([...stored.values()][0]), []);
    await send(reloaded, body);
    assert.notEqual(requests[1], requests[2], 'A confirmed identical send is a new submission');
  }
  stored.clear(); requests.length = 0;
  responseStatus = 422;
  const rejected = boot();
  await send(rejected, 'invalid submission');
  await send(rejected, 'invalid submission');
  assert.notEqual(requests[0], requests[1], 'A definitive first-attempt rejection frees its identity');
  stored.clear(); requests.length = 0; loseAcknowledgement = true;
  await assert.rejects(send(boot(), 'uncertain submission'));
  loseAcknowledgement = false;
  await send(boot(), 'uncertain submission');
  await send(boot(), 'uncertain submission');
  assert.equal(requests[0], requests[2], 'A later rejection cannot settle a previously uncertain send');
})().catch((error) => { console.error(error); process.exitCode = 1; });
