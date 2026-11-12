'use strict';
// Rules of the desktop shell (desktop/policy.cjs), plain Node, no Electron.
const path = require('node:path');
const p = require(path.join(__dirname, '..', 'desktop', 'policy.cjs'));
const results = [];
const test = (name, ok) => results.push({ test: name, result: ok ? 'PASS' : 'FAIL' });
const CONSOLE = 'http://127.0.0.1:8788';

test('web pages are allowed', p.siteUrlAllowed('https://www.youtube.com/watch?v=x', CONSOLE) && p.siteUrlAllowed('http://example.org/', CONSOLE));
test('the console itself is refused (no console inside its own pane)', !p.siteUrlAllowed('http://127.0.0.1:8788/api/vault', CONSOLE));
test('non-web schemes are refused', ['file:///etc/passwd', 'javascript:alert(1)', 'chrome://settings', 'data:text/html,x', 'about:blank'].every(u => !p.siteUrlAllowed(u, CONSOLE)));
test('credentials in URLs and overlong URLs are refused', !p.siteUrlAllowed('https://user:pw@example.org/', CONSOLE) && !p.siteUrlAllowed('https://example.org/' + 'a'.repeat(2100), CONSOLE) && !p.siteUrlAllowed(42, CONSOLE));
test('only the console top frame may drive the shell', p.senderAllowed('http://127.0.0.1:8788/', CONSOLE, true) && !p.senderAllowed('http://127.0.0.1:8788/', CONSOLE, false) && !p.senderAllowed('https://evil.example/', CONSOLE, true) && !p.senderAllowed('http://127.0.0.1:8794/', CONSOLE, true));
const longRedirect = 'https://accounts.google.com/CheckCookie?continue=' + encodeURIComponent('https://www.youtube.com/signin?next=' + 'x'.repeat(4000));
test('long sign-in redirects inside a page are followed (2048 is only for console-typed addresses)', p.siteNavigationAllowed(longRedirect, CONSOLE) && !p.siteUrlAllowed(longRedirect, CONSOLE));
test('long pop-up URLs from sign-in hosts still open', p.popupDecision(longRedirect, 'new-window', CONSOLE) === 'window');
test('in-page navigation still refuses the console, bridge and non-web schemes', !p.siteNavigationAllowed('http://localhost:8788/', CONSOLE) && !p.siteNavigationAllowed('file:///etc/passwd', CONSOLE) && !p.siteNavigationAllowed('http://127.0.0.1:8794/frame', CONSOLE));
test('only panes p1 and p2 exist', p.paneAllowed('p1') && p.paneAllowed('p2') && !p.paneAllowed('p3') && !p.paneAllowed({}));
const r = p.clampRect({ x: 10.4, y: 20.6, width: 500, height: 400 }, 1600, 1000);
test('bounds are rounded integers inside the window', r && r.x === 10 && r.y === 21 && r.width === 500 && r.height === 400);
test('bounds are clipped to the window', JSON.stringify(p.clampRect({ x: 1500, y: 900, width: 500, height: 500 }, 1600, 1000)) === JSON.stringify({ x: 1500, y: 900, width: 100, height: 100 }));
test('malformed or tiny bounds mean hidden', p.clampRect(null, 1600, 1000) === null && p.clampRect({ x: NaN, y: 0, width: 10, height: 10 }, 1600, 1000) === null && p.clampRect({ x: 0, y: 0, width: 20, height: 400 }, 1600, 1000) === null && p.clampRect({ x: '1', y: 0, width: 500, height: 500 }, 1600, 1000) === null);
test('sign-in pop-ups get a window; tab links stay in the pane', p.popupDecision('https://accounts.google.com/o/oauth2/auth', 'new-window', CONSOLE) === 'window' && p.popupDecision('https://example.org/', 'foreground-tab', CONSOLE) === 'same-view');
test('only sign-in hosts get a pop-up window (no look-alike windows from any site)', p.popupDecision('https://evil.example/login', 'new-window', CONSOLE) === 'same-view' && p.popupDecision('https://accounts.google.com.evil.example/', 'new-window', CONSOLE) === 'same-view');
test('a pop-up never opens further windows', p.popupDecision('https://accounts.google.com/x', 'new-window', CONSOLE, true) === 'same-view');
test('the console and bridge are refused under every loopback spelling', ['http://localhost:8788/', 'http://[::1]:8788/', 'http://0.0.0.0:8788/', 'http://127.0.0.2:8788/', 'http://127.0.0.1:8794/frame', 'https://localhost:8794/'].every(u => !p.siteUrlAllowed(u, CONSOLE)));
test('other local dev servers stay reachable', p.siteUrlAllowed('http://127.0.0.1:3100/', CONSOLE) && p.siteUrlAllowed('http://localhost:5173/', CONSOLE));
test('sub-frames: about/data/blob are fine; file, chrome, devtools and the console are not', ['about:blank', 'about:srcdoc', 'data:text/html,x', 'blob:https://x.example/1', 'https://www.youtube.com/embed/x'].every(p.frameUrlAllowed) && !['file:///etc/passwd', 'chrome://settings', 'devtools://devtools/x', 'javascript:alert(1)', 'http://localhost:8788/'].some(p.frameUrlAllowed));
test('the console is told no fragments and no token-like query', p.publicPageUrl('https://app.example/cb?code=abc&state=1#access_token=x') === 'https://app.example/cb' && p.publicPageUrl('https://www.youtube.com/watch?v=dQw4w9WgXcQ#t=10') === 'https://www.youtube.com/watch?v=dQw4w9WgXcQ' && p.publicPageUrl('not a url') === '');
test('pop-ups to non-web or console URLs are refused', p.popupDecision('file:///x', 'new-window', CONSOLE) === 'deny' && p.popupDecision('http://127.0.0.1:8788/', 'new-window', CONSOLE) === 'deny');
test('site permissions: full screen, clipboard writes and storage access only', p.permissionAllowed('fullscreen') && p.permissionAllowed('clipboard-sanitized-write') && p.permissionAllowed('storage-access') && p.permissionAllowed('top-level-storage-access') && ['media', 'geolocation', 'notifications', 'midi', 'openExternal', 'clipboard-read'].every(x => !p.permissionAllowed(x)));

const failed = results.filter(x => x.result === 'FAIL');
console.log(JSON.stringify({ total: results.length, passed: results.length - failed.length, failed: failed.length, results, scope: 'desktop shell policy, plain Node' }));
if (failed.length) process.exitCode = 1;
