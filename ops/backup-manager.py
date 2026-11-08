#!/usr/bin/env python3
"""Auditable BRNESTRM AES-256-GCM recovery snapshot and isolated restore drill.

Encrypts the complete source tree before writing into iCloud Drive. Never embeds
the local recovery key in a cloud archive. The key MUST be separately escrowed
outside the Mac by the device owner for disaster recovery.
"""
from pathlib import Path
import os,sys,tarfile,tempfile,hashlib,secrets,json,datetime,shutil,fcntl
from cryptography.hazmat.primitives.ciphers import Cipher,algorithms,modes
ROOT=Path.home()/'Library/Application Support/BRNESTRM/runtime'
HOME=Path.home()/'Library/Application Support/BRNESTRM'
KEY=HOME/'recovery-key-v1.bin'
BACKUPS=Path.home()/'Library/Mobile Documents/com~apple~CloudDocs/BRNESTRM Encrypted Recovery'
MAGIC=b'BRNESTRM-AES256-GCM-v1\n'
CRITICAL=('.brnestrm/sillok-ledger.jsonl','brnestrm-corpus/.brnestrm/graphrag.sqlite3',
          'brnestrm-gateway.py','brnestrm_session_supervisor.py','integration-handoff/gemini/candidate_auth_authority.py')
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def safe_files(root):
 for p in sorted(root.rglob('*')):
  if p.is_symlink():
   continue
  if p.is_file() and '__pycache__' not in p.parts and not p.name.endswith(('.pyc','.tmp','.lock')):
   yield p
