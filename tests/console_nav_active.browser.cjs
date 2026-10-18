// Run with Playwright installed: node --test tests/console_nav_active.browser.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../claude_discord/console/static');

test('the selected sidebar view is marked apart from hover', async t => {
  const browser = await chromium.launch({headless:true, args:['--no-sandbox']});
  t.after(() => browser.close());
  const page = await browser.newPage({viewport:{width:1440,height:900}});
  page.setDefaultTimeout(4000);
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  t.after(() => assert.deepEqual(errors, []));
  const items = [{id:'t1', title:'Thread 1', thread_id:'1', status:'idle', bucket:'me', priority:2}];
  await page.route('http://console.test/**', async route => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === '/' || pathname.startsWith('/static/')) {
      const file = pathname === '/' ? 'index.html' : path.basename(pathname);
      return route.fulfill({body:fs.readFileSync(path.join(root,file)),
        contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});
    }
    const data = pathname.endsWith('/board') ? {items, slots:{}, places:[]} : {};
    return route.fulfill({contentType:'application/json', body:JSON.stringify(data)});
  });
  await page.goto('http://console.test/?lang=ja#me');
  const views = page.locator('#nav-views > button:not([class*="nav-"])');
  await page.waitForSelector('#nav-views button.active');

  const current = page.locator('#nav-views button[aria-current="page"]');
  assert.equal(await current.count(), 1);
  assert.match(await current.innerText(), /受信箱/);

  const style = el => el.evaluate(n => {
    const s = getComputedStyle(n);
    return {bg: s.backgroundColor, bar: s.boxShadow, weight: Number(s.fontWeight)};
  });
  const active = await style(current);
  const other = views.filter({hasText:'完了候補'});
  await other.hover();
  const hovered = await style(other);
  assert.notEqual(active.bg, hovered.bg, 'selected must not look like hover');
  assert.notEqual(active.bar, 'none', 'selected needs an indicator bar');
  assert.equal(hovered.bar, 'none');
  assert.ok(active.weight >= 600);

  await other.click();
  await page.waitForFunction(() => document.querySelector('#nav-views button[aria-current="page"]')?.textContent.includes('完了候補'));
  assert.equal(await page.locator('#nav-views button[aria-current="page"]').count(), 1);
});
