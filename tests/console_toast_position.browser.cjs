// Toasts sit at the top right so they never cover the reply box the human is typing in.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {chromium}=require('playwright');
const fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'../claude_discord/console/static');
for(const [width,height] of [[1280,800],[390,844]])test(`toast stays clear of the reply box ${width}x${height}`,async t=>{
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
 await page.evaluate(()=>toast('a fairly long notification that has to wrap rather than run off the screen edge'));
 const box=await page.locator('.toast').boundingBox();
 const reply=await page.locator('.reply textarea').boundingBox();
 assert.ok(box.y<height/3,`toast top ${box.y}`);
 assert.ok(box.x+box.width<=width-8&&box.x>=8,`toast x ${box.x} w ${box.width}`);
 assert.ok(box.x+box.width>width*0.6,`toast should hug the right edge: ${box.x}+${box.width}`);
 const head=await page.locator('#d-head').boundingBox();
 if(head&&head.height)assert.ok(box.y>=head.y+head.height,`toast ${box.y} covers the header buttons ending at ${head.y + head.height}`);
 if(reply)assert.ok(box.y+box.height<reply.y,`toast ${box.y + box.height} overlaps reply ${reply.y}`);
});
