'use strict';
/**
 * Browser split pane contract.
 *  1. A failed browser-state request renders a visible, honest state instead of a blank pane,
 *     and only the latest request of a pane may render.
 *  2. The header toolbar gives up space in priority order; the close button is never hidden.
 *  3. The chat column never takes more than half of the split stage, and the splitter agrees.
 * Scope: isolated JS functions with a fake DOM plus CSS/source text checks; no server request.
 * Layout at real widths is checked separately in a browser (see the PR).
 */
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '..', 'te-agent-console.html'), 'utf8');
const css = html.slice(0, html.indexOf('<script>'));
const results = [];
const test = (name, ok) => results.push({ test: name, result: ok ? 'PASS' : 'FAIL' });
const tick = () => new Promise(r => setTimeout(r, 5));

function extract(start, end) {
  const i = html.indexOf(start), j = html.indexOf(end, i);
  if (i < 0 || j < 0) throw new Error('Missing source segment: ' + start);
  return html.slice(i, j);
}
function rule(selector) {
  const m = css.match(new RegExp(selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '\\s*\\{([^}]*)\\}'));
  return m ? m[1].replace(/\s+/g, ' ') : '';
}

class FakeEl {
  constructor(tag, doc) { this.tagName = tag; this.doc = doc; this.children = []; this.attrs = {}; this.dataset = {}; this.listeners = {}; this._text = ''; this.className = ''; this.parent = null; }
  set textContent(v) { this._text = String(v); this.children = []; }
  get textContent() { return [this._text, ...this.children.map(c => c.textContent)].filter(Boolean).join(' '); }
  set innerHTML(v) { this.htmlWrites = (this.htmlWrites || 0) + 1; this._text = ''; this.children = []; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k]; }
  append(...c) { c.forEach(x => { x.parent = this; }); this.children.push(...c); }
  replaceChildren(...c) { this._text = ''; this.children = []; this.append(...c); }
  addEventListener(t, f) { (this.listeners[t] ||= []).push(f); }
  contains(el) { for (let n = el; n; n = n.parent) if (n === this) return true; return false; }
  focus() { this.doc.activeElement = this; }
  find(cls) { if ((' ' + this.className + ' ').includes(' ' + cls + ' ')) return this; for (const c of this.children) { const f = c.find(cls); if (f) return f; } return null; }
}

const failureCode = extract('    // Browser pane failure states', '    async function navigateBrowser(');

// responses: list of { kind: 'status'|'throw'|'badjson'|'ok', status, delay }
function makeEnv(responses) {
  const doc = { activeElement: null };
  const vp1 = new FakeEl('div', doc), vp2 = new FakeEl('div', doc);
  const t1 = new FakeEl('span', doc), t2 = new FakeEl('span', doc);
  t1.textContent = 'P1 · Ledger :8765'; t2.textContent = 'P2 · ChatGPT Project';
  const els = { browserViewport: vp1, browserViewport2: vp2, pane1Title: t1, pane2Title: t2 };
  const log = { fetched: 0, warned: 0, rendered1: 0, rendered2: 0 };
  let n = 0;
  const ctx = {
    setTimeout,
    console: { warn: () => { log.warned++; } },
    navigator: { onLine: true },
    document: Object.assign(doc, { getElementById: id => els[id] || null, createElement: t => new FakeEl(t, doc) }),
    fetch: async () => {
      const r = responses[Math.min(n++, responses.length - 1)]; log.fetched++;
      if (r.delay) await new Promise(res => setTimeout(res, r.delay));
      if (r.kind === 'throw') throw new TypeError('Failed to fetch');
      if (r.kind === 'status') return { ok: false, status: r.status, json: async () => ({ ok: false, error: '<img src=x onerror=alert(1)>' }) };
      if (r.kind === 'badjson') return { ok: true, status: 200, json: async () => { throw new SyntaxError('bad json'); } };
      return { ok: true, status: 200, json: async () => ({ kind: 'web', url: 'about:blank', title: 'Real page' }) };
    },
    renderBrowserViewport: () => { log.rendered1++; vp1.replaceChildren(); t1.textContent = 'P1 · Real page'; },
    renderBrowserViewport2: () => { log.rendered2++; vp2.replaceChildren(); },
    updateCovisionRibbon: () => {}, updateNavBtnStates: () => {},
    activeFocusedPane: 'top', browserUrlInput: { value: '' }, browserState: null, browserState2: null,
  };
  vm.createContext(ctx);
  vm.runInContext(failureCode, ctx, { timeout: 1000 });
  return { ctx, vp1, vp2, t1, t2, log, doc };
}

