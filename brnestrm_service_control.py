"""BRNESTRM one-writer, test-gated local Mac service controller.

Never accesses Hermes keys or Sillok private contents other than SHA-256.
No automatic deployment on source changes; explicit release required.
"""
from __future__ import annotations
import argparse
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import time
import urllib.request

ROOT=Path(__file__).resolve().parent
STATE=ROOT/".brnestrm"
LABEL="com.local.brnestrm.gateway"
PLIST=Path.home()/"Library/LaunchAgents"/(LABEL+".plist")
DOMAIN="gui/"+str(os.getuid())
SOURCE_NAMES=[
 "brnestrm-gateway.py","brnestrm_agent_runtime.py","te-agent-console.html",
 "brnestrm_source_sync.py","brnestrm_session_supervisor.py",
 "brnestrm_service_control.py","brnestrm_operational_health.py",
 "brnestrm-graphrag-index.py","hermes_status_provider.py",
 "integration-handoff/gemini/candidate_auth_authority.py",
 "integration-handoff/gemini/macos_local_auth_helper.swift",
 "integration-handoff/gemini/macos_local_auth_helper",
 "integration-handoff/gemini/BRNESTRM_SOURCE_SCOPE.json"
]
# The verification suite itself is part of the release input. Changing a test
# during/after a green run invalidates the signed-off hash manifest.
SOURCE_NAMES += sorted(str(p.relative_to(ROOT)) for p in (ROOT/"tests").glob("test_*.py"))
SOURCE_NAMES += sorted(str(p.relative_to(ROOT)) for p in (ROOT/"tests").glob("test_*.cjs"))
SOURCE_NAMES += sorted(str(p.relative_to(ROOT)) for p in (
    ROOT/"integration-handoff/gemini/operational_staging/gemini_a_workspace"
).glob("test_*.py"))
TESTS=[
 ([sys.executable,"-B","-W","error::ResourceWarning","-m","unittest","discover","-s","tests","-p","test_*.py","-q"],"python"),
 (["node","tests/test_ui_contract.cjs"],"ui_contract"),
 (["node","tests/test_console_reliability.cjs"],"ui_reliability"),
 ([sys.executable,"-B","-W","error::ResourceWarning","-m","unittest","discover",
   "-s","integration-handoff/gemini/operational_staging/gemini_a_workspace",
   "-p","test_*.py","-q"],"security_rollback"),
 ([sys.executable,"-B","brnestrm_source_sync.py","--check"],"source_freshness"),
]
def digest(path):
 return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def snapshot_hashes():
 return {name:digest(ROOT/name) for name in SOURCE_NAMES}
def gateway_health():
 try:
  with urllib.request.urlopen("http://127.0.0.1:8788/api/status",timeout=2) as resp:
   info=json.load(resp)
   return resp.status==200 and info.get("contract")=="BRNESTRM_STATUS_MINIMAL_V1"
 except Exception:
  return False
