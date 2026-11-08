#!/usr/bin/env python3
"""BRNESTRM persistent GraphRAG indexer.

Input : a HyoDo `inspect` manifest pair (.hyodo/folder-manifest.json + chunks-manifest.json).
Output: brnestrm-corpus/.brnestrm/graphrag.sqlite3 (graph_nodes, graph_edges, meta).

Rules:
- Every chunk's bytes are re-read and their sha256 prefix must equal HyoDo's chunk_digest.
  Mismatch -> chunk skipped and counted (fail-closed). Nothing is "assumed indexed".
- Embeddings come from the local Ollama embeddinggemma-2:270m model only. If Ollama is
  unreachable the run aborts; no random/placeholder vectors are written.
- Edges are derived, not invented: `next` (adjacent chunks in a file), `mentions`
  (chunk text names another corpus file), `semantic` (cosine kNN above a threshold).
"""
import hashlib
import json
import math
import os
import sqlite3
import struct
import sys
import time
import urllib.request

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "brnestrm-corpus")
DB = os.path.join(ROOT, ".brnestrm", "graphrag.sqlite3")
OLLAMA = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
MODEL = "embeddinggemma-2:270m"
MAX_EMBED_CHARS = 6000
KNN = 3
SEM_THRESHOLD = 0.70


