const {test}=require('node:test'),assert=require('node:assert/strict');
const {chromium}=require('playwright');
const fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'../claude_discord/console/static');
async function setup(t,backend='codex',failure=false,width=1280){
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});t.after(()=>browser.close());
 const page=await browser.newPage({viewport:{width,height:900}});page.setDefaultTimeout(3500);
 let finish;const pending=new Promise(r=>finish=r),requests=[];
 let context={used:339902,window:1000000,pct:34};
 await page.route('http://console.test/**',async route=>{
  const req=route.request(),p=new URL(req.url()).pathname;
  if(p==='/'||p.startsWith('/static/')){const file=p==='/'?'index.html':path.basename(p);return route.fulfill({body:fs.readFileSync(path.join(root,file)),contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});}
  if(p.endsWith('/command')){requests.push(req.postDataJSON());await pending;if(failure)return route.fulfill({status:502,contentType:'application/json',body:JSON.stringify({error:'Compact failed'})});context=null;return route.fulfill({contentType:'application/json',body:JSON.stringify({message:'Conversation compacted'})});}
  let data={};
  if(p.endsWith('/board'))data={items:[{id:'t100',thread_id:'100',title:'Codex work',status:'idle',bucket:'idle',context}],slots:{},places:[]};
  else if(p.endsWith('/commands'))data={backend,model:'test-model',effort:'high',commands:[],skills:[],builtins:[{name:'compact',description:'Compact',handled:backend==='codex'}],backends:[],models:[],efforts:[]};
  else if(p.endsWith('/messages'))data={messages:[]};
  return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
 });
 await page.goto('http://console.test/?lang=en#idle/t100');await page.locator('#reply').waitFor();
 await page.waitForFunction(()=>document.querySelector('.slash-now')?.textContent.includes('test-model'));
 return {page,requests,finish};
}
for(const width of [1280,390]) test(`exact Codex compact runs on Enter with progress at ${width}px`,async t=>{
 const {page,requests,finish}=await setup(t,'codex',false,width);
 await page.locator('#reply').fill('/compact');await page.locator('#reply').press('Enter');
 await page.waitForFunction(()=>document.querySelector('.compact-status'));
 assert.equal(requests.length,1);assert.deepEqual(requests[0],{name:'compact',arg:''});
 assert.equal(await page.locator('#reply').isDisabled(),true);
 assert.ok((await page.getByRole('status').allTextContents()).some(s=>s.includes('Compacting')));
 finish();await page.waitForFunction(()=>!document.querySelector('#reply').disabled);
 assert.equal(await page.locator('#reply').inputValue(),'');
 assert.equal(await page.locator('.compact-status').count(),0);
 await page.waitForFunction(()=>!document.querySelector('.d-ctx'));
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
});
test('partial completion and Shift+Enter remain nonexecuting',async t=>{
 const {page,requests}=await setup(t);
 await page.locator('#reply').fill('/comp');await page.locator('.slash-opt').first().waitFor();
 await page.locator('#reply').press('Tab');assert.equal(await page.locator('#reply').inputValue(),'/compact ');
 assert.equal(requests.length,0);
 await page.locator('#reply').press('Shift+Enter');assert.match(await page.locator('#reply').inputValue(),/\n/);
 assert.equal(requests.length,0);
});
test('Claude builtin Enter still completes instead of invoking Codex compact',async t=>{
 const {page,requests}=await setup(t,'claude');
 await page.locator('#reply').fill('/compact');await page.locator('.slash-opt').first().waitFor();
 await page.locator('#reply').press('Enter');assert.equal(requests.length,0);
 assert.equal(await page.locator('#reply').inputValue(),'/compact ');
});


test('compact failure removes progress and keeps the command for retry',async t=>{
 const {page,finish}=await setup(t,'codex',true);
 await page.locator('#reply').fill('/compact');await page.locator('#reply').press('Enter');
 await page.locator('.compact-status').waitFor();finish();
 await page.waitForFunction(()=>!document.querySelector('#reply').disabled);
 assert.equal(await page.locator('#reply').inputValue(),'/compact');
 assert.equal(await page.locator('.compact-status').count(),0);
});
