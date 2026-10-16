const {test} = require('node:test');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname,'../claude_discord/console/static');
test('a thread deleted from Discord opens read only inside search on a phone', {timeout:20000}, async t=>{
  const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
  t.after(()=>browser.close());
  const page=await browser.newPage({viewport:{width:390,height:844},isMobile:true,hasTouch:true,locale:'ja-JP'});
  page.setDefaultTimeout(4000);
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const archived={id:'a555',title:'FortiGate 冗長化',bucket:'done',last_activity_at:'2026-10-03T00:00:00+00:00'};
  await page.route('**/*',async route=>{
    const url=new URL(route.request().url()), p=url.pathname;
    if(p==='/'||p.startsWith('/static/')){
      const file=p==='/'?'index.html':path.basename(p);
      return route.fulfill({body:fs.readFileSync(path.join(root,file)),contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});
    }
    let data={};
    if(p.endsWith('/board'))data={items:[],slots:{},places:[]};
    if(p.endsWith('/workspace'))data={available:false,entries:[]};
    if(p.endsWith('/search'))data={results:[{item:archived,snippet:'FGCP heartbeat',source:'archive',archived:true}]};
    if(p.endsWith('/archive/555'))data={thread_id:'555',title:'FortiGate 冗長化',messages:[
      {id:'1',author:'me',is_bot:false,kind:'human',content:'2台目が届いた <img src=x onerror=alert(1)>',created_at:'2026-10-01T00:00:00+00:00'},
      {id:'2',author:'AI',is_bot:true,kind:'agent',content:'**FGCP** で組みます',created_at:'2026-10-02T00:00:00+00:00'}]};
    return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.goto('http://console.test/');
  await page.waitForFunction(()=>Array.isArray(state.items));
  await page.locator('#search-messages').tap();
  await page.locator('#message-search-query').fill('fgcp');
  await page.locator('#message-search-query').press('Enter');
  await page.locator('.search-result').waitFor();
  assert.match(await page.locator('.search-result').textContent(),/削除済み/);
  await page.locator('.search-result').tap();
  await page.locator('#message-search .msg.agent').waitFor();
  assert.equal(await page.locator('#message-search').isVisible(),true);
  assert.equal(await page.locator('#message-search .msg').count(),2);
  assert.equal(await page.locator('#message-search .msg img').count(),0);
  assert.equal(await page.locator('#message-search .msg.agent strong').textContent(),'FGCP');
  assert.equal(await page.evaluate(()=>state.open),null);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  if(process.env.CONSOLE_SCREENSHOT_DIR)await page.screenshot({path:`${process.env.CONSOLE_SCREENSHOT_DIR}/archive.png`});
  await page.getByText('← 検索結果に戻る').tap();
  await page.locator('.search-result').waitFor();
  assert.deepEqual(errors,[]);
});
