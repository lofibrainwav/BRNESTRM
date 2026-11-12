'use strict';
// BRNESTRM desktop shell: the console (http://127.0.0.1:8788) in a window, and in place of its
// browser pane a real browser view (WebContentsView). Sites that refuse iframes (Google, YouTube,
// ChatGPT) then load natively: full frame rate, sound, hover, drag, pop-up sign-in windows.
//
// The views use their own persistent login partition. Sign in once inside the app; nothing is
// copied from any other browser profile. Only the console page can drive the views (preload.cjs),
// and only while the console really comes from this user's gateway process (listenerIsMine).
const { app, BrowserWindow, WebContentsView, ipcMain, session, dialog } = require('electron');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const policy = require('./policy.cjs');

const CONSOLE_URL = process.env.BRNESTRM_CONSOLE_URL || 'http://127.0.0.1:8788/';
const CONSOLE_ORIGIN = policy.originOf(CONSOLE_URL);
const CONSOLE_PORT = CONSOLE_ORIGIN ? new URL(CONSOLE_ORIGIN).port || '80' : '';
const CONSOLE_PARTITION = 'persist:brnestrm-console';
const SITE_PARTITION = 'persist:brnestrm-browser';
const MAX_POPUPS = 3;

if (!CONSOLE_ORIGIN || !/^http:\/\/(127\.0\.0\.1|localhost)(:\d+)?$/.test(CONSOLE_ORIGIN)) {
  throw new Error(`BRNESTRM_CONSOLE_URL must be a loopback http URL, got ${CONSOLE_URL}`);
}
// A remote-debugging port would hand the signed-in views to any local process. Tests opt in.
const DEBUG_SWITCHES = ['remote-debugging-port', 'remote-debugging-pipe', 'inspect', 'inspect-brk'];
if (process.env.BRNESTRM_DESKTOP_ALLOW_DEBUG !== '1' && DEBUG_SWITCHES.some(s => app.commandLine.hasSwitch(s))) {
  console.error('BRNESTRM desktop refuses to start with a debugging switch.');
  app.exit(1);
}
// A separate profile folder (tests, or a second copy) gets its own instance lock and login store.
if (process.env.BRNESTRM_DESKTOP_PROFILE_DIR) app.setPath('userData', path.resolve(process.env.BRNESTRM_DESKTOP_PROFILE_DIR));
if (!app.requestSingleInstanceLock()) app.exit(0);

let win = null;
let consoleTrusted = false;  // the current console page came from a gateway owned by this user
const views = new Map();     // pane -> { view, requested, bounds, fullscreen }
let popups = 0;

const sitePrefs = () => ({ partition: SITE_PARTITION, sandbox: true, contextIsolation: true, nodeIntegration: false, webSecurity: true });

// Who listens on the console port? Only a process of this macOS user counts as the gateway, so
// another account that grabs 127.0.0.1:8788 while the gateway is down gets no console and no shell.
function listenerIsMine() {
  try {
    const out = execFileSync('/usr/sbin/lsof', ['-nP', `-iTCP:${CONSOLE_PORT}`, '-sTCP:LISTEN', '-Fu'], { encoding: 'utf8', timeout: 3000 });
    const uids = out.split('\n').filter(l => l.startsWith('u')).map(l => Number(l.slice(1)));
    return uids.length > 0 && uids.every(uid => uid === process.getuid());
  } catch { return false; }
}

function sendState(pane) {
  const v = views.get(pane);
  if (!v || !win || win.isDestroyed() || !consoleTrusted) return;
  if (policy.originOf(win.webContents.getURL()) !== CONSOLE_ORIGIN) return;
  const wc = v.view.webContents;
  if (wc.isDestroyed()) return;
  const history = wc.navigationHistory;
  win.webContents.send('shell:state', {
    pane,
    url: policy.publicPageUrl(wc.getURL()),
    title: wc.getTitle(),
    loading: wc.isLoading(),
    canGoBack: history.canGoBack(),
    canGoForward: history.canGoForward(),
  });
}

