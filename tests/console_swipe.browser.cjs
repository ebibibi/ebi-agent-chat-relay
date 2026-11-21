const {test}=require('node:test');
const assert=require('node:assert/strict');
const {chromium}=require('playwright');
const fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'../claude_discord/console/static');
test('mobile swipes go back, finish or pin a row, and open or close the menu',async t=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});t.after(()=>browser.close());
 const page=await browser.newPage({viewport:{width:390,height:844},isMobile:true,hasTouch:true});page.setDefaultTimeout(4000);
 const errors=[];page.on('pageerror',e=>errors.push(e.message));t.after(()=>assert.deepEqual(errors,[]));
 const items=[{id:'t100',thread_id:'100',title:'A thread',status:'idle',bucket:'idle'}];
 const messages=[{id:'1',kind:'agent',content:'Hello\n\n```\n'+'wide_code_line_'.repeat(40)+'\n```'}];
 const writes=[];
 await page.route('http://console.test/**',async route=>{
  const name=new URL(route.request().url()).pathname;
  if(!['GET','HEAD'].includes(route.request().method()))writes.push(`${route.request().method()} ${name} ${route.request().postData()||''}`);
  if(name==='/'||name.startsWith('/static/')){const file=name==='/'?'index.html':path.basename(name);return route.fulfill({body:fs.readFileSync(path.join(root,file)),contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});}
  const data=name.endsWith('/board')?{items,slots:{},places:[]}:name.endsWith('/messages')?{messages}:/\/(commands|children|defaults)$/.test(name)?{backend:'claude',model:'opus',effort:'high',backends:['claude'],models:[],efforts:['high'],skills:[]}:{};
  return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
 });
 const cdp=await page.context().newCDPSession(page);
 const swipe=async(x1,x2,y=400)=>{
  await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x:x1,y}]});
  for(const f of [.33,.66,1])await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:x1+(x2-x1)*f,y}]});
  await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
  await page.waitForTimeout(50);
 };
 const navOpen=()=>page.evaluate(()=>$('nav').classList.contains('open'));
 await page.goto('http://console.test/?lang=en#idle/t100');
 await page.waitForSelector('#msgs .msg');
 // Dragging a wide code block scrolls it; it does not leave the thread.
 const code=await page.locator('#msgs pre').boundingBox();
 await swipe(code.x+20,code.x+200,code.y+code.height/2);
 assert.equal(await page.evaluate(()=>state.open),'t100');
 // A short or mostly vertical drag is a scroll, not a swipe.
 await swipe(40,90);
 assert.equal(await page.evaluate(()=>state.open),'t100');
 await swipe(40,300);
 assert.equal(await page.evaluate(()=>state.open),null);
 assert.equal(await navOpen(),false);
 // On a list row: right to left pins, left to right finishes; neither opens the thread.
 const row=await page.locator('#list .row').boundingBox();
 const y=row.y+row.height/2, touch=(type,x)=>cdp.send('Input.dispatchTouchEvent',{type,touchPoints:x===undefined?[]:[{x,y}]});
 const band=()=>page.evaluate(()=>{const l=$('list'),b=getComputedStyle(l,'::before'),r=document.querySelector('#list .row');
  return {kind:l.dataset.swipe,label:b.content,bg:b.backgroundColor,opacity:b.opacity,top:b.top,x:new DOMMatrix(getComputedStyle(r).transform).m41,rowTop:r.offsetTop+'px'};});
 // Mid-drag the row follows the finger over a coloured band that says what letting go will do.
 await touch('touchStart',40);await touch('touchMove',60);await touch('touchMove',120);
 let b=await band();
 assert.equal(b.kind,'finish');assert.equal(b.label,'"✓ Finish"');assert.equal(b.bg,'rgb(94, 196, 161)');
 assert.equal(b.x,80);assert.equal(b.top,b.rowTop);assert.equal(b.opacity,'0.55');
 // A poll re-renders the list mid-drag; the new row picks the drag up on the next move.
 await page.evaluate(()=>renderList());
 await touch('touchMove',220);await page.waitForTimeout(200);
 b=await band();assert.equal(b.x,180);assert.equal(b.opacity,'1');
 if(process.env.CONSOLE_SCREENSHOT_DIR)await page.screenshot({path:`${process.env.CONSOLE_SCREENSHOT_DIR}/swipe-finish.png`});
 await touch('touchMove',100);await page.waitForTimeout(200);
 b=await band();assert.equal(b.x,60);assert.equal(b.opacity,'0.55');
 // Let go short of a third of the row: it springs back and nothing is sent.
 await touch('touchEnd');await page.waitForTimeout(300);
 b=await band();assert.equal(b.kind,undefined);assert.equal(b.x,0);assert.deepEqual(writes,[]);
 assert.equal(await page.evaluate(()=>state.open),null);
 await touch('touchStart',300);await touch('touchMove',280);await touch('touchMove',200);
 await touch('touchMove',100);await page.waitForTimeout(200);
 b=await band();assert.equal(b.kind,'pin');assert.equal(b.label,'"📌 Pin"');assert.equal(b.bg,'rgb(255, 180, 84)');
 if(process.env.CONSOLE_SCREENSHOT_DIR)await page.screenshot({path:`${process.env.CONSOLE_SCREENSHOT_DIR}/swipe-pin.png`});
 await touch('touchCancel');await page.waitForTimeout(300);
 assert.deepEqual(writes,[]);
 await swipe(300,60,y);
 await page.waitForTimeout(400);
 assert.match(writes.join('\n'),/PATCH \S*\/items\/t100 \{"pinned":true\}/);
 writes.length=0;
 await swipe(40,300,y);
 await page.waitForTimeout(300);
 assert.match(writes.join('\n'),/POST \S*\/items\/t100\/done/);assert.equal(await page.locator('.bulk-selected').count(),0);
 assert.equal(await page.evaluate(()=>state.open),null);
 assert.equal(await navOpen(),false);
 // Below the rows, the list opens and closes the menu.
 await swipe(40,300,800);
 assert.equal(await navOpen(),true);
 await swipe(250,30,800);
 assert.equal(await navOpen(),false);
 // Wide screens keep the desktop layout and ignore touches.
 await page.setViewportSize({width:1200,height:800});
 await swipe(40,300);
 assert.equal(await navOpen(),false);
});
