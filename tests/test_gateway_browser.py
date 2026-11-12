#!/usr/bin/env python3
"""Browser API contract for the gateway (stdlib only, loopback, fake authority).

/api/browser/state and /api/browser/navigate open only for a verified capability with the
`control:browser` permission in the `te-design-house` scope. Pingpong and toggle stay closed.
The real authority is replaced by an in-memory fake; nothing leaves 127.0.0.1.
"""
import importlib.util, json, os, sys, tempfile, threading, types, urllib.error, urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = []


def check(name, ok):
    RESULTS.append({'test': name, 'result': 'PASS' if ok else 'FAIL'})


class FakeAuthority:
    calls = []

    @classmethod
    def get_gateway_singleton(cls, *a, **k):
        return cls()

    def verify_request_capability(self, *, token_string, request_session_id, request_origin,
                                  required_scope=None, required_permission=None):
        FakeAuthority.calls.append(('verify', token_string, required_scope, required_permission))
        grants = {'good': {'control:browser', 'read:knowledge'}, 'readonly': {'read:knowledge'}}
        if token_string not in grants or request_session_id != 'sess-operator-test':
            return False, 401, 'invalid_token', None
        if required_scope != 'te-design-house':
            return False, 403, 'scope_mismatch', None
        if required_permission not in grants[token_string]:
            return False, 403, 'permission_missing', None
        return True, 200, 'ok', {'sub': 'operator-test', 'perms': sorted(grants[token_string])}

    def authenticate_operator_and_bootstrap_scope(self, **kwargs):
        FakeAuthority.calls.append(('login', kwargs))
        return True, 200, 'ok', 'good'


