// Run with Playwright installed: node --test tests/console_parent_mode.browser.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../claude_discord/console/static');
const fixture = (id) => ({id, title: `Thread ${id}`, thread_id: id, status: 'idle', bucket: 'idle', priority: 2});

for (const width of [1440, 390]) test(`parent mode per thread and default at width ${width}`, async t => {
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  t.after(() => browser.close());
  const page = await browser.newPage({viewport: {width, height: 844}});
  page.setDefaultTimeout(4000);
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  t.after(() => assert.deepEqual(errors, []));
  const store = {def: {enabled: false, max_children: 5, max_children_limit: 50}, mode: 'default'};
  const posts = [];
  await page.route('http://console.test/**', async route => {
    const url = new URL(route.request().url());
    const p = url.pathname;
    if (p === '/' || p.startsWith('/static/')) {
      const file = p === '/' ? 'index.html' : path.basename(p);
      return route.fulfill({body: fs.readFileSync(path.join(root, file)),
        contentType: file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html'});
    }
    const post = route.request().method() === 'POST' ? route.request().postDataJSON() : null;
    let data = {};
    if (p.endsWith('/defaults/parent-mode')) {
      if (post) { posts.push(['default', post]); Object.assign(store.def, post); }
      data = store.def;
    } else if (p.endsWith('/parent-mode')) {
      if (post) { posts.push(['item', post]); store.mode = post.mode; }
      const enabled = store.mode === 'on' || (store.mode === 'default' && store.def.enabled);
      data = {mode: store.mode, enabled, source: store.mode === 'default' ? 'default' : 'thread',
        default_enabled: store.def.enabled, is_child: false};
    } else if (p.endsWith('/board')) data = {items: [fixture('t100')], slots: {}, places: []};
    else if (p.endsWith('/messages')) data = {messages: []};
    await route.fulfill({contentType: 'application/json', body: JSON.stringify(data)});
  });
  await page.goto('http://console.test/?lang=en');
  await page.waitForFunction(() => state.items.length > 0);
  await page.evaluate(() => openItem('t100'));
  if (width === 390) await page.locator('.d-more').click();
  const select = page.locator('select.team-parent-mode');
  await page.waitForFunction(() => !document.querySelector('select.team-parent-mode').disabled);
  assert.equal(await select.inputValue(), 'default');
  assert.match(await select.locator('option[value=default]').innerText(), /Default \(off\)/);
  await select.selectOption('on');
  await page.waitForFunction(() => document.querySelector('.team-parent').classList.contains('on'));
  assert.equal(store.mode, 'on');
  if (width === 390) { await page.locator('.mobile-tools-close').click(); await page.locator('.detail-nav-toggle').click(); }
  const nav = page.locator('.nav-parent-mode');
  assert.match(await nav.innerText(), /Parent mode default: off \(max 5 children\)/);
  await nav.click();
  await page.locator('.backend-picker input[type=number]').fill('3');
  await page.locator('.backend-picker input[type=number]').dispatchEvent('change');
  await page.waitForFunction(() => /max 3/.test(document.querySelector('.nav-parent-mode').textContent));
  await page.locator('.backend-picker').getByRole('button', {name: 'On', exact: true}).click();
  await page.waitForFunction(() => /default: on/.test(document.querySelector('.nav-parent-mode').textContent));
  assert.equal(await page.locator('.backend-picker').count(), 0);
  assert.deepEqual(posts, [['item', {mode: 'on'}], ['default', {max_children: 3}], ['default', {enabled: true}]]);
});
