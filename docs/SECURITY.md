# Security boundaries

Public snapshot only; no real keys, credential stores, signed helper binary,
Sillok events, private indexed corpus, customer documents, host identifiers,
archive encryption keys, user account names, or private workspace URLs.

Public auth is DENY ALL. Replace only through a privately reviewed and
server-verified Touch ID capability provider. Synthetic auth must never run
on the operational port.

Browser chat history encryption is optional. The passphrase is not saved
or transmitted by this UI; it must be retained separately by the operator.
No part of the browser local encrypted history is uploaded to GitHub.

No public issue should contain user private data, server logs or credentials.
