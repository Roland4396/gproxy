#!/usr/bin/env python3
"""Boot the retained v3 image on an isolated snapshot; never attach a network.

Requires root for entering the canary's private network namespace. Secrets are
read in memory from the existing environment file; output is metadata only.
The production container/data/compose are never changed. The canary is stopped
and removed in finally, including after an assertion failure.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time


def command(*args, **kwargs):
    return subprocess.run(args, check=True, text=True, capture_output=True, **kwargs).stdout.strip()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--snapshot", type=Path, required=True)
    p.add_argument("--env-file", type=Path, required=True)
    p.add_argument("--image", required=True)
    p.add_argument("--work", type=Path, required=True)
    args = p.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("root is required for namespace-only management HTTP")
    if args.work.exists():
        raise SystemExit("use a fresh private rehearsal directory")
    wal = Path(str(args.snapshot) + "-wal")
    if wal.exists() and wal.stat().st_size:
        raise SystemExit("snapshot must be closed and self-contained")
    source = args.snapshot.resolve()
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    args.work.mkdir(mode=0o700, parents=True)
    data = args.work / "data"
    data.mkdir(mode=0o700)
    shutil.copyfile(source, data / "gproxy.db")
    os.chown(data, 65532, 65532)
    os.chown(data / "gproxy.db", 65532, 65532)
    (data / "gproxy.db").chmod(0o600)
    name = "gproxy-upgrade-v3-rollback-rehearsal"
    # A prior canary must be investigated, not silently killed or reused.
    if subprocess.run(["docker", "inspect", name], stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL).returncode == 0:
        raise SystemExit("prior rehearsal container exists; inspect it first")
    created = False
    try:
        command("docker", "run", "-d", "--name", name, "--network", "none",
                "--memory", "512m", "--cpus", "0.5", "--env-file", str(args.env_file.resolve()),
                "--mount", f"type=bind,src={data.resolve()},dst=/app/data",
                "--label", "gproxy.upgrade.validation=rollback-only", args.image)
        created = True
        pid = int(command("docker", "inspect", "--format", "{{.State.Pid}}", name))
        # No host port and no bridge. Python reaches only the canary loopback.
        script = r'''
import http.cookiejar,json,time,urllib.request,sys
from pathlib import Path
values={}
for line in Path(sys.argv[1]).read_text().splitlines():
 if '=' in line and not line.lstrip().startswith('#'):
  k,v=line.split('=',1);values[k.strip()]=v.strip().strip('"').strip("'")
username=values.get('GPROXY_ADMIN_USER',values.get('ADMIN_USER','admin'))
password=values.get('GPROXY_ADMIN_PASSWORD',values.get('ADMIN_PASSWORD'))
assert password,'admin password missing'
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
base='http://127.0.0.1:8787/admin/api/'
for attempt in range(40):
 try:
  request=urllib.request.Request(base+'login',data=json.dumps({'username':username,'password':password}).encode(),headers={'Content-Type':'application/json'})
  with opener.open(request,timeout=3) as response:response.read();assert response.status==200
  break
 except OSError:
  if attempt==39:raise
  time.sleep(.5)
result={'login_http':200}
for family in ['providers','credentials']:
 with opener.open(base+family,timeout=5) as response: rows=json.load(response)
 assert isinstance(rows,list),family
 result[family]=len(rows)
print(json.dumps(result))
'''
        observed = json.loads(command("nsenter", "--target", str(pid), "--net", "python3", "-",
                                      str(args.env_file.resolve()), input=script))
        with sqlite3.connect(f"file:{source}?mode=ro&immutable=1", uri=True) as db:
            for family in ("providers", "credentials"):
                expected = db.execute(f"SELECT count(*) FROM {family}").fetchone()[0]
                assert observed[family] == expected, f"{family} parity failed"
        observed.update(image=args.image, network="none", published_ports=0,
                        snapshot_sha256=before, snapshot_unchanged=True,
                        paid_inference_requests=0)
        assert hashlib.sha256(source.read_bytes()).hexdigest() == before
        report = args.work / "result.json"
        report.write_text(json.dumps(observed, indent=2) + "\n")
        report.chmod(0o600)
        print(json.dumps(observed))
    finally:
        if created:
            raw = command("docker", "logs", name)
            log = args.work / "canary.log"
            log.write_text(raw)
            log.chmod(0o600)
            command("docker", "stop", "--time", "10", name)
            command("docker", "rm", name)


if __name__ == "__main__":
    main()
