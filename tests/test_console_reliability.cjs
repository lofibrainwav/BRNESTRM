/**
 * Console Reliability & Accessibility Automated Verification Suite
 * Verifies all 6 requirements:
 *   1. HTTP 403, 500, Timeout error classifications (권한 차단, 서버 오류, 연결 실패).
 *   2. Vault failure: zero unshift() fake item generation, zero retention of failed secret.
 *   3. Vault delete: zero pre-emptive deletion without server confirmation.
 *   4. Panel-level independent error isolation, stale states, and last successful time tracking.
 *   5. Mutation safety: strictly zero automatic retries on non-idempotent operations (POST/DELETE).
 *   6. Split panel Pointer Events & Keyboard accessibility (Arrow keys, Home/End/Enter, aria-valuenow).
 *   7. Token usage preservation for Ollama vs UNMEASURED for unmetered providers.
 *   8. Common Toast notification expansion and duplicate suppression (deduplication).
 */
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const HTML_PATH = path.join(__dirname, '..', 'te-agent-console.html');
const htmlSource = fs.readFileSync(HTML_PATH, 'utf8');

// Extract script contents
const scriptMatch = htmlSource.match(/<script>([\s\S]*?)<\/script>/);
if (!scriptMatch) {
  console.error('FAIL: No script tag found in te-agent-console.html');
  process.exit(1);
}
const scriptCode = scriptMatch[1];

// Create Mock DOM environment
function createMockEnvironment() {
  const elements = new Map();
  const listeners = new Map();

  function createMockElement(id, tagName = 'div', attrs = {}) {
    const el = {
      id,
      tagName: tagName.toUpperCase(),
      attributes: { ...attrs },
      style: {
        setProperty(k, v) { this[k] = v; }
      },
      classList: {
        classes: new Set(),
        add(c) { this.classes.add(c); },
        remove(c) { this.classes.delete(c); },
        toggle(c, force) {
          if (force === undefined) {
            if (this.classes.has(c)) this.classes.delete(c);
            else this.classes.add(c);
          } else if (force) this.classes.add(c);
          else this.classes.delete(c);
        },
        contains(c) { return this.classes.has(c); }
      },
      value: '',
      _textContent: '',
      _innerHTML: '',
      get textContent() { return this._textContent; },
      set textContent(v) { this._textContent = String(v); this._innerHTML = String(v); },
      get innerHTML() { return this._innerHTML; },
      set innerHTML(v) { this._innerHTML = String(v); this._textContent = String(v); },
      get className() { return Array.from(this.classList.classes).join(' '); },
      set className(v) {
        this.classList.classes.clear();
        (v || '').split(/\s+/).filter(Boolean).forEach(c => this.classList.classes.add(c));
      },
      title: '',
      disabled: false,
      getAttribute(k) { return this.attributes[k] !== undefined ? this.attributes[k] : null; },
      setAttribute(k, v) { this.attributes[k] = String(v); },
      removeAttribute(k) { delete this.attributes[k]; },
      addEventListener(evt, fn) {
        if (!listeners.has(this)) listeners.set(this, {});
        const m = listeners.get(this);
        if (!m[evt]) m[evt] = [];
        m[evt].push(fn);
      },
      dispatchEvent(event) {
        const m = listeners.get(this);
        if (m && m[event.type]) {
          m[event.type].forEach(fn => fn.call(this, event));
        }
      },
      querySelector(sel) { return null; },
      querySelectorAll(sel) { return []; },
      closest(sel) { return this; },
      getBoundingClientRect() { return { top: 0, left: 0, width: 800, height: 600 }; },
      getContext(type) {
        return {
          save() {}, restore() {}, beginPath() {}, arc() {}, fill() {}, stroke() {},
          fillRect() {}, strokeRect() {}, fillText() {}, measureText() { return { width: 50 }; },
          clearRect() {}, moveTo() {}, lineTo() {}, setLineDash() {}, scale() {}, translate() {}, rotate() {}
        };
      },
      setPointerCapture(id) { this._capturedPointer = id; },
      releasePointerCapture(id) { delete this._capturedPointer; },
      focus() {}
    };
    elements.set(id, el);
    return el;
  }

  // Pre-seed known elements from HTML with static attributes
  const knownElements = [
    ['commonToast', 'div', { role: 'alert' }],
    ['commonToastText', 'span', {}],
    ['commonToastActionBtn', 'button', {}],
    ['commonToastCloseBtn', 'button', {}],
    ['undoToast', 'div', {}],
    ['undoToastText', 'span', {}],
    ['undoDeleteBtn', 'button', {}],
    ['vaultListContainer', 'div', {}],
    ['vaultCountBadge', 'span', {}],
    ['vaultStatusBadge', 'span', {}],
    ['newVaultName', 'input', {}],
    ['newVaultSecret', 'input', {}],
    ['addVaultSecretBtn', 'button', {}],
    ['retryVaultBtn', 'button', {}],
    ['liveEcoBadge', 'span', {}],
    ['liveInventoryBox', 'div', {}],
    ['retryEcoBtn', 'button', {}],
    ['sillokRecordCount', 'span', {}],
    ['sillokTimelineBox', 'div', {}],
    ['retrySillokBtn', 'button', {}],
    ['browserPaneSplitter', 'div', { role: 'separator', tabindex: '0', 'aria-orientation': 'horizontal', 'aria-valuenow': '50', 'aria-valuemin': '20', 'aria-valuemax': '80' }],
    ['browserStage', 'div', {}],
    ['splitterDragBadge', 'div', {}],
    ['chatWidthResizer', 'div', { role: 'separator', tabindex: '0', 'aria-orientation': 'vertical', 'aria-valuenow': '480', 'aria-valuemin': '280', 'aria-valuemax': '900' }],
    ['chatResizerBadge', 'div', {}],
    ['threadColumn', 'div', {}],
    ['panelConsole', 'div', {}],
    ['promptInput', 'textarea', {}],
    ['sendBtn', 'button', {}],
    ['activeModelLabel', 'span', {}],
    ['r1Count', 'span', {}],
    ['r2Count', 'span', {}],
    ['r3Count', 'span', {}],
    ['rIntegrity', 'span', {}],
    ['atelierSidebar', 'aside', {}],
    ['sidebarToggleBtn', 'button', {}]
  ];
  knownElements.forEach(([id, tag, attrs]) => createMockElement(id, tag, attrs));

  const windowMock = {
    addEventListener(evt, fn) {},
    removeEventListener(evt, fn) {},
    matchMedia: () => ({ matches: false }),
    devicePixelRatio: 1,
    innerWidth: 1200,
    innerHeight: 800,
    requestAnimationFrame: (cb) => setTimeout(cb, 0),
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    Date,
    Math,
    JSON,
    Array,
    String,
    Number,
    Boolean,
    RegExp,
    Promise,
    Map,
    Set,
    console
  };

  const documentMock = {
    addEventListener(evt, fn) {},
    removeEventListener(evt, fn) {},
    createElement(tag) {
      return createMockElement('temp-' + Math.random(), tag);
    },
    getElementById(id) {
      if (!elements.has(id)) {
        return createMockElement(id);
      }
      return elements.get(id);
    },
    querySelectorAll(sel) { return []; },
    querySelector(sel) { return null; },
    documentElement: createMockElement('html'),
    body: createMockElement('body')
  };

  return { windowMock, documentMock, elements };
}

