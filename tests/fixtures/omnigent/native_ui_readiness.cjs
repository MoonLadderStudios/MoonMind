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
  const navigations = [];
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
    history: { state: null, replaceState(_state, _title, url) { navigations.push(url); } },
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
        status: failed ? 403 : 200,
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
    setTimeout: (callback, ms) => { timers.push({ callback, ms }); if (ms < 2000) queueMicrotask(callback); },
    addEventListener: (name, callback) => { listeners[name] = callback; },
  };
  context.window = context;
  vm.runInNewContext(adapter, context);
  assert.equal(context.__OMNIGENT_BASE_PATH__, '/omnigent-ui/workflow-chat/cb-1');
  assert.deepEqual(navigations, ['/omnigent-ui/workflow-chat/cb-1/c/cb-1']);
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
  assert.equal(navigations.length, 1, 'loading or delayed render cannot restore an unmatched URL');
  await context.fetch('/v1/sessions/cb-1?include_items=false');
  await context.fetch('/v1/sessions/other/items').catch(() => {});
  await context.fetch('/v1/sessions/cb-1/items?after=old-cursor').catch(() => {});
  mutate();
  assert.equal(ready().length, 0, 'metadata, foreign session and older pages cannot unlock readiness');
  await context.fetch('/v1/sessions/cb-1/items?order=desc&limit=100').catch(() => {});
  assert.equal(ready().length, 0, 'successful read is not a rendered conversation');
  if (failure) {
    if (failure === 'network') timers.filter((timer) => timer.ms === 60000).forEach((timer) => timer.callback());
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
    for (const message of [
      'ResizeObserver loop completed with undelivered notifications.',
      'ResizeObserver loop limit exceeded',
    ]) {
      listeners.error({ message });
      assert.equal(fatal().length, 0, 'responsive layout diagnostics are not native crashes');
    }
    listeners.error({ message: 'Synthetic application crash' });
    assert.equal(fatal().length, 1, 'crash after ready still reaches the parent');
  }
  listeners.pagehide();
  assert.ok(observers.every((observer) => !observer.active));
}
// Full-page presentation has no embedding shell to receive signals, so a
// failed or never-ready application must render its own unavailable surface.
async function fullPageJourney({ failure = null } = {}) {
  const timers = [];
  const reloads = [];
  let rendered = false;
  const element = (tag) => ({
    tag, textContent: '', children: [], attributes: {}, listeners: {},
    setAttribute(name, value) { this.attributes[name] = String(value); },
    addEventListener(name, callback) { this.listeners[name] = callback; },
    append(...nodes) { this.children.push(...nodes); },
  });
  const body = { children: [], replaceChildren(...nodes) { this.children = nodes; } };
  const root = { hasChildNodes: () => true, querySelector: () => (rendered ? {} : null) };
  const context = {
    URL, Request,
    __MOONMIND_OMNIGENT_CHAT__: { chatBindingId: 'cb-1', apiBase: '/api/workflow-chat-bindings/cb-1/omnigent' },
    location: {
      href: 'https://moonmind.test/omnigent-ui/workflow-chat/cb-1',
      origin: 'https://moonmind.test', protocol: 'https:', host: 'moonmind.test', search: '', hash: '',
      reload: () => reloads.push(true),
    },
    history: { state: null, replaceState() {} },
    document: { readyState: 'complete', body, getElementById: () => root, createElement: element },
    postMessage: () => {},
    fetch: async (url) => ({
      ok: !(failure === '403' && new URL(url).pathname.endsWith('/items')),
      status: failure === '403' && new URL(url).pathname.endsWith('/items') ? 403 : 200,
      clone: () => ({ json: async () => (new URL(url).pathname.endsWith('/items')
        ? { data: [], has_more: false } : { id: 'cb-1' }) }),
    }),
    XMLHttpRequest: class { open() {} },
    MutationObserver: class { observe() {} disconnect() {} },
    setTimeout: (callback, ms) => { timers.push({ callback, ms }); },
    addEventListener: () => {},
  };
  context.window = context;
  context.parent = context;
  vm.runInNewContext(adapter, context);
  const surface = () => JSON.stringify(body.children);
  if (failure) {
    await context.fetch('/v1/sessions/cb-1/items').catch(() => {});
    assert.ok(surface().includes('transcript_access_denied'), 'full-page denial must be visible');
  } else {
    assert.equal(body.children.length, 0, 'loading page is left to the native app');
    const deadline = timers.find((timer) => timer.ms === 60000);
    assert.ok(deadline, 'full-page startup needs a finite readiness deadline');
    deadline.callback();
    assert.ok(surface().includes('readiness_timeout'), 'never-ready page must become unavailable');
  }
  const retry = body.children[0].children.find((node) => node.tag === 'button');
  retry.listeners.click();
  assert.equal(reloads.length, 1, 'retry reloads the same authorized scoped surface');
}

async function fullPageReadyJourney() {
  // A ready full-page conversation keeps rendering; its deadline is inert.
  const timers = [];
  let replaced = false;
  const root = { hasChildNodes: () => true, querySelector: () => ({}) };
  const context = {
    URL, Request,
    __MOONMIND_OMNIGENT_CHAT__: { chatBindingId: 'cb-1', apiBase: '/api/workflow-chat-bindings/cb-1/omnigent' },
    location: {
      href: 'https://moonmind.test/omnigent-ui/workflow-chat/cb-1',
      origin: 'https://moonmind.test', protocol: 'https:', host: 'moonmind.test', search: '', hash: '',
    },
    history: { state: null, replaceState() {} },
    document: {
      readyState: 'complete', getElementById: () => root,
      body: { replaceChildren() { replaced = true; } },
    },
    postMessage: () => {},
    fetch: async (url) => ({
      ok: true,
      clone: () => ({ json: async () => (new URL(url).pathname.endsWith('/items')
        ? { data: [], has_more: false } : { id: 'cb-1' }) }),
    }),
    XMLHttpRequest: class { open() {} },
    MutationObserver: class { observe() {} disconnect() {} },
    setTimeout: (callback, ms) => { timers.push({ callback, ms }); },
    addEventListener: () => {},
  };
  context.window = context;
  context.parent = context;
  vm.runInNewContext(adapter, context);
  await context.fetch('/v1/sessions/cb-1');
  await context.fetch('/v1/sessions/cb-1/items');
  timers.forEach((timer) => timer.callback());
  assert.equal(replaced, false, 'a ready full-page conversation is never replaced');
}

(async () => {
  await fullPageJourney({ failure: '403' });
  await fullPageJourney();
  await fullPageReadyJourney();
  await journey({ items: [{ id: 'item-1', text: 'private transcript' }] });
  await journey({ readOnly: true, delayedRoot: true });
  for (const failure of ['403', 'metadata', 'network', 'json', 'schema']) await journey({ failure });
})().catch((error) => { console.error(error); process.exitCode = 1; });
