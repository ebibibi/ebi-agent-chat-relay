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
test('Shift-click selects the displayed range and completes just those IDs', async t => {
  const app = await setup(t, ['a','b','c','d'].map(id=>fixture(id)));
  await app.row('b').click();
  await app.row('d').click({modifiers:['Shift']});
  assert.deepEqual(await app.selected(), ['b','c','d']);
  assert.equal(await app.page.evaluate(() => state.open), 'b');
  await app.page.locator('#bulk-finish').click();
  await app.page.waitForFunction(() => document.querySelector('#bulk-status').textContent.includes('3 completed'));
  assert.deepEqual(app.requests, ['b','c','d']);
  assert.deepEqual(await app.selected(), []);
});
test('checkbox selection survives reorder by ID, reconciles filtering and supports keyboard', async t => {
  const app = await setup(t, ['a','b','c','d'].map(id=>fixture(id)));
  await app.row('a').locator('.bulk-check').check();
  await app.row('c').locator('.bulk-check').check();
  app.items = [app.items[3],app.items[2],app.items[0],app.items[1]];
  await app.page.evaluate(() => refresh());
  assert.deepEqual(await app.selected(), ['c','a']);
  await app.page.locator('#filter').fill('Thread c');
  assert.deepEqual(await app.selected(), ['c']);
  await app.page.locator('#bulk-clear').click();
  assert.deepEqual(await app.selected(), []);
  const cb = app.row('c').locator('.bulk-check');
  await cb.focus(); await app.page.keyboard.press('Space');
  assert.deepEqual(await app.selected(), ['c']);
  await app.page.locator('#bulk-finish').click();
  await app.page.waitForFunction(() => document.querySelector('#bulk-status').textContent.includes('1 completed'));
  assert.deepEqual(app.requests, ['c']);
});
test('families stay together and parent completion includes descendants', async t => {
  const app = await setup(t, [fixture('parent',{pinned_at:'2026-10-14'}),fixture('child',{parent_id:'parent'}),fixture('other')]);
  await app.page.evaluate(() => setView('tree'));
  await app.row('parent').click();
  await app.row('child').click({modifiers:['Shift']});
  assert.deepEqual(await app.selected(), ['parent','child']);
  await app.page.locator('#bulk-clear').click();
  await app.row('parent').locator('.bulk-check').check();
  await app.page.locator('#bulk-finish').click();
  await app.page.waitForFunction(() => document.querySelector('#bulk-status').textContent.includes('2 completed'));
  assert.deepEqual(app.requests, ['child','parent']);
  assert.equal(app.items.find(i=>i.id==='child').bucket,'done');
});
test('partial failure stays visible and retry only sends failures; busy clicks do not duplicate', async t => {
  const app = await setup(t, ['a','b','c'].map(id=>fixture(id)));
  app.fail.add('b');
  let release; app.gate = new Promise(resolve => {release=resolve;});
  await app.row('a').click(); await app.row('c').click({modifiers:['Shift']});
  await app.page.locator('#bulk-finish').click();
  assert.equal(await app.page.locator('#bulk-finish').isDisabled(),true);
  await app.page.evaluate(() => document.querySelector('#bulk-finish').click());
  release(); app.gate = null;
  await app.page.waitForFunction(() => document.querySelector('#bulk-status').textContent.includes('1 failed'));
  assert.deepEqual(app.requests,['a','b','c']);
  assert.deepEqual(await app.selected(),['b']);
  assert.match(await app.page.locator('#bulk-errors').innerText(),/Thread b.*Please retry/s);
  app.fail.clear(); await app.page.locator('#bulk-finish').click();
  await app.page.waitForFunction(() => document.querySelector('#bulk-status').textContent.includes('1 completed'));
  assert.deepEqual(app.requests,['a','b','c','b']);
});

