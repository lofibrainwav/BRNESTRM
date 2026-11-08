#!/usr/bin/env python3
"""Authoritative read-only BRNESTRM deployment and recovery status inspector."""
from pathlib import Path
import datetime,hashlib,json,os,subprocess,urllib.request
H=Path.home()/'Library/Application Support/BRNESTRM'
LIVE=H/'runtime'
SOURCE=Path(os.environ.get('BRNESTRM_DEV_SOURCE',str(Path.home()/'Desktop/te-design-house')))
STATE=LIVE/'.brnestrm'
def sha(p):
 h=hashlib.sha256()
 with Path(p).open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def launch(name):
 p=subprocess.run(['launchctl','print',f'gui/{os.getuid()}/{name}'],
                  capture_output=True,text=True,timeout=5)
 if p.returncode:return {'registered':False}
 ids=[x.strip().split('=',1)[-1].strip() for x in p.stdout.splitlines()
      if x.strip().startswith('pid =')]
 return {'registered':True,'pid':int(ids[0]) if ids else None}
def parse_operational_time(value):
    """Gateway uses -0800, migration marker uses -08:00; support Python 3.9."""
    try:
        return datetime.datetime.fromisoformat(value)
    except (ValueError,TypeError):
        return datetime.datetime.strptime(value,"%Y-%m-%dT%H:%M:%S%z")


def post_cutover_e2e(records,cutover,chain_healthy):
    """Evidence-only status. No user text, bearer tokens or prompts leave Sillok."""
    if not chain_healthy:
        return {"status":"UNVERIFIED_AUDIT_CHAIN","loginAt":None,"knowledgeCompletedAt":None}
    try:
        baseline=parse_operational_time(cutover)
    except (ValueError,TypeError):
        return {"status":"UNVERIFIED_CUTOVER_TIMESTAMP","loginAt":None,"knowledgeCompletedAt":None}
    login=None
    started=None
    for record in records:
        try:
            ts=parse_operational_time(record.get("timestamp",""))
        except (ValueError,TypeError):
            continue
        if ts<=baseline:
            continue
        target=record.get("target")
        outcome=record.get("synthesis")
        if target=="auth.login" and outcome=="SUCCESS":
            login=ts
            started=None
        elif target=="agent.knowledge" and outcome=="STARTED" and login and ts>=login:
            started=ts
        elif target=="agent.knowledge.complete" and outcome=="SUCCESS" and login and started and ts>=started:
            return {"status":"AUDITED_AUTH_AND_KNOWLEDGE_SUCCESS",
                    "loginAt":login.isoformat(),"knowledgeCompletedAt":ts.isoformat()}
    return {"status":"NO_COMPLETED_AUTHENTICATED_KNOWLEDGE_SEQUENCE",
            "loginAt":login.isoformat() if login else None,"knowledgeCompletedAt":None}


def main():
 release=json.loads((STATE/'latest-validated-release.json').read_text())
 watch=json.loads((STATE/'supervisor-status.json').read_text())
 monitor=json.loads((H/'health-alert-state.json').read_text())
 cutover=json.loads((STATE/'ACTIVE_RUNTIME.json').read_text())['changedAt']
 rows=[json.loads(line) for line in (STATE/'sillok-ledger.jsonl').read_text().splitlines() if line.strip()]
 with urllib.request.urlopen('http://127.0.0.1:8788/api/health/readiness',timeout=9) as response:
  readiness_snapshot=json.load(response)
 acceptance=post_cutover_e2e(rows,cutover,
                             readiness_snapshot.get('checks',{}).get('audit_chain')=='HEALTHY')

 ready=readiness_snapshot
 drift=[]
 for name,digest in release['files'].items():
  file=SOURCE/name
  if not file.is_file() or sha(file)!=digest:drift.append(name)
 source_matches=not drift
 runtime_matches=all(sha(LIVE/name)==h for name,h in release['files'].items())
 copied_ledger=sha(STATE/'sillok-ledger.jsonl')
 copy_index=sha(LIVE/'brnestrm-corpus/.brnestrm/graphrag.sqlite3')
 backup_dir=Path(os.environ.get('BRNESTRM_GDRIVE_RECOVERY_PATH',str(H/'UNCONFIGURED_CLOUD_SYNC')))
 backups=sorted(backup_dir.glob('*.aesgcm'))
 summary={'checkedAt':datetime.datetime.now().astimezone().isoformat(),
    'activeRuntime':str(LIVE),'developmentSource':str(SOURCE),
    'supervisedAgent':launch('com.local.brnestrm.supervised'),
    'alertAgent':launch('com.local.brnestrm.health-monitor'),
    'scheduledDailyBackupAgent':launch('com.local.brnestrm.daily-backup'),
    'gatewayPid':watch.get('pid'),'gatewayState':watch.get('status'),
    'readiness':ready.get('overall'),'componentChecks':ready.get('checks'),
    'monitorPhase':monitor.get('phase'),
    'latestScheduledBackup':json.loads((H/'latest-backup-status.json').read_text()) if (H/'latest-backup-status.json').is_file() else {'result':'NO_BACKUP_STATUS'},
    'releaseVerifiedSourceMatches':runtime_matches,
    'developerSourceMatchesDeployedVersion':source_matches,
    'developerSourceDrift':drift,
    'operationalLedgerMatchesReleaseSnapshot':copied_ledger==release['ledgerSha'],
    'operationalIndexMatchesReleaseSnapshot':copy_index==release['indexSha'],
    'remoteGoogleDriveMountedEncryptedArchives':len(backups),
    'encryptedRecoveryKeyOnSeparateDevice':'UNVERIFIED_OWNER_ACTION_REQUIRED',
    'actualTouchIDOnNewLaunchAgent':acceptance['status'],
    'postCutoverKnowledgeEvidence':acceptance,
    'completeMacRebootTest':'NOT_RUN',
    'supervisionScope':'AT_USER_LOGIN_NOT_PRELOGIN'}
 print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
