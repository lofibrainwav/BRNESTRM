# BRNESTRM — sanitized public source

Local-first agent console with read-only GraphRAG retrieval, a bounded
Researcher/Verifier chain and an operator-facing browser UI.

IMPORTANT: This is not a turnkey copy of the owner's Mac. The real Touch ID
capability issuer, native helper, personal files, Sillok ledger, real GraphRAG
SQLite DB, backup/recovery keys, original Git history and accounts are excluded.

The published authentication adapter is intentionally DENY ALL. Authenticated
private endpoints remain blocked until the owner installs a separately audited,
real local authority and appropriate model/data dependencies.

## Run the public UI

Python 3.9+ and optional Ollama; Node.js needed for tests.

    python3 -B brnestrm-gateway.py

Open http://127.0.0.1:8788 locally. Never bind a public network interface.

## Portable checks

    node tests/test_ui_contract.cjs
    node tests/test_console_reliability.cjs
    node tests/test_browser_split.cjs

## Chat history

Opt-in by selecting Save chats. AES-256-GCM ciphertext is stored only within
one browser's local storage; the separate passphrase stays in tab memory and
is required to unlock after refresh. It is NOT synchronized, and browser
storage clearing or lost passphrase makes it unrecoverable.

## Deployment limits

GraphRAG private content and macOS Touch ID cannot be reproduced from this
public snapshot. Do not deploy this release as a production auth authority.
No third-party secrets, logs, local runtime data, binary helpers, backups,
customer files or private project URLs may be committed.

No license grant is implied. See docs/SECURITY.md.


## Optional macOS operation scripts

The ops/ directory contains public, de-identified reference scripts for
local readiness monitoring, encrypted backup/restore and a status inspector.

Set BRNESTRM_GDRIVE_RECOVERY_PATH to your own private cloud-sync folder
before running the backup manager. Never put recovery keys or cloud
credentials in environment files tracked by Git. These examples do not
provision Keychain permissions, a private auth provider or working backup
destinations automatically. Recovery-key escrow is deliberately a separate
owner-held security step.


## Optional recovery-key escrow

`ops/recovery-key-escrow.py` is an interactive reference for encrypting a
local backup decryption key under a **separate owner-chosen passphrase**.
It uses AES-256-GCM and a slow PBKDF2-SHA256 key derivation, and refuses
to replace existing key/escrow files. Set `BRNESTRM_GDRIVE_RECOVERY_PATH`
to your own private, backed-up destination; no account or folder is
embedded in public source. The operator must enter the passphrase on their
own Mac and store it independently of the Mac and recovery archive.
Synthetic tests do not establish actual disaster-recovery escrow.

## Hermes inference after a Mac restart

See [macOS login recovery](docs/HERMES-INFERENCE-AUTOSTART.md) for the distinct local llama.cpp inference backend, its loopback-only service and health checks.


## User-login reboot recovery observer

`ops/reboot-verifier.py` is an evidence-only, local-only diagnostic for the
**next** login after its installation. It intentionally reports
`PENDING_FRESH_REBOOT` if started in the current boot, so manually running it
cannot fabricate evidence of new boot recovery. Production deployment requires
a user-approved LaunchAgent and a separately reviewed private receipt location;
no Mac reboot is initiated by this script.

## Independent recovery drill

`ops/independent-recovery-drill.py` uses only a mounted encrypted cloud
archive, its signed-off SHA-256 manifest, the encrypted escrow record and a
passphrase typed locally. It NEVER reads the live raw recovery key. Its
authenticated extraction is in memory and only critical digest matches and
file counts are reported. The actual owner must run it interactively to
complete the full disaster-recovery gate. Run `--self-test` first; that
check uses synthetic data only.


## Evidence-driven status semantics

`ops/ops-status.py` only labels post-login reboot recovery verified after
a fresh boot's independently armed `reboot-verifier` receipt. Locale-normalized
`ps -o lstart=` times are diagnostic data, **not** automatic-boot proof.
Backup runs verify the local/mounted cloud mirror, but
`remoteProviderByteReadback` explicitly stays `UNVERIFIED_BY_SCHEDULER`
until a separate provider-side download/hash check is implemented. Manual
provider downloads must not be represented as recurring automated checks.
