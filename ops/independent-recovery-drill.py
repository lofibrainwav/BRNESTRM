#!/usr/bin/env python3
"""Recovery-only proof from cloud archive + encrypted escrow + passphrase.

Does NOT read the local raw recovery key, live BRNESTRM runtime, Sillok, or
Keychain. Decrypted tar and file contents live ONLY in RAM, never on disk.
Passphrase is entered at the operator's Mac terminal; never provide it to AI.
"""
import argparse
import datetime
import getpass
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import secrets
import sys
import tarfile
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC=b"BRNESTRM-AES256-GCM-v1\n"
MAX_ENCRYPTED=256*1024*1024

def escrow_module():
    candidate=Path(__file__).with_name("recovery-key-escrow.py")
    if not candidate.is_file():
        candidate=Path(__file__).parent/"recovery-key-escrow.py"
    spec=importlib.util.spec_from_file_location("local_escrow_codec",candidate)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def digest(buffer):
    return hashlib.sha256(buffer).hexdigest()

def inspect_restored_tar(tar_bytes,manifest):
    expected=manifest["criticalBefore"]
    if not isinstance(expected,dict) or len(expected)<3:
        raise ValueError("missing_critical_digest_list")
    count=0
    hashes={}
    names=set()
    total_plain=0
    with tarfile.open(fileobj=io.BytesIO(tar_bytes),mode="r:gz") as archive:
        for member in archive:
            name=member.name
            path=Path(name)
            if (not member.isfile() or name.startswith("/") or
                ".." in path.parts or name in names):
                raise ValueError("invalid_archive_member")
            names.add(name)
            if member.size<0 or member.size>2*1024*1024*1024:
                raise ValueError("oversized_archive_member")
            total_plain+=member.size
            if total_plain>4*1024*1024*1024:
                raise ValueError("oversized_decompressed_archive")
            stream=archive.extractfile(member)
            if stream is None:
                raise ValueError("unreadable_archive_member")
            h=hashlib.sha256()
            while True:
                chunk=stream.read(1024*1024)
                if not chunk:break
                h.update(chunk)
            if name in expected:hashes[name]=h.hexdigest()
            count+=1
    if count!=manifest["archivedFileCount"]:
        raise ValueError("archived_file_count_mismatch")
    if set(hashes)!=set(expected) or any(hashes[k]!=v for k,v in expected.items()):
        raise ValueError("critical_file_digest_mismatch")
    return {"count":count,"criticalMatches":len(expected),"allMembersAuthenticated":True,
            "noDecryptedFilesWritten":True}

def decrypt_archive(raw,key,manifest):
    if len(raw)<len(MAGIC)+12+16 or len(raw)>MAX_ENCRYPTED:
        raise ValueError("bad_archive_size")
    if not raw.startswith(MAGIC):
        raise ValueError("bad_archive_magic")
    if digest(raw)!=manifest["archiveSha256"] or len(raw)!=manifest["bytes"]:
        raise ValueError("archive_ciphertext_hash_mismatch")
    nonce=raw[len(MAGIC):len(MAGIC)+12]
    encrypted=raw[len(MAGIC)+12:]
    plaintext=AESGCM(key).decrypt(nonce,encrypted,None)
    return inspect_restored_tar(plaintext,manifest)

def real_drill():
    module=escrow_module()
    folder=Path(os.environ.get("BRNESTRM_GDRIVE_RECOVERY_PATH",str(module.DEFAULT_DRIVE)))
    escrow=folder/module.NAME
    if not folder.is_dir() or not escrow.is_file():
        raise RuntimeError("drive_escrow_unavailable")
    archives=sorted(folder.glob("brnestrm-*.tar.gz.aesgcm"))
    if not archives:raise RuntimeError("backup_archive_unavailable")
    archive=archives[-1]
    manifest_file=folder/(archive.name+".manifest.json")
    if not manifest_file.is_file():raise RuntimeError("backup_manifest_unavailable")
    if not sys.stdin.isatty():raise RuntimeError("must_run_on_owner_terminal")
    print("Independent drill: using encrypted archive and escrow only; NOT local raw key.")
    password=getpass.getpass("Recovery escrow passphrase (NOT shown or saved): ")
    try:
        recovered=module.decrypt(json.loads(escrow.read_text()),password)
        if len(recovered)!=32:raise RuntimeError("recovered_key_length_invalid")
        manifest=json.loads(manifest_file.read_text())
        with archive.open("rb") as handle:
            raw=handle.read(MAX_ENCRYPTED+1)
        result=decrypt_archive(raw,recovered,manifest)
    finally:
        password=""
    print("INDEPENDENT_ESCROW_ARCHIVE_RECOVERY_PASS",
          json.dumps({"archivedFiles":result["count"],
            "criticalDigestMatches":result["criticalMatches"],
            "allMembersAuthenticated":True,
            "usedLocalRawKey":False,"wrotePlaintextToDisk":False}))
    return 0

def self_test():
    module=escrow_module()
    key=secrets.token_bytes(32)
    passphrase="SYNTHETIC_IN_MEMORY_PASSPHRASE_FOR_UNIT_TEST_2026"
    envelope=module.envelope(key,passphrase)
    recovered=module.decrypt(envelope,passphrase)
    assert recovered==key
    content={"example/a.txt":b"synthetic alpha","example/b.txt":b"synthetic beta",
             "example/c.txt":b"synthetic gamma"}
    tar=io.BytesIO()
    with tarfile.open(fileobj=tar,mode="w:gz") as archive:
        for name,value in content.items():
            info=tarfile.TarInfo(name)
            info.size=len(value)
            archive.addfile(info,io.BytesIO(value))
    nonce=secrets.token_bytes(12)
    raw=MAGIC+nonce+AESGCM(recovered).encrypt(nonce,tar.getvalue(),None)
    manifest={"archiveSha256":digest(raw),"bytes":len(raw),
              "archivedFileCount":len(content),
              "criticalBefore":{n:digest(v) for n,v in content.items()}}
    assert decrypt_archive(raw,recovered,manifest)["count"]==3
    try:decrypt_archive(raw,recovered[:-1]+bytes([recovered[-1]^1]),manifest)
    except Exception:pass
    else:raise AssertionError("wrong key accepted")
    print("INDEPENDENT_RECOVERY_SYNTHETIC_TEST_PASS",
          "no_live_key_read","no_live_data_read","wrong_key_denied")
    return 0

if __name__=="__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--self-test",action="store_true")
    args=ap.parse_args()
    try:
        raise SystemExit(self_test() if args.self_test else real_drill())
    except Exception as e:
        print("INDEPENDENT_RECOVERY_NOT_VERIFIED",type(e).__name__,file=sys.stderr)
        raise SystemExit(2)
