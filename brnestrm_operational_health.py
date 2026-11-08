"""BRNESTRM component readiness without confusing liveness with working features.

Pure, read-only probes. No tokens, source content, file paths or exception bodies
are returned to the HTTP client. Does not perform inference or invoke Touch ID.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import urllib.request


def _http_json(url, timeout=2.0):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError("unhealthy_http")
        return json.load(response)


def inspect(root, corpus, db_path, sillok_reader,
            ollama_url="http://127.0.0.1:11434",
            hermes_url="http://127.0.0.1:18434"):
    root = Path(root)
    corpus = Path(corpus)
    db_path = Path(db_path)
    started = time.monotonic()
    state = {}
    counts = {}
    try:
        current = sillok_reader()
        state["audit_chain"] = "HEALTHY" if current.get("ok") and current.get("chainValid") else "FAILED"
        counts["audit_records"] = current.get("count", 0) if state["audit_chain"] == "HEALTHY" else 0
    except Exception:
        state["audit_chain"] = "FAILED"

    try:
        with sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True, timeout=2) as db:
            db_ok = db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            rows = db.execute(
                "SELECT path,byte_start,byte_end,chunk_digest FROM graph_nodes"
            ).fetchall() if db_ok else []
        mismatch = 0
        base = corpus.resolve()
        for relative, start, end, digest in rows:
            try:
                src = (base / relative).resolve()
                if (not src.is_relative_to(base) or not src.is_file()
                        or src.is_symlink() or not isinstance(digest, str)
                        or not 0 <= start < end or end - start > 8192):
                    mismatch += 1
                    continue
                with src.open("rb") as f:
                    f.seek(start)
                    raw = f.read(end - start)
                if hashlib.sha256(raw).hexdigest()[:len(digest)] != digest:
                    mismatch += 1
            except (OSError, ValueError, TypeError):
                mismatch += 1
        state["knowledge_integrity"] = "HEALTHY" if db_ok and rows and mismatch == 0 else "FAILED"
        counts["knowledge_chunks"] = len(rows)
        counts["knowledge_digest_failures"] = mismatch
    except Exception:
        state["knowledge_integrity"] = "FAILED"

    try:
        scope = json.loads((root / "integration-handoff/gemini/BRNESTRM_SOURCE_SCOPE.json").read_text())
        if scope.get("status") != "APPLIED":
            raise ValueError("not_applied")
        files = scope["selectedPaths"]
        matching = all(
            hashlib.sha256((root / f["path"]).read_bytes()).hexdigest() == f["sha256"]
            for f in files
        )
        # Recompute all selected source hashes and canonical DB checksum, never
        # trust the index timestamp alone.
        matching &= hashlib.sha256(db_path.read_bytes()).hexdigest() == scope["indexSha256"]
        state["knowledge_freshness"] = "CURRENT" if matching else "STALE"
    except Exception:
        state["knowledge_freshness"] = "UNVERIFIED"

    try:
        models = _http_json(ollama_url.rstrip("/") + "/api/tags")
        state["ollama_api"] = "HEALTHY" if isinstance(models.get("models"), list) else "FAILED"
    except Exception:
        state["ollama_api"] = "FAILED"

    try:
        with urllib.request.urlopen(hermes_url.rstrip("/") + "/health", timeout=2.0) as r:
            state["hermes_http"] = "HEALTHY" if r.status == 200 else "FAILED"
    except Exception:
        state["hermes_http"] = "FAILED"

    essential = ("audit_chain", "knowledge_integrity", "knowledge_freshness",
                 "ollama_api", "hermes_http")
    overall = "READY" if all(
        state.get(k) in ("HEALTHY", "CURRENT") for k in essential
    ) else "DEGRADED"
    return {
        "contract": "BRNESTRM_READINESS_V1",
        "overall": overall,
        "checks": state,
        "counts": counts,
        "probes": {"ollama_api": "HTTP_ONLY_NOT_GENERATION",
                   "hermes_http": "HTTP_ONLY_NOT_GENERATION",
                   "knowledge_integrity": "ALL_INDEXED_CHUNK_DIGESTS"},
        "elapsedMs": int((time.monotonic() - started) * 1000),
        "auditAnchoring": "LOCAL_HASH_CHAIN_NOT_EXTERNALLY_ANCHORED",
    }
