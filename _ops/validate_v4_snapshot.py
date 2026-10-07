#!/usr/bin/env python3
"""Native v4 import + history audit + namespace-only management canary.

The destination must be a fresh private directory. The image is immutable and
already verified from CI. No host ports or external network are attached; no
inference, quota probe, OAuth refresh, or production mutation is performed.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

from audit_v4_snapshot import audit
from migrate_history_v3 import migrate


def run(args, **kw):
    return subprocess.run(args, check=True, capture_output=True, text=True, **kw).stdout.strip()


def env_values(path):
    out = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def private_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")
    path.chmod(0o600)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--snapshot", type=Path, required=True)
    p.add_argument("--env-file", type=Path, required=True)
    p.add_argument("--image", required=True)
    p.add_argument("--work", type=Path, required=True)
    args = p.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("root is required for namespace-only canary access")
    if args.work.exists():
        raise SystemExit("refusing a nonfresh validation directory")
    if args.snapshot.resolve() == Path("/home/ubuntu/gproxy/data/gproxy.db"):
        raise SystemExit("take an online backup first; never validate a live source")
    name = "gproxy-upgrade-v4-snapshot-canary"
    if subprocess.run(["docker", "inspect", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
        raise SystemExit("a prior canary exists; investigate it before proceeding")
    wal = Path(str(args.snapshot) + "-wal")
    if wal.exists() and wal.stat().st_size:
        raise SystemExit("source must be a closed self-contained snapshot")
    args.work.mkdir(mode=0o700, parents=True)
    data, input_dir = args.work / "data", args.work / "source"
    for directory in (data, input_dir):
        directory.mkdir(mode=0o700)
        os.chown(directory, 65532, 65532)
    copied = input_dir / "gproxy.db"
    shutil.copyfile(args.snapshot, copied)
    copied.chmod(0o400)
    os.chown(copied, 65532, 65532)
    before = hashlib.sha256(args.snapshot.read_bytes()).hexdigest()
    # Only the two keys. Passing the admin password would deliberately reset
    # its PHC hash, defeating preservation of imported authentication state.
    source_env = env_values(args.env_file)
    key = source_env["GPROXY_MASTER_KEY"]
    env = args.work / "canary.env"
    env.write_text(f"GPROXY_MASTER_KEY={key}\nGPROXY_IMPORT_SOURCE_MASTER_KEY={key}\nGPROXY_UPDATE_AUTOMATIC=false\n")
    env.chmod(0o600)
    imported = subprocess.run([
        "docker", "run", "--rm", "--network", "none", "--memory", "768m", "--cpus", "0.75",
        "--env-file", str(env.resolve()),
        "--mount", f"type=bind,src={data.resolve()},dst=/app/data",
        # Native source opens mode=ro. Its directory can contain the empty
        # WAL/SHM sidecars SQLite creates while reading a WAL-mode backup;
        # the snapshot itself is mode 0400 and its bytes are hash checked.
        "--mount", f"type=bind,src={input_dir.resolve()},dst=/source",
        args.image, "import", "--from-v3", "/source/gproxy.db",
    ], capture_output=True, text=True)
    log = args.work / "native-import.log"
    log.write_text(imported.stdout + imported.stderr)
    log.chmod(0o600)
    if imported.returncode:
        raise SystemExit(f"native import failed ({imported.returncode}); inspect private native-import.log")
    if hashlib.sha256(copied.read_bytes()).hexdigest() != before:
        raise SystemExit("native import changed source snapshot bytes")
    target = data / "gproxy.db"
    projection = migrate(copied, target)
    private_json(args.work / "history-projection.json", projection)
    parity = audit(copied, target)
    private_json(args.work / "parity-audit.json", parity)
    # A second history run must be an exact no-op, not duplicate native rows.
    repeated = migrate(copied, target)
    if not repeated.get("already_imported"):
        raise AssertionError("history projection idempotency failed")
    os.chown(target, 65532, 65532)
    created = False
    try:
        run(["docker", "run", "-d", "--name", name, "--network", "none", "--memory", "768m", "--cpus", "0.75",
             "--env-file", str(env.resolve()), "--mount", f"type=bind,src={data.resolve()},dst=/app/data",
             "--label", "gproxy.upgrade.validation=snapshot-only", args.image])
        created = True
        pid = run(["docker", "inspect", "--format", "{{.State.Pid}}", name])
        # Management reads only. No diagnostics/probe endpoints are touched.
        script = r'''
import http.cookiejar,json,time,urllib.request,urllib.error,sys
from pathlib import Path
values={}
for line in Path(sys.argv[1]).read_text().splitlines():
 if '=' in line and not line.lstrip().startswith('#'):
  k,v=line.split('=',1);values[k.strip()]=v.strip().strip('"').strip("'")
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
base='http://127.0.0.1:8787'
for attempt in range(80):
 try:
  with opener.open(base+'/healthz',timeout=3) as r:health=json.load(r);assert r.status==200
  break
 except OSError:
  if attempt==79:raise
  time.sleep(.25)
def api(path,data=None):
 req=urllib.request.Request(base+path,data=json.dumps(data).encode() if data is not None else None,headers={'Content-Type':'application/json'})
 with opener.open(req,timeout=8) as r:return json.load(r)
api('/admin/api/login',{'username':values['GPROXY_ADMIN_USER'],'password':values['GPROXY_ADMIN_PASSWORD']})
providers=api('/admin/api/providers');credentials=api('/admin/api/credentials')
assert isinstance(providers,list) and isinstance(credentials,list)
assert all(isinstance(r['id'],int) for r in credentials)
quota={}
for c in credentials:
 result=api('/admin/api/credentials/'+str(c['id'])+'/quota')
 assert isinstance(result['entries'],list) and isinstance(result['sources'],list)
 quota[str(c['id'])]={'entries':len(result['entries']),'sources':len(result['sources'])}
with opener.open(base+'/console/',timeout=8) as r:assert r.status==200;html=r.read();assert b'<html' in html
print(json.dumps({'health_http':200,'login_http':200,'providers':len(providers),'credentials':len(credentials),'quota':quota,'console_http':200}))
'''
        management = json.loads(run(["nsenter", "--target", pid, "--net", "python3", "-", str(args.env_file.resolve())], input=script))
        private_json(args.work / "management-canary.json", management)
        result = {"passed": True, "image": args.image, "network": "none", "published_ports": 0,
                  "source_sha256": before, "source_unchanged": hashlib.sha256(args.snapshot.read_bytes()).hexdigest() == before,
                  "archive_tables": len(parity["archives"]), "native_checks": parity["native_checks"],
                  "management": management, "paid_inference_requests": 0, "gpu_requests": 0}
        private_json(args.work / "result.json", result)
        print(json.dumps(result))
    finally:
        if created:
            logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True)
            log = args.work / "canary.log"
            log.write_text(logs.stdout + logs.stderr)
            log.chmod(0o600)
            run(["docker", "stop", "--time", "15", name])
            run(["docker", "rm", name])


if __name__ == "__main__":
    main()