test('child list entry opens the parent; direct child replies require a fresh explicit choice', async t => {
  const app = await setup(t, [fixture('child',{parent_id:'parent'}), fixture('other'),fixture('parent')]);
  assert.deepEqual(await app.page.locator('.row').evaluateAll(nodes=>nodes.map(n=>n.dataset.itemId)), ['parent','child','other']);
  await app.row('child').click();
  assert.equal(await app.page.evaluate(()=>state.open),'parent');
  assert.equal(await app.page.locator('#d-family').isVisible(),true);
  await app.page.locator('.family-open').click();
  assert.equal(await app.page.evaluate(()=>state.open),'child');
  assert.equal(await app.page.locator('form.reply').isVisible(),false);
  await app.page.locator('.family-direct').click();
  assert.equal(await app.page.locator('form.reply').isVisible(),true);
  assert.match(await app.page.locator('#reply').getAttribute('placeholder'),/child/i);
  await app.page.locator('.family-parent').click();
  await app.page.locator('.family-open').click();
  assert.equal(await app.page.locator('form.reply').isVisible(),false);
});
test('a failed descendant keeps its parent open and retries the family', async t => {
  const app = await setup(t,[fixture('parent'),fixture('child',{parent_id:'parent'}),fixture('grandchild',{parent_id:'child'})]);
  app.fail.add('grandchild');
  await app.row('parent').locator('.bulk-check').check();
  await app.page.locator('#bulk-finish').click();
  await app.page.waitForFunction(()=>document.querySelector('#bulk-status').textContent.includes('3 failed'));
  assert.deepEqual(app.requests,['grandchild']);
  assert.equal(app.items.find(i=>i.id==='parent').bucket,'idle');
  app.fail.clear();
  await app.page.locator('#bulk-finish').click();
  await app.page.waitForFunction(()=>document.querySelector('#bulk-status').textContent.includes('3 completed'));
  assert.deepEqual(app.requests,['grandchild','grandchild','child','parent']);
});
test('an already completed intermediate child cannot hide a failed grandchild', async t => {
  const app = await setup(t,[fixture('parent'),fixture('child',{parent_id:'parent',bucket:'done',status:'done'}),fixture('grandchild',{parent_id:'child'})]);
  app.fail.add('grandchild');
  await app.row('parent').locator('.bulk-check').check();
  await app.page.locator('#bulk-finish').click();
  await app.page.waitForFunction(()=>document.querySelector('#bulk-status').textContent.includes('failed'));
  assert.deepEqual(app.requests,['grandchild']);
  assert.equal(app.items.find(i=>i.id==='parent').bucket,'idle');
});
test('bulk toolbar and grouped rows fit desktop and mobile', async t => {
  const app = await setup(t,[fixture('parent'),fixture('child',{parent_id:'parent'}),fixture('other')]);
  await app.row('parent').locator('.bulk-check').check();
  for (const width of [1440,390]) {
    await app.page.setViewportSize({width,height:844});
    if (width === 390) await app.page.waitForFunction(()=>document.querySelector('#nav').getBoundingClientRect().right <= 0);
    assert.ok(await app.page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth));
    assert.equal(await app.page.locator('#bulk-finish').isVisible(),true);
    assert.match(await app.page.locator('#bulk-count').innerText(),/1 selected · 2 to complete/);
    if (process.env.CONSOLE_SCREENSHOT_DIR) await app.page.screenshot({path:`${process.env.CONSOLE_SCREENSHOT_DIR}/bulk-${width}.png`});
  }
});

