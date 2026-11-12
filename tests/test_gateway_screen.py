#!/usr/bin/env python3
"""Ego Lite screen proxy contract (stdlib only, loopback, fake authority and fake bridge)."""
import importlib.util, json, os, sys, tempfile, threading, types, urllib.error, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = []
PNG = bytes.fromhex('89504e470d0a1a0a0000000d4948445200000001000000010806000000'
                    '1f15c4890000000d49444154789c63f8cfc0f00f0003860180583a4f6b0000000049454e44ae426082')


def check(name, ok):
    RESULTS.append({'test': name, 'result': 'PASS' if ok else 'FAIL'})


class FakeAuthority:
    @classmethod
    def get_gateway_singleton(cls, *a, **k):
        return cls()

    def verify_request_capability(self, *, token_string, request_session_id, required_scope=None, required_permission=None, **k):
        grants = {'good': {'control:browser'}, 'readonly': {'read:knowledge'}}
        if token_string not in grants or request_session_id != 'sess-op':
            return False, 401, 'invalid_token', None
        if required_permission not in grants[token_string]:
            return False, 403, 'permission_missing', None
        return True, 200, 'ok', {'sub': 't'}


class Bridge(BaseHTTPRequestHandler):
    log = []
    pages = set()

    def _send(self, code, body, ctype='application/json', extra=None):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _record(self, body=None):
        Bridge.log.append({'method': self.command, 'path': self.path, 'secret': self.headers.get('x-bridge-secret'),
                           'origin': self.headers.get('Origin'), 'body': body})

    def do_GET(self):
        self._record()
        if self.path.startswith('/health'):
            return self._send(200, b'{"ok":true}')
        if self.path.startswith('/frame'):
            sess = self.path.split('session=')[-1]
            if sess not in Bridge.pages:
                return self._send(404, b'{"ok":false,"error":"no_page"}')
            return self._send(200, PNG, 'image/png', {'X-Page-Width': '1280', 'X-Page-Height': '720', 'X-Page-Url': 'https%3A%2F%2Fexample.org%2F'})
        return self._send(404, b'{}')

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
        self._record(body)
        if self.path == '/goto':
            Bridge.pages.add(body['session'])
            return self._send(200, json.dumps({'ok': True, 'url': body['url'], 'width': 1280, 'height': 720}).encode())
        if self.path == '/input':
            return self._send(200, b'{"ok":true}')
        if self.path == '/close':
            Bridge.pages.discard(body['session'])
            return self._send(200, b'{"ok":true,"closed":true}')
        return self._send(404, b'{}')

    def log_message(self, *a):
        pass


