#!/usr/bin/env python3
"""Desktop/iPhone WebKit console QA on a fresh isolated copy of migrated data.

Automatic quota probes are intercepted with a labelled synthetic fixture.
Only login and management reads reach the server. Screenshots/logs stay private.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess

from validate_v4_snapshot import env_values


JS = r'''
import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
const {webkit,devices}=await import(pathToFileURL(process.argv[2]).href);
const envFile=process.argv[3],work=process.argv[4];
const env={};
for(const line of fs.readFileSync(envFile,'utf8').split('\n')){
 if(line.includes('=')&&!line.trimStart().startsWith('#')){
  const at=line.indexOf('=');env[line.slice(0,at).trim()]=line.slice(at+1).trim().replace(/^['"]|['"]$/g,'');
 }
}
const url='http://127.0.0.1:8787/console';
const base='http://127.0.0.1:8787';
const result={engine:'WebKit',screens:[],surfaces:[],fixtureProbes:0,blockedMutations:0,externalRequests:0,pageErrors:[]};
const now=Date.now();
const windows=[['3p-5h',null,'antigravity_disabled'],['3p-weekly','100',null],['gemini-5h','35',null],['gemini-weekly','45',null]];
const fixture={observedAtMs:now,entries:windows.map(([id,usedPercent,label])=>({id,sourceId:id,kind:'window',label,subject:'account',modelScope:{model_prefixes:id.startsWith('3p')?['claude','gpt']:['gemini']},allowance:{used:null,limit:null,remaining:null,usedPercent,unlimited:false,unit:'percent',periodStartMs:null,periodEndMs:now+3600000,resetBehavior:'observed'},balance:null,breakdown:null}))};
const browser=await webkit.launch({headless:true});
try{
 for(const mode of ['desktop','iphone']){
  const context=await browser.newContext(mode==='desktop'?{viewport:{width:1440,height:1000},locale:'en-US'}:{...devices['iPhone 13'],locale:'en-US'});
  const page=await context.newPage();
  page.setDefaultTimeout(20000);
  page.on('pageerror',e=>result.pageErrors.push(String(e)));
  await context.route('**/*',async route=>{
   const req=route.request(),u=new URL(req.url());
   if(u.origin!==base){result.externalRequests++;return route.abort();}
   if(u.pathname.endsWith('/quota-probe')){result.fixtureProbes++;return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(fixture)});}
   if(u.pathname.endsWith('/quota-reset-credits'))return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({availableCount:0,options:[],creditExpirationsMs:[]})});
   if(!['GET','HEAD'].includes(req.method())&&!['/portal/api/login','/portal/api/logout'].includes(u.pathname)){
    result.blockedMutations++;return route.fulfill({status:412,contentType:'application/json',body:JSON.stringify({error:{code:'qa_readonly',message:'Readonly isolated QA'}})});
   }
   return route.continue();
  });
  async function screenshot(name){
   await page.waitForTimeout(180);
   const dimensions=await page.evaluate(()=>({width:innerWidth,height:innerHeight,documentWidth:document.documentElement.scrollWidth}));
   if(dimensions.documentWidth>dimensions.width+2)throw Error('page-level horizontal clipping at '+name);
   const file=path.join(work,mode+'-'+name+'.png');
   await page.screenshot({path:file,scale:'css'});result.screens.push({mode,name,file,dimensions});
  }
  async function fit(locator,name){
   const box=await locator.boundingBox();
   const v=page.viewportSize();
   if(!box||box.x<-.5||box.y<-.5||box.x+box.width>v.width+.5||box.y+box.height>v.height+.5)throw Error('primary control clipped: '+name);
  }
  async function nav(route){
   const href='/console'+route;
   const trigger=page.getByRole('button',{name:'Navigation',exact:true});
   if(await trigger.isVisible()){await trigger.click();await page.getByRole('dialog').waitFor();}
   const visible=page.locator('a[href="'+href+'"]:visible').first();
   if(!await visible.count()){
    const summary=page.locator('nav details:has(a[href="'+href+'"]):visible > summary').first();
    await summary.click();
   }
   await page.locator('a[href="'+href+'"]:visible').first().click();
   await page.waitForURL('**'+href);await page.waitForTimeout(250);
   result.surfaces.push({mode,route});
  }
  await page.goto(url,{waitUntil:'domcontentloaded'});
  await page.locator('#sign-in-name').waitFor();
  await fit(page.locator('button[type=submit]'),'login submit');await screenshot('login');
  await page.locator('#sign-in-name').fill(env.GPROXY_ADMIN_USER);
  await page.locator('#sign-in-password').fill('synthetic-wrong-password');
  await page.locator('button[type=submit]').click();await page.getByRole('alert').waitFor();
  await page.locator('#sign-in-password').fill(env.GPROXY_ADMIN_PASSWORD);
  await page.locator('button[type=submit]').click();
  await page.locator('header').waitFor();
  // The upstream privacy notice is a local UI acknowledgement, not a write.
  const dialogs=page.getByRole('dialog');
  if(await dialogs.count()){
   const close=dialogs.getByRole('button').last();await close.click();
  }
  await nav('/providers');await screenshot('providers');
  const search=page.getByRole('textbox',{name:'Search providers',exact:true});
  if(await search.count()){
   await search.fill('qa-does-not-exist');await page.waitForTimeout(250);await search.fill('');
  }
  const target=page.locator('a[href="/console/providers/v3-providers-4/credentials"]:visible');
  await target.first().click();await page.waitForURL('**/v3-providers-4/credentials');
  await page.getByRole('button',{name:'Upstream allowance',exact:true}).first().waitFor();
  await screenshot('credentials');
  await page.getByRole('button',{name:'Upstream allowance',exact:true}).first().click();
  const dialog=page.getByRole('dialog');await dialog.waitFor();
  await dialog.getByText('Claude / GPT · 5-hour quota (inactive)',{exact:true}).waitFor();
  for(const text of ['Claude / GPT · weekly quota','Gemini · 5-hour quota','Gemini · weekly quota'])await dialog.getByText(text,{exact:true}).first().waitFor();
  const inactive=dialog.locator('[data-slot=card]').filter({hasText:'Claude / GPT · 5-hour quota (inactive)'}).last();
  if(await inactive.locator('time,[role=progressbar]').count())throw Error('inactive window incorrectly advertises progress/reset');
  if(!(await inactive.innerText()).includes('Check the weekly quota'))throw Error('inactive weekly guidance missing');
  await screenshot('inactive-quota-fixture');
  const trend=dialog.getByRole('button',{name:/Gemini · 5-hour quota.*Show/}).first();
  if(await trend.count()){
   await trend.click();await page.waitForTimeout(350);await trend.click();
  }
  await dialog.getByRole('button',{name:'Close',exact:true}).last().click();
  await page.getByRole('tab',{name:'Routing rules',exact:true}).click();
  await page.waitForURL('**/v3-providers-4/routing');await screenshot('routing');
  await page.getByRole('tab',{name:'Models',exact:true}).click();
  await page.waitForURL('**/v3-providers-4/models');await screenshot('models-pricing');
  for(const route of ['/model-routes','/observation/usage','/observation/upstream','/identity/audit','/settings']){
   await nav(route);await screenshot(route.split('/').filter(Boolean).join('-'));
  }
  // Locale changes are normal menu input and local browser preferences only.
  const english=page.getByRole('button',{name:'English',exact:true});
  if(await english.count()){
   await english.click();await page.getByRole('menuitemradio',{name:'简体中文',exact:true}).click();
   await screenshot('locale-zh-CN');
   await page.getByRole('button',{name:'简体中文',exact:true}).click();await page.getByRole('menuitemradio',{name:'繁體中文',exact:true}).click();
   await screenshot('locale-zh-TW');
   await page.getByRole('button',{name:'繁體中文',exact:true}).click();await page.getByRole('menuitemradio',{name:'English',exact:true}).click();
  }
  if(mode==='iphone')await fit(page.getByRole('button',{name:'Navigation',exact:true}),'navigation toggle');
  await context.close();
 }
 if(result.pageErrors.length)throw Error('console runtime errors were recorded');
 if(!result.fixtureProbes)throw Error('quota fixture was not exercised');
 result.passed=true;result.paidInferenceRequests=0;result.realQuotaProbes=0;
 fs.writeFileSync(path.join(work,'browser-result.json'),JSON.stringify(result,null,2)+'\n',{mode:0o600});
 console.log(JSON.stringify({passed:true,engine:result.engine,screens:result.screens.length,surfaces:result.surfaces.length,fixtureProbes:result.fixtureProbes,realQuotaProbes:0,pageErrors:0}));
}catch(e){
 fs.writeFileSync(path.join(work,'browser-failure.json'),JSON.stringify({error:String(e),result},null,2)+'\n',{mode:0o600});throw e;
}finally{await browser.close();}
'''


def run(args, **kw):
    return subprocess.run(args, text=True, capture_output=True, check=True, **kw).stdout.strip()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--image", required=True)
    p.add_argument("--snapshot", type=Path, required=True)
    p.add_argument("--env-file", type=Path, required=True)
    p.add_argument("--work", type=Path, required=True)
    p.add_argument("--playwright", type=Path, default=Path("/home/ubuntu/silllytaven/migration/node_modules/playwright/index.mjs"))
    p.add_argument("--node", type=Path, default=Path("/home/ubuntu/.nvm/versions/node/v20.19.6/bin/node"))
    args = p.parse_args()
    if os.geteuid() != 0 or args.work.exists():
        raise SystemExit("requires root and a fresh private QA directory")
    if args.snapshot.resolve() == Path("/home/ubuntu/gproxy/data/gproxy.db"):
        raise SystemExit("never browse a production data mount in a canary")
    name = "gproxy-upgrade-v4-webkit-canary"
    if subprocess.run(["docker", "inspect", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
        raise SystemExit("prior WebKit canary exists; investigate it first")
    wal = Path(str(args.snapshot) + "-wal")
    if wal.exists() and wal.stat().st_size:
        raise SystemExit("supply a closed migrated database")
    os.umask(0o077)
    args.work.mkdir(mode=0o700, parents=True)
    data = args.work / "data"
    data.mkdir(mode=0o700)
    target = data / "gproxy.db"
    shutil.copyfile(args.snapshot, target)
    for file in (data, target):
        os.chown(file, 65532, 65532)
    env = args.work / "canary.env"
    env.write_text("GPROXY_MASTER_KEY=" + env_values(args.env_file)["GPROXY_MASTER_KEY"] + "\nGPROXY_UPDATE_AUTOMATIC=false\n")
    env.chmod(0o600)
    created = False
    try:
        run(["docker", "run", "-d", "--name", name, "--network", "none", "--memory", "768m", "--cpus", "0.75",
             "--env-file", str(env.resolve()), "--mount", f"type=bind,src={data.resolve()},dst=/app/data",
             "--label", "gproxy.upgrade.validation=webkit-only", args.image])
        created = True
        pid = run(["docker", "inspect", "--format", "{{.State.Pid}}", name])
        import time
        # Readiness only through the canary loopback. No bridge/host port.
        ready = "import urllib.request;op=urllib.request.build_opener(urllib.request.ProxyHandler({}));r=op.open('http://127.0.0.1:8787/healthz',timeout=2);assert r.status==200"
        for n in range(80):
            done = subprocess.run(["nsenter", "--target", pid, "--net", "python3", "-c", ready], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if done.returncode == 0:
                break
            if n == 79:
                raise SystemExit("WebKit canary did not become ready")
            time.sleep(0.25)
        browser_env = {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": "/home/ubuntu/.cache/ms-playwright"}
        browser = subprocess.run(["nsenter", "--target", pid, "--net", str(args.node.resolve()), "--input-type=module", "-",
                                  str(args.playwright.resolve()), str(args.env_file.resolve()), str(args.work.resolve())],
                                 input=JS, text=True, capture_output=True, env=browser_env)
        log = args.work / "browser.log"
        log.write_text(browser.stdout + browser.stderr)
        log.chmod(0o600)
        if browser.returncode:
            raise SystemExit(f"WebKit QA failed ({browser.returncode}); inspect private browser.log")
        print(browser.stdout.strip())
    finally:
        if created:
            logs = subprocess.run(["docker", "logs", name], text=True, capture_output=True)
            log = args.work / "canary.log"
            log.write_text(logs.stdout + logs.stderr)
            log.chmod(0o600)
            run(["docker", "stop", "--time", "15", name])
            run(["docker", "rm", name])


if __name__ == "__main__":
    main()
