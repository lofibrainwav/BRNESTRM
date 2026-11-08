# Hermes inference backend — macOS login recovery

The Hermes desktop app and its `ai.hermes.gateway` LaunchAgent are **not**
the separate OpenAI-compatible llama.cpp server that BRNESTRM expects on
`http://127.0.0.1:18434`. Restarting the Hermes app alone may leave
BRNESTRM's `hermes_http` check degraded.

## Owner-controlled local LaunchAgent

Install a **distinct** user-login LaunchAgent, for example
`com.local.brnestrm.hermes-llama`, pointing to an existing, authorized
`llama-server` binary and a locally downloaded GGUF model. Do not start a
second process when port 18434 is already occupied.

Recommended `ProgramArguments` (replace both filesystem paths with valid
absolute paths on the target Mac; launchd does not expand `$HOME`):

```text
/opt/homebrew/bin/llama-server
-m
/absolute/private/path/to/existing-model.gguf
--host
127.0.0.1
--port
18434
--ctx-size
32768
--parallel
1
--threads
4
--no-webui
--cors-origins
http://127.0.0.1:8788
```

Use `RunAtLoad = true`, `KeepAlive` restricted to failure/crash, a
`ThrottleInterval` of 30 seconds, `Umask = 63`, and private stdout/stderr
log paths. Validate the plist with `plutil -lint`, then register with
`launchctl bootstrap gui/$(id -u) /absolute/path/to/plist`. Always check
whether an existing service and listener are present before bootstrapping.
Re-register after changing the plist to pick up new arguments; a rapid
`bootout` followed immediately by `bootstrap` may transiently return
error 5 while launchd is unloading the former service.

## Verify without changing user data

```sh
launchctl list | grep -E 'ai.hermes.gateway|com.local.brnestrm.hermes-llama'
lsof -nP -iTCP:18434 -sTCP:LISTEN
curl -fsS --max-time 5 http://127.0.0.1:18434/health
curl -fsS --max-time 6 http://127.0.0.1:8788/api/health/readiness
```

Wait for the model to load before concluding the server failed; startup can
take longer than process registration. HTTP 200 health is not proof of actual
inference; a synthetic, low-token chat completion tests that separately.
BRNESTRM is ready only when the real dependency checks succeed.

## Security boundary

Binding `127.0.0.1` prevents LAN exposure but **does not stop other local
processes** from accessing an unauthenticated inference backend. The
`--cors-origins` flag should use **one exact browser origin**, not a
comma-delimited value: some llama.cpp versions emit that entire value as
one invalid `Access-Control-Allow-Origin` header. CORS limits browser
reads; it is not authentication. Never bind `0.0.0.0` without a separately
reviewed authorization, firewall and TLS design. No user credentials,
prompt text or private model data belong in this public repository.


## Authenticated inference API

Use `--api-key-file /absolute/private/path/to/0600-key-file` with a
**private key file, never an inline `--api-key` argument** in committed
scripts or LaunchAgents. Any BRNESTRM Keychain client credentials and the
Hermes client's configured provider credentials must match the active
server key **before** enabling authentication. Test an anonymous
`GET /v1/models` returns HTTP 401 and then perform a synthetic request
through the actual authorized BRNESTRM client, since `GET /health` may
return 200 even if authenticated inference is broken.

Avoid dueling supervisors: the Hermes managed runtime has its own
process-ownership state. A separately installed login LaunchAgent must
not be mistaken for a Hermes-managed server, and stale ownership records
should not be treated as proof of an active Hermes runtime. Reconcile the
service ownership contract before changing model port or native Hermes
runtime settings.
