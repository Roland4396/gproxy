#!/usr/bin/env python3
"""Account-session wire/restart regression in a network-none fake deployment.

Only documented synthetic OAuth credentials and messages are used. No host
ports, production data, real inference or external network are involved.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid


SCRIPT = r'''
import hashlib,json,threading,time,urllib.request,uuid
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
observed=[]
fail_once=False
second_sent=threading.Event()
class Mock(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def log_message(self,*args):pass
 def do_POST(self):
  global fail_once
  body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
  assert self.path.split('?')[0] in ['/v1internal:generateContent','/v1internal:streamGenerateContent']
  token=self.headers.get('Authorization')
  account={'Bearer synthetic-a':'a','Bearer synthetic-a-refreshed':'a','Bearer synthetic-b':'b'}[token]
  request=body['request'];session=request['sessionId']
  assert session.startswith('-') and 0<int(session[1:])<2**63
  header=self.headers['x-vscode-sessionid'];parsed=uuid.UUID(header)
  assert parsed.version==4 and str(parsed)==header
  row={'account':account,'sessionId':session,'headerSessionId':header,
   'requestId':body['requestId'],'model':body['model'],'stream':'alt=sse' in self.path}
  observed.append(row)
  if fail_once:
   fail_once=False;row['status']=503
   raw=json.dumps({'error':{'code':503,'message':'synthetic transient','status':'UNAVAILABLE'}}).encode()
   self.send_response(503);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw);return
  row['status']=200
  def response(text,finish=False):
   candidate={'content':{'role':'model','parts':[{'text':text}]}}
   if finish:candidate['finishReason']='STOP'
   return {'response':{'candidates':[candidate],'usageMetadata':{'promptTokenCount':1,'candidatesTokenCount':1,'totalTokenCount':2}}}
  self.send_response(200)
  if row['stream']:
   self.send_header('Content-Type','text/event-stream');self.send_header('Connection','close');self.end_headers()
   self.wfile.write(('data: '+json.dumps(response('SESSION_'))+'\n\n').encode());self.wfile.flush()
   time.sleep(.5);second_sent.set()
   self.wfile.write(('data: '+json.dumps(response('AUDIT_OK',True))+'\n\n').encode());self.wfile.flush();self.close_connection=True
  else:
   raw=json.dumps(response('SESSION_AUDIT_OK',True)).encode()
   self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
server=ThreadingHTTPServer(('127.0.0.1',8899),Mock)
threading.Thread(target=server.serve_forever,daemon=True).start()
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}));base='http://127.0.0.1:8787'
for n in range(150):
 try:
  with opener.open(base+'/healthz',timeout=2) as r:assert r.status==200;r.read()
  break
 except OSError:
  if n==149:raise
  time.sleep(.2)
def api(path,data=None,method=None):
 req=urllib.request.Request(base+path,data=None if data is None else json.dumps(data).encode(),method=method,
  headers={'Content-Type':'application/json','Authorization':'Bearer synthetic-gateway-key'})
 with opener.open(req,timeout=30) as r:
  raw=r.read();return json.loads(raw) if raw else None
def secret(token,project='synthetic-project'):
 return {'access_token':token,'refresh_token':'synthetic-refresh','token_type':'Bearer','provider_fields':{'project_id':project}}
provider='session-audit-provider';a='session-audit-a';b='session-audit-b'
if BOOTSTRAP:
 api('/admin/api/providers',{'id':provider,'name':'session-audit','channel':'antigravity','baseUrl':'http://127.0.0.1:8899'})
 for cid,token in [(a,'synthetic-a'),(b,'synthetic-b')]:
  api('/admin/api/credentials',{'id':cid,'providerId':provider,'authKind':'oauth','label':'synthetic-only','secret':secret(token)})
 api('/admin/api/providers/'+provider,{'config':{'account_session_id':True,'model_credentials':{
  'gemini-3.8-flash-high':[a],'gemini-4-argon-high':[a],'gemini-3.1-flash-image':[a],'gemini-3.8-flash':[b]}}},'PATCH')
assert api('/admin/api/providers/'+provider)['config']['account_session_id'] is True
def generate(model,text,stream=False,caller_session=None):
 body={'model':model,'stream':stream,'messages':[{'role':'user','content':text}]}
 if caller_session is not None:body['sessionId']=caller_session
 if not stream:
  result=api('/session-audit/v1/chat/completions',body)
  assert result['choices'][0]['message']['content']=='SESSION_AUDIT_OK'
  return
 req=urllib.request.Request(base+'/session-audit/v1/chat/completions',data=json.dumps(body).encode(),
  headers={'Content-Type':'application/json','Authorization':'Bearer synthetic-gateway-key'})
 second_sent.clear();first=False;lines=[]
 with opener.open(req,timeout=30) as r:
  assert 'text/event-stream' in r.headers['Content-Type']
  for line in r:
   if line.startswith(b'data:'):
    if not first:assert not second_sent.is_set(),'stream buffered';first=True
    lines.append(line)
    if b'[DONE]' in line:break
 assert first and b'SESSION_' in b''.join(lines) and b'AUDIT_OK' in b''.join(lines)
generate('gemini-3.8-flash-high','synthetic first chat')
generate('gemini-4-argon-high','synthetic changed prompt',True,'unstable-caller-id')
generate('gemini-3.8-flash','synthetic first chat')
if BOOTSTRAP:
 generate('gemini-3.1-flash-image','synthetic other model')
 api('/admin/api/credentials/'+a,{'secret':secret('synthetic-a-refreshed','different-discovered-project')},'PATCH')
 generate('gemini-4-argon-high','synthetic after refresh')
 fail_once=True;before=len(observed)
 generate('gemini-3.8-flash-high','synthetic retry')
 assert len(observed)==before+2 and observed[-2]['status']==503 and observed[-1]['status']==200
generate('gemini-3.8-flash-high','synthetic return to account a')
def expected(cid):
 seed=f'antigravity-account-session-v1:{len(provider)}:{provider}:{len(cid)}:{cid}'
 n=max(1,int.from_bytes(hashlib.sha256(seed.encode()).digest()[:8],'big')&((1<<63)-1))
 return '-'+str(n)
for account,cid in [('a',a),('b',b)]:
 rows=[r for r in observed if r['account']==account]
 assert rows and {r['sessionId'] for r in rows}=={expected(cid)}
 assert len({r['headerSessionId'] for r in rows})==1
assert len({r['sessionId'] for r in observed})==2
assert len({r['headerSessionId'] for r in observed})==2
assert len({r['requestId'] for r in observed})==len(observed)
server.shutdown();server.server_close()
print(json.dumps({'observed':observed,'stream_unbuffered':True}))
'''


def run(args, **kw):
    return subprocess.run(args, text=True, capture_output=True, check=True, **kw).stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--work', type=Path, required=True)
    args = parser.parse_args()
    if os.geteuid() != 0 or args.work.exists():
        raise SystemExit('requires root and a fresh private work directory')
    os.umask(0o077)
    args.work.mkdir(parents=True, mode=0o700)
    data = args.work / 'data'
    data.mkdir(mode=0o700)
    os.chown(data, 65532, 65532)
    env = args.work / 'synthetic.env'
    env.write_text('GPROXY_MASTER_KEY=' + '71'*32 + '\nGPROXY_ADMIN_USER=synthetic-admin\nGPROXY_ADMIN_PASSWORD=synthetic-password-for-local-qa\nGPROXY_BOOTSTRAP_ADMIN_API_KEY=synthetic-gateway-key\nGPROXY_UPDATE_AUTOMATIC=false\nGPROXY_UPDATE_CHECK_INTERVAL=86400\n')
    name = 'gproxy-account-session-canary-' + uuid.uuid4().hex[:10]
    created = False
    try:
        run(['docker','run','-d','--name',name,'--network','none','--memory','768m','--cpus','0.75',
             '--env-file',str(env.resolve()),'--mount',f'type=bind,src={data.resolve()},dst=/app/data',args.image])
        created = True
        phases = []
        for bootstrap in [True, False]:
            if not bootstrap:
                run(['docker','restart','--time','10',name])
            pid = run(['docker','inspect','--format','{{.State.Pid}}',name])
            phases.append(json.loads(run(['nsenter','--target',pid,'--net','python3','-'],
                                         input=f'BOOTSTRAP={bootstrap!r}\n'+SCRIPT)))
        for account in ['a', 'b']:
            before = next(r for r in phases[0]['observed'] if r['account']==account)
            after = next(r for r in phases[1]['observed'] if r['account']==account)
            for key in ['sessionId','headerSessionId']:
                assert before[key] == after[key], (account,key)
        rows = [r for phase in phases for r in phase['observed']]
        assert len({r['requestId'] for r in rows}) == len(rows)
        result = {'passed':True,'image':args.image,'legal_numeric_body_and_uuid_headers':True,
                  'stable_across_messages_models_refresh_retry_switch_and_restart':True,
                  'different_accounts_isolated':True,'request_ids_unique':True,
                  'json_and_unbuffered_sse':True,'phases':phases,'mock_requests':len(rows),
                  'external_network':False,'published_ports':0,'paid_inference_requests':0}
        (args.work/'result.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result))
    except subprocess.CalledProcessError as error:
        (args.work/'error.txt').write_text((error.stdout or '')+'\n'+(error.stderr or ''))
        raise
    finally:
        if created:
            logs = subprocess.run(['docker','logs',name],text=True,capture_output=True)
            (args.work/'canary.log').write_text(logs.stdout+logs.stderr)
            run(['docker','stop','--time','10',name])
            run(['docker','rm',name])
        shutil.rmtree(data,ignore_errors=True)
        env.unlink(missing_ok=True)
        exists = subprocess.run(['docker','inspect',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0
        (args.work/'cleanup.json').write_text(json.dumps({'container_removed':not exists,'test_database_deleted':not data.exists()})+'\n')


if __name__ == '__main__':
    main()
