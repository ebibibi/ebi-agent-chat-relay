// On a phone the usage strip folds to the default backend: the others sit behind a +N toggle on
// the same row, a sign-in chip stays out, and changing the default re-folds. Wide screens show all.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {chromium}=require('playwright');
const fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'../claude_discord/console/static');
const win=(type,utilization)=>({type,utilization,resets_at:Math.floor(Date.now()/1000)+3600,status:'allowed',reset:false});
const USAGE={backends:[
 {backend:'claude',profile:null,available:true,unavailable_until:null,windows:[win('five_hour',.09),win('seven_day',.34)]},
 {backend:'codex',profile:null,available:true,unavailable_until:null,windows:[win('five_hour',.1)]},
 {backend:'antigravity',profile:null,available:true,unavailable_until:null,windows:[win('gemini',.95)]}]};
for(const [width,height] of [[1280,800],[390,844]])test(`usage fold ${width}x${height}`,async t=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});t.after(()=>browser.close());
 const page=await browser.newPage({viewport:{width,height},isMobile:width<900,hasTouch:width<900});page.setDefaultTimeout(4000);
 const errors=[];page.on('pageerror',e=>errors.push(e.message));t.after(()=>assert.deepEqual(errors,[]));
 let def='claude';
 await page.route('http://console.test/**',async route=>{
  const name=new URL(route.request().url()).pathname;
  if(name==='/'||name.startsWith('/static/')){const file=name==='/'?'index.html':path.basename(name);return route.fulfill({body:fs.readFileSync(path.join(root,file)),contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});}
  const data=name.endsWith('/board')?{items:[],slots:{},places:[]}
   :name.endsWith('/usage')?{now:Math.floor(Date.now()/1000),...USAGE}
   :name.endsWith('/defaults')?{backend:def,model:null,effort:null}
   :name.endsWith('/cli-login')?{backends:[{backend:'claude',logged_in:true,method:null,flow:null},{backend:'codex',logged_in:false,method:null,flow:null}]}
   :{};
  return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
 });
 await page.goto('http://console.test/?lang=en');
 const names=()=>page.locator('#usage .u-be:not(.cl-chip) .u-name').evaluateAll(e=>e.map(x=>x.firstChild.textContent));
 const toggle=page.locator('#usage .u-toggle');
 await page.locator('#usage .cl-chip').waitFor();
 if(width>=900){
  assert.deepEqual(await names(),['Claude','Codex','Antigravity']);
  assert.equal(await toggle.count(),0);
  return;
 }
 await page.waitForFunction(()=>document.querySelectorAll('#usage .u-be:not(.cl-chip) .u-name').length===1);
 assert.deepEqual(await names(),['Claude']);
 assert.equal(await toggle.textContent(),'+2 ▾');
 assert.equal(await toggle.getAttribute('aria-expanded'),'false');
 assert.equal(await page.locator('#usage .cl-chip').count(),1,'a sign-in chip is not folded away');
 // The toggle shares the default backend's row instead of taking one of its own.
 const [be,tg]=await Promise.all([page.locator('#usage .u-be:not(.cl-chip)').first().boundingBox(),toggle.boundingBox()]);
 assert.ok(tg.y<be.y+be.height,'toggle sits beside the folded backend');
 await toggle.click();
 assert.deepEqual(await names(),['Claude','Codex','Antigravity']);
 assert.equal(await toggle.textContent(),'▴');
 await toggle.click();
 assert.deepEqual(await names(),['Claude']);
 // Keyboard and screen-reader users stay on the toggle through its own re-render and the
 // periodic one, instead of being dropped to the top of the page.
 const onToggle=()=>page.evaluate(()=>document.activeElement?.classList.contains('u-toggle'));
 await toggle.focus();await page.keyboard.press('Space');
 assert.equal(await toggle.textContent(),'▴');
 assert.equal(await onToggle(),true,'focus stays on the toggle after it re-renders');
 await page.evaluate(()=>window.dispatchEvent(new Event('resize')));
 assert.equal(await onToggle(),true,'focus survives a periodic re-render');
 // A new default re-folds the strip around it, even while it is expanded.
 def='codex';
 await page.evaluate(()=>window.ConsoleBackend.loadDefaults());
 assert.deepEqual(await names(),['Codex']);
 assert.equal(await toggle.textContent(),'+2 ▾');
 assert.equal(await toggle.getAttribute('aria-expanded'),'false');
});