def main():
    tmp = tempfile.mkdtemp(prefix='brnestrm-screen-test-')
    bridge = ThreadingHTTPServer(('127.0.0.1', 0), Bridge)
    threading.Thread(target=bridge.serve_forever, daemon=True).start()
    secret_file = Path(tmp) / 'ego-bridge.secret'
    secret_file.write_text('s' * 64)
    os.chmod(secret_file, 0o600)
    os.environ.update({'SILLOK_LEDGER_FILE': os.path.join(tmp, 'sillok.jsonl'),
                       'BRNESTRM_EGO_BRIDGE_PORT': str(bridge.server_address[1]),
                       'BRNESTRM_EGO_BRIDGE_SECRET_FILE': str(secret_file),
                       # A dead proxy: the bridge calls must ignore it (loopback only, never proxied).
                       'HTTP_PROXY': 'http://127.0.0.1:9', 'http_proxy': 'http://127.0.0.1:9'})
    fake = types.ModuleType('candidate_auth_authority')
    fake.CandidateAuthAuthority = FakeAuthority
    sys.modules['candidate_auth_authority'] = fake
    sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location('gw_screen_test', ROOT / 'brnestrm-gateway.py')
    gw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gw)
    srv = ThreadingHTTPServer(('127.0.0.1', 0), gw.Handler)
    port = srv.server_address[1]
    os.environ['BRNESTRM_TEST_PORT'] = str(port)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    origin = f'http://127.0.0.1:{port}'
    direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # the test client itself skips the dead proxy

    def call(method, path, body=None, token=None, with_origin=True):
        h = {}
        if token:
            h.update({'Authorization': 'Bearer ' + token, 'X-Session-ID': 'sess-op'})
        if with_origin:
            h['Origin'] = origin
        data = None if body is None else json.dumps(body).encode()
        if data is not None:
            h['Content-Type'] = 'application/json'
        req = urllib.request.Request(origin + path, data=data, method=method, headers=h)
        try:
            with direct.open(req, timeout=10) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def jsn(b):
        try:
            return json.loads(b)
        except Exception:
            return {}

    try:
        code, _, _ = call('GET', '/api/browser/screen?sessionId=sess-1')
        check('screen without a token is refused', code in (401, 403))
        code, _, _ = call('GET', '/api/browser/screen?sessionId=sess-1', token='readonly')
        check('screen without control:browser is refused', code == 403)

        code, _, body = call('POST', '/api/browser/navigate', {'url': 'https://example.org/', 'sessionId': 'sess-1'}, token='good')
        gotos = [r for r in Bridge.log if r['path'] == '/goto']
        check('navigating a web page opens it in Ego Lite', code == 200 and jsn(body).get('screen') is True and gotos and gotos[-1]['body'] == {'session': 'sess-1', 'url': 'https://example.org/'})
        check('the gateway authenticates to the bridge and sends no Origin', gotos and gotos[-1]['secret'] == 's' * 64 and gotos[-1]['origin'] is None)
        n = len(gotos)
        code, _, body = call('POST', '/api/browser/navigate', {'url': 'http://127.0.0.1:8765/api/tasks', 'sessionId': 'sess-1'}, token='good')
        check('the ledger stays a console view (no Ego navigation)', code == 200 and jsn(body).get('kind') == 'ledger' and len([r for r in Bridge.log if r['path'] == '/goto']) == n)
        check('switching to the ledger closes the session tab', any(r['path'] == '/close' and r['body'] == {'session': 'sess-1'} for r in Bridge.log) and 'sess-1' not in Bridge.pages)
        code, _, _ = call('POST', '/api/browser/screen/input', {'sessionId': 'sess-1', 'type': 'key', 'key': 'Enter'}, token='good')
        check('input to a session showing the ledger is refused', code == 409)
        call('POST', '/api/browser/navigate', {'url': 'https://example.org/', 'sessionId': 'sess-1'}, token='good')

        code, headers, body = call('GET', '/api/browser/screen?sessionId=sess-1', token='good')
        check('the screen is proxied as a PNG with the page size', code == 200 and body == PNG and headers.get('Content-Type') == 'image/png' and headers.get('X-Page-Width') == '1280' and headers.get('Cache-Control') == 'no-store')
        code, _, body = call('GET', '/api/browser/screen?sessionId=sess-2', token='good')
        check('a session with a page in its state but none in Ego is opened on demand', code == 200 and body == PNG and 'sess-2' in Bridge.pages)
        code, _, body = call('GET', '/api/browser/screen?sessionId=sess-ledger', token='good')
        check('a ledger session has no screen', code == 409)
        code, _, _ = call('GET', '/api/browser/screen?sessionId=../x', token='good')
        check('malformed sessions are rejected', code == 400)

        code, _, body = call('POST', '/api/browser/screen/input', {'sessionId': 'sess-1', 'type': 'click', 'x': 10, 'y': 20}, token='good')
        inputs = [r for r in Bridge.log if r['path'] == '/input']
        check('input is forwarded with the session id', code == 200 and inputs and inputs[-1]['body'] == {'session': 'sess-1', 'type': 'click', 'x': 10, 'y': 20})
        code, _, _ = call('POST', '/api/browser/screen/input', {'sessionId': 'sess-1', 'type': 'viewport', 'width': 900, 'height': 700}, token='good')
        check('the pane size is forwarded', code == 200 and [r for r in Bridge.log if r['path'] == '/input'][-1]['body'] == {'session': 'sess-1', 'type': 'viewport', 'width': 900, 'height': 700})
        code, _, _ = call('POST', '/api/browser/screen/input', {'sessionId': 'sess-1', 'type': 'click', 'x': 10, 'y': 20}, token='good', with_origin=False)
        check('input without an Origin header is refused', code == 403)
        code, _, _ = call('POST', '/api/browser/screen/input', {'sessionId': 'sess-1', 'type': 'click', 'x': 10, 'y': 20}, token='readonly')
        check('input without control:browser is refused', code == 403)
        for label, payload in (('unknown type', {'type': 'shell'}), ('extra fields', {'type': 'key', 'key': 'Enter', 'cmd': 'x'}),
                               ('huge text', {'type': 'type', 'text': 'x' * 3000})):
            code, _, _ = call('POST', '/api/browser/screen/input', dict(payload, sessionId='sess-1'), token='good')
            check(f'invalid input is refused before the bridge: {label}', code == 400)
        lines = Path(os.environ['SILLOK_LEDGER_FILE']).read_text()
        check('typed text never reaches the audit ledger', 'hello-secret-text' not in lines)

        bridge.shutdown(); bridge.server_close()
        code, _, body = call('GET', '/api/browser/screen?sessionId=sess-1', token='good')
        check('with the bridge down the screen is 503, not an error page', code == 503 and jsn(body).get('error') == 'screen_unavailable')
        code, _, body = call('POST', '/api/browser/navigate', {'url': 'https://example.com/', 'sessionId': 'sess-1'}, token='good')
        check('navigation still works without the bridge (screen false)', code == 200 and jsn(body).get('screen') is False)
    finally:
        srv.shutdown()

    failed = [r for r in RESULTS if r['result'] == 'FAIL']
    print(json.dumps({'total': len(RESULTS), 'passed': len(RESULTS) - len(failed), 'failed': len(failed), 'results': RESULTS,
                      'scope': 'loopback gateway with fake authority and fake Ego bridge'}))
    if failed:
        sys.exit(1)


if __name__ == '__main__':
    main()
