"""Session-scoped, one-owner BRNESTRM watchdog for Mac Desktop TCC constraints.

If launchd lacks Desktop access, run this supervisor from the same authorized
user session as the original Gateway. It starts only a test-gated version,
refuses duplicate owners, limits crashes and never overwrites Sillok.
"""
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.request

ROOT=Path(__file__).resolve().parent
STATE=ROOT/".brnestrm"
GATEWAY=ROOT/"brnestrm-gateway.py"
RELEASE=STATE/"latest-validated-release.json"
STOP=False
MAX_RESTARTS=3
WINDOW_SECONDS=300
MAX_UNHEALTHY=3
HEALTH_INTERVAL=10
def digest(p):
 return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def approved():
 if not RELEASE.is_file():
  return False,"NO_TEST_GATE_RECEIPT"
 try:
  receipt=json.loads(RELEASE.read_text())
  if receipt.get("status")!="GATE_VERIFIED":
   return False,"RELEASE_NOT_VERIFIED"
  for name,sha in receipt["files"].items():
   if digest(ROOT/name)!=sha:
    return False,"SOURCE_CHANGED_AFTER_GATE:"+name
  return True,"VERIFIED"
 except Exception:
  return False,"VERIFICATION_RECEIPT_INVALID"
def health():
 """Process liveness only. A broken dependency is a separate DEGRADED state."""
 try:
  with urllib.request.urlopen("http://127.0.0.1:8788/api/status",timeout=6) as resp:
   return resp.status==200 and json.load(resp).get("contract")=="BRNESTRM_STATUS_MINIMAL_V1"
 except Exception:
  return False


def readiness():
 try:
  with urllib.request.urlopen("http://127.0.0.1:8788/api/health/readiness",timeout=7) as resp:
   data=json.load(resp)
  if resp.status!=200 or data.get("contract")!="BRNESTRM_READINESS_V1":
   return False,["readiness_unknown"]
  failed=sorted(k for k,v in data.get("checks",{}).items() if v not in ("HEALTHY","CURRENT"))
  return data.get("overall")=="READY" and not failed,failed
 except Exception:
  return False,["readiness_unavailable"]
def any_listener():
 try:
  with socket.create_connection(("127.0.0.1",8788),timeout=0.4):return True
 except OSError:return False
def record(status,pid=None,reason=None,restarts=0):
 data={"status":status,"pid":pid,"reason":reason,"restartsInWindow":restarts,
       "observedAt":datetime.datetime.now().astimezone().isoformat(),
       "supervisorPid":os.getpid(),"supervision":"USER_SESSION_ONLY_TCC_CONSTRAINED"}
 temp=STATE/"supervisor-status.json.tmp"
 temp.write_text(json.dumps(data,indent=2))
 temp.replace(STATE/"supervisor-status.json")
 print("SUPERVISOR",status,"GATEWAY_PID",pid,"REASON",reason or "-",flush=True)
def stop_signal(signum,frame):
 global STOP
 STOP=True
def start_gateway():
 env=os.environ.copy()
 for x in ("BRNESTRM_ALLOW_SYNTHETIC_AUTH","BRNESTRM_TEST_PORT",
           "SILLOK_LEDGER_FILE","BRNESTRM_RESTORE_FD","BRNESTRM_RESTORE_CHECK_ONLY"):
  env.pop(x,None)
 env["PYTHONDONTWRITEBYTECODE"]="1"
 env["PYTHONUNBUFFERED"]="1"
 log=open(STATE/"supervised-gateway.log","ab",buffering=0)
 proc=subprocess.Popen([sys.executable,"-B",str(GATEWAY)],
     cwd=str(ROOT),stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,
     close_fds=True,start_new_session=True,env=env)
 return proc,log
def stop_gateway(proc):
 if proc is None:return
 if proc.poll() is None:
  proc.terminate()
  try:proc.wait(timeout=6)
  except subprocess.TimeoutExpired:
   proc.kill()
   proc.wait(timeout=5)