def key():
 HOME.mkdir(parents=True,exist_ok=True,mode=0o700)
 if not KEY.exists():
  fd=os.open(str(KEY),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
  try:
   with os.fdopen(fd,'wb') as out:out.write(secrets.token_bytes(32))
  except:
   KEY.unlink(missing_ok=True)
   raise
 if not KEY.is_file() or KEY.stat().st_mode&0o077 or KEY.stat().st_size!=32:
  raise RuntimeError('recovery_key_permissions_or_length_invalid')
 return KEY.read_bytes()
def archive():
 if not ROOT.is_dir() or ROOT.is_symlink():raise RuntimeError('source_unavailable')
 BACKUPS.mkdir(mode=0o700,parents=True,exist_ok=True)
 check={name:sha(ROOT/name) for name in CRITICAL}
 created=datetime.datetime.now().astimezone()
 label=created.strftime('%Y%m%d-%H%M%S')
 temp=Path(tempfile.mkdtemp(prefix='backup-build-',dir=HOME))
 try:
  packed=temp/'runtime.tar.gz'
  files=list(safe_files(ROOT))
  with tarfile.open(packed,'w:gz',dereference=False) as ar:
   for p in files:
    member=ar.gettarinfo(str(p),arcname=p.relative_to(ROOT).as_posix())
    # macOS copies may contain hardlinked regular files. Store their
    # independent bytes rather than omitting link-only members.
    if member.islnk():
     member.type=tarfile.REGTYPE
     member.linkname=''
     member.size=p.stat().st_size
    if not member.isfile():continue
    with p.open('rb') as stream:ar.addfile(member,stream)
  # A concurrent change invalidates the archive, rather than storing a
  # coherent-looking snapshot that was actually assembled across two states.
  if check!={name:sha(ROOT/name) for name in CRITICAL}:
   raise RuntimeError('critical_files_changed_during_archive')
  nonces=secrets.token_bytes(12)
  encryptor=Cipher(algorithms.AES(key()),modes.GCM(nonces)).encryptor()
  filename='brnestrm-'+label+'.tar.gz.aesgcm'
  partial=BACKUPS/(filename+'.partial')
  dest=BACKUPS/filename
  with packed.open('rb') as src,partial.open('xb') as output:
   output.write(MAGIC+nonces)
   for block in iter(lambda:src.read(1024*1024),b''):
    output.write(encryptor.update(block))
   output.write(encryptor.finalize()+encryptor.tag)
   output.flush();os.fsync(output.fileno())
  partial.replace(dest)
  metadata={'format':'BRNESTRM_AES256_GCM_V1','createdAt':created.isoformat(),
    'encryptedArchive':str(dest),'archiveSha256':sha(dest),'bytes':dest.stat().st_size,
    'archivedFileCount':len(files),'criticalBefore':check,
    'encryption':'AES-256-GCM with unique 96-bit nonce',
    'recoveryKey':'USER_MAC_LOCAL_ONLY_NOT_ESCROWED','cloudSync':'UNVERIFIED'}
  summary=BACKUPS/(filename+'.manifest.json')
  summary.write_text(json.dumps(metadata,indent=2))
  os.chmod(summary,0o600)
  print('BACKUP_CREATED',json.dumps({'encryptedBytes':dest.stat().st_size,
       'fileCount':len(files),'criticalShaPrefix':{n:v[:12] for n,v in check.items()},
       'cloudSync':'UNVERIFIED','keyOffMac':'NO'},ensure_ascii=False),flush=True)
  return dest,metadata
 finally:shutil.rmtree(temp,ignore_errors=True)
def restore_drill(archive,metadata):
 # All recovered bytes stay in an isolated temp area, never in live source.
 temp=Path(tempfile.mkdtemp(prefix='backup-recovery-test-',dir=HOME))
 try:
  pack=temp/'plain.tar.gz'
  with Path(archive).open('rb') as src,pack.open('wb') as output:
   header=src.read(len(MAGIC)+12)
   if header[:len(MAGIC)]!=MAGIC:raise RuntimeError('invalid_archive_version')
   nonce=header[len(MAGIC):]
   size=Path(archive).stat().st_size
   if size<len(MAGIC)+12+16:raise RuntimeError('archive_too_small')
   src.seek(size-16)
   tag=src.read(16)
   src.seek(len(MAGIC)+12)
   decryptor=Cipher(algorithms.AES(key()),modes.GCM(nonce,tag)).decryptor()
   length=size-(len(MAGIC)+12+16)
   while length:
    chunk=src.read(min(1024*1024,length));length-=len(chunk)
    if not chunk:raise RuntimeError('short_encrypted_archive')
    output.write(decryptor.update(chunk))
   output.write(decryptor.finalize())
  restored=temp/'restored';restored.mkdir()
  count=0
  with tarfile.open(pack,'r:gz') as ar:
   for info in ar:
    if not info.isfile() or info.name.startswith('/') or '..' in Path(info.name).parts:
     raise RuntimeError('unsafe_tar_member')
    dest=(restored/info.name).resolve()
    if not dest.is_relative_to(restored.resolve()):
     raise RuntimeError('archive_path_escape')
    dest.parent.mkdir(parents=True,exist_ok=True)
    with ar.extractfile(info) as i,dest.open('wb') as o:shutil.copyfileobj(i,o)
    count+=1
  if count!=metadata['archivedFileCount']:
   raise RuntimeError('restore_file_count_mismatch')
  matches={name:sha(restored/name)==expected for name,expected in metadata['criticalBefore'].items()}
  if not all(matches.values()):raise RuntimeError('restored_files_not_identical')
  print('ISOLATED_RESTORE_PASS',json.dumps({'filesRestored':count,'criticalHashMatches':matches,'liveFilesChanged':False},ensure_ascii=False),flush=True)
  return True
 finally:shutil.rmtree(temp,ignore_errors=True)
def mirror_personal_drive(archive_path,manifest):
 target=Path(os.environ['BRNESTRM_GDRIVE_RECOVERY_PATH'])
 if not target.is_dir():raise RuntimeError('personal_drive_backup_folder_missing')
 transferred=[]
 for f in (Path(archive_path),Path(str(archive_path)+'.manifest.json')):
  dest=target/f.name
  if dest.is_file():
   if sha(dest)!=sha(f):raise RuntimeError('existing_personal_drive_file_conflicts')
  else:
   temp=target/(f.name+'.partial')
   with f.open('rb') as src,temp.open('xb') as out:
    shutil.copyfileobj(src,out,1024*1024)
   os.replace(temp,dest)
  if sha(dest)!=sha(f):raise RuntimeError('personal_drive_local_bytes_mismatch')
  transferred.append(dest.name)
 print('PERSONAL_GOOGLE_DRIVE_LOCAL_MIRROR_VERIFIED',transferred,flush=True)
 return transferred


def run_scheduled():
 HOME.mkdir(mode=0o700,parents=True,exist_ok=True)
 fd=os.open(HOME/'backup.singleton.lock',os.O_CREAT|os.O_RDWR,0o600)
 try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError:
  os.close(fd)
  print('BACKUP_ALREADY_RUNNING',flush=True)
  return 2
 status_path=HOME/'latest-backup-status.json'
 try:
  backup_path,manifest=archive()
  assert restore_drill(backup_path,manifest)
  mirrored=mirror_personal_drive(backup_path,manifest)
  status={'result':'ENCRYPTED_BACKUP_VERIFIED','time':datetime.datetime.now().astimezone().isoformat(),
   'source':'LIBRARY_RUNTIME','archivedFiles':manifest['archivedFileCount'],
   'sha256':manifest['archiveSha256'],'encryptedArchiveName':Path(backup_path).name,
   'localRestore':'PASS','personalGoogleDriveMountedMirror':'HASH_MATCH',
   'remoteProviderByteReadback':'UNVERIFIED_BY_SCHEDULER',
   'externalKeyEscrow':'OWNER_ACTION_REQUIRED','mirroredFiles':mirrored}
  temp=status_path.with_suffix('.tmp');temp.write_text(json.dumps(status,indent=2))
  os.chmod(temp,0o600);os.replace(temp,status_path)
  print('BACKUP_SCHEDULED_RUN_PASS',status['sha256'],flush=True)
  return 0
 except Exception as err:
  status={'result':'BACKUP_FAILED','time':datetime.datetime.now().astimezone().isoformat(),
          'errorType':type(err).__name__}
  temp=status_path.with_suffix('.tmp');temp.write_text(json.dumps(status,indent=2))
  os.chmod(temp,0o600);os.replace(temp,status_path)
  print('BACKUP_SCHEDULED_RUN_FAILED',type(err).__name__,flush=True)
  return 3
 finally:
  fcntl.flock(fd,fcntl.LOCK_UN);os.close(fd)


if __name__=='__main__':
 raise SystemExit(run_scheduled())
