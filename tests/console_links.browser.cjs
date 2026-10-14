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

test('copy a canonical URL without query credentials; pasted and relative links navigate in place', async t => {
  const app = await setup(t, [fixture('t100'), fixture('t200')]);
  const page = app.page;
  await page.evaluate(() => {history.replaceState(null,'','/?token=private#idle/t100'); return openItem('t100');});
  await page.locator('.thread-copy-link').click();
  assert.equal(await page.evaluate(() => window.copied), 'http://console.test/#focus/t100');
  await page.evaluate(() => {const box=document.createElement('div');box.id='test-links';box.append(MD.render('[target](/#focus/t200) [absolute](http://console.test/#focus/t100)'));document.body.append(box);});
  await page.locator('#test-links a').first().click();
  await page.waitForFunction(() => state.open === 't200');
  assert.equal(page.context().pages().length, 1);
  await page.goBack();
  await page.waitForFunction(() => state.open === 't100');
  await page.goForward();
  await page.waitForFunction(() => state.open === 't200');
  await page.locator('#test-links a').last().click();
  await page.waitForFunction(() => state.open === 't100');
});
test('hash changes open completed child threads and keep direct reply opt-in', async t => {
  const app=await setup(t,[fixture('t100'),fixture('t200',{parent_id:'t100',status:'done',bucket:'done'})]);
  await app.page.evaluate(() => {location.hash='#focus/t200';});
  await app.page.waitForFunction(() => state.open === 't200');
  assert.equal(await app.page.evaluate(() => state.directReply ?? null),null);
  assert.equal(await app.page.getByRole('button',{name:'Reply directly to this child',exact:true}).count(),1);
  await app.page.reload();
  await app.page.waitForFunction(() => state.open === 't200' && state.messagesFor === 't200');
});
test('missing targets report an error; untrusted links stay external and unsafe URLs stay text', async t => {
  const app=await setup(t,[fixture('t100')]);
  await app.page.evaluate(() => openItem('t100'));
  await app.page.evaluate(() => {const box=document.createElement('div');box.id='test-links';box.append(MD.render('[missing](/#focus/t404) [external](https://elsewhere.test/#focus/t100) [bad](javascript:alert) [bad relative](//evil.test/#focus/t100)'));document.body.append(box);});
  assert.equal(await app.page.locator('#test-links a').count(),2);
  assert.equal(await app.page.locator('#test-links a').last().getAttribute('target'),'_blank');
  await app.page.locator('#test-links a').first().click();
  await app.page.waitForFunction(() => document.querySelector('.toast')?.textContent.includes('not found'));
  assert.equal(await app.page.evaluate(() => state.open),'t100');
});
test('clipboard denial gives an error instead of claiming success', async t => {
  const app=await setup(t,[fixture('t100')]);
  await app.page.evaluate(() => {navigator.clipboard.writeText=async()=>{throw new Error('denied');};return openItem('t100');});
  await app.page.locator('.thread-copy-link').click();
  await app.page.waitForFunction(() => document.querySelector('.toast')?.textContent.includes('denied'));
  assert.equal(await app.page.locator('.thread-copy-link').isDisabled(),false);
});
