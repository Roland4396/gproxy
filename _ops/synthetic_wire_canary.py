#!/usr/bin/env python3
"""Installed-image JSON/SSE smoke against a namespace-local synthetic server.

This fresh database contains only documented fake credentials. No production
configuration is copied, and the container has no external network or ports.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess


def run(args, **kw):
    return subprocess.run(args, text=True, capture_output=True, check=True, **kw).stdout.strip()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--image", required=True)
    p.add_argument("--work", type=Path, required=True)
    args = p.parse_args()
    if os.geteuid() != 0 or args.work.exists():
        raise SystemExit("requires root and a fresh private work directory")
    name = "gproxy-upgrade-v4-synthetic-wire-canary"
    if subprocess.run(["docker", "inspect", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
        raise SystemExit("prior synthetic canary exists; inspect it first")
    args.work.mkdir(mode=0o700, parents=True)
    data = args.work / "data"
    data.mkdir(mode=0o700)
    os.chown(data, 65532, 65532)
    env = args.work / "synthetic.env"
    env.write_text("GPROXY_MASTER_KEY=" + "71" * 32 + "\nGPROXY_ADMIN_USER=synthetic-admin\nGPROXY_ADMIN_PASSWORD=synthetic-password-for-local-qa\nGPROXY_BOOTSTRAP_ADMIN_API_KEY=synthetic-gateway-key\nGPROXY_UPDATE_AUTOMATIC=false\nGPROXY_UPDATE_CHECK_INTERVAL=86400\n")
    env.chmod(0o600)
    created = False
    try:
        run(["docker", "run", "-d", "--name", name, "--network", "none", "--memory", "768m", "--cpus", "0.75",
             "--env-file", str(env.resolve()), "--mount", f"type=bind,src={data.resolve()},dst=/app/data",
             "--label", "gproxy.upgrade.validation=synthetic-wire", args.image])
        created = True
        pid = run(["docker", "inspect", "--format", "{{.State.Pid}}", name])
        script = r'''
import json,socket,threading,time,urllib.request,urllib.error
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
second_sent=threading.Event()
cancelled=threading.Event()
observed=[]
class Mock(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def log_message(self,*args):pass
 def do_POST(self):
  body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
  assert self.headers['Authorization']=='Bearer synthetic-upstream-key'
  assert body['model']=='mock-model'
  assert self.path=='/v1/chat/completions'
  abort=body.get('messages',[{}])[0].get('content')=='synthetic early disconnect'
  observed.append({'stream':bool(body.get('stream')),'path':self.path,'abort':abort})
  if not body.get('stream'):
   result={'id':'synthetic-json','object':'chat.completion','created':1791400000,'model':'mock-model',
    'choices':[{'index':0,'message':{'role':'assistant','content':'SYNTHETIC_OK'},'finish_reason':'stop'}],
    'usage':{'prompt_tokens':3,'completion_tokens':2,'total_tokens':5}}
   raw=json.dumps(result).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw);return
  self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Connection','close');self.end_headers()
  def chunk(delta,finish=None):
   raw={'id':'synthetic-sse','object':'chat.completion.chunk','created':1791400000,'model':'mock-model','choices':[{'index':0,'delta':delta,'finish_reason':finish}]}
   self.wfile.write(('data: '+json.dumps(raw)+'\n\n').encode());self.wfile.flush()
  chunk({'role':'assistant','content':'FIRST'})
  if abort:
   self.connection.settimeout(5)
   try:
    if self.connection.recv(1)==b'':cancelled.set()
   except socket.timeout:pass
   self.close_connection=True
   return
  time.sleep(1.2)
  second_sent.set();chunk({'content':'SECOND'});chunk({},'stop')
  self.wfile.write(b'data: [DONE]\n\n');self.wfile.flush();self.close_connection=True
server=ThreadingHTTPServer(('127.0.0.1',8899),Mock)
threading.Thread(target=server.serve_forever,daemon=True).start()
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
base='http://127.0.0.1:8787'
for n in range(100):
 try:
  with opener.open(base+'/healthz',timeout=3) as r:assert r.status==200;r.read()
  break
 except OSError:
  if n==99:raise
  time.sleep(.2)
def request(path,data,method='POST'):
 req=urllib.request.Request(base+path,data=json.dumps(data).encode(),method=method,
  headers={'Content-Type':'application/json','Authorization':'Bearer synthetic-gateway-key'})
 with opener.open(req,timeout=10) as r:return json.load(r)
provider=request('/admin/api/providers',{'name':'synthetic','channel':'openai','baseUrl':'http://127.0.0.1:8899'})
credential=request('/admin/api/credentials',{'providerId':provider['id'],'authKind':'api_key','label':'synthetic-only','secret':{'api_key':'synthetic-upstream-key'}})
assert credential['hasSecret'] is True
body={'model':'synthetic/mock-model','messages':[{'role':'user','content':'synthetic smoke'}]}
data=request('/v1/chat/completions',body)
assert data['choices'][0]['message']['content']=='SYNTHETIC_OK'
scoped={**body,'model':'mock-model'}
data=request('/synthetic/v1/chat/completions',scoped)
assert data['choices'][0]['message']['content']=='SYNTHETIC_OK'
start=time.monotonic()
req=urllib.request.Request(base+'/synthetic/v1/chat/completions',data=json.dumps({**scoped,'stream':True}).encode(),
 headers={'Content-Type':'application/json','Authorization':'Bearer synthetic-gateway-key'})
with opener.open(req,timeout=10) as response:
 assert response.status==200 and 'text/event-stream' in response.headers['Content-Type']
 lines=[];first=None
 for raw in response:
  line=raw.decode().strip()
  if line.startswith('data:'):
   lines.append(line)
   if first is None:
    first=time.monotonic()-start
    assert not second_sent.is_set(),'gateway buffered first event until upstream sent later event'
   if line=='data: [DONE]':break
 elapsed=time.monotonic()-start
assert any('FIRST' in l for l in lines) and any('SECOND' in l for l in lines)
assert len(observed)==3 and observed[0]['stream'] is False and observed[2]['stream'] is True
req=urllib.request.Request(base+'/synthetic/v1/chat/completions',data=json.dumps({**scoped,'stream':True,
 'messages':[{'role':'user','content':'synthetic early disconnect'}]}).encode(),
 headers={'Content-Type':'application/json','Authorization':'Bearer synthetic-gateway-key'})
with opener.open(req,timeout=10) as response:
 assert response.readline().startswith(b'data:')
assert cancelled.wait(5),'upstream connection was not released after downstream disconnected'
assert len(observed)==4 and observed[-1]['abort'] is True
server.shutdown()
print(json.dumps({'json_http':200,'sse_http':200,'first_event_before_upstream_second':True,
 'first_event_ms':round(first*1000),'stream_elapsed_ms':round(elapsed*1000),'upstream_requests':4,
 'provider_scoped_json_and_sse':True,'upstream_released_after_downstream_disconnect':True,
 'synthetic_only':True,'external_network':False,'paid_inference_requests':0}))
'''
        result = json.loads(run(["nsenter", "--target", pid, "--net", "python3", "-"], input=script))
        result.update(image=args.image, published_ports=0)
        path = args.work / "result.json"
        path.write_text(json.dumps(result, indent=2) + "\n")
        path.chmod(0o600)
        print(json.dumps(result))
    finally:
        if created:
            logs = subprocess.run(["docker", "logs", name], text=True, capture_output=True)
            path = args.work / "canary.log"
            path.write_text(logs.stdout + logs.stderr)
            path.chmod(0o600)
            run(["docker", "stop", "--time", "15", name])
            run(["docker", "rm", name])


if __name__ == "__main__":
    main()
