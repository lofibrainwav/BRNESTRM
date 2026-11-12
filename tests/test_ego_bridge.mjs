// Contract for ops/ego-screen-bridge.mjs with a fake Ego API (plain Node, loopback only).
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import net from 'node:net';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const results = [];
const test = (name, ok) => results.push({ test: name, result: ok ? 'PASS' : 'FAIL' });
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==', 'base64');

const port = await new Promise(r => { const s = net.createServer(); s.listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => r(p)); }); });
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'ego-bridge-test-'));
// Config arrives the way ops/run-ego-screen-bridge.sh sends it (Ego's runtime has no env);
// the env values below are decoys that must lose to the injected config.
process.env.BRNESTRM_EGO_BRIDGE_PORT = '1';
process.env.BRNESTRM_EGO_BRIDGE_SECRET_FILE = path.join(tmp, 'decoy-secret');
globalThis.__BRNESTRM_BRIDGE__ = { port, secretFile: path.join(tmp, 'secret'), profile: 'Profile 1', space: 'test space', opTimeoutMs: 400 };

const calls = [];
let closed = 0;
let newPages = 0;
const hang = new Set();          // page numbers whose screenshot never finishes
let titleFor = n => 'T' + n;
function fakePage(n) {
  let url = 'about:blank';
  newPages++;
  return {
    async goto(u) { calls.push(['goto', n, u]); url = u; },
    async info() { return { url, title: titleFor(n), w: 1280, h: 720 }; },
    async screenshot({ path: p }) { if (hang.has(n)) await new Promise(() => {}); fs.writeFileSync(p, PNG); return p; },
    async close() { closed++; },
    async evaluate(s) { calls.push(['eval', n, s]); },
    async cdp(m, a) {
      if (m === 'Page.getNavigationHistory') return { currentIndex: 1, entries: [{ id: 11, url: 'https://a.example/' }, { id: 12, url }] };
      if (m === 'Page.navigateToHistoryEntry') { calls.push(['history', n, a.entryId]); return {}; }
      if (m === 'Page.handleJavaScriptDialog') { calls.push(['dialog', n, a.accept]); return {}; }
      calls.push(m === 'Input.dispatchMouseEvent' ? ['wheel', n, a.type, a.deltaY, a.x] : ['cdp', n, m, a && a.width, a && a.height]);
    },
    mouse: { async click(x, y, o) { calls.push(['click', n, x, y, o.button]); }, async move(x, y) { calls.push(['move', n, x, y]); }, async wheel(dx, dy) { calls.push(['wheel', n, dx, dy]); } },
    keyboard: { async insertText(t) { calls.push(['text', n, t]); }, async press(k) { calls.push(['key', n, k]); } },
  };
}
let pageNo = 0;
// Like Ego: the space already exists from an earlier run, so profileId is refused and the bridge reattaches.
globalThis.taskSpace = async (name, opts) => {
  calls.push(['space', name, opts && opts.profileId]);
  if (opts && opts.profileId) throw new Error(`taskSpace profileId only applies when creating a new task space; "${name}" already exists`);
  return { spaceId: 'fake', async newPage() { return fakePage(++pageNo); }, async finish() { calls.push(['finish']); } };
};
let exited = null;
process.exit = code => { exited = code; };
const signalHandlersBefore = process.listenerCount('SIGTERM');

const src = fs.readFileSync(path.join(ROOT, 'ops/ego-screen-bridge.mjs'), 'utf8');
const tmpModule = path.join(tmp, 'bridge.mjs');
fs.writeFileSync(tmpModule, src);
await import(tmpModule);
const secret = fs.readFileSync(path.join(tmp, 'secret'), 'utf8').trim();

function call(method, p, { body, headers = {}, auth = true } = {}) {
  return new Promise(resolve => {
    const h = { host: `127.0.0.1:${port}`, ...(auth ? { 'x-bridge-secret': secret } : {}), ...headers };
    const data = body === undefined ? null : Buffer.from(typeof body === 'string' ? body : JSON.stringify(body));
    if (data) { h['content-type'] = 'application/json'; h['content-length'] = data.length; }
    const req = http.request({ host: '127.0.0.1', port, method, path: p, headers: h }, res => {
      const chunks = []; res.on('data', c => chunks.push(c)); res.on('end', () => resolve({ code: res.statusCode, headers: res.headers, body: Buffer.concat(chunks) }));
    });
    req.on('error', e => resolve({ code: 0, error: e.message }));
    if (data) req.write(data); req.end();
  });
}
const j = r => { try { return JSON.parse(r.body.toString()); } catch { return {}; } };

