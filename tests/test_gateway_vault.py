#!/usr/bin/env python3
"""Vault & Keys contract (stdlib only, loopback): fake authority with step-up and a fake `security`
binary backed by a temp file. The real Keychain and Touch ID are never touched."""
import importlib.util, json, os, stat, sys, tempfile, threading, types, urllib.error, urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = []
SECRET = 'ghp_TestSecretValue_1234567890'


def check(name, ok):
    RESULTS.append({'test': name, 'result': 'PASS' if ok else 'FAIL'})


FAKE_SECURITY = r'''#!/usr/bin/env python3
import json, shlex, sys
store_path, log_path = sys.argv[0] + ".store", sys.argv[0] + ".argv"
with open(log_path, "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\n")
try:
    store = json.load(open(store_path))
except Exception:
    store = {}
def opt(args, flag):
    return args[args.index(flag) + 1] if flag in args else None
args = sys.argv[1:]
if args == ["-i"]:
    args = shlex.split(sys.stdin.read().strip())
cmd = args[0] if args else ""
if cmd == "add-generic-password":
    key = opt(args, "-s") + "/" + opt(args, "-a")
    if key in store and "-U" not in args:
        sys.stderr.write("security: The specified item already exists in the keychain.\n")
        sys.exit(45)
    store[key] = opt(args, "-w")
    with open(sys.argv[0] + ".acl", "a") as f:
        f.write(json.dumps({"key": key, "trusted": opt(args, "-T")}) + "\n")
elif cmd == "delete-generic-password":
    store.pop(opt(args, "-s") + "/" + opt(args, "-a"), None)
elif cmd == "find-generic-password":
    key = opt(args, "-s") + "/" + opt(args, "-a")
    if key not in store:
        sys.exit(44)
    if "-w" in args:
        print(store[key])
json.dump(store, open(store_path, "w"))
'''


class FakeAuthority:
    approve = True
    actions = []

    @classmethod
    def get_gateway_singleton(cls, *a, **k):
        return cls()

    def verify_request_capability(self, *, token_string, request_session_id, required_scope=None, required_permission=None, **k):
        grants = {'good': {'vault:manage', 'control:browser'}, 'nobrowse': {'read:knowledge'}}
        if token_string not in grants or request_session_id != 'sess-op':
            return False, 401, 'invalid_token', None
        if required_permission not in grants[token_string]:
            return False, 403, 'permission_missing', None
        return True, 200, 'ok', {'sub': 'owner-local'}

    def step_up(self, *, token_string, request_session_id, request_origin, required_scope, required_permission, action, peer_port=None):
        ok, code, msg, _ = self.verify_request_capability(token_string=token_string, request_session_id=request_session_id,
                                                         required_scope=required_scope, required_permission=required_permission)
        if not ok:
            return False, code, msg
        FakeAuthority.actions.append(action)
        return (True, 200, 'ok') if FakeAuthority.approve else (False, 403, 'touchid_denied')


