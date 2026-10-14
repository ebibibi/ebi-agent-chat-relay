const {test} = require('node:test');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname,'../claude_discord/console/static');
test('message search shows safe snippets, ignores stale requests and resumes the original thread', {timeout:20000}, async t=>{
  const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
  t.after(()=>browser.close());
  const page=await browser.newPage({viewport:{width:390,height:844},locale:'ja-JP'});
  page.setDefaultTimeout(4000);
  const item={id:'t1',thread_id:'1',title:'Original conversation',bucket:'done',status:'done',priority:2};
  await page.route('**/*',async route=>{
    const url=new URL(route.request().url()), p=url.pathname;
    if(p==='/'||p.startsWith('/static/')){
      const file=p==='/'?'index.html':path.basename(p);
      return route.fulfill({body:fs.readFileSync(path.join(root,file)),contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});
    }
    let data={};
    if(p.endsWith('/board'))data={items:[item],slots:{},places:[]};
    if(p.endsWith('/messages'))data={messages:[{id:'m1',is_bot:true,content:'Migration done'}]};
    if(p.endsWith('/workspace'))data={available:false,entries:[]};
    if(p.endsWith('/search')){
      if(url.searchParams.get('q')==='slow')await new Promise(resolve=>setTimeout(resolve,800));
      data={results:url.searchParams.get('q')==='empty'?[]:[{item,snippet:'Earlier migration <img src=x onerror=alert(1)>',source:'body'}]};
    }
    return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.goto('http://console.test/');
  await page.waitForFunction(()=>state.items.length===1);
  await page.locator('#filter').fill('migration');
  await page.locator('#search-messages').click();
  await page.locator('.search-result').waitFor();
  assert.match(await page.locator('.search-result').textContent(),/Earlier migration/);
  assert.equal(await page.locator('.search-result img').count(),0);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await page.locator('#message-search-query').fill('slow');
  await page.locator('#message-search-query').press('Enter');
  await page.locator('#message-search-query').fill('empty');
  await page.locator('#message-search-query').press('Enter');
  await page.waitForTimeout(1000);
  assert.equal(await page.locator('.search-result').count(),0);
  await page.locator('#message-search-query').fill('migration');
  await page.locator('#message-search-query').press('Enter');
  await page.locator('.search-result').click();
  await page.waitForFunction(()=>state.open==='t1'&&state.messagesFor==='t1');
  assert.equal(await page.locator('#message-search').isVisible(),false);
  assert.match(await page.locator('#detail').textContent(),/Migration done/);
});