def embed(text):
    body = json.dumps({"model": MODEL, "input": text[:MAX_EMBED_CHARS]}).encode()
    req = urllib.request.Request(f"{OLLAMA}/api/embed", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        vec = json.loads(r.read())["embeddings"][0]
    if not vec:
        raise RuntimeError("empty embedding")
    return vec


def cos(a, b):
    d = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return d / (na * nb) if na and nb else 0.0


def title_of(text, path):
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#"):
            return s.lstrip("#").strip()[:120]
    return os.path.basename(path)


def main():
    hy = os.path.join(ROOT, ".hyodo")
    with open(os.path.join(hy, "folder-manifest.json"), "r", encoding="utf-8") as f:
        folder = json.load(f)
    with open(os.path.join(hy, "chunks-manifest.json"), "r", encoding="utf-8") as f:
        chunks = json.load(f)["chunks"]
    by_digest = {f["digest"]: f["path"] for f in folder["files"] if not f.get("ignored")}

    os.makedirs(os.path.dirname(DB), exist_ok=True)
    tmp_db = f"{DB}.tmp.{os.getpid()}"
    if os.path.exists(tmp_db):
        os.remove(tmp_db)
    con = sqlite3.connect(tmp_db)
    os.chmod(tmp_db, 0o600)

    try:
        con.executescript(
            """
            PRAGMA user_version = 1;
            CREATE TABLE graph_nodes (id TEXT PRIMARY KEY, path TEXT, byte_start INT, byte_end INT,
                                      chunk_digest TEXT, title TEXT, dim INT, embedding BLOB);
            CREATE TABLE graph_edges (src TEXT, dst TEXT, kind TEXT, weight REAL);
            CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
            """
        )

        t0 = time.time()
        nodes, skipped = [], []
        for c in chunks:
            path = by_digest.get(c["file_digest"])
            if not path:
                skipped.append((c["chunk_id"], "file digest not in folder manifest"))
                continue
            s, e = c["byte_range"]
            with open(os.path.join(ROOT, path), "rb") as fh:
                fh.seek(s)
                raw = fh.read(e - s)
            if hashlib.sha256(raw).hexdigest()[: len(c["chunk_digest"])] != c["chunk_digest"]:
                skipped.append((c["chunk_id"], "chunk digest mismatch"))
                continue
            text = raw.decode("utf-8", errors="replace")
            vec = embed(text)
            nodes.append({"id": c["chunk_id"], "path": path, "s": s, "e": e,
                          "digest": c["chunk_digest"], "title": title_of(text, path),
                          "text": text, "vec": vec})
            con.execute("INSERT INTO graph_nodes VALUES (?,?,?,?,?,?,?,?)",
                        (c["chunk_id"], path, s, e, c["chunk_digest"], nodes[-1]["title"],
                         len(vec), struct.pack(f"{len(vec)}f", *vec)))

        edges = []
        # next: adjacent chunks within one file
        by_path = {}
        for n in nodes:
            by_path.setdefault(n["path"], []).append(n)
        for lst in by_path.values():
            lst.sort(key=lambda n: n["s"])
            for a, b in zip(lst, lst[1:]):
                edges.append((a["id"], b["id"], "next", 1.0))
        # mentions: chunk text names another corpus file (by basename)
        first_chunk = {p: lst[0]["id"] for p, lst in by_path.items()}
        for n in nodes:
            for p, cid in first_chunk.items():
                if p != n["path"] and os.path.basename(p) in n["text"]:
                    edges.append((n["id"], cid, "mentions", 1.0))
        # semantic kNN across files
        for n in nodes:
            scored = sorted(((cos(n["vec"], m["vec"]), m["id"]) for m in nodes
                             if m["path"] != n["path"]), reverse=True)[:KNN]
            for sim, mid in scored:
                if sim >= SEM_THRESHOLD:
                    edges.append((n["id"], mid, "semantic", round(sim, 4)))
        con.executemany("INSERT INTO graph_edges VALUES (?,?,?,?)", edges)

        meta = {
            "model": MODEL, "dim": str(len(nodes[0]["vec"])) if nodes else "0",
            "indexed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "hyodo_generated_at": folder.get("generated_at", ""),
            "chunks_in_manifest": str(len(chunks)), "chunks_indexed": str(len(nodes)),
            "chunks_skipped": json.dumps(skipped), "edges": str(len(edges)),
            "seconds": f"{time.time() - t0:.1f}",
        }
        con.executemany("INSERT INTO meta VALUES (?,?)", meta.items())
        con.commit()

        # Pre-swap verification (Fail-closed: never overwrite active DB with broken or empty index)
        integrity = [r[0] for r in con.execute("PRAGMA integrity_check").fetchall()]
        if integrity != ["ok"]:
            raise RuntimeError(f"Index integrity check failed: {integrity}")

        node_count = con.execute("SELECT COUNT(*) FROM graph_nodes").fetchone()[0]
        edge_count = con.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]
        if node_count == 0 or len(nodes) == 0:
            raise RuntimeError("Index verification failed: 0 nodes generated")

        # Verify no broken edge references
        orphan_edges = con.execute(
            """
            SELECT COUNT(*) FROM graph_edges e
            WHERE e.src NOT IN (SELECT id FROM graph_nodes)
               OR e.dst NOT IN (SELECT id FROM graph_nodes)
            """
        ).fetchone()[0]
        if orphan_edges > 0:
            raise RuntimeError(f"Index verification failed: {orphan_edges} orphan edges found")

        # Fail-closed if any chunks were skipped (manifest mismatch / corrupt chunk bytes)
        if skipped:
            print(f"Index rebuild aborted: {len(skipped)} chunks skipped. Existing DB preserved untouched.", file=sys.stderr)
            con.close()
            if os.path.exists(tmp_db):
                os.remove(tmp_db)
            return 1

        con.close()

        # Atomic swap: replaces existing DB only after full verification succeeds and 0 chunks skipped
        os.replace(tmp_db, DB)
        os.chmod(DB, 0o600)

    except Exception:
        try:
            con.close()
        except Exception:
            pass
        if os.path.exists(tmp_db):
            os.remove(tmp_db)
        raise
    os.chmod(DB, 0o600)

    kinds = {}
    for _, _, k, _ in edges:
        kinds[k] = kinds.get(k, 0) + 1
    print(json.dumps({**meta, "edge_kinds": kinds, "db": DB}, indent=1))
    return 0 if not skipped else 1


if __name__ == "__main__":
    sys.exit(main())