test('the secret file is created private (0600)', (fs.statSync(path.join(tmp, 'secret')).mode & 0o777) === 0o600 && secret.length >= 64);
test('injected config wins over env (port, secret file)', !fs.existsSync(path.join(tmp, 'decoy-secret')));
test('the task space asks for the configured profile first', calls[0] && calls[0][0] === 'space' && calls[0][1] === 'test space' && calls[0][2] === 'Profile 1');
test('an existing task space is reattached without profileId', calls[1] && calls[1][0] === 'space' && calls[1][2] === undefined);
test('requests without the secret are refused', (await call('GET', '/health', { auth: false })).code === 403);
test('a wrong secret is refused', (await call('GET', '/health', { auth: false, headers: { 'x-bridge-secret': 'x'.repeat(64) } })).code === 403);
test('browser requests (any Origin) are refused', (await call('GET', '/health', { headers: { origin: 'http://127.0.0.1:8788' } })).code === 403);
test('a foreign Host header is refused (DNS rebinding)', (await call('GET', '/health', { headers: { host: `evil.example:${port}` } })).code === 403);
test('health answers with the secret', (await call('GET', '/health')).code === 200);
test('frame for a session without a page is 404', (await call('GET', '/frame?session=sess-1')).code === 404);
test('goto refuses non-http URLs', (await call('POST', '/goto', { body: { session: 'sess-1', url: 'javascript:alert(1)' } })).code === 400 && (await call('POST', '/goto', { body: { session: 'sess-1', url: 'file:///etc/passwd' } })).code === 400);
test('goto refuses malformed sessions', (await call('POST', '/goto', { body: { session: '../x', url: 'https://example.org' } })).code === 400);
const g = await call('POST', '/goto', { body: { session: 'sess-1', url: 'https://example.org/' } });
test('goto opens the page and reports its size', g.code === 200 && j(g).width === 1280 && calls.some(c => c[0] === 'goto' && c[2] === 'https://example.org/'));
const f = await call('GET', '/frame?session=sess-1');
test('frame returns a PNG with the page size', f.code === 200 && f.headers['content-type'] === 'image/png' && f.body.subarray(1, 4).toString() === 'PNG' && f.headers['x-page-width'] === '1280' && f.headers['cache-control'] === 'no-store');
test('click is forwarded in CSS pixels', (await call('POST', '/input', { body: { session: 'sess-1', type: 'click', x: 10, y: 20 } })).code === 200 && calls.some(c => c[0] === 'click' && c[2] === 10 && c[3] === 20 && c[4] === 'left'));
test('wheel is one wheel event at the pointer', (await call('POST', '/input', { body: { session: 'sess-1', type: 'wheel', x: 5, y: 5, dx: 0, dy: 300 } })).code === 200 && calls.some(c => c[0] === 'wheel' && c[2] === 'mouseWheel' && c[3] === 300 && c[4] === 5));
test('typed text is inserted', (await call('POST', '/input', { body: { session: 'sess-1', type: 'type', text: '안녕 hello' } })).code === 200 && calls.some(c => c[0] === 'text' && c[2] === '안녕 hello'));
test('allowed keys and chords are pressed', (await call('POST', '/input', { body: { session: 'sess-1', type: 'key', key: 'Enter' } })).code === 200 && (await call('POST', '/input', { body: { session: 'sess-1', type: 'key', key: 'ControlOrMeta+a' } })).code === 200);
for (const [label, body] of [
  ['unknown input type', { session: 'sess-1', type: 'drag' }],
  ['off-range click', { session: 'sess-1', type: 'click', x: -1, y: 5 }],
  ['non-numeric click', { session: 'sess-1', type: 'click', x: '10', y: 5 }],
  ['overlong text', { session: 'sess-1', type: 'type', text: 'x'.repeat(2001) }],
  ['key injection', { session: 'sess-1', type: 'key', key: 'Enter; rm -rf' }],
  ['unknown key', { session: 'sess-1', type: 'key', key: 'F13' }],
]) test(`invalid input is refused: ${label}`, (await call('POST', '/input', { body })).code === 400);
test('input for a session without a page is 404', (await call('POST', '/input', { body: { session: 'sess-none', type: 'key', key: 'Enter' } })).code === 404);
test('a viewport for a session without a page is kept for later', (await call('POST', '/input', { body: { session: 'sess-vp', type: 'viewport', width: 900, height: 700 } })).code === 200);
await call('POST', '/goto', { body: { session: 'sess-vp', url: 'https://example.org/' } });
test('a new page is laid out at the kept pane size', calls.some(c => c[0] === 'cdp' && c[2] === 'Emulation.setDeviceMetricsOverride' && c[3] === 900 && c[4] === 700));
test('viewport resizes an open page', (await call('POST', '/input', { body: { session: 'sess-1', type: 'viewport', width: 1100, height: 800 } })).code === 200 && calls.some(c => c[0] === 'cdp' && c[3] === 1100 && c[4] === 800));
for (const [label, body] of [['tiny', { width: 10, height: 700 }], ['fractional', { width: 900.5, height: 700 }], ['huge', { width: 9000, height: 700 }]])
  test(`invalid viewport is refused: ${label}`, (await call('POST', '/input', { body: { session: 'sess-1', type: 'viewport', ...body } })).code === 400);
