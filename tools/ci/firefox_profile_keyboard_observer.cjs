// Temporary, passive MoonMind#4736 failure capture. Remove after a failing
// keyboard transition is understood; a passing instrumented run proves no fix.
// Loaded only by the existing Firefox CI command. Never log text, values or URLs.
function observeProfileKeyboard() {
  const testName = 'opens the Provider Profile column filter by keyboard and returns focus on Escape';
  const testFile = '/frontend/src/browser/workflowListResponsiveToolbar.browser.test.tsx';
  const selector = 'button[aria-label="Provider Profile filter. No filter applied."]';
  const dialogSelector = '[role="dialog"][aria-label="Provider Profile filter"]';
  const prefix = 'MM_PROFILE_KEYBOARD ';
  const ids = new WeakMap();
  let nextId = 0;
  let trigger = null;
  let records = 0;
  let observer;
  let host;
  const listeners = [];
  const inTest = () => {
    const state = window.__vitest_worker__;
    return state?.current?.name === testName && state?.filepath?.endsWith(testFile);
  };
  const describe = (node) => {
    if (!node) return null;
    if (!ids.has(node)) ids.set(node, ++nextId);
    return {
      node: ids.get(node), connected: node.isConnected === true,
      disabled: node.disabled === true,
      ariaDisabled: node.getAttribute?.('aria-disabled') === 'true',
      expanded: node.getAttribute?.('aria-expanded') === 'true',
    };
  };
  const emit = (phase, extra = {}) => {
    try {
      if (records >= 200) return;
      records += 1;
      const current = document.querySelector(selector);
      const dialog = document.querySelector(dialogSelector);
      console.debug(prefix + JSON.stringify({
        phase, sequence: records, time: Date.now(),
        trigger: describe(trigger), current: describe(current),
        active: describe(document.activeElement), sameTrigger: trigger === current,
        hasFocus: document.hasFocus(), width: innerWidth, height: innerHeight,
        desktop: matchMedia('(min-width: 768px)').matches,
        hostActive: describe(host?.document.activeElement),
        frame: describe(window.frameElement),
        ownFrameActive: !!host && window.frameElement === host.document.activeElement,
        hostHasFocus: host?.document.hasFocus() ?? null,
        dialog: describe(dialog), facetControls: dialog?.querySelectorAll('input').length ?? 0,
        ...extra,
      }));
    } catch { /* Diagnostics must never change the original test result. */ }
  };
  const stop = () => {
    observer?.disconnect();
    for (const [target, type, handler, capture] of listeners) {
      target.removeEventListener(type, handler, capture);
    }
    listeners.length = 0;
    trigger = null;
  };
  const listen = (target, type, handler, capture = true) => {
    target.addEventListener(type, handler, { capture, passive: true });
    listeners.push([target, type, handler, capture]);
  };
  const eventHandler = (scope, phase) => (event) => {
    try {
      if (!inTest()) { stop(); return; }
      if (event.type.startsWith('key') && !['Enter', 'Escape'].includes(event.key)) return;
      emit(`${scope}-${phase}-${event.type}`, {
        target: describe(event.target), related: describe(event.relatedTarget),
        targetIsTrigger: event.target === trigger, trusted: event.isTrusted,
        defaultPrevented: event.defaultPrevented,
        key: ['Enter', 'Escape'].includes(event.key) ? event.key : null,
      });
    } catch { /* Best effort only; do not touch the event. */ }
  };
  document.addEventListener('focus', (event) => {
    try {
      if (trigger || !inTest() || !event.target?.matches?.(selector)) return;
      trigger = event.target;
      // The host can receive a misdirected key even when the test iframe does not.
      try { if (top.document) host = top; } catch { host = null; }
      for (const [target, scope] of [[window, 'frame'], [host, 'host']]) {
        if (!target || (scope === 'host' && target === window)) continue;
        for (const type of ['keydown', 'keypress', 'keyup', 'click', 'focus', 'blur']) {
          listen(target, type, eventHandler(scope, 'capture'));
          listen(target, type, eventHandler(scope, 'bubble'), false);
        }
      }
      listen(window, 'resize', eventHandler('frame', 'capture'));
      observer = new MutationObserver((mutations) => {
        try {
          if (!inTest()) { emit('test-ended'); stop(); return; }
          emit('mutation', {
            batches: mutations.length,
            triggerAttributes: mutations.filter((m) => m.type === 'attributes' && m.target === trigger).length,
            triggerRemoved: mutations.some((m) => [...m.removedNodes].some((node) => node === trigger || node.contains?.(trigger))),
            dialogMutations: mutations.filter((m) => m.target.closest?.(dialogSelector)).length,
          });
        } catch { /* DOM observation is best effort too. */ }
      });
      observer.observe(document, {
        subtree: true, childList: true, attributes: true,
        attributeFilter: ['disabled', 'aria-disabled', 'aria-expanded'],
      });
      emit('trigger-focused', { target: describe(event.target), trusted: event.isTrusted });
    } catch { /* A capture setup failure is not a test failure or a pass. */ }
  }, { capture: true, passive: true });
}

