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
