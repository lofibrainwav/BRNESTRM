"""Scoped Markdown-only source sync for BRNESTRM GraphRAG.

No credentials, hidden folders, symlinks, binary DOC/PDF, or arbitrary workspace
directories. --plan is read-only; --apply stages all changes before atomic index
swap, with verified backup of original DB and HyoDo manifests.
"""
from pathlib import Path
import argparse,fcntl,hashlib,importlib.util,json,os,re,secrets,shutil,sqlite3,subprocess,tempfile,time
ROOT=Path(__file__).resolve().parent
CORPUS=ROOT/"brnestrm-corpus"
DB=CORPUS/".brnestrm/graphrag.sqlite3"
HYODO=Path.home()/".local/bin/hyodo"
MAX_SIZE=256*1024
DANGEROUS=re.compile(r"(?i)(private.key|password|credentials|\.env|token[-_]?value|secret[-_]?value|access[-_]?key)")
NEVER_NAMES={"AGENTS.md","LICENCE.md","LICENSE.md"}
def catalog():
    files=[ROOT/x for x in ("README.md","te-design-index.md",
                             "te-design-philosophy.md","te-design-spec-v1.md")]
    files+=sorted((ROOT/"research_notes").rglob("*.md"))
    files+=sorted((ROOT/"te-design-package/components").glob("*.md"))
    eligible=[]; excluded=[]
    for file in sorted(set(files)):
        rel=file.relative_to(ROOT).as_posix()
        reason=None
        if not file.is_file() or file.is_symlink():reason="non_regular"
        elif file.name in NEVER_NAMES:reason="policy_excluded"
        elif DANGEROUS.search(rel):reason="sensitive_filename"
        elif file.stat().st_size>MAX_SIZE:reason="oversized"
        elif not file.resolve().is_relative_to(ROOT.resolve()):reason="escaped_root"
        else:
            raw=file.read_bytes()
            if b"-----BEGIN PRIVATE KEY-----" in raw or b"sk-proj-" in raw:reason="sensitive_content_pattern"
        if reason: excluded.append({"path":rel,"reason":reason})
        else: eligible.append({"path":rel,"sha256":hashlib.sha256(file.read_bytes()).hexdigest(),
                               "bytes":file.stat().st_size})
    return eligible,excluded
def manifest():
    eligible,excluded=catalog()
    return {"version":"BRNESTRM_SOURCE_POLICY_V1","selectedPaths":eligible,"excluded":excluded,
            "inputRoots":["research_notes/**/*.md","te-design-package/components/*.md",
                          "README.md","te-design-index.md","te-design-philosophy.md","te-design-spec-v1.md"],
            "binaryDocuments":"EXCLUDED_NOT_PARSED",
            "privateFiles":"EXCLUDED","isolatedStaging":True}