function hardenSiteContents(wc, pane) {
  const isPopup = !pane;
  wc.setWindowOpenHandler(({ url, disposition }) => {
    const decision = policy.popupDecision(url, disposition, CONSOLE_ORIGIN, isPopup);
    if (decision === 'window' && popups < MAX_POPUPS) {
      return { action: 'allow', overrideBrowserWindowOptions: { parent: win, width: 520, height: 720, webPreferences: sitePrefs() } };
    }
    if (decision !== 'deny') wc.loadURL(url).catch(() => {});
    return { action: 'deny' };
  });
  // A page may not navigate to file:, chrome:, the console or the bridge, and the like.
  wc.on('will-navigate', (event, url) => { if (!policy.siteNavigationAllowed(url, CONSOLE_ORIGIN)) event.preventDefault(); });
  wc.on('will-redirect', (event, url) => { if (!policy.siteNavigationAllowed(url, CONSOLE_ORIGIN)) event.preventDefault(); });
  wc.on('will-frame-navigate', event => { if (!event.isMainFrame && !policy.frameUrlAllowed(event.url)) event.preventDefault(); });
  if (isPopup) {
    // A pop-up always shows which site it is, so a look-alike sign-in window is recognisable.
    popups++;
    wc.on('destroyed', () => { popups--; });
    wc.on('page-title-updated', (event, title) => {
      event.preventDefault();
      const owner = BrowserWindow.fromWebContents(wc);
      let host = '';
      try { host = new URL(wc.getURL()).host; } catch {}
      if (owner) owner.setTitle(`${host} — ${title}`);
    });
  }
}

function viewFor(pane) {
  if (views.has(pane)) return views.get(pane);
  const view = new WebContentsView({ webPreferences: sitePrefs() });
  view.setBackgroundColor('#0a0d14');
  view.setVisible(false);
  win.contentView.addChildView(view);
  const entry = { view, requested: '', bounds: null, fullscreen: false };
  views.set(pane, entry);
  const wc = view.webContents;
  hardenSiteContents(wc, pane);
  for (const ev of ['did-navigate', 'did-navigate-in-page', 'page-title-updated', 'did-start-loading', 'did-stop-loading', 'did-fail-load']) {
    wc.on(ev, () => sendState(pane));
  }
  // YouTube's full-screen button: fill the window (Esc leaves), then go back to the pane.
  wc.on('enter-html-full-screen', () => {
    entry.fullscreen = true;
    const [w, h] = win.getContentSize();
    win.contentView.removeChildView(view); win.contentView.addChildView(view);
    view.setBounds({ x: 0, y: 0, width: w, height: h });
  });
  wc.on('leave-html-full-screen', () => {
    entry.fullscreen = false;
    if (entry.bounds) view.setBounds(entry.bounds);
  });
  return entry;
}

function scaledBounds(rect) {
  const zoom = win.webContents.getZoomFactor() || 1;
  const [w, h] = win.getContentSize();
  const scaled = rect && typeof rect === 'object'
    ? { x: rect.x * zoom, y: rect.y * zoom, width: rect.width * zoom, height: rect.height * zoom }
    : null;
  return policy.clampRect(scaled, w, h);
}

function place(entry, rect) {
  const bounds = scaledBounds(rect);
  if (!bounds) { entry.view.setVisible(false); return; }
  entry.bounds = bounds;
  if (!entry.fullscreen) entry.view.setBounds(bounds);
  entry.view.setVisible(true);
}

function hideAll() {
  for (const entry of views.values()) {
    entry.view.setVisible(false);
    entry.view.webContents.setAudioMuted(true);
  }
}

// Every message must come from the trusted console's own top frame, in this window.
function fromConsole(event) {
  return consoleTrusted && Boolean(win) && event.sender === win.webContents && event.senderFrame
    && policy.senderAllowed(event.senderFrame.url, CONSOLE_ORIGIN, event.senderFrame === win.webContents.mainFrame);
}

ipcMain.on('shell:show', (event, msg) => {
  if (!fromConsole(event) || !msg || !policy.paneAllowed(msg.pane) || !policy.siteUrlAllowed(msg.url, CONSOLE_ORIGIN)) return;
  const entry = viewFor(msg.pane);
  // Re-renders send the same address again; only a new address loads (links followed inside
  // the view are kept).
  if (msg.url !== entry.requested) {
    entry.requested = msg.url;
    entry.view.webContents.loadURL(msg.url).catch(() => {});
  }
  place(entry, msg.rect);
  entry.view.webContents.setAudioMuted(false);
  sendState(msg.pane);
});