function install(playwright, write) {
  const firefox = playwright.firefox;
  const marker = Symbol.for('moonmind.profileKeyboardObserver');
  if (firefox[marker]) return;
  firefox[marker] = true;
  const launch = firefox.launch;
  let pageNumber = 0;
  firefox.launch = async function (...args) {
    const browser = await launch.apply(this, args);
    try {
      const newContext = browser.newContext;
      browser.newContext = async function (...contextArgs) {
        const context = await newContext.apply(this, contextArgs);
        try {
          await context.addInitScript(observeProfileKeyboard);
          context.on('page', (page) => {
            try {
              const pageId = ++pageNumber;
              page.on('console', (message) => {
                try {
                  const text = message.text();
                  if (text.startsWith('MM_PROFILE_KEYBOARD ')) {
                    write({ page: pageId, ...JSON.parse(text.slice('MM_PROFILE_KEYBOARD '.length)) });
                  }
                } catch { /* Reporting cannot replace the test result. */ }
              });
              // Observe dispatch without evaluating/focusing a frame or doing I/O.
              // Restrict keys to the two controls in question, never typed content.
              for (const method of ['down', 'up']) {
                const original = page.keyboard[method];
                page.keyboard[method] = function (...keys) {
                  try {
                    if (keys[0] === 'Enter' || keys[0] === 'Escape') {
                      write({ phase: `command-${method}`, page: pageId, key: keys[0], time: Date.now() });
                    }
                  } catch { /* Preserve the native call, return and exception. */ }
                  return original.apply(this, keys);
                };
              }
            } catch { /* A listener installation failure must not affect the page. */ }
          });
          write({ phase: 'capture-installed' });
        } catch {
          try { write({ phase: 'capture-unavailable' }); } catch { /* Best effort. */ }
        }
        return context;
      };
    } catch { /* Return the original successful browser even if patching fails. */ }
    return browser;
  };
}

module.exports = { observeProfileKeyboard, install };

if (process.env.MOONMIND_BROWSER_ENGINES === 'firefox') {
  try {
    const fs = require('node:fs');
    const path = require('node:path');
    const directory = path.join(process.cwd(), 'artifacts', 'firefox-profile-keyboard');
    let bytes = 0;
    const lines = [];
    // No filesystem work on the input-dispatch path. Flush the bounded buffer
    // only when the existing test process exits, including a failing test exit.
    process.once('exit', () => {
      try {
        if (!lines.length) return;
        fs.mkdirSync(directory, { recursive: true });
        fs.writeFileSync(path.join(directory, `${process.pid}.jsonl`), lines.join(''));
      } catch { /* Storage errors cannot replace the original exit code. */ }
    });
    const write = (record) => {
      try {
        const line = JSON.stringify(record) + '\n';
        if (lines.length >= 400 || bytes + Buffer.byteLength(line) > 512 * 1024) return;
        bytes += Buffer.byteLength(line);
        lines.push(line);
      } catch { /* Original CI status is authoritative even if storage fails. */ }
    };
    install(require('playwright'), write);
  } catch { /* Missing diagnostics must not hide the original browser failure. */ }
}
