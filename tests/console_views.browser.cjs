// Saved views: save a rule from the filter box, list it in the menu, open it, delete it.
// Run: NODE_PATH=<dir with playwright> node --test tests/console_views.browser.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../claude_discord/console/static');

test('a rule saved as a view selects threads across lists', {timeout: 30000}, async t => {
  const browser = await chromium.launch({headless:true,args:['--no-sandbox']});
  t.after(() => browser.close());
  const page = await browser.newPage({viewport:{width:1440,height:900},locale:'ja-JP'});
  page.setDefaultTimeout(5000);
  page.on('pageerror', e => console.log('PAGEERROR', e.message));
  page.on('dialog', d => d.accept(d.type() === 'prompt' ? 'Teams返信' : undefined));
  const row = (id, title, status, bucket, tags) => ({id, thread_id:id.slice(1), title, status, bucket, priority:2, tags});
  const items = [row('t1','Site reply','review','me',['teams-exporter']), row('t2','Lab reply','running','ai',['teams-exporter']),
    row('t3','Old reply','done','done',['teams-exporter']), row('t4','Fix bug','waiting','me',[])];
  let views = [];
  const posted = [];
  await page.route('http://console.test/**', async route => {
    const req = route.request();
    const p = new URL(req.url()).pathname;
    if (p === '/' || p.startsWith('/static/')) {
      const file = p === '/' ? 'index.html' : path.basename(p);
      return route.fulfill({body:fs.readFileSync(path.join(root,file)),contentType:
        file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});
    }
    let data = {};
    if (p.endsWith('/board')) data = {items,slots:{},places:[],views};
    else if (p.endsWith('/views') && req.method() === 'POST') {
      const body = req.postDataJSON();
      posted.push(body);
      views = [...views, {id:'v0123456789', name:body.name, query:body.query, position:0}];
      data = {views};
    } else if (p.includes('/views/') && req.method() === 'DELETE') { views = []; data = {views}; }
    else if (p.endsWith('/workspace/plan')) data = {available:false};
    else if (p.endsWith('/workspace')) data = {available:false,entries:[]};
    return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.goto('http://console.test/');
  await page.waitForFunction(() => state.items.length === 4);
  await page.evaluate(() => setView('recent'));
  // Typing a rule narrows the list and offers to save it.
  assert.equal(await page.locator('#save-view').isHidden(), true);
  await page.fill('#filter', 'tag:teams-exporter');
  await page.waitForFunction(() => document.querySelectorAll('#list .row').length === 3);
  await page.locator('#save-view').click();
  assert.deepEqual(posted, [{name:'Teams返信', query:'tag:teams-exporter'}]);
  // The view opens: done stays hidden unless the rule asks, and every list's items are in it.
  await page.waitForFunction(() => document.getElementById('view-title').textContent === '🔖 Teams返信');
  assert.equal(await page.inputValue('#filter'), '');
  await page.evaluate(() => setHideDone(false));
  await page.waitForFunction(() => document.querySelectorAll('#list .row').length === 2);
  assert.match(await page.locator('.view-bar').innerText(), /tag:teams-exporter/);
  const nav = page.locator('.nav-views-saved');
  assert.match(await nav.innerText(), /保存ビュー[\s\S]*Teams返信\s*2/);
  assert.equal(await page.evaluate(() => location.hash), '#v0123456789');
  assert.match(await page.locator('#list .row').first().innerText(), /#teams-exporter/);
  // A term that cannot be read is shown instead of silently matching nothing.
  await page.fill('#filter', 'p:9');
  await page.locator('.view-unknown').waitFor();
  await page.fill('#filter', '');
  // Survives a reload, then deleting it returns to the default list.
  await page.reload();
  await page.waitForFunction(() => document.getElementById('view-title').textContent === '🔖 Teams返信');
  await page.locator('.view-bar').getByRole('button', {name:'🗑 削除'}).click();
  await page.waitForFunction(() => !document.querySelector('.nav-views-saved'));
  assert.equal(await page.evaluate(() => state.view), 'focus');
});