def main():
    tmp = tempfile.mkdtemp(prefix='brnestrm-vault-test-')
    sec = Path(tmp) / 'security'
    sec.write_text(FAKE_SECURITY)
    sec.chmod(0o700)
    os.environ.update({'SILLOK_LEDGER_FILE': os.path.join(tmp, 'sillok.jsonl'),
                       'BRNESTRM_SECURITY_BIN': str(sec),
                       'BRNESTRM_VAULT_INDEX': os.path.join(tmp, 'brnestrm', 'vault-index.json'),
                       'BRNESTRM_VAULT_SERVICE': 'BRNESTRM-vault-test'})
    fake = types.ModuleType('candidate_auth_authority')
    fake.CandidateAuthAuthority = FakeAuthority
    sys.modules['candidate_auth_authority'] = fake
    sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location('gw_vault_test', ROOT / 'brnestrm-gateway.py')
    gw = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gw)
    srv = ThreadingHTTPServer(('127.0.0.1', 0), gw.Handler)
    port = srv.server_address[1]
    os.environ['BRNESTRM_TEST_PORT'] = str(port)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    origin = f'http://127.0.0.1:{port}'
    direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(method, path, body=None, token='good', with_origin=True):
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
                return r.status, json.loads(r.read() or b'{}')
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b'{}')
            except ValueError:
                return e.code, {}

    store = lambda: json.loads((Path(str(sec) + '.store')).read_text()) if Path(str(sec) + '.store').exists() else {}
    try:
        check('listing without a token is refused', call('GET', '/api/vault', token=None)[0] in (401, 403))
        check('listing without vault:manage is refused', call('GET', '/api/vault', token='nobrowse')[0] == 403)
        code, body = call('GET', '/api/vault')
        check('an empty vault lists as empty', code == 200 and body.get('vault') == [])

        code, body = call('POST', '/api/vault/add', {'name': 'GITHUB_TOKEN', 'secret': SECRET, 'scope': 'Global'})
        check('saving asks for one step-up naming the key', code == 200 and FakeAuthority.actions[-1] == 'vault save GITHUB_TOKEN')
        check('the secret is stored in the Keychain (base64), not in the index', store().get('BRNESTRM-vault-test/GITHUB_TOKEN') and SECRET not in Path(os.environ['BRNESTRM_VAULT_INDEX']).read_text())
        check('the response lists the name and never the value', SECRET not in json.dumps(body) and body['vault'][0]['name'] == 'GITHUB_TOKEN')
        argv_log = Path(str(sec) + '.argv').read_text()
        check('the secret never appears in a process argument list', SECRET not in argv_log and 'Z2hw' not in argv_log)
        acl = [json.loads(l) for l in Path(str(sec) + '.acl').read_text().splitlines()]
        check('items are created with an empty trusted-app list (every read asks the owner)', acl and all(a['trusted'] == '' for a in acl))
        mode = stat.S_IMODE(os.stat(os.environ['BRNESTRM_VAULT_INDEX']).st_mode)
        check('the index file is private (0600)', mode == 0o600)
        code, body = call('GET', '/api/vault')
        check('listing shows names only, with Keychain presence', code == 200 and [v['name'] for v in body['vault']] == ['GITHUB_TOKEN'] and body['vault'][0]['present'] is True and SECRET not in json.dumps(body))
        code, body = call('POST', '/api/vault/add', {'name': 'GITHUB_TOKEN', 'secret': SECRET + '-v2'})
        check('saving an existing name asks to replace it, and replaces it', code == 200 and FakeAuthority.actions[-1] == 'vault replace GITHUB_TOKEN' and len(body['vault']) == 1)

        FakeAuthority.approve = False
        code, body = call('POST', '/api/vault/add', {'name': 'OPENAI_KEY', 'secret': 'sk-denied'})
        check('a denied Touch ID saves nothing', code == 403 and 'BRNESTRM-vault-test/OPENAI_KEY' not in store())
        code, _ = call('POST', '/api/vault/delete', {'id': 'GITHUB_TOKEN'})
        check('a denied Touch ID deletes nothing', code == 403 and 'BRNESTRM-vault-test/GITHUB_TOKEN' in store())
        FakeAuthority.approve = True

        n = len(FakeAuthority.actions)
        check('mutations without an Origin are refused before any prompt', call('POST', '/api/vault/add', {'name': 'X', 'secret': 'y'}, with_origin=False)[0] == 403 and len(FakeAuthority.actions) == n)
        check('mutations without vault:manage are refused before any prompt', call('POST', '/api/vault/add', {'name': 'X', 'secret': 'y'}, token='nobrowse')[0] == 403 and len(FakeAuthority.actions) == n)
        for label, payload in (('quote in name', {'name': 'A"B', 'secret': 'x'}), ('space in name', {'name': 'A B', 'secret': 'x'}),
                               ('empty secret', {'name': 'A', 'secret': ''}), ('huge secret', {'name': 'A', 'secret': 'x' * 9000}),
                               ('newline in name', {'name': 'A\nB', 'secret': 'x'}), ('trailing newline in name', {'name': 'GITHUB_TOKEN\n', 'secret': 'x'}),
                               ('secret over 2048', {'name': 'A', 'secret': 'x' * 2049})):
            check(f'invalid input is refused before any prompt: {label}', call('POST', '/api/vault/add', payload)[0] == 400 and len(FakeAuthority.actions) == n)
        check('deleting an unknown key is 404 without a prompt', call('POST', '/api/vault/delete', {'id': 'NOPE'})[0] == 404 and len(FakeAuthority.actions) == n)

        code, body = call('POST', '/api/vault/delete', {'id': 'GITHUB_TOKEN'})
        check('deleting asks for a step-up and removes the Keychain item', code == 200 and FakeAuthority.actions[-1] == 'vault delete GITHUB_TOKEN' and 'BRNESTRM-vault-test/GITHUB_TOKEN' not in store() and body['vault'] == [])
        ledger = Path(os.environ['SILLOK_LEDGER_FILE']).read_text()
        check('the audit ledger names the key but never holds its value', 'GITHUB_TOKEN' in ledger and SECRET not in ledger)

    finally:
        srv.shutdown()

    failed = [r for r in RESULTS if r['result'] == 'FAIL']
    print(json.dumps({'total': len(RESULTS), 'passed': len(RESULTS) - len(failed), 'failed': len(failed), 'results': RESULTS,
                      'scope': 'loopback gateway, fake authority with step-up, fake security binary'}))
    if failed:
        sys.exit(1)


if __name__ == '__main__':
    main()
