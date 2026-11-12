'use strict';
// Pure rules for the desktop shell, kept apart from Electron so they can be tested with plain Node.

const PANES = new Set(['p1', 'p2']);
// Local BRNESTRM services that a site view must never open, under any loopback spelling.
const PROTECTED_PORTS = new Set(['8788', '8794']);
const LOOPBACK_HOSTS = new Set(['localhost', '[::1]', '[::]', '0.0.0.0']);
// Sign-in providers whose window.open() gets a real pop-up window; everything else stays in the pane.
const SIGN_IN_HOSTS = new Set(['accounts.google.com', 'accounts.youtube.com', 'appleid.apple.com', 'auth.openai.com',
  'login.microsoftonline.com', 'login.live.com', 'github.com', 'www.facebook.com', 'discord.com']);
// Frame schemes that are never allowed, even inside a page (about:, data:, blob: frames are normal).
const BLOCKED_FRAME_SCHEMES = new Set(['file:', 'chrome:', 'chrome-extension:', 'devtools:', 'javascript:', 'view-source:']);
const SECRET_PARAM = /(token|code|secret|session|auth|key|password|sig)/i;
// Site views may ask for these and nothing else (no camera, microphone, location, notifications…).
const ALLOWED_PERMISSIONS = new Set(['fullscreen', 'clipboard-sanitized-write']);

function originOf(url) {
  try { return new URL(url).origin; } catch { return null; }
}

// A page the browser pane may open: http(s), at most 2048 characters, never the console itself.
function siteUrlAllowed(url, consoleOrigin) {
  if (typeof url !== 'string' || url.length > 2048) return false;
  let u;
  try { u = new URL(url); } catch { return false; }
  if (u.protocol !== 'https:' && u.protocol !== 'http:') return false;
  if (u.username || u.password) return false;
  return u.origin !== consoleOrigin && !isProtectedLocal(u);
}

function isLoopbackHost(hostname) {
  return LOOPBACK_HOSTS.has(hostname) || /^127\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(hostname);
}

function isProtectedLocal(u) {
  const port = u.port || (u.protocol === 'https:' ? '443' : '80');
  return isLoopbackHost(u.hostname) && PROTECTED_PORTS.has(port);
}

// Sub-frames: same rules for the protected local services, plus a short list of schemes.
function frameUrlAllowed(url) {
  let u;
  try { u = new URL(url); } catch { return false; }
  if (BLOCKED_FRAME_SCHEMES.has(u.protocol)) return false;
  return !((u.protocol === 'http:' || u.protocol === 'https:') && isProtectedLocal(u));
}

// What the console is told about a view's page: no fragment, and no query when it carries
// anything that looks like a token or code (OAuth redirects, magic links).
function publicPageUrl(url) {
  try {
    const u = new URL(url);
    u.hash = '';
    if ([...u.searchParams.keys()].some(k => SECRET_PARAM.test(k))) u.search = '';
    return u.toString();
  } catch { return ''; }
}

// Only the console page (its own origin, top frame) may drive the shell.
function senderAllowed(frameUrl, consoleOrigin, isMainFrame) {
  return Boolean(isMainFrame) && originOf(frameUrl) === consoleOrigin;
}

function paneAllowed(pane) {
  return typeof pane === 'string' && PANES.has(pane);
}

// Integer bounds inside the window; anything malformed or empty means "hide".
function clampRect(rect, winW, winH) {
  if (!rect || typeof rect !== 'object') return null;
  const n = v => (typeof v === 'number' && Number.isFinite(v) ? Math.round(v) : NaN);
  let x = n(rect.x), y = n(rect.y), w = n(rect.width), h = n(rect.height);
  if ([x, y, w, h].some(Number.isNaN)) return null;
  x = Math.max(0, Math.min(x, winW)); y = Math.max(0, Math.min(y, winH));
  w = Math.max(0, Math.min(w, winW - x)); h = Math.max(0, Math.min(h, winH - y));
  return w >= 40 && h >= 40 ? { x, y, width: w, height: h } : null;
}

// window.open() with features (sign-in popups) gets a real child window in the same login
// partition; links that would open a tab stay in the pane; anything not http(s) is refused.
function popupDecision(url, disposition, consoleOrigin, fromPopup = false) {
  if (!siteUrlAllowed(url, consoleOrigin)) return 'deny';
  if (fromPopup) return 'same-view';           // a pop-up never opens further windows
  const host = new URL(url).hostname;
  return disposition === 'new-window' && SIGN_IN_HOSTS.has(host) ? 'window' : 'same-view';
}

function permissionAllowed(permission) {
  return ALLOWED_PERMISSIONS.has(permission);
}

module.exports = { siteUrlAllowed, frameUrlAllowed, publicPageUrl, senderAllowed, paneAllowed, clampRect, popupDecision, permissionAllowed, originOf, isLoopbackHost };