test('family cards show boundaries, roles and filtered counts on narrow and wide lists', async t => {
  const app = await setup(t,[fixture('parent'),fixture('child',{parent_id:'parent'}),
    fixture('grandchild',{parent_id:'child'}),fixture('sibling',{parent_id:'parent'}),fixture('other')]);
  const group = app.page.locator('.thread-group');
  assert.equal(await group.count(),1);
  assert.match(await group.locator('.thread-group-heading').innerText(),/Group.*3 child threads/s);
  assert.equal(await app.row('parent').locator('.family-role').innerText(),'Parent');
  assert.equal(await app.row('child').locator('.family-role').innerText(),'Child');
  assert.equal(await app.row('grandchild').locator('.family-role').innerText(),'Child · level 2');
  assert.equal(await app.row('other').locator('.family-role').count(),0);
  assert.equal(await app.row('other').evaluate(n=>Boolean(n.closest('.thread-group'))),false);
  assert.deepEqual(await group.locator('.row').evaluateAll(ns=>ns.map(n=>n.dataset.itemId)),['parent','child','grandchild','sibling']);
  await app.page.locator('#filter').fill('Thread grandchild');
  assert.match(await group.locator('.thread-group-heading').innerText(),/2 of 3 shown/);
  assert.equal(await app.row('sibling').count(),0);
  await app.page.locator('#filter').fill('');
  for (const width of [1440,390,320]) {
    await app.page.setViewportSize({width,height:844});
    const bounds = await group.evaluate(n=>{const r=n.getBoundingClientRect(),s=getComputedStyle(n);return {width:r.width,border:parseFloat(s.borderTopWidth),overflow:n.scrollWidth>n.clientWidth};});
    assert.ok(bounds.border>=1);
    assert.equal(bounds.overflow,false);
    assert.ok(await app.page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    if (process.env.CONSOLE_SCREENSHOT_DIR) await app.page.screenshot({path:`${process.env.CONSOLE_SCREENSHOT_DIR}/groups-${width}.png`});
  }
  await app.page.evaluate(()=>setView('tree'));
  await app.row('parent').locator('.tw').click();
  assert.equal(await group.locator('.row').count(),1);
  assert.match(await group.locator('.thread-group-heading').innerText(),/0 of 3 shown/);
  app.items = app.items.map(i=>({...i,title:i.id==='parent' ? 'お客様への回答をまとめる' :
    i.id==='child' ? '調査結果を確認する' : i.id==='grandchild' ? '<img src=x onerror=alert(1)>' : '関連する作業を整理する'}));
  await app.page.evaluate(async()=>{await refresh();setLanguage('ja');setView('idle');});
  assert.match(await group.locator('.thread-group-heading').innerText(),/親子グループ.*子スレッド 3件/s);
  assert.equal(await app.row('parent').locator('.family-role').innerText(),'親');
  assert.equal(await app.row('child').locator('.family-role').innerText(),'子');
  assert.equal(await group.locator('img').count(),0);
  assert.match(await app.row('grandchild').innerText(),/<img src=x onerror=alert\(1\)>/);
  assert.equal(await group.evaluate(n=>n.scrollWidth>n.clientWidth),false);
  if (process.env.CONSOLE_SCREENSHOT_DIR) await app.page.screenshot({path:`${process.env.CONSOLE_SCREENSHOT_DIR}/groups-ja-320.png`});
});

test('refresh preserves a nested family row as the scroll anchor', async t => {
  const items = Array.from({length:35},(_,n)=>[fixture(`p${n}`),fixture(`c${n}`,{parent_id:`p${n}`})]).flat();
  const app = await setup(t,items);
  const pos=()=>app.page.evaluate(()=>{const list=$('list'),edge=list.getBoundingClientRect().top;const row=[...list.querySelectorAll('.row')].find(n=>n.getBoundingClientRect().bottom>edge);return {id:row.dataset.itemId,offset:row.getBoundingClientRect().top-edge};});
  await app.row('c20').evaluate(n=>{const list=document.querySelector('#list');list.scrollTop+=n.getBoundingClientRect().top-list.getBoundingClientRect().top+4;});
  const before=await pos();
  assert.equal(before.id,'c20');
  app.items.unshift(fixture('new-parent'),fixture('new-child',{parent_id:'new-parent'}));
  await app.page.evaluate(()=>refresh());
  const after=await pos();
  assert.equal(after.id,before.id);
  assert.ok(Math.abs(after.offset-before.offset)<1);
});
