const {test} = require('node:test');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname,'../claude_discord/console/static');
test('attachment and preview copy the full original text, with retry after denial', {timeout:20000}, async t=>{
  const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
  t.after(()=>browser.close());
  const page=await browser.newPage({viewport:{width:1440,height:1000},locale:'ja-JP'});
  page.setDefaultTimeout(5000);
  page.on('pageerror', error => console.error(error.message));
  const text='日本語の原文\n'+'x'.repeat(2*1024*1024)+'\nEND-OF-FULL-FILE';
  const local='/console/api/files/1/'+'a'.repeat(32)+'/full.txt';
  await page.addInitScript(()=>{
    window.copied=[]; window.denyCopy=false;
    Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{
      if(window.denyCopy)throw new Error('Clipboard denied');
      window.copied.push(text);
    }}});
  });
  await page.route('**/*',async route=>{
    const url=new URL(route.request().url());
    if(url.hostname==='files.test'||url.pathname===local)return route.fulfill({contentType:'text/plain',body:text});
    const p=url.pathname;
    if(p==='/'||p.startsWith('/static/')){
      const file=p==='/'?'index.html':path.basename(p);
      return route.fulfill({body:fs.readFileSync(path.join(root,file)),contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});
    }
    let data={};
    if(p.endsWith('/board'))data={items:[{id:'t1',thread_id:'1',title:'Files',bucket:'idle',status:'idle',priority:2}],slots:{},places:[]};
    if(p.endsWith('/messages'))data={messages:[{id:'m1',is_bot:true,content:local,attachments:[{filename:'remote.txt',url:'https://files.test/remote.txt'}]}]};
    if(p.endsWith('/workspace'))data={available:false,entries:[]};
    return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.goto('http://console.test/');
  await page.waitForFunction(()=>state.items.length===1);
  await page.evaluate(()=>openItem('t1'));
  await page.locator('.file .file-copy').click();
  await page.waitForFunction(()=>window.copied.length===1);
  assert.equal(await page.evaluate(()=>window.copied[0]),text);
  await page.locator('.live-files .file-copy').waitFor();
  await page.locator('.live-files .file-copy').click();
  await page.waitForFunction(()=>window.copied.length===2);
  assert.equal(await page.evaluate(()=>window.copied[1]),text);
  await page.locator('.file-look').click();
  await page.locator('.pv-note').waitFor();
  assert.ok(!(await page.locator('.pv-body').textContent()).includes('END-OF-FULL-FILE'));
  await page.locator('.pv-bar .file-copy').click();
  await page.waitForFunction(()=>window.copied.length===3);
  assert.equal(await page.evaluate(()=>window.copied[2]),text);
  await page.evaluate(()=>{window.denyCopy=true;});
  await page.locator('.pv-bar .file-copy').click();
  await page.getByText(/Clipboard denied/).waitFor();
  assert.equal(await page.locator('.pv-bar .file-copy').isEnabled(),true);
  await page.evaluate(()=>{window.denyCopy=false;});
  await page.locator('.pv-bar .file-copy').click();
  await page.waitForFunction(()=>window.copied.length===4);
});