def main():
    tmp = tempfile.mkdtemp(prefix='brnestrm-gw-test-')
    os.environ['SILLOK_LEDGER_FILE'] = os.path.join(tmp, 'sillok.jsonl')
    fake = types.ModuleType('candidate_auth_authority')
    fake.CandidateAuthAuthority = FakeAuthority
    sys.modules['candidate_auth_authority'] = fake
    sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location('brnestrm_gateway_under_test', ROOT / 'brnestrm-gateway.py')
    gw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gw)

    srv = ThreadingHTTPServer(('127.0.0.1', 0), gw.Handler)
    port = srv.server_address[1]
    os.environ['BRNESTRM_TEST_PORT'] = str(port)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    origin = f'http://127.0.0.1:{port}'

    def call(method, path, body=None, token=None, session='sess-operator-test', origin_header=origin):
        headers = {'Accept': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
            headers['X-Session-ID'] = session
        if origin_header:
            headers['Origin'] = origin_header
        data = None if body is None else json.dumps(body).encode()
        if data is not None:
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(origin + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read() or b'{}')
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b'{}')

    try:
        code, body = call('GET', '/api/browser/state?sessionId=sess-1')
        check('state without a token is refused', code in (401, 403) and body.get('ok') is False and 'kind' not in body)
        code, body = call('GET', '/api/browser/state?sessionId=sess-1', token='readonly')
        check('state without control:browser is refused', code == 403 and 'kind' not in body)
        code, body = call('GET', '/api/browser/state?sessionId=sess-1', token='forged')
        check('a forged token is refused', code == 401 and 'kind' not in body)
        code, body = call('GET', '/api/browser/state?sessionId=sess-1', token='good')
        check('state with control:browser returns the session state', code == 200 and body.get('kind') == 'ledger' and body.get('sessionId') == 'sess-1')
        check('the capability is checked for scope and permission', ('verify', 'good', 'te-design-house', 'control:browser') in FakeAuthority.calls)

        url = 'https://www.youtube.com/watch?v=abcDEF12345'
        code, body = call('POST', '/api/browser/navigate', {'url': url, 'sessionId': 'sess-1'}, token='good')
        check('navigate with control:browser updates the state', code == 200 and body.get('kind') == 'youtube' and body.get('videoId') == 'abcDEF12345')
        code, body = call('GET', '/api/browser/state?sessionId=sess-1', token='good')
        check('the next state read returns the navigated page', code == 200 and body.get('videoId') == 'abcDEF12345')
        code, body = call('GET', '/api/browser/state?sessionId=sess-2', token='good')
        check('sessions are independent', code == 200 and body.get('kind') == 'chatgpt')

        code, _ = call('POST', '/api/browser/navigate', {'url': url, 'sessionId': 'sess-1'}, token='readonly')
        check('navigate without control:browser is refused', code == 403)
        code, _ = call('POST', '/api/browser/navigate', {'url': url, 'sessionId': 'sess-1'}, token='good', origin_header=None)
        check('navigate without an Origin header is refused', code == 403)
        code, _ = call('POST', '/api/browser/navigate', {'url': url, 'sessionId': 'sess-1'}, token='good', origin_header='https://evil.example')
        check('navigate from another origin is refused', code == 403)
        code, _ = call('POST', '/api/browser/navigate', {'url': 'javascript:alert(1)', 'sessionId': 'sess-1'}, token='good')
        check('non-http URLs are rejected', code == 400)
        code, _ = call('POST', '/api/browser/navigate', {'url': 'https://example.com/' + 'a' * 2100, 'sessionId': 'sess-1'}, token='good')
        check('overlong URLs are rejected', code == 400)
        code, _ = call('POST', '/api/browser/navigate', {'url': url, 'sessionId': '../etc'}, token='good')
        check('malformed session ids are rejected', code == 400)
        code, _ = call('GET', '/api/browser/state?sessionId=' + 'x' * 80, token='good')
        check('malformed session ids are rejected on read', code == 400)

        for path in ('/api/browser/pingpong', '/api/browser/toggle'):
            code, _ = call('POST', path, {'sessionId': 'sess-1'}, token='good')
            check(f'{path} stays closed even with a valid token', code == 403)

        ledger = Path(os.environ['SILLOK_LEDGER_FILE'])
        lines = ledger.read_text().splitlines() if ledger.exists() else []
        check('each navigation is recorded in the audit ledger', any('BROWSER_NAVIGATE' in l for l in lines))

        code, _ = call('POST', '/api/browser/navigate', {'url': 'https://user:s3cret@host.example/p?q=1', 'sessionId': 'sess-1'}, token='good')
        led = ledger.read_text()
        check('the audit ledger keeps only the hostname (no credentials, path or query)', code == 200 and 'host=host.example' in led and 's3cret' not in led and 'q=1' not in led)
        code, body = call('POST', '/api/browser/navigate', {'url': 'HTTP://Evil.example/x', 'sessionId': 'sess-1'}, token='good')
        check('an upper-case scheme is kept, not prefixed twice', code == 200 and body.get('url') == 'HTTP://Evil.example/x' and 'host=evil.example' in ledger.read_text())
        for own in ('http://127.0.0.1:8788/', 'http://localhost:8788/api/vault'):
            code, _ = call('POST', '/api/browser/navigate', {'url': own, 'sessionId': 'sess-1'}, token='good')
            check(f'the console cannot open the gateway itself ({own})', code == 400)
        code, body = call('POST', '/api/browser/navigate', {'url': 'yt:op-1 field', 'sessionId': 'sess-1'}, token='good')
        check('yt: queries become a YouTube search', code == 200 and body.get('kind') == 'youtube' and body.get('url', '').startswith('https://www.youtube.com/results?search_query=') and body.get('searchQuery') == 'op-1 field')
        code, body = call('POST', '/api/browser/navigate', {'url': 'https://example.org/', 'sessionId': 'sess-1'}, token='good')
        check('media fields from an earlier YouTube page are cleared', code == 200 and body.get('kind') == 'web' and 'videoId' not in body)
        with gw._browser_lock:
            gw._browser_sessions['sess-restored'] = dict(gw._init_session_browser('sess-restored'), recentMessages=[{'role': 'user', 'text': 'PRIVATE'}], transcript='PRIVATE', lastPingPong={'prompt': 'PRIVATE'})
        code, body = call('GET', '/api/browser/state?sessionId=sess-restored', token='good')
        check('state responses omit restored conversation content', code == 200 and 'PRIVATE' not in json.dumps(body) and 'recentMessages' not in body)
        codes = [call('GET', f'/api/browser/state?sessionId=sess-lru-{i}', token='good')[0] for i in range(80)]
        check('sessions beyond the cap evict the least recently used instead of failing', all(c == 200 for c in codes) and len(gw._browser_sessions) <= gw.BROWSER_MAX_SESSIONS)
        os.environ['SILLOK_LEDGER_FILE'] = tmp  # a directory: the audit write must fail closed
        code, body = call('POST', '/api/browser/navigate', {'url': url, 'sessionId': 'sess-1'}, token='good')
        check('an audit failure blocks navigation without leaking paths', code == 500 and body.get('error') == 'audit_failure')
        os.environ['SILLOK_LEDGER_FILE'] = str(ledger)

        code, body = call('POST', '/api/auth/login', {'scope': 'te-design-house', 'permissions': ['read:provenance', 'read:knowledge', 'control:browser'], 'sessionId': 'sess-operator-test'}, token=None)
        check('login may request control:browser', code == 200 and 'control:browser' in body.get('permissions', []))
        login = [c for c in FakeAuthority.calls if c[0] == 'login']
        check('login passes the TCP peer port to the authority', bool(login) and isinstance(login[-1][1].get('peer_port'), int))
        code, _ = call('POST', '/api/auth/login', {'scope': 'te-design-house', 'permissions': ['control:everything'], 'sessionId': 'sess-operator-test'}, token=None)
        check('login still rejects unknown permissions', code == 403)
    finally:
        srv.shutdown()

    failed = [r for r in RESULTS if r['result'] == 'FAIL']
    print(json.dumps({'total': len(RESULTS), 'passed': len(RESULTS) - len(failed), 'failed': len(failed), 'results': RESULTS,
                      'scope': 'loopback gateway with a fake authority; no live service'}))
    if failed:
        sys.exit(1)


if __name__ == '__main__':
    main()