test('back steps browser history from outside the page', (await call('POST', '/input', { body: { session: 'sess-1', type: 'back' } })).code === 200 && calls.some(c => c[0] === 'history' && c[2] === 11) && !calls.some(c => c[0] === 'eval'));
test('reload reloads from the browser side', (await call('POST', '/input', { body: { session: 'sess-1', type: 'reload' } })).code === 200 && calls.some(c => c[0] === 'cdp' && c[2] === 'Page.reload'));
test('forward at the newest entry does nothing', (await call('POST', '/input', { body: { session: 'sess-1', type: 'forward' } })).code === 200 && calls.filter(c => c[0] === 'history').length === 1);
test('editing chords are allowed', (await call('POST', '/input', { body: { session: 'sess-1', type: 'key', key: 'Meta+c' } })).code === 200 && (await call('POST', '/input', { body: { session: 'sess-1', type: 'key', key: 'ControlOrMeta+Shift+z' } })).code === 200);
for (const key of ['Meta+q', 'Meta+w', 'Control+n', 'ControlOrMeta+t', 'Meta+s', 'Meta+p'])
  test(`browser/app chord is refused: ${key}`, (await call('POST', '/input', { body: { session: 'sess-1', type: 'key', key } })).code === 400);
titleFor = () => 'bad \uD800 title';
const odd = await call('GET', '/frame?session=sess-1');
test('a lone surrogate in the title does not break frames', odd.code === 200 && typeof odd.headers['x-page-title'] === 'string');
titleFor = n => 'T' + n;
const before = newPages;
await Promise.all([call('POST', '/goto', { body: { session: 'sess-race', url: 'https://example.org/' } }), call('POST', '/goto', { body: { session: 'sess-race', url: 'https://example.org/' } })]);
test('two requests for a new session open one tab', newPages - before === 1);
hang.add(newPages);
const busy = await call('GET', '/frame?session=sess-race');
test('a hung page answers 504 page_busy and its dialog is dismissed', busy.code === 504 && j(busy).error === 'page_busy' && calls.some(c => c[0] === 'dialog' && c[2] === false));
hang.clear();
test('the session is usable again after a hang', (await call('POST', '/input', { body: { session: 'sess-race', type: 'key', key: 'Enter' } })).code === 200);
test('close shuts the session tab', j(await call('POST', '/close', { body: { session: 'sess-race' } })).closed === true && (await call('GET', '/frame?session=sess-race')).code === 404);
test('oversized bodies are refused', (await call('POST', '/goto', { body: 'x'.repeat(20000) })).code === 400 || (await call('POST', '/goto', { body: 'x'.repeat(20000) })).code === 0);
for (let i = 0; i < 10; i++) await call('POST', '/goto', { body: { session: `sess-cap-${i}`, url: 'https://example.org/' } });
test('at most 8 pages are kept; the least recently used is closed', j(await call('GET', '/health')).pages === 8 && closed >= 3);

test('shutdown without the secret is refused', (await call('POST', '/shutdown', { auth: false })).code === 403);
test('shutdown answers and finishes the task space without exiting Ego\'s process', (await call('POST', '/shutdown')).code === 200 && await new Promise(r => setTimeout(() => r(exited === null && calls.some(c => c[0] === 'finish')), 300)));
test('no signal handlers are installed in Ego\'s process', process.listenerCount('SIGTERM') === signalHandlersBefore);
test('after shutdown the port is closed', (await call('GET', '/health')).code === 0);

const failed = results.filter(r => r.result === 'FAIL');
console.log(JSON.stringify({ total: results.length, passed: results.length - failed.length, failed: failed.length, results, scope: 'bridge with a fake Ego API on loopback' }));
fs.rmSync(tmp, { recursive: true, force: true });
process.exitCode = failed.length ? 1 : 0;
