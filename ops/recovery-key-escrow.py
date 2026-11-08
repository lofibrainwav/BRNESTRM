#!/usr/bin/env python3
"""Encrypted key escrow for BRNESTRM disaster recovery.

Do NOT paste the passphrase or the 32-byte recovery key into chats/email.
Operator enters passphrase directly in the Mac terminal; no background escrow.
"""
from pathlib import Path
import argparse,base64,datetime,getpass,hashlib,json,os,secrets,sys,tempfile
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

DEFAULT_KEY=Path.home()/"Library/Application Support/BRNESTRM/recovery-key-v1.bin"
DEFAULT_DRIVE=Path(os.environ.get('BRNESTRM_GDRIVE_RECOVERY_PATH',str(Path.home()/'BRNESTRM_RECOVERY_FOLDER_NOT_CONFIGURED')))
NAME="brnestrm-recovery-key-escrow-v1.json"
AAD=b"BRNESTRM_RECOVERY_KEY_ESCROW_V1"
ITERATIONS=650_000

def b64(b):return base64.b64encode(b).decode('ascii')
def un64(s):return base64.b64decode(s,validate=True)
def derive(passphrase,salt):
    if len(passphrase)<16:raise RuntimeError("passphrase_too_short")
    kdf=PBKDF2HMAC(algorithm=hashes.SHA256(),length=32,salt=salt,iterations=ITERATIONS)
    return kdf.derive(passphrase.encode('utf-8'))
def envelope(secret,passphrase):
    if len(secret)!=32:raise RuntimeError("invalid_recovery_key_length")
    salt=secrets.token_bytes(16);nonce=secrets.token_bytes(12)
    ciphertext=AESGCM(derive(passphrase,salt)).encrypt(nonce,secret,AAD)
    return {'version':1,'format':'BRNESTRM_KEY_ESCROW_AES256_GCM',
            'kdf':'PBKDF2-HMAC-SHA256','iterations':ITERATIONS,
            'salt':b64(salt),'nonce':b64(nonce),'ciphertext':b64(ciphertext),
            'createdAt':datetime.datetime.now().astimezone().isoformat(),
            'warning':'Store the separate passphrase in an independent password manager.'}
def decrypt(record,passphrase):
    if record.get('version')!=1 or record.get('iterations')!=ITERATIONS or record.get('format')!='BRNESTRM_KEY_ESCROW_AES256_GCM':
        raise RuntimeError("invalid_format")
    salt=un64(record['salt']);nonce=un64(record['nonce'])
    ct=un64(record['ciphertext'])
    if len(salt)!=16 or len(nonce)!=12 or len(ct)!=48:
        raise RuntimeError("invalid_ciphertext_structure")
    return AESGCM(derive(passphrase,salt)).decrypt(nonce,ct,AAD)
def prompt(verify=False):
    if not sys.stdin.isatty():raise RuntimeError("interactive_terminal_required")
    first=getpass.getpass("Separate recovery escrow passphrase (not your Mac login): ")
    if len(first)<16:raise RuntimeError("passphrase_too_short")
    if not verify:
        second=getpass.getpass("Confirm the same passphrase: ")
        if first!=second:raise RuntimeError("passphrase_confirmation_mismatch")
    return first
def secure_read(path):
    if not path.is_file() or path.is_symlink() or path.stat().st_size!=32 or path.stat().st_mode & 0o077:
        raise RuntimeError("recovery_key_missing_or_insecure_permissions")
    return path.read_bytes()
def atomic_create(dest,record):
    if dest.exists():raise RuntimeError("escrow_already_exists_refuse_overwrite")
    if not dest.parent.is_dir():raise RuntimeError("escrow_directory_missing")
    tmp=dest.with_name(dest.name+".partial-"+secrets.token_hex(5))
    fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(fd,'w') as f:
            json.dump(record,f,indent=2)
            f.flush();os.fsync(f.fileno())
        if dest.exists():raise RuntimeError("escrow_created_concurrently")
        os.replace(tmp,dest)
    finally:tmp.unlink(missing_ok=True)
    if dest.stat().st_mode & 0o077:raise RuntimeError("escrow_file_permissions_invalid")
def create(keyfile,escrowfile):
    passphrase=prompt(False)
    key=secure_read(keyfile)
    record=envelope(key,passphrase)
    if decrypt(record,passphrase)!=key:raise RuntimeError("local_escrow_roundtrip_mismatch")
    atomic_create(escrowfile,record)
    verifyfile=json.loads(escrowfile.read_text())
    if decrypt(verifyfile,passphrase)!=key:raise RuntimeError("written_escrow_roundtrip_mismatch")
    print("ENCRYPTED_KEY_ESCROW_CREATED_AND_VERIFIED",
          "sha256="+hashlib.sha256(escrowfile.read_bytes()).hexdigest(),
          "no_plaintext_key_uploaded=True")
    print("OWNER_ACTION: Keep the passphrase in an independent secure password manager.")
def verify(keyfile,escrowfile):
    passphrase=prompt(True)
    current=secure_read(keyfile)
    restored=decrypt(json.loads(escrowfile.read_text()),passphrase)
    if restored!=current:raise RuntimeError("escrow_key_does_not_match_local_key")
    print("KEY_ESCROW_ROUNDTRIP_VERIFIED")
def restore(keyfile,escrowfile):
    if keyfile.exists():raise RuntimeError("key_already_exists_refuse_overwrite")
    passphrase=prompt(True)
    secret=decrypt(json.loads(escrowfile.read_text()),passphrase)
    if len(secret)!=32:raise RuntimeError("decryption_invalid")
    keyfile.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd=os.open(keyfile,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'wb') as out:
        out.write(secret);out.flush();os.fsync(out.fileno())
    if secure_read(keyfile)!=secret:raise RuntimeError("restored_key_verification_failed")
    print("RECOVERY_KEY_RESTORED_TO_PRIVATE_LOCAL_FILE")
if __name__=='__main__':
    ap=argparse.ArgumentParser(description="Interactive AES-256-GCM recovery key escrow")
    ap.add_argument('action',choices=['create','verify','restore'])
    ap.add_argument('--key-file',default=str(DEFAULT_KEY))
    ap.add_argument('--escrow-file',default=str(Path(os.environ.get('BRNESTRM_GDRIVE_RECOVERY_PATH',str(DEFAULT_DRIVE)))/NAME))
    args=ap.parse_args()
    try:
        {'create':create,'verify':verify,'restore':restore}[args.action](Path(args.key_file),Path(args.escrow_file))
    except Exception as e:
        # No passphrase, key material, or exception message is printed.
        print("KEY_ESCROW_OPERATION_FAILED",type(e).__name__,file=sys.stderr)
        raise SystemExit(2)
