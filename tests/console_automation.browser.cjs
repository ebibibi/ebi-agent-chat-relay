// The nav opens the automation AI dialog: every scheduler job with its choice and last run.
// Changing one job to a fixed backend and saving PUTs the whole policy with the CSRF header.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {chromium}=require('playwright');
const fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'../claude_discord/console/static');
const JOBS={default:{mode:'backend',backend:'codex',model:null,effort:null},master:{backend:'claude',model:'opus',effort:'high'},
 configured:false,jobs_error:null,backends:{codex:{efforts:['low','medium','high'],models:['gpt-5.5']},claude:{efforts:['low','high'],models:['opus','sonnet']}},
 jobs:[
  {id:'deep',name:'Deep work',enabled:true,schedules:['07:00'],entry:{mode:'master'},effective:{mode:'master'},last_run:{mode:'master',backend:'claude',model:'opus',effort:'high',thread_id:'9',at:'2026-11-16T05:00:00+00:00'}},
  {id:'plain',name:'Plain job',group:'Reports',enabled:false,schedules:[],ai_routes:['direct'],entry:null,effective:{mode:'backend',backend:'codex',model:null,effort:null},last_run:null}]};
for(const [width,height] of [[1280,800],[390,844]])test(`automation AI ${width}x${height}`,async t=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});t.after(()=>browser.close());
 const page=await browser.newPage({viewport:{width,height},isMobile:width<900,hasTouch:width<900});page.setDefaultTimeout(4000);
 const errors=[];page.on('pageerror',e=>errors.push(e.message));t.after(()=>assert.deepEqual(errors,[]));
 const puts=[];
 await page.route('http://console.test/**',async route=>{
  const req=route.request();const name=new URL(req.url()).pathname;
  if(name==='/'||name.startsWith('/static/')){const file=name==='/'?'index.html':path.basename(name);return route.fulfill({body:fs.readFileSync(path.join(root,file)),contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});}
  if(name==='/console/api/automation/jobs')return route.fulfill({contentType:'application/json',body:JSON.stringify(JOBS)});
  if(name==='/console/api/automation/ai-policy'&&req.method()==='PUT'){puts.push({body:JSON.parse(req.postData()),csrf:req.headers()['x-console-request']});return route.fulfill({contentType:'application/json',body:'{}'});}
  const data=name.endsWith('/board')?{items:[],slots:{},places:[]}:name.endsWith('/usage')?{now:Math.floor(Date.now()/1000),backends:[]}:{};
  return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
 });
 await page.goto('http://console.test/?lang=en');
 const nav=page.locator('.nav-automation').first();
 await nav.waitFor({state:'attached'});
 assert.equal(await page.locator('.nav-automation').count(),1);
 await nav.evaluate(b=>b.click());
 const card=page.locator('.card.automation');
 await card.waitFor();
 assert.match(await card.textContent(),/Not saved yet/);
 assert.match(await card.textContent(),/Claude · opus · high/);
 const rows=card.locator('tbody tr');
 assert.equal(await rows.count(),2);
 assert.match(await rows.nth(0).textContent(),/Last run: Claude · opus · high \(Follow the master\)/);
 assert.match(await rows.nth(1).textContent(),/Reports · disabled/);
 assert.match(await rows.nth(1).textContent(),/Not applied yet/);
 assert.doesNotMatch(await rows.nth(0).textContent(),/Not applied yet/);
 // Plain job: use the default → Claude with a model and effort.
 const plain=rows.nth(1);
 await plain.locator('select.auto-choice').selectOption('claude');
 await plain.locator('input.auto-model').fill('sonnet');
 await plain.locator('select.auto-effort').selectOption('high');
 await card.locator('button.primary').click();
 await page.waitForFunction(()=>document.querySelector('.toast')!==null);
 assert.equal(puts.length,1);
 assert.equal(puts[0].csrf,'1');
 assert.deepEqual(puts[0].body,{default:{mode:'backend',backend:'codex',model:null,effort:null},
  jobs:{deep:{mode:'master'},plain:{mode:'backend',backend:'claude',model:'sonnet',effort:'high'}}});
});
