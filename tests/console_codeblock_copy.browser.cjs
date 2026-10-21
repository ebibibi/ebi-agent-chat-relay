// Fenced code blocks in a message carry a copy button, so a long URL can be copied on a phone
// without selecting it. navigator.clipboard is missing on plain http, so execCommand is the fallback.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {chromium}=require('playwright');
const fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'../claude_discord/console/static');
const url='https://login.microsoftonline.com/common/oauth2/v2.0/authorize?client_id=abc&redirect_uri=http%3A%2F%2Flocalhost&scope=openid+offline_access&state='+'x'.repeat(300);
const content='Open this:\n```\n'+url+'\n```\nand `inline` stays plain.\n```sh\necho  "two  spaces"\nexit 0\n```';

async function open(t,{width,height,clipboard}){
 const browser=await chromium.launch({headless:true,args:['--no-sandbox']});t.after(()=>browser.close());
 const page=await browser.newPage({viewport:{width,height},isMobile:width<900,hasTouch:width<900});page.setDefaultTimeout(4000);
 const errors=[];page.on('pageerror',e=>errors.push(e.message));t.after(()=>assert.deepEqual(errors,[]));
 await page.addInitScript(clipboard=>{
  window.copied=[];
  if(clipboard)Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async s=>{window.copied.push(s);}}});
  else{
   Object.defineProperty(navigator,'clipboard',{configurable:true,value:undefined});
   document.execCommand=cmd=>{if(cmd!=='copy')return false;const a=document.activeElement;window.copied.push(a.value.slice(a.selectionStart,a.selectionEnd));return true;};
  }
 },clipboard);
 const items=[{id:'t100',thread_id:'100',title:'Auth',status:'idle',bucket:'idle'}];
 await page.route('http://console.test/**',async route=>{
  const name=new URL(route.request().url()).pathname;
  if(name==='/'||name.startsWith('/static/')){const file=name==='/'?'index.html':path.basename(name);return route.fulfill({body:fs.readFileSync(path.join(root,file)),contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});}
  const data=name.endsWith('/board')?{items,slots:{},places:[]}:name.endsWith('/messages')?{messages:[{id:'1',kind:'agent',is_bot:true,content}]}:{};
  return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
 });
 await page.goto('http://console.test/?lang=ja#idle/t100');
 await page.waitForSelector('#msgs .msg pre');
 return page;
}

for(const [width,height] of [[1280,800],[390,844]])test(`code block copy button ${width}x${height}`,async t=>{
 const page=await open(t,{width,height,clipboard:true});
 assert.equal(await page.locator('#msgs pre').count(),2);
 assert.equal(await page.locator('#msgs .code-copy').count(),2,'one button per fenced block, none for inline code');
 const pre=await page.locator('#msgs pre').first().boundingBox();
 const btn=await page.locator('#msgs .code-copy').first().boundingBox();
 assert.ok(btn.x+btn.width<=pre.x+pre.width+1&&btn.x+btn.width>pre.x+pre.width-24,`button hugs the right edge: ${JSON.stringify({pre,btn})}`);
 assert.ok(btn.y>=pre.y-1&&btn.y<pre.y+16,`button sits at the top: ${JSON.stringify({pre,btn})}`);
 assert.ok(btn.x+btn.width<=width,'button stays on screen even when the code overflows');
 const first=page.locator('#msgs .code-copy').first();
 await first.click();
 await page.waitForFunction(()=>window.copied.length===1);
 assert.equal(await page.evaluate(()=>window.copied[0]),url);
 assert.equal(await first.textContent(),'コピーしました');
 await page.locator('#msgs .code-copy').nth(1).click();
 await page.waitForFunction(()=>window.copied.length===2);
 assert.equal(await page.evaluate(()=>window.copied[1]),'echo  "two  spaces"\nexit 0');
 await page.waitForFunction(()=>document.querySelector('#msgs .code-copy').textContent==='コピー',null,{timeout:2500});
});

test('code block copy falls back to execCommand without navigator.clipboard',async t=>{
 const page=await open(t,{width:390,height:844,clipboard:false});
 const first=page.locator('#msgs .code-copy').first();
 await first.click();
 await page.waitForFunction(()=>window.copied.length===1);
 assert.equal(await page.evaluate(()=>window.copied[0]),url);
 assert.equal(await first.textContent(),'コピーしました');
 assert.equal(await page.locator('textarea.code-copy-buffer').count(),0,'the temporary textarea is removed');
});