def write_atomic(target,source):
    """Durable single-file replacement; release manifest is committed last."""
    target=Path(target)
    target.parent.mkdir(parents=True,exist_ok=True)
    temp=target.with_name(target.name+".sync-"+str(os.getpid()))
    try:
        shutil.copy2(source,temp)
        with open(temp,"rb") as f:
            os.fsync(f.fileno())
        os.replace(temp,target)
        fd=os.open(target.parent,os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
    finally:
        if temp.exists():temp.unlink()


def publish_generation(stage_generation, destination_generation, new_db, live_db, expected_sha):
    """Old DB and all its source bytes remain untouched until one atomic DB swap.

    New source directory is append-only and installed before the new DB.
    If interrupted here, its unreferenced directory is harmless; no live
    file from the previous generation is ever overwritten.
    """
    stage_generation=Path(stage_generation)
    destination_generation=Path(destination_generation)
    new_db=Path(new_db)
    live_db=Path(live_db)
    if not stage_generation.is_dir() or not new_db.is_file():
        raise RuntimeError("incomplete_staged_generation")
    if destination_generation.exists():
        raise RuntimeError("generation_collision")
    if hashlib.sha256(live_db.read_bytes()).hexdigest()!=expected_sha:
        raise RuntimeError("active_index_changed_during_build")
    destination_generation.parent.mkdir(parents=True,exist_ok=True)
    os.replace(stage_generation,destination_generation)
    # The ONLY publication point for live query results:
    write_atomic(live_db,new_db)


def run(apply=False):
    plan=manifest()
    print("SOURCE_POLICY",json.dumps({"eligible":len(plan["selectedPaths"]),
          "excluded":len(plan["excluded"]),"excludedExamples":plan["excluded"][:7],
          "binary":"EXCLUDED_NOT_PARSED"}),flush=True)
    if not apply:return
    if not HYODO.is_file() or not DB.is_file():
        raise RuntimeError("missing_existing_index_or_hyodo")
    lock=ROOT/".brnestrm/index.sync.lock"
    lock_fd=os.open(lock,os.O_CREAT|os.O_RDWR,0o600)
    try:
        fcntl.flock(lock_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(lock_fd)
        raise RuntimeError("INDEX_BUILD_ALREADY_IN_PROGRESS")
    stage=None
    try:
        old_db_sha=hashlib.sha256(DB.read_bytes()).hexdigest()
        backup=ROOT/".brnestrm"/("source-sync-backup-"+time.strftime("%Y%m%d-%H%M%S"))
        backup.mkdir(mode=0o700)
        for file in (DB,CORPUS/".hyodo/folder-manifest.json",CORPUS/".hyodo/chunks-manifest.json"):
            shutil.copy2(file,backup/file.name)
        stage=Path(tempfile.mkdtemp(prefix="source-stage-",dir=ROOT/".brnestrm"))
        gen_id="gen-"+time.strftime("%Y%m%d%H%M%S")+"-"+str(os.getpid())+"-"+secrets.token_hex(3)
        stage_gen=stage/"generations"/gen_id
        with sqlite3.connect("file:"+str(DB.resolve())+"?mode=ro",uri=True) as con:
            old_files=[r[0] for r in con.execute("SELECT DISTINCT path FROM graph_nodes")]
        # Preserve core documents but DO NOT duplicate the earlier generated
        # te-design-house sources; they are replaced by this immutable generation.
        for name in old_files:
            if name.startswith(("te-design-house/","generations/")):
                continue
            source=CORPUS/name
            dest=stage/name
            if not source.is_file() or source.is_symlink():
                raise RuntimeError("missing_old_source")
            dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source,dest)
        for item in plan["selectedPaths"]:
            source=ROOT/item["path"]
            if hashlib.sha256(source.read_bytes()).hexdigest()!=item["sha256"]:
                raise RuntimeError("concurrent_input_modification")
            dest=stage_gen/"te-design-house"/item["path"]
            dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source,dest)
        cmd=[str(HYODO),"inspect",str(stage),"--root",str(stage),
             "--ignore","*.sqlite3","--ignore","*.json","--ignore","*.png",
             "--ignore","*.DS_Store","--report","json"]
        process=subprocess.run(cmd,capture_output=True,text=True,timeout=60)
        if process.returncode!=0:raise RuntimeError("hyodo_inspect_failed")
        folder=json.loads((stage/".hyodo/folder-manifest.json").read_text())
        chunks=json.loads((stage/".hyodo/chunks-manifest.json").read_text())
        print("HYODO_STAGED",len(folder["files"]),"FILES",len(chunks["chunks"]),"CHUNKS",flush=True)
        spec=importlib.util.spec_from_file_location("sync_indexer",ROOT/"brnestrm-graphrag-index.py")
        idx=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(idx)
        idx.ROOT=str(stage);idx.DB=str(stage/".brnestrm/graphrag.sqlite3")
        result=idx.main()
        if result not in (None,0):raise RuntimeError("index_generation_failed")
        new_db=Path(idx.DB)
        with sqlite3.connect("file:"+str(new_db.resolve())+"?mode=ro",uri=True) as con:
            integrity=con.execute("PRAGMA integrity_check").fetchone()[0]
            nodes=con.execute("SELECT COUNT(*) FROM graph_nodes").fetchone()[0]
            edges=con.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]
            skipped=json.loads(dict(con.execute("SELECT k,v FROM meta"))["chunks_skipped"])
            meta=dict(con.execute("SELECT k,v FROM meta"))
        if integrity!="ok" or nodes<61 or skipped:
            raise RuntimeError("index_integrity_validation_failed")
        # Recheck source digests AFTER expensive embedding, before the swap.
        if any(hashlib.sha256((ROOT/x["path"]).read_bytes()).hexdigest()!=x["sha256"]
               for x in plan["selectedPaths"]):
            raise RuntimeError("input_modified_while_indexing")
        publish_generation(stage_gen,CORPUS/"generations"/gen_id,new_db,DB,old_db_sha)
        # Crash between publication and metadata update yields stale/unverified,
        # never a false CURRENT. Old files and the pre-cutover DB remain backed up.
        write_atomic(CORPUS/".hyodo/folder-manifest.json",stage/".hyodo/folder-manifest.json")
        write_atomic(CORPUS/".hyodo/chunks-manifest.json",stage/".hyodo/chunks-manifest.json")
        plan.update({"status":"APPLIED","nodes":nodes,"edges":edges,
                     "indexedAt":meta.get("indexed_at"),"activeGeneration":gen_id,
                     "backup":str(backup),
                     "indexSha256":hashlib.sha256(DB.read_bytes()).hexdigest()})
        plan_tmp=stage/"verified-scope.json"
        plan_tmp.write_text(json.dumps(plan,indent=2))
        write_atomic(ROOT/"integration-handoff/gemini/BRNESTRM_SOURCE_SCOPE.json",plan_tmp)
        print("INDEX_APPLIED",json.dumps({"nodes":nodes,"edges":edges,
              "generation":gen_id,"scopeEntries":len(plan["selectedPaths"]),
              "backup":str(backup)}),flush=True)
    finally:
        if stage is not None:shutil.rmtree(stage,ignore_errors=True)
        fcntl.flock(lock_fd,fcntl.LOCK_UN)
        os.close(lock_fd)
def check_freshness():
    scope=ROOT/"integration-handoff/gemini/BRNESTRM_SOURCE_SCOPE.json"
    if not scope.is_file() or not DB.is_file():
        return False,"MISSING_INDEX_OR_SCOPE"
    try:
        old=json.loads(scope.read_text())
        current=manifest()
        if current["selectedPaths"] != old.get("selectedPaths"):
            return False,"SOURCE_DIGEST_DRIFT"
        if hashlib.sha256(DB.read_bytes()).hexdigest() != old.get("indexSha256"):
            return False,"INDEX_DIGEST_DRIFT"
        return True,"CURRENT"
    except (OSError,ValueError):
        return False,"UNREADABLE_OR_INVALID_MANIFEST"


if __name__=="__main__":
    cli=argparse.ArgumentParser()
    cli.add_argument("--apply",action="store_true")
    cli.add_argument("--check",action="store_true")
    cli.add_argument("--sync-if-stale",action="store_true")
    args=cli.parse_args()
    if args.check or args.sync_if_stale:
        current,reason=check_freshness()
        print("SOURCE_FRESHNESS",reason,flush=True)
        if args.check:raise SystemExit(0 if current else 2)
        if current:raise SystemExit(0)
    run(args.apply or args.sync_if_stale)
