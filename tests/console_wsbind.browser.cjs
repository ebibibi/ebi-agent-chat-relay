// Workspace binding badge: source icons, confirm a guess, pick, "no Workspace", automatic,
// and a capture's Workspace choice not carried over to the next capture.
// Run: NODE_PATH=<dir with playwright> node --test tests/console_wsbind.browser.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../claude_discord/console/static');

test('Workspace binding badge and picker', {timeout: 30000}, async t => {
  const browser = await chromium.launch({headless:true,args:['--no-sandbox']});
  t.after(() => browser.close());
  const page = await browser.newPage({viewport:{width:1440,height:900},locale:'ja-JP'});
  page.setDefaultTimeout(5000);
  const entries = [
    {path:'Work',name:'Work',kind:'context',context:'Work',file:'single-actions.md',revision:'a',tasks:[]},
    {path:'Work/Website',name:'Website',kind:'project',context:'Work',file:'status.md',revision:'b',tasks:[]},
    {path:'Home/Garden',name:'Garden',kind:'project',context:'Home',file:'status.md',revision:'c',tasks:[]},
  ];
  const item = {id:'t1',thread_id:'1',title:'Fix the site',status:'waiting',bucket:'me',priority:2,
    workspace_path:'Work/Website',workspace_source:'heuristic',workspace_reason:'cwd'};
  const loose = {id:'t2',thread_id:'2',title:'Chat',status:'waiting',bucket:'me',priority:2};
  const patches = [], posts = [];
  await page.route('http://console.test/**', async route => {
    const req = route.request();
    const p = new URL(req.url()).pathname;
    if (p === '/' || p.startsWith('/static/')) {
      const file = p === '/' ? 'index.html' : path.basename(p);
      return route.fulfill({body:fs.readFileSync(path.join(root,file)),contentType:
        file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});
    }
    let data = {};
    if (p.endsWith('/board')) data = {items:[item,loose],slots:{},places:[]};
    else if (p.endsWith('/workspace/plan')) data = {available:false,reason:'plugin'};
    else if (p.endsWith('/workspace')) data = {available:true,entries};
    else if (p.endsWith('/items/t1') && req.method() === 'PATCH') {
      const body = req.postDataJSON(); patches.push(body);
      if (body.workspace_path) Object.assign(item, {workspace_path:body.workspace_path,workspace_source:'pinned',workspace_reason:null});
      else if (body.workspace_source === 'pinned') Object.assign(item, {workspace_path:null,workspace_source:'pinned',workspace_reason:null});
      else Object.assign(item, {workspace_path:null,workspace_source:null,workspace_reason:null});
      data = {item};
    } else if (p.endsWith('/items') && req.method() === 'POST') {
      posts.push(req.postDataJSON());
      data = {item:{id:'c'+posts.length,title:'x',bucket:'todo',status:'open',priority:2}};
    } else if (p.endsWith('/messages')) data = {messages:[]};
    return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
  });
  const loaded = page.waitForResponse(r => r.url().endsWith('/workspace'));
  await page.goto('http://console.test/');
  await loaded;
  await page.evaluate(() => setView('me'));
  // List rows: a guess shows 🧭, an unbound thread shows no tag.
  await page.locator('.ws-bind').first().waitFor();
  assert.equal(await page.locator('.row .ws-bind').count(), 1);
  assert.match(await page.locator('.row .ws-bind').innerText(), /🧭 📂 Work\/Website/);
  assert.match(await page.locator('.row .ws-bind').getAttribute('title'), /作業フォルダが一致/);
  await page.evaluate(() => openItem('t1'));
  const control = page.locator('.ws-bind-control');
  await control.waitFor();
  assert.match(await control.innerText(), /作業フォルダが一致/);
  // Confirm pins the guess.
  await control.getByRole('button', {name:'✓ 確定'}).click();
  await page.waitForFunction(() => document.querySelector('.ws-bind-control .ws-pinned'));
  assert.deepEqual(patches.at(-1), {workspace_path:'Work/Website'});
  assert.equal(await control.getByRole('button', {name:'✓ 確定'}).count(), 0);
  // Search and pick another entry.
  await control.getByRole('button', {name:'変更'}).click();
  await page.locator('.ws-bind-search').fill('home gar');
  assert.equal(await page.locator('.ws-bind-option').count(), 1);
  await page.locator('.ws-bind-search').press('Enter');
  await page.waitForFunction(() => /Home\/Garden/.test(document.querySelector('.ws-bind-control').innerText));
  assert.deepEqual(patches.at(-1), {workspace_path:'Home/Garden'});
  // Recently used comes first next time; "no Workspace" and "automatic".
  await page.locator('.ws-bind-control').getByRole('button', {name:'変更'}).click();
  assert.match(await page.locator('.ws-bind-list').innerText(), /最近[\s\S]*Home\/Garden[\s\S]*すべて/);
  await page.getByRole('option', {name:'👤 Workspace なし'}).click();
  await page.waitForFunction(() => /Workspace なし/.test(document.querySelector('.ws-bind-control').innerText));
  assert.deepEqual(patches.at(-1), {workspace_path:null,workspace_source:'pinned'});
  assert.equal(await page.locator('.row .ws-bind').count(), 0);
  await page.locator('.ws-bind-control').getByRole('button', {name:'変更'}).click();
  await page.getByRole('option', {name:'🔄 自動に戻す'}).click();
  await page.waitForFunction(() => /未判定/.test(document.querySelector('.ws-bind-control').innerText));
  assert.deepEqual(patches.at(-1), {workspace_path:null});
  // Escape closes the picker.
  await page.locator('.ws-bind-control').getByRole('button', {name:'変更'}).click();
  await page.locator('.ws-bind-search').press('Escape');
  assert.equal(await page.locator('.ws-bind-pop').count(), 0);
  // The capture picker applies to one capture only and is not remembered.
  await page.locator('#workspace-capture select').selectOption('Work');
  await page.locator('#capture-input').fill('First');
  await page.locator('#capture-input').press('Control+Enter');
  await page.waitForFunction(() => document.querySelector('#workspace-capture select').value === '');
  assert.equal(posts[0].workspace_path, 'Work');
  await page.locator('#capture-input').fill('Second');
  await page.locator('#capture-input').press('Control+Enter');
  for (let i = 0; i < 50 && posts.length < 2; i++) await page.waitForTimeout(100);
  assert.equal(posts[1].workspace_path, undefined);
  assert.equal(await page.evaluate(() => localStorage.getItem('workspacePath')), null);
});
