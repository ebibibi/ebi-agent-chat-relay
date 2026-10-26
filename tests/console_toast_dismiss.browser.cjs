// Every toast has a ✕ that removes it at once, without firing the toast's own click.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {chromium}=require('playwright');
const fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'../claude_discord/console/static');
for(const [width,height] of [[1280,800],[390,844]])test(`toast dismisses from its own button ${width}x${height}`,async t=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});t.after(()=>browser.close());
 const page=await browser.newPage({viewport:{width,height},isMobile:width<900,hasTouch:width<900});page.setDefaultTimeout(4000);
 const errors=[];page.on('pageerror',e=>errors.push(e.message));t.after(()=>assert.deepEqual(errors,[]));
 const items=[{id:'t100',thread_id:'100',title:'Ship it',status:'idle',bucket:'idle'}];
 await page.route('http://console.test/**',async route=>{
  const name=new URL(route.request().url()).pathname;
  if(name==='/'||name.startsWith('/static/')){const file=name==='/'?'index.html':path.basename(name);return route.fulfill({body:fs.readFileSync(path.join(root,file)),contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});}
  const data=name.endsWith('/board')?{items,slots:{},places:[]}:name.endsWith('/messages')?{messages:[{id:'1',kind:'agent',content:'Finished.'}]}:{};
  return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
 });
 await page.goto('http://console.test/?lang=en#idle/t100');
 await page.waitForSelector('#msgs .msg');
 // A notification toast: clicking its body opens a thread, so ✕ must not.
 await page.evaluate(()=>{window.opened=0;const t=h('div',{class:'toast clickable',role:'status',text:'🔔 news',onclick:()=>{t.remove();window.opened++;}});showToast(t);});
 const x=page.locator('.toast.clickable .toast-x');
 assert.equal(await x.getAttribute('aria-label'),'Dismiss notification');
 const box=await x.boundingBox();
 assert.ok(box.width>=20&&box.height>=20,`✕ too small to hit: ${box.width}x${box.height}`);
 await x.click();
 assert.equal(await page.locator('.toast').count(),0);
 assert.equal(await page.evaluate(()=>window.opened),0);
 // A plain message toast closes the same way.
 await page.evaluate(()=>toast('saved'));
 await page.locator('.toast .toast-x').click();
 assert.equal(await page.locator('.toast').count(),0);
});
