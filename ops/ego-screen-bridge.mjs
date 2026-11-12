// BRNESTRM console "screen" bridge for Ego Lite.
//
// Start with ops/run-ego-screen-bridge.sh (it runs this file inside Ego Lite's embedded runtime).
// Ego's runtime does not pass environment variables through, so the launcher prepends
// `globalThis.__BRNESTRM_BRIDGE__ = {...}`; process.env is only a fallback for tests.
// The server lives in Ego's shared Node helper and outlives the CLI; POST /shutdown stops it.
// It installs no signal handlers and never calls process.exit: that process belongs to Ego.
//
// It keeps one Ego task space (default profile "Profile 1") with one Page per console browser
// session, and serves that Page's screen and input on 127.0.0.1 only. Every request must carry
// the shared secret from BRNESTRM_EGO_BRIDGE_SECRET_FILE (created 0600 on first start). Requests
// from browsers (any Origin header) are refused: only the BRNESTRM gateway talks to the bridge.
// It never reads cookies, storage or saved credentials, and never sends anything off this Mac.
const http = await import('node:http');
const fs = await import('node:fs');
const path = await import('node:path');
const os = await import('node:os');
const crypto = await import('node:crypto');

const CFG = globalThis.__BRNESTRM_BRIDGE__ || {};
const PORT = Number(CFG.port || process.env.BRNESTRM_EGO_BRIDGE_PORT || 8794);
const SECRET_FILE = CFG.secretFile || process.env.BRNESTRM_EGO_BRIDGE_SECRET_FILE || path.join(os.homedir(), '.brnestrm', 'ego-bridge.secret');
const PROFILE = CFG.profile || process.env.BRNESTRM_EGO_PROFILE || 'Profile 1';
const SPACE = CFG.space || process.env.BRNESTRM_EGO_SPACE || 'BRNESTRM console screen';
const MAX_PAGES = 8;
const MAX_BODY = 16 * 1024;
const SESSION_RE = /^sess-[A-Za-z0-9_-]{1,80}$/;
const KEY_RE = /^((Control|Meta|Alt|Shift|ControlOrMeta)\+){0,3}([A-Za-z0-9]|Enter|Backspace|Tab|Escape|Delete|ArrowUp|ArrowDown|ArrowLeft|ArrowRight|Home|End|PageUp|PageDown|Space)$/;
// With Control/Meta, only editing shortcuts (select all, copy, cut, paste, undo, redo, find, bold,
// italic, underline). Browser/app chords such as Meta+q, Meta+w, Meta+n, Meta+s, Meta+p are refused.
const CHORD_LETTERS = new Set(['a', 'c', 'v', 'x', 'z', 'y', 'f', 'b', 'i', 'u']);
const OP_TIMEOUT = Number(CFG.opTimeoutMs) || 10000;         // a page that hangs (busy loop, dialog) must not hold its session forever
const MAX_W = 3840, MAX_H = 2400;

function loadSecret() {
  if (!fs.existsSync(SECRET_FILE)) {
    fs.mkdirSync(path.dirname(SECRET_FILE), { recursive: true, mode: 0o700 });
    fs.writeFileSync(SECRET_FILE, crypto.randomBytes(32).toString('hex'), { mode: 0o600, flag: 'wx' });
  }
  // Check and read the same open file, so it cannot be swapped between the check and the read.
  const fd = fs.openSync(SECRET_FILE, fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0));
  let value;
  try {
    const st = fs.fstatSync(fd);
    if (!st.isFile() || (st.mode & 0o077) !== 0) throw new Error(`secret file must be a private regular file: ${SECRET_FILE}`);
    if (typeof process.getuid === 'function' && st.uid !== process.getuid()) throw new Error(`secret file must be owned by this user: ${SECRET_FILE}`);
    value = fs.readFileSync(fd, 'utf8').trim();
  } finally { fs.closeSync(fd); }
  if (value.length < 32) throw new Error('secret too short');
  return Buffer.from(value);
}
const SECRET = loadSecret();
const shotDir = fs.mkdtempSync(path.join(os.tmpdir(), 'brnestrm-ego-'));

let task;
try {
  task = await taskSpace(SPACE, { profileId: PROFILE });
} catch (e) {
  // The space survives restarts; it was created with PROFILE, so reattach without the option.
  if (!/already exists/.test(String(e && e.message))) throw e;
  task = await taskSpace(SPACE);
}
const pages = new Map();          // session id -> Page (insertion order = least recently used first)
const queues = new Map();         // session id -> promise chain, so one Page never runs two commands at once
const sizes = new Map();          // session id -> {width, height} of the console pane, applied to its Page

function fitViewport(p, size) {
  // Lay the page out at the pane's size so the frame fills the pane without letterboxing.
  return p.cdp('Emulation.setDeviceMetricsOverride', { width: size.width, height: size.height, deviceScaleFactor: 1, mobile: false });
}

function withTimeout(promise, ms) {
  let timer;
  return Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error('page_busy')), ms); })])
    .finally(() => clearTimeout(timer));
}