ipcMain.on('shell:bounds', (event, msg) => {
  if (!fromConsole(event) || !msg || !policy.paneAllowed(msg.pane) || !views.has(msg.pane)) return;
  place(views.get(msg.pane), msg.rect);
});

ipcMain.on('shell:hide', (event, msg) => {
  if (!fromConsole(event) || !msg || !policy.paneAllowed(msg.pane) || !views.has(msg.pane)) return;
  const entry = views.get(msg.pane);
  entry.view.setVisible(false);
  // A hidden pane must not keep playing sound.
  entry.view.webContents.setAudioMuted(true);
});

ipcMain.on('shell:nav', (event, msg) => {
  if (!fromConsole(event) || !msg || !policy.paneAllowed(msg.pane) || !views.has(msg.pane)) return;
  const wc = views.get(msg.pane).view.webContents;
  if (msg.action === 'back' && wc.navigationHistory.canGoBack()) wc.navigationHistory.goBack();
  else if (msg.action === 'forward' && wc.navigationHistory.canGoForward()) wc.navigationHistory.goForward();
  else if (msg.action === 'reload') wc.reload();
});

function createWindow() {
  win = new BrowserWindow({
    width: 1600, height: 1000, minWidth: 900, minHeight: 600,
    title: 'BRNESTRM', backgroundColor: '#0a0d14',
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      partition: CONSOLE_PARTITION, sandbox: true, contextIsolation: true, nodeIntegration: false, webSecurity: true,
    },
  });
  // The window only ever shows the console: client navigations and server redirects alike.
  const stayOnConsole = (event, url) => { if (policy.originOf(url) !== CONSOLE_ORIGIN) event.preventDefault(); };
  win.webContents.on('will-navigate', stayOnConsole);
  win.webContents.on('will-redirect', stayOnConsole);
  win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  win.webContents.on('did-start-navigation', details => {
    // A console (re)load drops its pane state: hide and mute every view until the console asks again.
    if (details.isMainFrame && !details.isSameDocument) hideAll();
  });
  win.on('closed', () => { win = null; views.clear(); });
  win.loadURL(CONSOLE_URL);
}

app.on('web-contents-created', (_event, wc) => {
  // No <webview> tags anywhere; pop-up windows follow the same rules as the pane views.
  wc.on('will-attach-webview', event => event.preventDefault());
  if (wc.getType() === 'window' && win && wc !== win.webContents) hardenSiteContents(wc, null);
});

app.whenReady().then(() => {
  for (const part of [SITE_PARTITION, CONSOLE_PARTITION]) {
    const s = session.fromPartition(part);
    s.setPermissionRequestHandler((_wc, permission, callback) => callback(part === SITE_PARTITION && policy.permissionAllowed(permission)));
    s.setPermissionCheckHandler((_wc, permission) => part === SITE_PARTITION && policy.permissionAllowed(permission));
    s.setDevicePermissionHandler(() => false);
    s.setDisplayMediaRequestHandler((_req, callback) => callback({}));
  }
  // Every console page load is checked: the listener on the console port must be this user's.
  session.fromPartition(CONSOLE_PARTITION).webRequest.onBeforeRequest((details, callback) => {
    if (details.resourceType !== 'mainFrame') return callback({});
    consoleTrusted = policy.originOf(details.url) === CONSOLE_ORIGIN && listenerIsMine();
    if (!consoleTrusted) hideAll();
    callback({ cancel: !consoleTrusted });
  });
  // Downloads ask where to save, one at a time; a page cannot pile up save dialogs.
  let downloading = false;
  session.fromPartition(SITE_PARTITION).on('will-download', (_event, item) => {
    if (downloading) { item.cancel(); return; }
    downloading = true;
    item.once('done', () => { downloading = false; });
  });
  createWindow();
  win.webContents.on('did-fail-load', (_e, _code, _desc, url, isMainFrame) => {
    if (isMainFrame && !consoleTrusted) {
      dialog.showMessageBox(win, { type: 'warning', message: 'BRNESTRM gateway not found',
        detail: `Nothing owned by this user is serving ${CONSOLE_ORIGIN}. Start the gateway, then reload (Cmd+R).` }).catch(() => {});
    }
  });
});

app.on('second-instance', () => { if (win) { if (win.isMinimized()) win.restore(); win.focus(); } });
app.on('window-all-closed', () => app.quit());
