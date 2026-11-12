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
const ytFnRaw = () => extract('function renderYouTubeWorkstation(', '\n    }\n');

// responses: list of { kind: 'status'|'throw'|'badjson'|'ok', status, delay }
function makeEnv(responses, opts = {}) {
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
    fetch: async (url, init) => {
      log.lastInit = init || null;
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
    isDualBrowser: !!opts.dual, currentAuthToken: opts.token || null, currentAuthSessionId: 'sess-operator-test',
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
  test('signed-out locked copy names the real recovery path (Touch ID sign-in)', !!box && /Sign in with Touch ID/.test(box.textContent) && /stays closed/.test(box.textContent));
  test('signed-out requests carry no Authorization header', !(e.log.lastInit && e.log.lastInit.headers && e.log.lastInit.headers.Authorization));
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
  e = makeEnv([{ kind: 'status', status: 403 }], { dual: true });
  await e.ctx.fetchBrowserState('sess-2'); await tick();
  test('pane 2 failure renders in pane 2 only (dual mode)', e.vp2.children[0].dataset.kind === 'locked' && e.vp1.children.length === 0 && e.t2.textContent === 'P2 · Browser view locked' && e.t1.textContent === 'P1 · Ledger :8765');
  e = makeEnv([{ kind: 'status', status: 403 }]);
  vm.runInContext("paneSessionId.p1 = 'sess-2'", e.ctx);
  await e.ctx.fetchBrowserState('sess-2'); await tick();
  test('in single mode the session shown in pane 1 renders into pane 1', e.vp1.children[0].dataset.kind === 'locked' && e.vp2.children.length === 0);
  e = makeEnv([{ kind: 'ok', delay: 30 }]);
  const pending = e.ctx.fetchBrowserState('sess-1');
  vm.runInContext("paneSessionId.p1 = 'sess-9'", e.ctx);
  await pending; await tick();
  test('an answer for a session no longer shown in the pane is dropped', e.log.rendered1 === 0 && e.vp1.children.length === 0);
  e = makeEnv([{ kind: 'ok', delay: 30 }]);
  const pending2 = e.ctx.fetchBrowserState('sess-1');
  vm.runInContext('browserAuthEpoch++', e.ctx);
  await pending2; await tick();
  test('an answer from before a sign-in change is dropped', e.log.rendered1 === 0);
  e = makeEnv([{ kind: 'status', status: 403 }], { token: 'tok' });
  await e.ctx.fetchBrowserState('sess-1'); await tick();
  test('signed-in requests send the capability and session headers', e.log.lastInit.headers.Authorization === 'Bearer tok' && e.log.lastInit.headers['X-Session-ID'] === 'sess-operator-test');
  test('a signed-in 403 says the sign-in lacks browser control', /does not include browser control/.test(e.vp1.children[0].textContent));
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
  test('the splitter uses the same half-width cap', (html.match(/window\.innerWidth - 360, splitChatHalfWidth\(\)\)/g) || []).length >= 2 && html.includes('function splitChatHalfWidth()'));

  // 4. Split-mode pane position and the YouTube watch layout
  test('split mode resets the full-bleed 54px inset of the browser stage', /\.main-stage\.browser-split-mode \.browser-stage\s*\{\s*inset:\s*auto !important;\s*\}/.test(css));
  test('the browser pane is a named size container', /\.browser-viewport\s*\{\s*container:\s*browserpane \/ size;\s*\}/.test(css));
  const ytPlayer = rule('.yt-workstation > .yt-ws-player-box');
  test('the YouTube player keeps 16:9 and also fits the pane height', /max-height:\s*none !important/.test(ytPlayer) && /aspect-ratio:\s*16 \/ 9/.test(ytPlayer) && /calc\(var\(--yt-player-max-h\) \* 16 \/ 9\)/.test(ytPlayer));
  test('up next sits beside the player when the pane is wide', /@container browserpane \(min-width: 760px\)\s*\{\s*\.yt-workstation:has\(> \.yt-ws-player-box\)/.test(css) && /:has\(> \.yt-ws-player-box\) > \.yt-ws-results-scroll\s*\{\s*grid-column:\s*2/.test(css));
  test('the playing video title is shown under the player as text', ytFnRaw().includes('class="yt-ws-now"') && /setNowPlaying = \(v\) =>[\s\S]*?textContent = String\(v\.title/.test(ytFnRaw()));
  test('up next rows use watch-page thumbnails and two-line titles', /width:\s*168px !important/.test(rule('.yt-card-row > div:first-child')) && /-webkit-line-clamp:\s*2/.test(rule('.yt-card-row .yt-card-title')));
  const ytFn = ytFnRaw();
  test('YouTube result fields from the server are escaped', !/\$\{v\.(id|url|duration)\}/.test(ytFn) && ytFn.includes('encodeURIComponent(String(v.id))') && ytFn.includes('embed/${encodeURIComponent(clickVid)}'));

  // 5. Console-wide fixes from the adversarial pass
  test('the workspace cannot be scrolled sideways by a focused hidden control', /\.workspace-body\s*\{\s*overflow:\s*clip;\s*\}/.test(css));
  test('closed drawers are kept inert', /\['atelierSidebar', el => el\.classList\.contains\('is-collapsed'\)\]/.test(html) && /\['btwSideDrawer', el => !el\.classList\.contains\('is-open'\)\]/.test(html) && html.includes('el.inert = isClosed(el)'));
  test('the chat-vault dialog leaves the transformed sidebar and is modal', html.includes("if(dialog.parentElement!==document.body) document.body.appendChild(dialog);") && html.includes('shell.inert=open') && /chatVaultDialog'\)\.addEventListener\('keydown'[\s\S]{0,120}Escape/.test(html));
  test('Escape also closes the hub popovers', /if \(e\.key === 'Escape'\) \{[\s\S]{0,260}?if \(typeof closeAllPopovers === 'function'\) closeAllPopovers\(\);/.test(html));
  test('every referenced CSS name is defined in the console itself', (() => {
    const used = new Set([...html.matchAll(/var\((--[a-zA-Z0-9-]+)\)/g)].map(m => m[1]));
    const defined = new Set([...html.matchAll(/(--[a-zA-Z0-9-]+)\s*:/g)].map(m => m[1]));
    const scriptSet = new Set([...html.matchAll(/setProperty\('(--[a-zA-Z0-9-]+)'/g)].map(m => m[1]));
    return [...used].every(v => defined.has(v) || scriptSet.has(v));
  })());
  test('radar surfaces use light ink in Paper', /\.radar-workspace,\s*\.radar-stat-box,\s*\.radar-inspector-pane\s*\{\s*--ink-primary:\s*#F2EFE6/.test(css));
  test('radar inspector cannot overflow its card', /\.radar-workspace\s*\{\s*grid-template-columns:\s*minmax\(0, 1fr\) minmax\(0, 320px\);\s*\}/.test(css));
  test('toasts no longer cover the composer', /\.toast-bar\s*\{\s*bottom:\s*auto;\s*top:\s*66px;\s*\}/.test(css));
  test('header tooltips open below the header', /\.club-header \[data-tip\]::after\s*\{\s*bottom:\s*auto;\s*top:\s*calc\(100% \+ 8px\);/.test(css));
  test('the gateway LED is not green until readiness is READY', html.includes('<span class="led-dot" id="masterGatewayLed"></span>') && html.includes("classList.toggle('ready', state === 'READY')"));
  test('the integrity tile has no default OK and its colour follows the text', html.includes('id="rIntegrity" style="color: var(--ink-muted);">UNOBSERVED') && !html.includes("|| 'sqlite_pragma_ok'") && !html.includes('|| 13} Sillok') && (html.match(/rIntEl\.style\.color/g) || []).length === 3);
  test('a failed helper task does not keep a green LED', html.includes("getElementById('helperDeskLed')?.classList?.replace('ready', 'brass')"));
  test('static copy no longer claims unchecked connections', !/verification are connected|use live GraphRAG|Wired workspace :8765|Connected Ollama|Auto-Inject ON/.test(html));
  test('no control characters slipped into the file', !/[\x00-\x08\x0B\x0C\x0E-\x1F]/.test(html));

  // 6. Findings of the adversarial verification pass
  const escCtx = {}; vm.createContext(escCtx);
  vm.runInContext(extract('    function escapeHtml(str) {', '\n    }\n') + '\n    }', escCtx);
  const esc = escCtx.escapeHtml('x" onload="alert(1)\' <b>&');
  test('escapeHtml escapes quotes for attribute values', esc === 'x&quot; onload=&quot;alert(1)&#39; &lt;b&gt;&amp;' && escCtx.escapeHtml(null) === '');
  test('player and web frames escape their URLs', html.includes('embed/${encodeURIComponent(String(videoId))}') && html.includes('src="${escapeHtml(browserState.url)}"') && html.includes('src="${escapeHtml(browserState2.url)}"') && !/src="\$\{browserState2?\.url\}"/.test(html));
  e = makeEnv([{ kind: 'ok' }]);
  vm.runInContext("paneSessionId.p1 = 'sess-new-1'", e.ctx);
  await e.ctx.fetchBrowserState('sess-new-1'); await tick();
  test('a newly created session id still renders its state', e.log.rendered1 === 1);
  e = makeEnv([{ kind: 'ok' }]); e.ctx.fetch = async () => ({ ok: true, status: 200, json: async () => ({}) });
  await e.ctx.fetchBrowserState('sess-1'); await tick();
  test('a 200 without a usable state is an error, not a blank frame', e.log.rendered1 === 0 && e.vp1.children[0].dataset.kind === 'error' && e.vp1.children[0].textContent.includes('HTTP 200'));
  test('a completed navigation invalidates older state requests', /if \(epoch !== browserAuthEpoch \|\| paneSessionId\[pane\] !== sessionId\) return false;\s*\/\/ A completed navigation[^\n]*\n\s*browserStateSeq\[sessionId\] = \(browserStateSeq\[sessionId\] \|\| 0\) \+ 1;/.test(html));
  const okRe = /\(\/\(\^\|_\)OK\$\/\.test\(rIntEl\.textContent\) && !\/NOT\/\.test\(rIntEl\.textContent\)\)/;
  test('NOT_OK integrity is never green', (html.match(new RegExp(okRe.source, 'g')) || []).length === 2);
  test('Escape closes the vault dialog wherever focus is, and Tab stays inside', /if \(vaultDialog && !vaultDialog\.hidden\) \{ closeChatVaultDialog\(\); return; \}/.test(html) && html.includes("if(event.key!=='Tab') return;"));
  test('radar toolbar wraps instead of overlapping the inspector', /\.radar-canvas-toolbar,\s*\.radar-filter-group\s*\{\s*flex-wrap:\s*wrap;\s*\}/.test(css) && /--polo-brass:\s*#CAA96B/.test(css));
  test('chat resizer reports the rendered width', html.includes("chatResizerForAria.setAttribute('aria-valuenow', String(w))") && (html.match(/const snapped = Math\.max\(340, Math\.min\(480, splitChatHalfWidth\(\)\)\);/g) || []).length === 2 && !/\n\s*resizer\.setAttribute\('aria-valuenow', '480'\);/.test(html));

  // 7. Browser unlock and remaining debts
  test('login asks for control:browser', html.includes("permissions: ['read:provenance', 'read:knowledge', 'control:browser']"));
  test('navigate sends the capability headers', html.includes("headers: { 'Content-Type': 'application/json', ...browserAuthHeaders() }"));
  test('signing in or out reloads the visible panes', html.includes("if (typeof onBrowserAuthChanged === 'function') onBrowserAuthChanged(!!authenticated);") && /function onBrowserAuthChanged\(signedIn\) \{[\s\S]{0,400}refreshVisibleBrowserPanes\(\);/.test(html));
  test('swapping with an empty pane is refused instead of duplicating', /if \(!browserState \|\| !browserState2\) \{\s*showToast\('Both panes need a loaded page/.test(html));
  test('resizer aria range matches the real limits', html.includes('aria-valuemin="340" aria-valuemax="840"') && html.includes("chatResizerForAria.setAttribute('aria-valuemax'") && !html.includes("'aria-valuemin', '280'"));
  test('no hard-coded pane routing by session name remains', !/=== 'sess-2'\)|\? 'sess-2' : 'sess-1'/.test(html) && !/sid = \(paneId === 'p2'\) \? 'sess-2'/.test(html));
  test('answers are dropped when the pane shows another session or the sign-in changed', html.includes("const isLatest = () => seq === browserStateSeq[sessionId] && epoch === browserAuthEpoch && paneSessionId[pane] === sessionId;") && html.includes("if (epoch !== browserAuthEpoch || paneSessionId[pane] !== sessionId) return false;"));
  test('signing out clears kept browser states and the URL bar', /if \(!signedIn\) \{\s*browserState = null;\s*browserState2 = null;\s*if \(browserUrlInput\) browserUrlInput\.value = '';/.test(html));
  test('dual mode never shows the same session in both panes', html.includes("if (paneSessionId.p2 === paneSessionId.p1) {"));
  test('reload, back and forward use the focused pane mapping', (html.match(/const pane = focusedBrowserPane\(\), sid = paneSessionId\[pane\];/g) || []).length === 3);
  test('YouTube cards navigate the session shown in their pane', html.includes('const sid = paneSessionId[ytPane];') && html.includes('navigateBrowser(clickUrl, sid, false, ytPane);'));
  test('resizer aria max follows window resizes', html.includes("window.addEventListener('resize', () => {\n      const r = document.getElementById('chatWidthResizer');"));

  const frameCtx = { location: { href: 'http://127.0.0.1:8788/', origin: 'http://127.0.0.1:8788' }, URL }; vm.createContext(frameCtx);
  vm.runInContext(extract('    function isSafeFrameUrl(value) {', '\n    }\n') + '\n    }', frameCtx);
  test('web frames refuse the console origin and non-http URLs', frameCtx.isSafeFrameUrl('https://example.org/') && !frameCtx.isSafeFrameUrl('http://127.0.0.1:8788/api/vault') && !frameCtx.isSafeFrameUrl('/api/vault') && !frameCtx.isSafeFrameUrl('javascript:alert(1)') && !frameCtx.isSafeFrameUrl('data:text/html,x'));
  test('both web panes check the frame URL before rendering', (html.match(/\} else if \(!isSafeFrameUrl\(browserState2?\.url\)\) \{/g) || []).length === 2);

  // Honest copy elsewhere in the pane
  test('the ChatGPT card no longer claims an authenticated session', !html.includes('is active and authenticated'));

  const failed = results.filter(x => x.result === 'FAIL');
  console.log(JSON.stringify({ total: results.length, passed: results.length - failed.length, failed: failed.length, results, scope: 'isolated JS functions, fake DOM and CSS/source text; no server request' }));
  if (failed.length) process.exitCode = 1;
})().catch(e => { console.error(e.stack); process.exitCode = 1; });
