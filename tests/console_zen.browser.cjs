// Focus modes: one thread full screen, and a queue that moves to the next thread on send.
// The Focus effort chips narrow the list and the queue to one time bucket.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {chromium}=require('playwright');
const fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'../claude_discord/console/static');
const item=(id,title,focus,effort,minutes,extra={})=>({id,thread_id:id.slice(1),title,status:'waiting',bucket:'me',priority:1,focus,actionable:true,effort,ai:{priority:1,minutes,next_step:'answer'},last_activity_at:'2026-10-24T01:00:00Z',...extra});
async function open(t,width,items,replies){
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});t.after(()=>browser.close());
 const page=await browser.newPage({viewport:{width,height:800},isMobile:width<900,hasTouch:width<900});page.setDefaultTimeout(4000);
 const errors=[];page.on('pageerror',e=>errors.push(e.message));t.after(()=>assert.deepEqual(errors,[]));
 await page.route('http://console.test/**',async route=>{
  const name=new URL(route.request().url()).pathname;
  if(name==='/'||name.startsWith('/static/')){const file=name==='/'?'index.html':path.basename(name);return route.fulfill({body:fs.readFileSync(path.join(root,file)),contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});}
  if(name.endsWith('/reply')){const id=decodeURIComponent(name.split('/').slice(-2)[0]);replies.push(id);const it=items.find(i=>i.id===id);Object.assign(it,{status:'running',bucket:'ai',running:true,focus:null});return route.fulfill({contentType:'application/json',body:JSON.stringify({delivery:'started'})});}
  const data=name.endsWith('/board')?{items,slots:{},places:[]}:name.endsWith('/messages')?{messages:[{id:'1',kind:'agent',content:'Question for you.'}]}:{};
  return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
 });
 await page.goto('http://console.test/?lang=en#focus');
 await page.waitForSelector('.effort-bar');
 return page;
}
for(const width of [1280,390])test(`queue works through threads one by one ${width}`,async t=>{
 const items=[item('t1','Quick one',230,'quick',2),item('t2','Deep one',220,'deep',45),item('t3','Second quick',210,'quick',1)];
 const replies=[];
 const page=await open(t,width,items,replies);
 // The rows carry the estimate; the quick chip narrows Focus to quick work.
 assert.ok((await page.locator('.row .tag.effort').first().textContent()).includes('2 min'));
 await page.locator('.effort-bar .chip',{hasText:'Quick'}).click();
 assert.equal(await page.locator('#list .row').count(),2);
 await page.locator('.zen-start').click();
 await page.waitForFunction(()=>state.open==='t1');
 assert.ok(await page.evaluate(()=>document.body.classList.contains('zen-queue')));
 assert.equal(await page.locator('.list-pane').isVisible(),false);
 assert.ok((await page.locator('.zen-bar').textContent()).includes('1 / 2'));
 await page.locator('#reply').fill('yes');
 await page.locator('#reply').press('Control+Enter');
 // Sending moves straight on to the next quick thread, skipping the deep one.
 await page.waitForFunction(()=>state.open==='t3');
 assert.deepEqual(replies,['t1']);
 assert.ok((await page.locator('.zen-bar').textContent()).includes('2 / 2'));
 await page.locator('.zen-skip').click();
 await page.waitForSelector('#zen-empty:not([hidden])');
 // A thread that starts needing you joins the queue and opens on its own.
 items.push(item('t4','New question',240,'quick',2));
 await page.evaluate(()=>refresh());
 await page.waitForFunction(()=>state.open==='t4');
 await page.keyboard.press('Escape');
 await page.locator('body').press('Escape');
 await page.waitForFunction(()=>!document.body.classList.contains('zen'));
 assert.equal(await page.evaluate(()=>state.open),'t4');
});
test('single-thread focus hides the list and leaves with Esc',async t=>{
 const items=[item('t1','Quick one',230,'quick',2)];
 const page=await open(t,1280,items,[]);
 await page.locator('#list .row').first().click();
 await page.waitForSelector('.d-zen');
 await page.locator('.d-zen').click();
 assert.ok(await page.evaluate(()=>document.body.classList.contains('zen')));
 assert.equal(await page.locator('.nav').isVisible(),false);
 assert.ok((await page.locator('.zen-bar').textContent()).includes('Focus mode'));
 await page.locator('body').press('Escape');
 await page.waitForFunction(()=>!document.body.classList.contains('zen'));
 assert.equal(await page.evaluate(()=>state.open),'t1');
 await page.keyboard.press('z');
 assert.ok(await page.evaluate(()=>document.body.classList.contains('zen')));
});