// One command at a time per Page. A command that overruns OP_TIMEOUT fails with page_busy and
// the queue moves on; a JavaScript dialog (alert/confirm/beforeunload) is dismissed.
function serial(session, fn, ms = OP_TIMEOUT) {
  const prev = queues.get(session) || Promise.resolve();
  const next = prev.catch(() => {}).then(() => withTimeout(Promise.resolve().then(fn), ms));
  next.catch(e => {
    const p = pages.get(session);
    if (e && e.message === 'page_busy' && p) p.cdp('Page.handleJavaScriptDialog', { accept: false }).catch(() => {});
  });
  queues.set(session, next.catch(() => {}));
  return next;
}

const creating = new Map();       // session id -> pending Page, so two requests never open two tabs

async function pageFor(session, create) {
  if (pages.has(session)) {
    const p = pages.get(session);
    pages.delete(session); pages.set(session, p);
    return p;
  }
  if (creating.has(session)) return creating.get(session);
  if (!create) return null;
  const pending = (async () => {
    while (pages.size >= MAX_PAGES) {
      const [oldest, old] = pages.entries().next().value;
      pages.delete(oldest);
      await old.close().catch(() => {});
    }
    const p = await task.newPage();
    pages.set(session, p);
    if (sizes.has(session)) await fitViewport(p, sizes.get(session)).catch(() => {});
    return p;
  })();
  creating.set(session, pending);
  try { return await pending; } finally { creating.delete(session); }
}

async function closePage(session) {
  const p = pages.get(session);
  pages.delete(session); sizes.delete(session); queues.delete(session);
  if (p) await p.close().catch(() => {});
  return Boolean(p);
}

async function stepHistory(p, delta) {
  // From the browser side, so the page's own script cannot override history.back().
  const h = await p.cdp('Page.getNavigationHistory');
  const entry = h && Array.isArray(h.entries) ? h.entries[h.currentIndex + delta] : null;
  if (entry) await p.cdp('Page.navigateToHistoryEntry', { entryId: entry.id });
}

function chordAllowed(key) {
  const parts = key.split('+');
  const base = parts[parts.length - 1];
  const mods = parts.slice(0, -1);
  if (!mods.some(m => m === 'Control' || m === 'Meta' || m === 'ControlOrMeta')) return true;
  return base.length > 1 || CHORD_LETTERS.has(base.toLowerCase());
}

const headerText = s => {
  const v = String(s || '');
  try { return encodeURIComponent(typeof v.toWellFormed === 'function' ? v.toWellFormed() : v); } catch { return ''; }
};

function authorized(req) {
  if (req.headers.origin !== undefined) return false;
  const host = req.headers.host || '';
  if (host !== `127.0.0.1:${PORT}` && host !== `localhost:${PORT}`) return false;
  const got = Buffer.from(String(req.headers['x-bridge-secret'] || ''));
  return got.length === SECRET.length && crypto.timingSafeEqual(got, SECRET);
}