def run():
 STATE.mkdir(parents=True,exist_ok=True,mode=0o700)
 fd=os.open(str(STATE/"supervisor.singleton.lock"),os.O_CREAT|os.O_RDWR,0o600)
 try:
  fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError:
  os.close(fd)
  print("Another BRNESTRM watchdog is already active; refusing duplicate.",flush=True)
  return 2
 os.ftruncate(fd,0)
 os.write(fd,str(os.getpid()).encode())
 signal.signal(signal.SIGTERM,stop_signal)
 signal.signal(signal.SIGINT,stop_signal)
 proc=None;log=None
 failures=[]
 try:
  ok,msg=approved()
  if not ok:
   record("BLOCKED_UNVERIFIED_SOURCE",reason=msg)
   return 3
  if any_listener():
   record("BLOCKED_8788_PORT_IN_USE",reason="existing_gateway_must_be_stopped_before_supervision")
   return 4
  while not STOP:
   failures=[t for t in failures if time.monotonic()-t<WINDOW_SECONDS]
   ok,msg=approved()
   if not ok:
    record("BLOCKED_UNVERIFIED_SOURCE",pid=proc.pid if proc else None,reason=msg)
    return 5
   if len(failures)>=MAX_RESTARTS:
    record("CIRCUIT_OPEN",reason="three restarts within 5 minutes",restarts=len(failures))
    return 6
   proc,log=start_gateway()
   record("STARTING",pid=proc.pid,restarts=len(failures))
   ready=False
   for _ in range(40):
    if STOP or proc.poll() is not None:break
    if health():
     ready=True;break
    time.sleep(0.3)
   if not ready:
    stop_gateway(proc)
    log.close();log=None
    failures=[x for x in failures if time.monotonic()-x<WINDOW_SECONDS]
    failures.append(time.monotonic())
    record("START_FAILURE",pid=proc.pid,restarts=len(failures))
    time.sleep(min(10,2*len(failures)))
    continue
   ready_checks,unready=readiness()
   record("HEALTHY" if ready_checks else "DEGRADED_DEPENDENCIES",pid=proc.pid,
          reason=",".join(unready) if unready else None,restarts=len(failures))
   consecutive_bad=0
   healthy_since=time.monotonic()
   while not STOP and proc.poll() is None:
    for _ in range(HEALTH_INTERVAL):
     if STOP or proc.poll() is not None:break
     time.sleep(1)
    if STOP or proc.poll() is not None:break
    if health():
     consecutive_bad=0
     ready_checks,unready=readiness()
     record("HEALTHY" if ready_checks else "DEGRADED_DEPENDENCIES",
            pid=proc.pid,reason=",".join(unready) if unready else None,
            restarts=len(failures))
     # External model/index faults do not get fixed by endlessly restarting
     # the BRNESTRM gateway, so never recycle on dependency failure alone.
    else:
     consecutive_bad+=1
     record("GATEWAY_UNRESPONSIVE",pid=proc.pid,reason="gateway_http_unhealthy",
            restarts=len(failures))
     if consecutive_bad>=MAX_UNHEALTHY:break
   if STOP:break
   stop_gateway(proc)
   log.close();log=None
   failures=[x for x in failures if time.monotonic()-x<WINDOW_SECONDS]
   failures.append(time.monotonic())
   record("RESTART_BACKOFF",pid=proc.pid,reason="process_exited_or_three_health_failures",restarts=len(failures))
   time.sleep(min(30,5*len(failures)))
 finally:
  stop_gateway(proc)
  if log:log.close()
  if STOP:
   record("STOPPED",reason="operator_requested_shutdown")
  fcntl.flock(fd,fcntl.LOCK_UN)
  os.close(fd)
 return 0
if __name__=="__main__":
 sys.exit(run())