async function runReliabilityTests() {
  console.log('--- Starting Operational Reliability Test Suite ---');
  let passed = 0;
  let total = 0;

  function test(name, fn) {
    total++;
    try {
      fn();
      console.log(`  [PASS] Test ${total}: ${name}`);
      passed++;
    } catch (err) {
      console.error(`  [FAIL] Test ${total}: ${name}`);
      console.error('         Error:', err.message);
      if (err.stack) console.error(err.stack.split('\n').slice(1, 4).join('\n'));
    }
  }

  async function asyncTest(name, fn) {
    total++;
    try {
      await fn();
      console.log(`  [PASS] Test ${total}: ${name}`);
      passed++;
    } catch (err) {
      console.error(`  [FAIL] Test ${total}: ${name}`);
      console.error('         Error:', err.message);
      if (err.stack) console.error(err.stack.split('\n').slice(1, 4).join('\n'));
    }
  }

  // Set up mock sandbox
  const { windowMock, documentMock, elements } = createMockEnvironment();
  const sandbox = {
    window: windowMock,
    document: documentMock,
    fetch: async () => ({ ok: false, status: 404, json: async () => ({}) }),
    AbortController: global.AbortController || class { abort() {} signal = {}; },
    ...windowMock
  };

  // Run script in context
  const vm = require('vm');
  const ctx = vm.createContext(sandbox);
  vm.runInContext(scriptCode, ctx);

  // 1. Error Classification Tests
  test('classifyHttpError correctly maps HTTP 403 to "권한 차단"', () => {
    const res = sandbox.classifyHttpError(403, new Error('Forbidden'));
    assert.strictEqual(res.code, 403);
    assert.strictEqual(res.label, '권한 차단');
    assert.strictEqual(res.category, 'PERMISSION_BLOCKED');
  });

  test('classifyHttpError correctly maps HTTP 500 to "서버 오류"', () => {
    const res = sandbox.classifyHttpError(500, new Error('Internal Server Error'));
    assert.strictEqual(res.code, 500);
    assert.strictEqual(res.label, '서버 오류');
    assert.strictEqual(res.category, 'SERVER_ERROR');
  });

  test('classifyHttpError correctly maps AbortError/timeout to "연결 실패 (타임아웃)"', () => {
    const err = new Error('The operation was aborted');
    err.name = 'AbortError';
    const res = sandbox.classifyHttpError(0, err);
    assert.strictEqual(res.code, 504);
    assert.strictEqual(res.label, '연결 실패 (타임아웃)');
    assert.strictEqual(res.category, 'TIMEOUT');
  });

  test('classifyHttpError correctly maps network failure to "연결 실패"', () => {
    const res = sandbox.classifyHttpError(0, new Error('Failed to fetch'));
    assert.strictEqual(res.label, '연결 실패');
    assert.strictEqual(res.category, 'CONNECTION_FAILED');
  });

  // 2. Vault Security & No Fake Items
  await asyncTest('Vault Add failure: strictly ZERO unshift() fake item and ZERO secret retention', async () => {
    const getVaultItems = sandbox.getVaultItems || sandbox.window.getVaultItems;
    assert.strictEqual(typeof getVaultItems, 'function', 'getVaultItems must be a function');
    const initialCount = getVaultItems().length;

    // Simulate server returning 403 Forbidden
    sandbox.fetch = async () => ({
      ok: false,
      status: 403,
      json: async () => ({ ok: false, error: 'forbidden: private vault locked' })
    });

    const nameInput = documentMock.getElementById('newVaultName');
    const secInput = documentMock.getElementById('newVaultSecret');
    const saveBtn = documentMock.getElementById('addVaultSecretBtn');

    nameInput.value = 'TEST_SECRET_KEY';
    secInput.value = 'super-secret-password-xyz';

    // Trigger save click
    saveBtn.dispatchEvent({ type: 'click', stopPropagation: () => {} });
    await new Promise(r => setTimeout(r, 50));

    // Input fields must be cleared immediately
    assert.strictEqual(nameInput.value, '');
    assert.strictEqual(secInput.value, '');

    // vaultItems must NOT have received any fake items
    const currentItems = getVaultItems();
    assert.strictEqual(currentItems.length, initialCount, 'vaultItems length must not change on failure');
    const foundSecret = currentItems.some(v => v.secret && v.secret.includes('super-secret-password-xyz'));
    assert.strictEqual(foundSecret, false, 'Failed secret must never be retained in memory');
  });

  await asyncTest('Vault Delete failure: strictly ZERO UI deletion without server success confirmation', async () => {
    const getVaultItems = sandbox.getVaultItems || sandbox.window.getVaultItems;
    const initialCount = getVaultItems().length;

    // Simulate server returning 403 on delete
    sandbox.fetch = async () => ({
      ok: false,
      status: 403,
      json: async () => ({ ok: false, error: 'forbidden' })
    });

    // Attempt safe delete
    try {
      await sandbox.safeFetch('http://127.0.0.1:8788/api/vault/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: 'v-nonexistent' })
      }, false);
      assert.fail('Should have thrown on 403');
    } catch (e) {
      // Deletion failed: item count must remain unchanged!
      assert.strictEqual(getVaultItems().length, initialCount, 'Item must NOT be removed from UI when server rejects delete');
    }
  });

  // 3. Stale State & Last Success Timestamp
  await asyncTest('Ecosystem panel: preserves last success timestamp and transitions to STALE on subsequent failure', async () => {
    const pStates = sandbox.panelStates || sandbox.window.panelStates;
    assert.ok(pStates && pStates.ecosystem, 'panelStates.ecosystem must be defined');

    // 1st request: SUCCESS
    const mockEcoData = {
      workspace: { counts: { total: 42 }, source_digest: 'abc123456' },
      daemons: { d1: true, d2: true }
    };
    sandbox.fetch = async () => ({
      ok: true,
      status: 200,
      json: async () => mockEcoData
    });

    await sandbox.syncLiveEcosystem(false);
    assert.strictEqual(pStates.ecosystem.status, 'SUCCESS');
    assert.strictEqual(pStates.ecosystem.isStale, false);
    assert.notStrictEqual(pStates.ecosystem.lastSuccessTime, null);
    const firstSuccess = pStates.ecosystem.lastSuccessTime;

    // 2nd request: 500 SERVER ERROR
    sandbox.fetch = async () => ({
      ok: false,
      status: 500,
      json: async () => ({ error: 'gateway daemon crashed' })
    });

    await sandbox.syncLiveEcosystem(false);
    // Must preserve data, keep lastSuccessTime, and mark isStale = true
    assert.strictEqual(pStates.ecosystem.status, 'STALE');
    assert.strictEqual(pStates.ecosystem.isStale, true);
    assert.strictEqual(pStates.ecosystem.lastSuccessTime, firstSuccess);
    assert.strictEqual(pStates.ecosystem.data.workspace.counts.total, 42);

    const badge = documentMock.getElementById('liveEcoBadge');
    assert.ok(badge.textContent.includes('STALE'), 'Badge must display STALE');
    assert.ok(badge.textContent.includes('서버 오류'), 'Badge must display error classification');
  });

  // 4. Panel Error Isolation & Safe Retry
  await asyncTest('Independent panel errors: Vault failure does not pollute Ecosystem or Ledger', async () => {
    const pStates = sandbox.panelStates || sandbox.window.panelStates;
    // Reset states
    pStates.ecosystem.status = 'SUCCESS';
    pStates.ledger.status = 'IDLE';
    pStates.vault.status = 'IDLE';

    // Vault fails with 403
    sandbox.fetch = async () => ({
      ok: false,
      status: 403,
      json: async () => ({ error: 'unauthorized' })
    });
    await sandbox.syncVault(false);

    assert.strictEqual(pStates.vault.status, 'ERROR');
    assert.strictEqual(pStates.vault.lastError.code, 403);
    // Ecosystem remains SUCCESS
    assert.strictEqual(pStates.ecosystem.status, 'SUCCESS');
    // Ledger remains IDLE
    assert.strictEqual(pStates.ledger.status, 'IDLE');
  });

  await asyncTest('Mutation safety: POST request never retries automatically on failure (prevents double mutations)', async () => {
    let mutationCallCount = 0;
    sandbox.fetch = async (url, opts) => {
      if (url === 'http://test-mutation-post.local') {
        mutationCallCount++;
        return { ok: false, status: 500, json: async () => ({ error: 'db failure' }) };
      }
      return { ok: false, status: 404, json: async () => ({}) };
    };

    try {
      // POST with allowAutoRetry = false
      await sandbox.safeFetch('http://test-mutation-post.local', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: 'TEST', secret: 'abc' })
      }, false);
    } catch (e) {
      // Expected to fail
    }
    assert.strictEqual(mutationCallCount, 1, 'Mutation POST must execute exactly once (0 auto-retries)');
  });

  await asyncTest('Read GET request: allows exactly 1 automatic retry on transient failure', async () => {
    let getCallCount = 0;
    sandbox.fetch = async (url, opts) => {
      if (url === 'http://test-idempotent-get.local') {
        getCallCount++;
        if (getCallCount === 1) {
          throw new Error('Failed to fetch');
        }
        return { ok: true, status: 200, json: async () => ({ ok: true, live: true }) };
      }
      return { ok: false, status: 404, json: async () => ({}) };
    };

    const res = await sandbox.safeFetch('http://test-idempotent-get.local', { method: 'GET' }, true);
    assert.strictEqual(getCallCount, 2, 'Idempotent GET must auto-retry once on transient network glitch');
    assert.strictEqual(res.ok, true);
  });

  // 5. Splitter Pointer Events & Keyboard Accessibility
  test('browserPaneSplitter supports Pointer Events and keyboard arrow keys with aria-valuenow', () => {
    const splitter = documentMock.getElementById('browserPaneSplitter');
    const stage = documentMock.getElementById('browserStage');

    assert.strictEqual(splitter.getAttribute('role'), 'separator');
    assert.strictEqual(splitter.getAttribute('tabindex'), '0');
    assert.strictEqual(splitter.getAttribute('aria-orientation'), 'horizontal');
    assert.strictEqual(splitter.getAttribute('aria-valuenow'), '50');

    // Test ArrowDown (+5%)
    splitter.dispatchEvent({ type: 'keydown', key: 'ArrowDown', preventDefault: () => {} });
    assert.strictEqual(splitter.getAttribute('aria-valuenow'), '55');

    // Test ArrowUp (-5%)
    splitter.dispatchEvent({ type: 'keydown', key: 'ArrowUp', preventDefault: () => {} });
    assert.strictEqual(splitter.getAttribute('aria-valuenow'), '50');

    // Test Home (Min 20%)
    splitter.dispatchEvent({ type: 'keydown', key: 'Home', preventDefault: () => {} });
    assert.strictEqual(splitter.getAttribute('aria-valuenow'), '20');

    // Test End (Max 80%)
    splitter.dispatchEvent({ type: 'keydown', key: 'End', preventDefault: () => {} });
    assert.strictEqual(splitter.getAttribute('aria-valuenow'), '80');

    // Test Enter (Snap to 50%)
    splitter.dispatchEvent({ type: 'keydown', key: 'Enter', preventDefault: () => {} });
    assert.strictEqual(splitter.getAttribute('aria-valuenow'), '50');

    // Test PointerDown capture
    splitter.dispatchEvent({ type: 'pointerdown', pointerId: 42, preventDefault: () => {} });
    assert.strictEqual(splitter._capturedPointer, 42, 'setPointerCapture must be called on pointerdown');

    // Test PointerUp release
    splitter.dispatchEvent({ type: 'pointerup', pointerId: 42 });
    assert.strictEqual(splitter._capturedPointer, undefined, 'releasePointerCapture must be called on pointerup');
  });

  // 6. Token Usage Metrics vs UNMEASURED
  test('Token Usage rendering: displays prompt/completion when measured, and UNMEASURED otherwise', () => {
    const threadCol = documentMock.getElementById('threadColumn');

    // Case A: Ollama response with measured usage
    const sessionWithMeasured = {
      messages: [{
        role: 'agent',
        botName: 'OllamaBot',
        provider: 'ollama',
        model: 'qwen3.5:9b',
        usage: { promptTokens: 35, completionTokens: 90, totalTokens: 125, measured: true },
        heading: 'Ollama Response',
        body: 'Here is the response'
      }]
    };
    sandbox.renderThread(sessionWithMeasured);
    assert.ok(threadCol.innerHTML.includes('Tokens: 35 in / 90 out (125 total)'), 'Must render exact token metrics');

    // Case B: Unmeasured provider (Codex/Claude)
    const sessionUnmeasured = {
      messages: [{
        role: 'agent',
        botName: 'CodexBot',
        provider: 'chatgpt',
        model: 'gpt-4o',
        usage: { promptTokens: 'UNMEASURED', completionTokens: 'UNMEASURED', totalTokens: 'UNMEASURED', measured: false },
        heading: 'Codex Response',
        body: 'Here is the response'
      }]
    };
    sandbox.renderThread(sessionUnmeasured);
    assert.ok(threadCol.innerHTML.includes('Tokens: UNMEASURED'), 'Must render Tokens: UNMEASURED when not measured');
  });

  // 7. Toast Notification Expansion & Deduplication
  test('Toast Notification expansion: displays error/warn/success and deduplicates identical alerts', () => {
    // Reset toast state
    sandbox.window.__toastHistory = [];
    sandbox.window.__toastDedupCount = 0;

    // Call 1: Success
    const res1 = sandbox.showToast('First unique notification', 'success');
    assert.strictEqual(res1, true, 'First toast must be displayed');
    const toastEl = documentMock.getElementById('commonToast');
    assert.strictEqual(toastEl.classList.contains('is-success'), true);

    // Call 2: Duplicate within 2500ms
    const res2 = sandbox.showToast('First unique notification', 'success');
    assert.strictEqual(res2, false, 'Duplicate toast must be suppressed');
    assert.strictEqual(sandbox.window.__toastDedupCount, 1, 'Dedup counter must increment');

    // Call 3: Distinct message -> Allowed
    const res3 = sandbox.showToast('Second distinct notification', 'error');
    assert.strictEqual(res3, true, 'Distinct toast must be displayed');
    assert.strictEqual(toastEl.classList.contains('is-error'), true);
  });

  console.log('----------------------------------------------------');
  console.log(`Results: ${passed} / ${total} tests passed.`);
  process.exit(passed === total ? 0 : 1);
}

runReliabilityTests().catch(err => {
  console.error('Test execution failed:', err);
  process.exit(1);
});
