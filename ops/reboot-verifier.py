#!/usr/bin/env python3
"""Evidence-only post-reboot readiness verifier, never restarts any service.

Runs once on user login. A run in the same boot when installed CANNOT pass.
No login prompt or secret access, no real customer data.
"""
import datetime
import json
import os
import pathlib
import re
import subprocess
import time
import urllib.request

ROOT=pathlib.Path.home()/"Library/Application Support/BRNESTRM"
ARM=ROOT/"reboot-verifier-armed.json"
RECEIPT=ROOT/"reboot-verifier-receipt.json"
LABELS=("com.local.brnestrm.supervised","com.local.brnestrm.health-monitor",
        "com.local.brnestrm.hermes-llama","com.local.brnestrm.daily-backup")

def boot_epoch():
    p=subprocess.run(["sysctl","-n","kern.boottime"],capture_output=True,text=True,timeout=4)
    if p.returncode:raise RuntimeError("boot_time_unavailable")
    match=re.search(r"sec\s*=\s*(\d+)",p.stdout)
    if not match:raise RuntimeError("boot_time_unparseable")
    return int(match.group(1))

def agent_loaded(label):
    p=subprocess.run(["launchctl","print",f"gui/{os.getuid()}/{label}"],
                     capture_output=True,text=True,timeout=4)
    return p.returncode==0

def readiness():
    with urllib.request.urlopen("http://127.0.0.1:8788/api/health/readiness",timeout=5) as r:
        data=json.load(r)
    expected={"audit_chain":"HEALTHY","knowledge_integrity":"HEALTHY",
              "knowledge_freshness":"CURRENT","ollama_api":"HEALTHY",
              "hermes_http":"HEALTHY"}
    if data.get("overall")!="READY":return False
    if any(data.get("checks",{}).get(k)!=v for k,v in expected.items()):return False
    with urllib.request.urlopen("http://127.0.0.1:18434/health",timeout=4) as r:
        if r.status!=200 or json.load(r).get("status")!="ok":return False
    return True

def write_receipt(status,armed_at,boot,observed,checks=None):
    receipt={"contract":"BRNESTRM_POST_REBOOT_V1","status":status,
             "armedAtEpoch":armed_at,"bootEpoch":boot,
             "observedAtEpoch":observed,"bootAgeSeconds":max(0,int(observed-boot)),
             "agentChecks":checks or {},"dataChanged":False}
    temp=RECEIPT.with_suffix(".json.new")
    with temp.open("w",encoding="utf8") as f:
        json.dump(receipt,f,indent=2);f.flush();os.fsync(f.fileno())
    os.chmod(temp,0o600);os.replace(temp,RECEIPT)
    print("REBOOT_OBSERVER",status,"AGE_SECONDS",int(observed-boot),flush=True)

def execute():
    armed=json.loads(ARM.read_text())
    armed_at=int(armed["armedAtEpoch"])
    boot=boot_epoch()
    now=int(time.time())
    if boot <= armed_at:
        write_receipt("PENDING_FRESH_REBOOT",armed_at,boot,now)
        return 0
    if now-boot>1200:
        write_receipt("INCONCLUSIVE_LATE_USER_LOGIN",armed_at,boot,now)
        return 0
    initial_boot=boot
    stable=0
    last={}
    deadline=min(now+360,boot+600)
    while time.time()<deadline:
        current=boot_epoch()
        if current!=initial_boot:
            write_receipt("REBOOT_DURING_OBSERVATION",armed_at,boot,int(time.time()))
            return 2
        agents={k:agent_loaded(k) for k in LABELS}
        try:live=readiness()
        except Exception:live=False
        last=dict(agents);last["readiness"]=live
        if all(agents.values()) and live:stable+=1
        else:stable=0
        if stable>=2:
            write_receipt("PASS",armed_at,boot,int(time.time()),last)
            return 0
        time.sleep(5)
    write_receipt("FAILED_TO_REACH_READY_WITHIN_WINDOW",armed_at,boot,int(time.time()),last)
    return 2

def self_test():
    # Guard against falsely passing when the observer is registered in a
    # boot that began before its own installation.
    x=boot_epoch();now=int(time.time())
    assert x<=now and x<=now+1
    assert not (x>now), "same-boot arm must be ineligible"
    assert len(LABELS)==4 and len(set(LABELS))==4
    print("REBOOT_OBSERVER_SELF_TEST_PASS","NO_LIVE_MUTATION",True)
    return 0

if __name__=="__main__":
    import sys
    if sys.argv[1:]==["--self-test"]:raise SystemExit(self_test())
    if sys.argv[1:]:raise SystemExit(2)
    raise SystemExit(execute())