(async () => {
  // 1. Failure states
  let e = makeEnv([{ kind: 'status', status: 403 }]);
  await e.ctx.fetchBrowserState('sess-1'); await tick();
  let box = e.vp1.children[0], live = box && box.find('browser-unavailable-live');
  test('403 renders a visible locked state', !!box && box.dataset.kind === 'locked' && /locked/i.test(box.textContent));
  test('state text sits in a polite status region', !!live && live.getAttribute('role') === 'status');
  test('Check again button is outside the announced region', !!live && !live.find('browser-unavailable-retry') && !!box.find('browser-unavailable-retry'));
  test('locked state names the code and that nothing runs', !!box && box.textContent.includes('HTTP 403') && /nothing is running/i.test(box.textContent));
  test('locked copy does not promise a recovery path', !!box && !/until|sign in|log in/i.test(box.textContent) && /by design/i.test(box.textContent));
  test('no state claims an active or authenticated session', !!box && !/\b(active|authenticated|connected)\b/i.test(box.textContent));
  test('server error text is not rendered', !!box && !box.textContent.includes('<img'));
  test('failure state is built with DOM text, not innerHTML', !e.vp1.htmlWrites);
  test('pane title follows the failure state', e.t1.textContent === 'P1 · Browser view locked');

  const retry = box.find('browser-unavailable-retry');
  test('the recheck control is a labelled button', retry.textContent === 'Check again' && retry.type === 'button');
  retry.focus();
  await retry.listeners.click[0](); await tick();
  const retry2 = e.vp1.children[0].find('browser-unavailable-retry');
  test('Check again asks the server once more', e.log.fetched === 2);
  test('keyboard focus moves to the new Check again button', e.doc.activeElement === retry2 && retry2 !== retry);
  test('a repeated failure shows when it was checked', /checked \d/.test(e.vp1.children[0].textContent));

  e = makeEnv([{ kind: 'throw' }]);
  await e.ctx.fetchBrowserState('sess-1'); await tick();
  test('no response (CSP or service down) renders a blocked state', e.vp1.children[0].dataset.kind === 'blocked' && e.vp1.children[0].textContent.includes('No response'));
  test('blocked failure is still logged for developers', e.log.warned === 1);
  e = makeEnv([{ kind: 'throw' }]); e.ctx.navigator.onLine = false;
  await e.ctx.fetchBrowserState('sess-1'); await tick();
  test('offline browser renders an offline state', e.vp1.children[0].dataset.kind === 'offline');
  e = makeEnv([{ kind: 'status', status: 500 }]);
  await e.ctx.fetchBrowserState('sess-1'); await tick();
  test('5xx renders an error state with its code', e.vp1.children[0].dataset.kind === 'error' && e.vp1.children[0].textContent.includes('HTTP 500'));
  e = makeEnv([{ kind: 'badjson' }]);
  await e.ctx.fetchBrowserState('sess-1'); await tick();
  test('an unreadable 200 is an error with HTTP 200, not "no response"', e.vp1.children[0].dataset.kind === 'error' && e.vp1.children[0].textContent.includes('HTTP 200'));
  e = makeEnv([{ kind: 'status', status: 403 }]);
  await e.ctx.fetchBrowserState('sess-2'); await tick();
  test('pane 2 failure renders in pane 2 only', e.vp2.children[0].dataset.kind === 'locked' && e.vp1.children.length === 0 && e.t2.textContent === 'P2 · Browser view locked' && e.t1.textContent === 'P1 · Ledger :8765');
  e = makeEnv([{ kind: 'ok' }]);
  await e.ctx.fetchBrowserState('sess-1'); await tick();
  test('a good response still renders the real viewport', e.log.rendered1 === 1 && e.vp1.children.length === 0);

  // Ordering: only the latest request of a pane renders.
  e = makeEnv([{ kind: 'status', status: 403, delay: 40 }, { kind: 'ok' }]);
  await Promise.all([e.ctx.fetchBrowserState('sess-1'), e.ctx.fetchBrowserState('sess-1')]); await tick();
  test('a slow failure cannot overwrite a newer success', e.vp1.children.length === 0 && e.log.rendered1 === 1 && e.t1.textContent === 'P1 · Real page');
  e = makeEnv([{ kind: 'ok', delay: 40 }, { kind: 'status', status: 403 }]);
  await Promise.all([e.ctx.fetchBrowserState('sess-1'), e.ctx.fetchBrowserState('sess-1')]); await tick();
  test('a slow success cannot overwrite a newer failure', e.log.rendered1 === 0 && e.vp1.children[0].dataset.kind === 'locked');

  // 2. Toolbar
  test('failure state has its own styles', !!rule('.browser-unavailable') && !!rule('.browser-unavailable-title') && !!rule('.browser-unavailable-live'));
  const chips = rule('.header-omnibar-slot .browser-teleport-chips');
  test('header chips shrink and scroll before anything else', /flex:\s*0 1 auto/.test(chips) && /min-width:\s*0/.test(chips));
  test('header toolbar adapts to its own width', /\.header-omnibar-slot\s*\{\s*container-type:\s*inline-size;\s*\}/.test(css) && /@container[^{]*\{\s*(\/\*[^*]*\*\/\s*)?\.header-omnibar-slot/.test(css));
  const containerBlocks = css.split('@container').slice(1).map(b => b.slice(0, b.indexOf('\n    }\n') + 1));
  test('close button is never hidden by the responsive toolbar rules',
    containerBlocks.length >= 5 && !containerBlocks.some(b => /closeBrowserStageBtn|browser-close-btn|browser-actions-right/.test(b)));
  test('the narrowest tier leaves only the close button', containerBlocks.some(b => /browser-nav-group/.test(b) && /browser-url-bar/.test(b) && /addBrowserPaneBtn/.test(b)));

  // 3. Chat width
  const chatWidth = 'min(var(--split-chat-width, 480px), 50%)';
  test('chat column never exceeds half of the split stage', rule('.main-stage.browser-split-mode .thread-scroll').split(chatWidth).length >= 4);
  test('docked composer follows the same chat width', rule('.main-stage.browser-split-mode .composer-dock').split(chatWidth).length >= 3);
  test('the splitter uses the same half-width cap', (html.match(/window\.innerWidth - 360, splitChatHalfWidth\(\)\)/g) || []).length === 2 && html.includes('function splitChatHalfWidth()'));

  // Honest copy elsewhere in the pane
  test('the ChatGPT card no longer claims an authenticated session', !html.includes('is active and authenticated'));

  const failed = results.filter(x => x.result === 'FAIL');
  console.log(JSON.stringify({ total: results.length, passed: results.length - failed.length, failed: failed.length, results, scope: 'isolated JS functions, fake DOM and CSS/source text; no server request' }));
  if (failed.length) process.exitCode = 1;
})().catch(e => { console.error(e.stack); process.exitCode = 1; });
