#!/usr/bin/env python3
"""Network-none synthetic end-to-end regression; never opens production data."""
import argparse,json,os,shutil,subprocess,uuid
from pathlib import Path
SCRIPT=r'''
import json,threading,time,urllib.request,urllib.error,datetime
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
calls=[];generation_count=0;left=0;summary_fails=False
reset=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(days=6)).isoformat().replace('+00:00','Z')
class Mock(BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def log_message(self,*args):pass
 def do_POST(self):
  global generation_count
  body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
  path=self.path.split('?')[0];calls.append(path)
  if path=='/v1internal:fetchAvailableModels':
   data={'models':{
    'gemini-4-argon-high':{'modelProvider':'MODEL_PROVIDER_GOOGLE','quotaInfo':{'remainingFraction':left,'resetTime':reset}},
    'gemini-3.8-flash-high':{'modelProvider':'MODEL_PROVIDER_GOOGLE','quotaInfo':{'remainingFraction':0,'resetTime':reset}}}}
   status=200
  elif path=='/v1internal:retrieveUserQuotaSummary':
   status=503 if summary_fails else 200
   data={'error':{'code':503,'message':'synthetic quota endpoint unavailable'}} if summary_fails else {'groups':[{'buckets':[
    {'bucketId':'gemini-weekly','remainingFraction':0,'resetTime':reset},
    {'bucketId':'gemini-5h','remainingFraction':1,'disabled':True,'resetTime':reset},
    {'bucketId':'gemini-argon-wtu-weekly','remainingFraction':left,'resetTime':reset}]}]}
  elif path=='/v1internal:generateContent':
   generation_count+=1
   assert body['model']=='gemini-4-argon-high',body['model']
   if BOOTSTRAP and generation_count==1:
    status=429;data={'error':{'code':429,'status':'RESOURCE_EXHAUSTED','message':'You have exhausted your capacity on this model. Your quota will reset after 23h0m0s.'}}
   else:
    status=200;data={'response':{'modelVersion':'synthetic-argon-model','candidates':[{'content':{'role':'model','parts':[{'text':'SYNTHETIC_RECOVERED'}]},'finishReason':'STOP'}],'usageMetadata':{'promptTokenCount':1,'candidatesTokenCount':1,'totalTokenCount':2}}}
  else:raise AssertionError('unexpected endpoint '+path)
  raw=json.dumps(data).encode();self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
server=ThreadingHTTPServer(('127.0.0.1',8899),Mock);threading.Thread(target=server.serve_forever,daemon=True).start()
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}));base='http://127.0.0.1:8787'
for n in range(150):
 try:
  with opener.open(base+'/healthz',timeout=2) as r:assert r.status==200;r.read()
  break
 except OSError:
  if n==149:raise
  time.sleep(.2)
def api(path,data=None,method=None):
 req=urllib.request.Request(base+'/admin/api/'+path,data=None if data is None else json.dumps(data).encode(),method=method,headers={'Content-Type':'application/json','Authorization':'Bearer synthetic-gateway-key'})
 with opener.open(req,timeout=30) as r:
  raw=r.read();return json.loads(raw) if raw else None
provider='synthetic-recovery-provider';cid='synthetic-recovery-account'
if BOOTSTRAP:
 api('providers',{'id':provider,'name':'synthetic-recovery','channel':'antigravity','baseUrl':'http://127.0.0.1:8899','config':{'account_session_id':True,'model_credentials':{'gemini-4-argon-high':[cid]}}})
 api('credentials',{'id':cid,'providerId':provider,'authKind':'oauth','label':'synthetic-only','secret':{'access_token':'synthetic-token','refresh_token':'synthetic-refresh','token_type':'Bearer','provider_fields':{'project_id':'synthetic-project'}}})
def generate(model='gemini-4-argon-high'):
 return api('models/test',{'providerId':provider,'credentialId':cid,'model':model})
def blocked():
 before=generation_count;r=generate();assert not r['ok'] and generation_count==before,r
if BOOTSTRAP:
 r=generate();assert r['status']==429,r;blocked()
 # Failed and exhausted quota refreshes must never release the refusal.
 summary_fails=True;r=api('credentials/'+cid+'/quota-diagnostics',{});assert r.get('error'),r;blocked()
 summary_fails=False;r=api('credentials/'+cid+'/quota-diagnostics',{});assert not r.get('error'),r;blocked()
 left=1;r=api('credentials/'+cid+'/quota-diagnostics',{});assert not r.get('error'),r
 # Fresh positive Argon clears the old response block and cached block;
 # ordinary Gemini still has an exhausted weekly window.
 r=generate();assert r.get('ok') and r.get('reply')=='SYNTHETIC_RECOVERED',('positive independent allowance did not recover',r)
 assert generation_count==2,generation_count
else:
 left=1
 r=generate();assert r.get('ok') and r.get('reply')=='SYNTHETIC_RECOVERED',('old exhaustion returned after restart',r)
 assert generation_count==1
before=generation_count;r=generate('gemini-3.8-flash-high');assert not r['ok'] and generation_count==before,('unrelated Gemini quota was cleared',r)
server.shutdown();server.server_close()
print(json.dumps({'phase':'first' if BOOTSTRAP else 'after_restart','passed':True,'fake_generation_calls':generation_count,'failed_or_empty_quota_did_not_unblock':True,'unrelated_gemini_exhaustion_preserved':True,'calls':calls}))
'''
def run(args,**kw):return subprocess.run(args,text=True,capture_output=True,check=True,**kw).stdout.strip()
def main():
 p=argparse.ArgumentParser();p.add_argument('--image',required=True);p.add_argument('--work',type=Path,required=True);a=p.parse_args()
 if os.geteuid()!=0 or a.work.exists():raise SystemExit('requires root and fresh private work dir')
 os.umask(0o077);a.work.mkdir(parents=True,mode=0o700);data=a.work/'data';data.mkdir();os.chown(data,65532,65532)
 env=a.work/'synthetic.env';env.write_text('GPROXY_MASTER_KEY='+'71'*32+'\nGPROXY_ADMIN_USER=synthetic-admin\nGPROXY_ADMIN_PASSWORD=synthetic-only-qa-password\nGPROXY_BOOTSTRAP_ADMIN_API_KEY=synthetic-gateway-key\nGPROXY_UPDATE_AUTOMATIC=false\nGPROXY_UPDATE_CHECK_INTERVAL=86400\n')
 name='gproxy-argon-recovery-canary-'+uuid.uuid4().hex[:10];created=False
 try:
  run(['docker','run','-d','--name',name,'--network','none','--memory','768m','--cpus','0.75','--env-file',str(env.resolve()),'--mount',f'type=bind,src={data.resolve()},dst=/app/data',a.image]);created=True
  phases=[]
  for bootstrap in [True,False]:
   if not bootstrap:run(['docker','restart','--time','10',name])
   pid=run(['docker','inspect','--format','{{.State.Pid}}',name]);phases.append(json.loads(run(['nsenter','--target',pid,'--net','python3','-'],input=f'BOOTSTRAP={bootstrap!r}\n'+SCRIPT)))
  result={'passed':True,'image':a.image,'network':'none','paid_inference_requests':0,'published_ports':0,'phases':phases};(a.work/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
 except subprocess.CalledProcessError as e:
  (a.work/'error.txt').write_text((e.stdout or '')+'\n'+(e.stderr or ''));raise
 finally:
  if created:
   logs=subprocess.run(['docker','logs',name],capture_output=True,text=True);(a.work/'canary.log').write_text(logs.stdout+logs.stderr);run(['docker','stop','--time','10',name]);run(['docker','rm',name])
  shutil.rmtree(data,ignore_errors=True);env.unlink(missing_ok=True);exists=subprocess.run(['docker','inspect',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0;(a.work/'cleanup.json').write_text(json.dumps({'container_removed':not exists,'test_database_deleted':not data.exists()})+'\n')
if __name__=='__main__':main()
