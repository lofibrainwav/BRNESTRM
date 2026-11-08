#!/usr/bin/env python3
"""Non-invasive BRNESTRM component monitor with local notification and audit journal.

Separate from the process supervisor; never restarts the Gateway, changes data,
uses credentials, sends external traffic or exposes user document contents.
"""
from pathlib import Path
import argparse,datetime,fcntl,json,os,subprocess,time,urllib.request
HOME=Path.home()/'Library/Application Support/BRNESTRM'
ROOT=HOME/'runtime'
SUPERVISOR=ROOT/'.brnestrm/supervisor-status.json'
STATE=HOME/'health-alert-state.json'
JOURNAL=HOME/'health-alerts.jsonl'
LOCK=HOME/'health-alert-monitor.lock'
URL='http://127.0.0.1:8788/api/health/readiness'
MIN_FAILURES=2
INTERVAL=30
def now():return datetime.datetime.now().astimezone().isoformat()
def probe():
 issues=[]
 try:
  with urllib.request.urlopen(URL,timeout=8) as result:
   data=json.load(result)
  if data.get('contract')!='BRNESTRM_READINESS_V1':
   issues.append('readiness_contract')
  else:
   issues.extend(str(k) for k,v in data.get('checks',{}).items()
                 if v not in ('CURRENT','HEALTHY'))
   if data.get('overall')!='READY' and not issues:issues.append('overall_degraded')
 except Exception:
  issues.append('gateway_readiness_unreachable')
 try:
  status=json.loads(SUPERVISOR.read_text())
  updated=datetime.datetime.fromisoformat(status['observedAt'])
  age=(datetime.datetime.now().astimezone()-updated).total_seconds()
  if not 0 <= age <= 55:issues.append('supervisor_stale')
  if status.get('status') not in ('HEALTHY','DEGRADED_DEPENDENCIES'):
   issues.append('supervisor_'+str(status.get('status','UNKNOWN')).lower())
 except Exception:
  issues.append('supervisor_missing')
 # A successful service process is not sufficient if backups silently stop.
 try:
  backup=json.loads((HOME/'latest-backup-status.json').read_text())
  updated=datetime.datetime.fromisoformat(backup['time'])
  age=(datetime.datetime.now().astimezone()-updated).total_seconds()
  if backup.get('result')!='ENCRYPTED_BACKUP_VERIFIED':
   issues.append('scheduled_backup_failed')
  elif not 0 <= age < 172800:
   issues.append('scheduled_backup_stale_over_48h')
  elif backup.get('personalGoogleDriveMountedMirror')!='HASH_MATCH':
   issues.append('personal_drive_backup_mirror_unverified')
 except Exception:
  issues.append('backup_status_missing')
 return sorted(set(issues))
def append_journal(event,issues,notice_attempted=None,notice_exit=None):
 HOME.mkdir(parents=True,exist_ok=True,mode=0o700)
 entry={'observedAt':now(),'event':event,'components':issues,
        'localNotificationAttempted':notice_attempted,
        'localNotificationExit':notice_exit,
        'externalDelivery':'NOT_CONFIGURED'}
 fd=os.open(JOURNAL,os.O_CREAT|os.O_WRONLY|os.O_APPEND,0o600)
 with os.fdopen(fd,'a',encoding='utf-8') as f:f.write(json.dumps(entry,ensure_ascii=False)+'\n')
 print('MONITOR',json.dumps(entry,ensure_ascii=False),flush=True)
def notify(title,desc):
 # One short, unprivileged Notification Center call. Notification permissions
 # and actual visibility are NOT assumed from the process return code.
 esc=lambda s:s.replace('\\','\\\\').replace('"','\\"')
 cmd='display notification "'+esc(desc[:140])+'" with title "'+esc(title[:80])+'"'
 try:
  run=subprocess.run(['/usr/bin/osascript','-e',cmd],
                    stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=7)
  return True,run.returncode
 except Exception:return True,-1
def step(issues,status,send=notify,journal=append_journal):
 fail=status.get('failures',0)
 healthy=status.get('healthy',0)
 phase=status.get('phase','INITIAL')
 if issues:
  fail+=1;healthy=0
  if fail>=MIN_FAILURES and phase!='DEGRADED':
   attempt,code=send('BRNESTRM DEGRADED',', '.join(issues))
   journal('DEGRADED',issues,attempt,code)
   phase='DEGRADED'
 else:
  healthy+=1;fail=0
  if healthy>=2 and phase=='DEGRADED':
   attempt,code=send('BRNESTRM RECOVERED','Component readiness checks returned READY')
   journal('RECOVERED',[],attempt,code)
   phase='READY'
  elif healthy>=2 and phase=='INITIAL':
   phase='READY'
 return {'phase':phase,'failures':fail,'healthy':healthy,
         'lastChecks':issues,'lastObservedAt':now()}
def load_state():
 try:
  state=json.loads(STATE.read_text())
  if not isinstance(state,dict):raise ValueError('bad_state')
  return state
 except Exception:return {'phase':'INITIAL','failures':0,'healthy':0}
def save(state):
 temp=STATE.with_suffix('.tmp')
 temp.write_text(json.dumps(state,indent=2))
 os.chmod(temp,0o600);os.replace(temp,STATE)
def run():
 HOME.mkdir(parents=True,exist_ok=True,mode=0o700)
 fd=os.open(LOCK,os.O_WRONLY|os.O_CREAT,0o600)
 try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError:
  print('ONE_MONITOR_ALREADY_RUNNING',flush=True);os.close(fd);return 0
 try:
  while True:
   issues=probe()
   updated=step(issues,load_state())
   save(updated)
   time.sleep(INTERVAL)
 finally:
  fcntl.flock(fd,fcntl.LOCK_UN);os.close(fd)
def self_test():
 seen=[]
 events=[]
 def fake(title,description):seen.append(title);return (True,0)
 def journal(*args):events.append(args)
 s={'phase':'INITIAL','failures':0,'healthy':0}
 s=step([],s,send=fake,journal=journal)
 s=step([],s,send=fake,journal=journal)
 assert s['phase']=='READY'
 s=step(['hermes_http'],s,send=fake,journal=journal);assert s['phase']=='READY'
 s=step(['hermes_http'],s,send=fake,journal=journal);assert s['phase']=='DEGRADED'
 s=step(['hermes_http'],s,send=fake,journal=journal);assert len(seen)==1
 s=step([],s,send=fake,journal=journal);assert s['phase']=='DEGRADED'
 s=step([],s,send=fake,journal=journal);assert s['phase']=='READY' and len(seen)==2
 assert [e[0] for e in events]==['DEGRADED','RECOVERED']
 print('MONITOR_INJECTED_FAILURE_TEST_PASS',seen,'NO_REAL_EVENTS_WRITTEN',True)
 return 0
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--once',action='store_true')
 ap.add_argument('--self-test',action='store_true')
 args=ap.parse_args()
 if args.self_test:raise SystemExit(self_test())
 if args.once:
  print('CHECK',{'issues':probe(),'at':now()},flush=True)
  raise SystemExit(0)
 raise SystemExit(run())
