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


def check_reboot_evidence(agents):
    """Examine boottime vs process lstart to measure reboot recovery without guessing."""
    try:
        import re
        boot_out = subprocess.check_output(['sysctl', '-n', 'kern.boottime'], timeout=3).decode()
        m = re.search(r'sec\s*=\s*(\d+)', boot_out)
        if not m:
            return {'status': 'UNKNOWN_BOOTTIME'}
        boot_sec = int(m.group(1))
        boot_dt = datetime.datetime.fromtimestamp(boot_sec)

        delays = {}
        for name, info in agents.items():
            pid = info.get('pid')
            if not pid:
                delays[name] = None
                continue
            try:
                out = subprocess.check_output(['ps', '-p', str(pid), '-o', 'lstart='], timeout=3, env={**os.environ, 'LC_ALL': 'C'}).decode().strip()
                dt = datetime.datetime.strptime(out, '%a %b %d %H:%M:%S %Y')
                delays[name] = round((dt - boot_dt).total_seconds())
            except Exception:
                delays[name] = None

        sup = delays.get('supervised')
        alt = delays.get('alert')
        herm = delays.get('hermes')

        detail = {
            'bootTime': boot_dt.isoformat(),
            'delaysSecondsAfterBoot': delays,
        }
        # A process start close to boot is NOT proof of automatic startup:
        # it may have been started manually. Only the armed, fresh-boot
        # LaunchAgent observer is eligible to complete this gate.
        try:
            armed = json.loads((H/'reboot-verifier-armed.json').read_text())
            witness = json.loads((H/'reboot-verifier-receipt.json').read_text())
            armed_epoch = int(armed['armedAtEpoch'])
            if boot_sec <= armed_epoch:
                detail['status'] = 'PENDING_FRESH_REBOOT'
                detail['note'] = 'Observer armed after this boot; a fresh reboot is required'
            elif (witness.get('contract')=='BRNESTRM_POST_REBOOT_V1'
                  and witness.get('armedAtEpoch')==armed_epoch
                  and witness.get('bootEpoch')==boot_sec
                  and witness.get('status')=='PASS'
                  and witness.get('bootAgeSeconds',10**9)<=600
                  and all(witness.get('agentChecks',{}).get(k) is True for k in (
                    'com.local.brnestrm.supervised',
                    'com.local.brnestrm.health-monitor',
                    'com.local.brnestrm.hermes-llama',
                    'com.local.brnestrm.daily-backup','readiness'))):
                detail['status'] = 'VERIFIED_CLEAN_REBOOT_AUTO_RECOVERY'
                detail['note'] = 'Fresh user-login boot witnessed all required services and READY'
            else:
                detail['status'] = 'PENDING_POST_REBOOT_OBSERVER'
                detail['note'] = 'No matching successful fresh-boot observer receipt'
        except Exception:
            detail['status'] = 'UNVERIFIED_POST_REBOOT_OBSERVER'
        if herm is not None and herm > 360:
            detail['hermesStartDelayed'] = True
        detail['verificationScope'] = 'POST_USER_LOGIN_ONLY'

        return detail
    except Exception as e:
        return {'status': 'REBOOT_CHECK_ERROR', 'error': str(e)}


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
 sup_agent=launch('com.local.brnestrm.supervised')
 alert_agent=launch('com.local.brnestrm.health-monitor')
 hermes_agent=launch('com.local.brnestrm.hermes-llama')
 reboot_evidence=check_reboot_evidence({
  'supervised':sup_agent,
  'alert':alert_agent,
  'hermes':hermes_agent,
 })
 summary={'checkedAt':datetime.datetime.now().astimezone().isoformat(),
    'activeRuntime':str(LIVE),'developmentSource':str(SOURCE),
    'supervisedAgent':sup_agent,
    'alertAgent':alert_agent,
    'hermesLlamaAgent':hermes_agent,
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
    'completeMacRebootTest':reboot_evidence,
    'supervisionScope':'AT_USER_LOGIN_NOT_PRELOGIN'}
 print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