def run_preflight():
 STATE.mkdir(mode=0o700,parents=True,exist_ok=True)
 lock_fd=os.open(str(STATE/"deploy.lock"),os.O_CREAT|os.O_RDWR,0o600)
 try:
  fcntl.flock(lock_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError:
  os.close(lock_fd)
  raise RuntimeError("DEPLOY_LOCK_HELD: another release is running")
 try:
  return _preflight_locked()
 finally:
  fcntl.flock(lock_fd,fcntl.LOCK_UN)
  os.close(lock_fd)
def _preflight_locked():
 stamp=datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
 out=STATE/("validated-release-"+stamp)
 out.mkdir(mode=0o700)
 original=snapshot_hashes()
 ledger=STATE/"sillok-ledger.jsonl"
 index=ROOT/"brnestrm-corpus/.brnestrm/graphrag.sqlite3"
 ledger_sha=digest(ledger)
 index_sha=digest(index)
 results={}
 for cmd,name in TESTS:
  env=os.environ.copy()
  env["PYTHONDONTWRITEBYTECODE"]="1"
  env.pop("BRNESTRM_ALLOW_SYNTHETIC_AUTH",None)
  env.pop("BRNESTRM_TEST_PORT",None)
  env.pop("SILLOK_LEDGER_FILE",None)
  run=subprocess.run(cmd,cwd=ROOT,env=env,capture_output=True,text=True,timeout=130)
  results[name]={"exit":run.returncode,
                 "lastLines":(run.stdout+"\n"+run.stderr).splitlines()[-7:]}
  print("GATE",name,"PASS" if run.returncode==0 else "FAIL",flush=True)
  if run.returncode:
   (out/"failed-gate.json").write_text(json.dumps(results,indent=2))
   raise RuntimeError("TEST_GATE_FAILED: "+name)
 if snapshot_hashes()!=original:
  raise RuntimeError("SOURCES_CHANGED_DURING_TEST")
 if digest(ledger)!=ledger_sha or digest(index)!=index_sha:
  raise RuntimeError("OPERATIONAL_STATE_CHANGED_DURING_TEST")
 for name in SOURCE_NAMES:
  dest=out/name
  dest.parent.mkdir(parents=True,exist_ok=True)
  shutil.copy2(ROOT/name,dest)
 shutil.copy2(ledger,out/"sillok-ledger.jsonl")
 os.chmod(out/"sillok-ledger.jsonl",0o600)
 shutil.copy2(index,out/"graphrag.sqlite3")
 report={"status":"GATE_VERIFIED","timestamp":datetime.datetime.now().astimezone().isoformat(),
         "root":str(ROOT),"files":original,"ledgerSha":ledger_sha,"indexSha":index_sha,
         "tests":results,"snapshot":str(out)}
 (out/"receipt.json").write_text(json.dumps(report,indent=2))
 (STATE/"latest-validated-release.json").write_text(json.dumps(report,indent=2))
 print("VALIDATED_RELEASE",out,flush=True)
 return report
def install_plist():
 if (STATE/"launchagent-compatibility.json").exists():
  raise RuntimeError("TCC_BLOCKED: tested LaunchAgent cannot read protected Desktop; see stability runbook")
 PLIST.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
 if PLIST.exists():
  existing=plistlib.loads(PLIST.read_bytes())
  if existing.get("Label")!=LABEL:
   raise RuntimeError("Unexpected job occupies launch agent file")
 backup=PLIST.with_name(PLIST.name+".before-"+datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
 if PLIST.is_file():shutil.copy2(PLIST,backup)
 config={"Label":LABEL,
         "ProgramArguments":[sys.executable,"-B",str(ROOT/"brnestrm-gateway.py")],
         "WorkingDirectory":str(ROOT),"RunAtLoad":True,"KeepAlive":True,
         "ThrottleInterval":30,"ProcessType":"Interactive",
         "EnvironmentVariables":{"PYTHONDONTWRITEBYTECODE":"1","PYTHONUNBUFFERED":"1"},
         "Umask":0o077,"StandardOutPath":str(STATE/"gateway-stdout.log"),
         "StandardErrorPath":str(STATE/"gateway-stderr.log")}
 tmp=PLIST.with_name(PLIST.name+".tmp")
 with open(tmp,"wb") as f:plistlib.dump(config,f)
 os.chmod(tmp,0o600)
 os.replace(tmp,PLIST)
 print("LAUNCH_AGENT_PLIST_READY",PLIST)
def bootstrap():
 if (STATE/"launchagent-compatibility.json").exists():
  raise RuntimeError("TCC_BLOCKED: do not enable LaunchAgent until macOS access is verified")
 receipt_path=STATE/"latest-validated-release.json"
 if not receipt_path.exists():raise RuntimeError("NO_VERIFIED_RELEASE")
 receipt=json.loads(receipt_path.read_text())
 if receipt.get("status")!="GATE_VERIFIED" or snapshot_hashes()!=receipt["files"]:
  raise RuntimeError("STALE_VERIFIED_RELEASE")
 if digest(STATE/"sillok-ledger.jsonl")!=receipt["ledgerSha"]:
  raise RuntimeError("LEDGER_DRIFT_SINCE_GATE: rerun preflight after reviewing new audit records")
 if not PLIST.exists():raise RuntimeError("MISSING_AGENT_PLIST")
 job=subprocess.run(["launchctl","bootstrap",DOMAIN,str(PLIST)],
                    capture_output=True,text=True,timeout=15)
 print("LAUNCHD_BOOTSTRAP_EXIT",job.returncode)
 if job.returncode:
  print("LAUNCHD_ERROR",job.stderr[:300])
  raise RuntimeError("LAUNCHD_BOOTSTRAP_FAILED")
 for _ in range(35):
  if gateway_health():
   print("SUPERVISED_GATEWAY_HEALTH_OK")
   return
  time.sleep(.3)
 raise RuntimeError("BOOTSTRAPPED_BUT_HEALTH_CHECK_FAILED")
def status():
 job=subprocess.run(["launchctl","print",DOMAIN+"/"+LABEL],capture_output=True,text=True,timeout=5)
 print("LAUNCHD_AGENT_REGISTERED",job.returncode==0)
 state_file=STATE/"supervisor-status.json"
 if state_file.exists():
  try:
   supervisor=json.loads(state_file.read_text())
   print("USER_SESSION_SUPERVISOR",supervisor.get("status"),"SUPERVISED_PID",supervisor.get("pid"))
   print("RESTARTS_CURRENT_WINDOW",supervisor.get("restartsInWindow"))
   try:
    age=(datetime.datetime.now().astimezone()-
         datetime.datetime.fromisoformat(supervisor["observedAt"])).total_seconds()
    pid=int(supervisor.get("supervisorPid",0))
    os.kill(pid,0)
    print("SUPERVISOR_HEARTBEAT", "FRESH" if 0<=age<35 else "STALE",
          "AGE_SECONDS",round(age,1))
   except (OSError,ValueError,TypeError,KeyError):
    print("SUPERVISOR_HEARTBEAT","UNKNOWN_OR_DEAD")
  except (OSError,ValueError):
   print("USER_SESSION_SUPERVISOR","STATE_UNAVAILABLE")
 print("REBOOT_AUTO_START", "BLOCKED_MACOS_TCC" if (STATE/"launchagent-compatibility.json").exists() else "UNVERIFIED")
 if job.returncode==0:
  for line in job.stdout.splitlines():
   line=line.strip()
   if line.startswith(("pid =","state =","last exit code =")):print(line)
 print("GATEWAY_HEALTH_OK",gateway_health())
 try:
  with urllib.request.urlopen("http://127.0.0.1:8788/api/health/readiness",timeout=6) as r:
   detail=json.load(r)
  print("DEPENDENCY_READINESS",detail.get("overall"),"CHECKS",detail.get("checks"))
 except Exception:
  print("DEPENDENCY_READINESS","UNKNOWN")
 print("SOURCE_LOCK_PRESENT",(STATE/"gateway.singleton.lock").is_file())
 if (STATE/"latest-validated-release.json").exists():
  r=json.loads((STATE/"latest-validated-release.json").read_text())
  print("RELEASE_SOURCE_MATCH",snapshot_hashes()==r.get("files"))
def main():
 p=argparse.ArgumentParser()
 p.add_argument("command",choices=("preflight","install-agent","bootstrap","status"))
 args=p.parse_args()
 if args.command=="preflight":run_preflight()
 elif args.command=="install-agent":install_plist()
 elif args.command=="bootstrap":bootstrap()
 elif args.command=="status":status()
if __name__=="__main__":main()
