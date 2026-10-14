// Run with Playwright installed: node --test tests/console_bulk.browser.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../claude_discord/console/static');
const fixture = (id, extra = {}) => ({id, title: `Thread ${id}`, thread_id: id,
  status: 'idle', bucket: 'idle', priority: 2, ...extra});
async function setup(t, items) {
  const browser = await chromium.launch({headless:true, args:['--no-sandbox']});
  t.after(() => browser.close());
  const page = await browser.newPage({viewport:{width:1440,height:900}});
  page.setDefaultTimeout(4000);
  const app = {items, requests:[], fail: new Set(), gate: null};
  await page.addInitScript(() => Object.defineProperty(navigator, 'clipboard', {value: {writeText: async text => {window.copied = text;}}}));
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  t.after(() => assert.deepEqual(errors, []));
  await page.route('http://console.test/**', async route => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === '/' || pathname.startsWith('/static/')) {
      const file = pathname === '/' ? 'index.html' : path.basename(pathname);
      return route.fulfill({body:fs.readFileSync(path.join(root,file)),
        contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});
    }
    const done = pathname.match(/\/items\/([^/]+)\/done$/);
    if (done) {
      const id = decodeURIComponent(done[1]); app.requests.push(id);
      if (app.gate) await app.gate;
      if (app.fail.has(id)) return route.fulfill({status:503, contentType:'application/json', body:JSON.stringify({error:'Please retry'})});
      app.items = app.items.map(i => i.id === id ? {...i,bucket:'done',status:'done'} : i);
    }
    const data = pathname.endsWith('/board') ? {items:app.items,slots:{},places:[]} :
      pathname.endsWith('/messages') ? {messages:[]} : {};
    await route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.goto('http://console.test/?lang=en');
  await page.waitForFunction(() => state.items.length > 0);
  await page.evaluate(() => setView('idle'));
  app.page = page;
  app.row = id => page.locator(`.row[data-item-id="${id}"]`).last();
  app.selected = () => page.locator('.bulk-check:checked').evaluateAll(nodes => [...new Set(nodes.map(n => n.closest('.row').dataset.itemId))]);
  return app;
}


for (const width of [1440,390]) test(`language switch preserves work at width ${width}`,async t=>{
 const app=await setup(t,[fixture('t100')]);const page=app.page;
 await page.setViewportSize({width,height:844});
 await page.locator('#search-messages').click();
 await page.getByRole('button',{name:'Close',exact:true}).click();
 await page.evaluate(()=>{window.noReload=true;return openItem('t100');});
 await page.locator('#reply').fill('Keep my reply');
 await page.locator('#capture-input').fill('Keep my capture');
 await page.route('**/console/api/uploads',r=>r.fulfill({contentType:'application/json',body:JSON.stringify({url:'/console/api/files/1/test/report.txt'})}));
 await page.locator('#detail input[type=file]').setInputFiles({name:'report.txt',mimeType:'text/plain',buffer:Buffer.from('notes')});
 await page.waitForFunction(()=>window.ConsolePaste.ready('t100')?.length===1);
 if(width===390)await page.locator('.detail-nav-toggle').click();
 await page.locator('#language-select').selectOption('ja');
 assert.equal(await page.locator('html').getAttribute('lang'),'ja');
 assert.equal(await page.evaluate(()=>window.noReload),true);
 assert.equal(await page.locator('#reply').inputValue(),'Keep my reply');
 assert.equal(await page.locator('#capture-input').inputValue(),'Keep my capture');
 assert.equal(await page.evaluate(()=>state.open),'t100');
 assert.equal(await page.evaluate(()=>window.ConsolePaste.ready('t100').length),1);
 assert.equal(await page.locator('#detail .paste-tray').isVisible(),true);
 assert.match(await page.locator('#reply').getAttribute('placeholder'),/返信/);
 assert.match(await page.locator('.nav-auth').innerText(),/パスキー/);
 await page.locator('#d-head button[aria-label=close]').click();
 await page.locator('#search-messages').click();
 await page.getByRole('button',{name:'閉じる',exact:true}).click();
 await app.row('t100').click();
 if(width===390)await page.locator('.detail-nav-toggle').click();
 await page.locator('#language-select').selectOption('en');
 assert.match(await page.locator('.nav-auth').innerText(),/Passkeys/);
 assert.equal(new URL(page.url()).searchParams.get('lang'),'en');
 await page.reload();
 assert.equal(await page.locator('html').getAttribute('lang'),'en');
 assert.equal(await page.locator('#language-select').inputValue(),'en');
 assert.equal(await page.evaluate(()=>localStorage.getItem('lang')),'en');
});