function readJson(req) {
  return new Promise((resolve, reject) => {
    let size = 0; const chunks = [];
    req.on('data', c => { size += c.length; if (size > MAX_BODY) { reject(new Error('too_large')); req.destroy(); } else chunks.push(c); });
    req.on('end', () => { try { resolve(JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}')); } catch { reject(new Error('bad_json')); } });
    req.on('error', reject);
  });
}

const num = (v, lo, hi) => typeof v === 'number' && Number.isFinite(v) && v >= lo && v <= hi;

function validateInput(b) {
  if (!b || typeof b !== 'object' || !SESSION_RE.test(String(b.session || ''))) return 'bad_session';
  switch (b.type) {
    case 'click': return num(b.x, 0, 10000) && num(b.y, 0, 10000) && (b.button === undefined || ['left', 'middle', 'right'].includes(b.button)) ? null : 'bad_click';
    case 'wheel': return num(b.x, 0, 10000) && num(b.y, 0, 10000) && num(b.dx, -5000, 5000) && num(b.dy, -5000, 5000) ? null : 'bad_wheel';
    case 'type': return typeof b.text === 'string' && b.text.length > 0 && b.text.length <= 2000 ? null : 'bad_text';
    case 'key': return typeof b.key === 'string' && KEY_RE.test(b.key) && chordAllowed(b.key) ? null : 'bad_key';
    case 'viewport': return Number.isInteger(b.width) && Number.isInteger(b.height) && num(b.width, 320, MAX_W) && num(b.height, 240, MAX_H) ? null : 'bad_viewport';
    case 'back': case 'forward': case 'reload': return null;
    default: return 'bad_type';
  }
}

async function info(p) {
  const i = await p.info();
  return { url: i.url, title: i.title, width: i.w, height: i.h };
}

function send(res, code, obj) {
  const body = Buffer.from(JSON.stringify(obj));
  res.writeHead(code, { 'Content-Type': 'application/json', 'Content-Length': body.length, 'Cache-Control': 'no-store' });
  res.end(body);
}

let shotSeq = 0;
const server = http.createServer(async (req, res) => {
  try {
    if (!authorized(req)) return send(res, 403, { ok: false, error: 'forbidden' });
    const url = new URL(req.url, `http://127.0.0.1:${PORT}`);
    if (req.method === 'POST' && url.pathname === '/shutdown') {
      send(res, 200, { ok: true });
      setTimeout(shutdown, 50);
      return;
    }
    if (req.method === 'GET' && url.pathname === '/health') {
      return send(res, 200, { ok: true, profile: PROFILE, pages: pages.size });
    }
    if (req.method === 'GET' && url.pathname === '/frame') {
      const session = url.searchParams.get('session') || '';
      if (!SESSION_RE.test(session)) return send(res, 400, { ok: false, error: 'bad_session' });
      const p = await pageFor(session, false);
      if (!p) return send(res, 404, { ok: false, error: 'no_page' });
      const file = path.join(shotDir, `f${++shotSeq}.png`);
      const [png, meta] = await serial(session, async () => {
        await p.screenshot({ path: file });
        const data = fs.readFileSync(file); fs.unlinkSync(file);
        return [data, await info(p)];
      });
      res.writeHead(200, { 'Content-Type': 'image/png', 'Content-Length': png.length, 'Cache-Control': 'no-store',
        'X-Page-Width': String(meta.width), 'X-Page-Height': String(meta.height),
        'X-Page-Url': headerText(meta.url), 'X-Page-Title': headerText(meta.title) });
      return res.end(png);
    }
    if (req.method === 'POST' && url.pathname === '/goto') {
      const b = await readJson(req);
      if (!SESSION_RE.test(String(b.session || ''))) return send(res, 400, { ok: false, error: 'bad_session' });
      if (typeof b.url !== 'string' || !/^https?:\/\//i.test(b.url) || b.url.length > 2048) return send(res, 400, { ok: false, error: 'bad_url' });
      const p = await pageFor(b.session, true);
      const meta = await serial(b.session, async () => {
        try { await p.goto(b.url, { timeout: 15000, waitUntil: 'domcontentloaded' }); } catch (_) { /* slow SPA: keep what committed */ }
        return info(p);
      }, 20000);
      return send(res, 200, { ok: true, ...meta });
    }
    if (req.method === 'POST' && url.pathname === '/close') {
      const b = await readJson(req);
      if (!SESSION_RE.test(String(b.session || ''))) return send(res, 400, { ok: false, error: 'bad_session' });
      return send(res, 200, { ok: true, closed: await closePage(b.session) });
    }
    if (req.method === 'POST' && url.pathname === '/input') {
      const b = await readJson(req);
      const problem = validateInput(b);
      if (problem) return send(res, 400, { ok: false, error: problem });
      if (b.type === 'viewport') {
        sizes.set(b.session, { width: b.width, height: b.height });
        while (sizes.size > 64) sizes.delete(sizes.keys().next().value);
      }
      const p = await pageFor(b.session, false);
      if (!p && b.type === 'viewport') return send(res, 200, { ok: true, pending: true });
      if (!p) return send(res, 404, { ok: false, error: 'no_page' });
      await serial(b.session, async () => {
        if (b.type === 'viewport') return fitViewport(p, sizes.get(b.session));
        if (b.type === 'click') await p.mouse.click(b.x, b.y, { button: b.button || 'left' });
        // One wheel event, not page.mouse.wheel(): that animates a gesture and can take seconds,
        // holding up this session's frames while it runs.
        else if (b.type === 'wheel') await p.cdp('Input.dispatchMouseEvent', { type: 'mouseWheel', x: b.x, y: b.y, deltaX: b.dx, deltaY: b.dy });
        else if (b.type === 'type') await p.keyboard.insertText(b.text);
        else if (b.type === 'key') await p.keyboard.press(b.key);
        else if (b.type === 'back') await stepHistory(p, -1);
        else if (b.type === 'forward') await stepHistory(p, 1);
        else if (b.type === 'reload') await p.cdp('Page.reload', {});
      });
      return send(res, 200, { ok: true });
    }
    return send(res, 404, { ok: false, error: 'not_found' });
  } catch (e) {
    const msg = e && e.message;
    if (msg === 'page_busy') return send(res, 504, { ok: false, error: 'page_busy' });
    const known = ['too_large', 'bad_json'].includes(msg);
    return send(res, known ? 400 : 500, { ok: false, error: known ? msg : 'bridge_error' });
  }
});

await new Promise((resolve, reject) => { server.once('error', reject); server.listen(PORT, '127.0.0.1', resolve); });
console.log(`BRNESTRM_EGO_BRIDGE_LISTEN=127.0.0.1:${PORT} profile=${PROFILE} space=${task.spaceId}`);

async function shutdown() {
  server.close();
  server.closeAllConnections?.();
  try { await task.finish({ keep: [] }); } catch (_) {}
  fs.rmSync(shotDir, { recursive: true, force: true });
}
