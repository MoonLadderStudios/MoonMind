// Execute the served adapter against the pinned application's hydration journey.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const adapter = fs.readFileSync(process.argv[2], 'utf8');

async function journey({ items = [], readOnly = false, failure = null, delayedRoot = false } = {}) {
  const messages = [];
  const timers = [];
  const observers = [];
  const listeners = {};
  let rendered = false;
  let mounted = false;
  const root = {
    hasChildNodes: () => mounted,
    querySelector: (selector) => {
      assert.equal(selector, '[role="log"]');
      return rendered ? {} : null;
    },
  };
  const context = {
    URL, Request,
    __MOONMIND_OMNIGENT_CHAT__: {
      chatBindingId: 'cb-1', apiBase: '/api/workflow-chat-bindings/cb-1/omnigent', readOnly,
    },
    location: {
      href: 'https://moonmind.test/omnigent-ui/workflow-chat/cb-1?embedded=1',
      origin: 'https://moonmind.test', protocol: 'https:', host: 'moonmind.test', search: '', hash: '',
    },
    history: { state: null, replaceState() {} },
    document: { readyState: 'complete', getElementById: () => root },
    parent: { postMessage: (message, origin) => {
      assert.equal(origin, 'https://moonmind.test');
      assert.equal(message.chatBindingId, 'cb-1');
      messages.push(message);
    } },
    fetch: async (url) => {
      const path = new URL(url).pathname;
      if (failure === 'network' && path.endsWith('/items')) throw new Error('private network detail');
      const failed = (failure === '403' && path.endsWith('/items')) ||
        (failure === 'metadata' && path.endsWith('/cb-1'));
      return {
        ok: !failed,
        clone: () => ({ json: async () => {
          if (failure === 'json' && path.endsWith('/items')) throw new Error('private invalid body');
          if (path.endsWith('/items')) return failure === 'schema' ? {} : { data: items, has_more: false };
          return { id: 'cb-1' };
        } }),
      };
    },
    XMLHttpRequest: class { open() {} },
    MutationObserver: class {
      constructor(callback) { this.callback = callback; observers.push(this); }
      observe() { this.active = true; }
      disconnect() { this.active = false; }
    },
    setTimeout: (callback, ms) => { timers.push({ callback, ms }); },
    addEventListener: (name, callback) => { listeners[name] = callback; },
  };
  context.window = context;
  vm.runInNewContext(adapter, context);
  const mutate = () => observers.filter((observer) => observer.active).forEach((observer) => observer.callback());
  const ready = () => messages.filter((message) => message.type.endsWith('.ready'));
  const fatal = () => messages.filter((message) => message.type.endsWith('.fatal'));

  // Root shell mounts before data. A handled error shell also satisfies this
  // old readiness condition, but must never cancel the parent's deadline.
  mounted = !delayedRoot;
  mutate();
  assert.equal(ready().length, 0);
  // Reproduce a slow 8-second hydration: URL restoration expires at 5 seconds.
  timers.filter((timer) => timer.ms <= 8000).forEach((timer) => timer.callback());
  mounted = true;
  mutate();
  await context.fetch('/v1/sessions/cb-1?include_items=false');
  await context.fetch('/v1/sessions/other/items').catch(() => {});
  await context.fetch('/v1/sessions/cb-1/items?after=old-cursor').catch(() => {});
  mutate();
  assert.equal(ready().length, 0, 'metadata, foreign session and older pages cannot unlock readiness');
  await context.fetch('/v1/sessions/cb-1/items?order=desc&limit=100').catch(() => {});
  assert.equal(ready().length, 0, 'successful read is not a rendered conversation');
  if (failure) {
    assert.ok(fatal().length > 0, 'handled essential failure must reach the embedding route');
    // A later unrelated rerender must not replace authoritative failure.
    rendered = true;
    mutate();
    assert.equal(ready().length, 0);
    assert.ok(!JSON.stringify(messages).includes('private'));
  } else {
    rendered = true;
    mutate();
    assert.equal(ready().length, 1, 'late transcript/validated empty render must announce ready');
    mutate();
    assert.equal(ready().length, 1);
    listeners.error();
    assert.equal(fatal().length, 1, 'crash after ready still reaches the parent');
  }
  listeners.pagehide();
  assert.ok(observers.every((observer) => !observer.active));
}
(async () => {
  await journey({ items: [{ id: 'item-1', text: 'private transcript' }] });
  await journey({ readOnly: true, delayedRoot: true });
  for (const failure of ['403', 'metadata', 'network', 'json', 'schema']) await journey({ failure });
})().catch((error) => { console.error(error); process.exitCode = 1; });
