// Filter lists by top-level Workspace context and group a bucket view by entry.
// Run: NODE_PATH=<dir with playwright> node --test tests/console_wsfilter.browser.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../claude_discord/console/static');

test('Workspace context filter and grouping', {timeout: 30000}, async t => {
  const browser = await chromium.launch({headless:true,args:['--no-sandbox']});
  t.after(() => browser.close());
  const page = await browser.newPage({viewport:{width:1440,height:900},locale:'ja-JP'});
  page.setDefaultTimeout(5000);
  const row = (id, title, ws) => ({id, thread_id:id.slice(1), title, status:'waiting', bucket:'me', priority:2,
    workspace_path:ws, workspace_source: ws ? 'pinned' : null});
  const items = [row('t1','Site fix','Work/Site'), row('t2','Site docs','Work/Site'), row('t3','Report','Work/Report'),
    row('t4','Garden','Home/Garden'), row('t5','Chat',null)];
  await page.route('http://console.test/**', async route => {
    const p = new URL(route.request().url()).pathname;
    if (p === '/' || p.startsWith('/static/')) {
      const file = p === '/' ? 'index.html' : path.basename(p);
      return route.fulfill({body:fs.readFileSync(path.join(root,file)),contentType:
        file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});
    }
    let data = {};
    if (p.endsWith('/board')) data = {items,slots:{},places:[]};
    else if (p.endsWith('/workspace/plan')) data = {available:false};
    else if (p.endsWith('/workspace')) data = {available:true,entries:[]};
    return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
  });
  const loaded = page.waitForResponse(r => r.url().endsWith('/workspace'));
  await page.goto('http://console.test/');
  await loaded;
  await page.evaluate(() => setView('me'));
  const bar = page.locator('.ws-filter-bar');
  await bar.waitFor();
  assert.match(await bar.innerText(), /Home 1[\s\S]*Work 3[\s\S]*未判定 1/);
  await bar.getByRole('button', {name:/^Work 3/}).click();
  await page.waitForFunction(() => document.querySelectorAll('#list .row').length === 3);
  await bar.getByRole('button', {name:/未判定/}).click();
  await page.waitForFunction(() => document.querySelectorAll('#list .row').length === 4);
  assert.deepEqual(await page.evaluate(() => JSON.parse(localStorage.getItem('wsFilter'))), ['Work','__none__']);
  await bar.getByRole('button', {name:'すべて'}).click();
  await page.waitForFunction(() => document.querySelectorAll('#list .row').length === 5);
  // Grouping: one heading per entry, unresolved last.
  await page.locator('.ws-filter-bar').getByRole('button', {name:'≡ プロジェクト別'}).click();
  await page.waitForFunction(() => [...document.querySelectorAll('#list .group')].some(g => g.textContent === '🗂️ Work/Site'));
  const groups = await page.locator('#list .group').allInnerTexts();
  assert.deepEqual(groups, ['🗂️ Home/Garden','🗂️ Work/Report','🗂️ Work/Site','❔ 未判定']);
  // Survives a reload.
  await page.reload();
  await page.locator('#list .group').first().waitFor();
  assert.equal(await page.locator('#list .group').count(), 4);
});
