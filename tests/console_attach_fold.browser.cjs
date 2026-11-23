// Many attachments must not push Send off the screen: past a few, the tray folds into "+N".
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {chromium}=require('playwright');
const fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'../claude_discord/console/static');
const files=Array.from({length:10},(_,i)=>({name:`a-rather-long-attachment-name-number-${i}.pdf`,mimeType:'application/pdf',buffer:Buffer.from(`file ${i}`)}));
for(const [width,height,mobile] of [[1440,900],[1100,560],[390,844,true]])test(`many attachments keep Send reachable ${width}x${height}`,async t=>{
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});t.after(()=>browser.close());
 const page=await browser.newPage({viewport:{width,height},isMobile:!!mobile,hasTouch:!!mobile});page.setDefaultTimeout(4000);
 const errors=[];page.on('pageerror',e=>errors.push(e.message));t.after(()=>assert.deepEqual(errors,[]));
 const items=[{id:'t100',thread_id:'100',title:'Attachments',status:'idle',bucket:'idle'}];
 await page.route('http://console.test/**',async route=>{
  const name=new URL(route.request().url()).pathname;
  if(name==='/'||name.startsWith('/static/')){const file=name==='/'?'index.html':path.basename(name);return route.fulfill({body:fs.readFileSync(path.join(root,file)),contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});}
  const data=name.endsWith('/board')?{items,slots:{},places:[]}:name.endsWith('/messages')?{messages:[{id:'1',kind:'human',content:'hi'}]}:name.endsWith('/uploads')?{url:`/console/api/files/1/${Math.random()}/f.pdf`}:{};
  return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
 });
 await page.goto('http://console.test/?lang=en#idle/t100');
 await page.waitForSelector('#msgs .msg');
 await page.locator('#detail input[type=file]').setInputFiles(files);
 await page.waitForFunction(()=>ConsolePaste.ready('t100')?.length===10);
 const send=page.locator('#detail .reply button[type=submit]');
 const onTop=b=>{const r=b.getBoundingClientRect();return r.bottom<=innerHeight&&document.elementFromPoint(r.left+r.width/2,r.top+r.height/2)===b;};
 const reachable=()=>send.evaluate(onTop);
 const tray=page.locator('#detail .paste-tray');
 assert.equal(await tray.locator('.paste-file').count(),3);
 const more=tray.locator('.paste-more');
 assert.equal(await more.textContent(),'+7');
 assert.equal(await more.getAttribute('aria-expanded'),'false');
 assert.equal(await reachable(),true,'folded');
 assert.equal(await more.evaluate(onTop),true,'the toggle is in sight');
 if(process.env.CONSOLE_SCREENSHOT_DIR)await page.screenshot({path:`${process.env.CONSOLE_SCREENSHOT_DIR}/attach-folded-${width}.png`});
 await more.click();
 assert.equal(await tray.locator('.paste-file').count(),10);
 assert.equal(await tray.locator('.paste-more').getAttribute('aria-expanded'),'true');
 assert.equal(await reachable(),true,'expanded');
 assert.equal(await tray.locator('.paste-more').evaluate(onTop),true,'the toggle stays in sight');
 assert.equal(await page.locator('#reply').inputValue(),'','the toggle must not send');
 if(process.env.CONSOLE_SCREENSHOT_DIR)await page.screenshot({path:`${process.env.CONSOLE_SCREENSHOT_DIR}/attach-open-${width}.png`});
 // Removing files until few are left drops the toggle; folding again after that starts closed.
 for(let i=0;i<6;i++)await tray.locator('.paste-x').first().click();
 assert.equal(await tray.locator('.paste-file').count(),4);
 assert.equal(await tray.locator('.paste-more').count(),0);
 await page.locator('#detail input[type=file]').setInputFiles(files.slice(0,1));
 await page.waitForFunction(()=>ConsolePaste.ready('t100')?.length===5);
 assert.equal(await tray.locator('.paste-more').textContent(),'+2');
});
