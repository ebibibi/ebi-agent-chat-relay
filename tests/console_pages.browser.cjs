// A page registered as data shows in the menu, opens as a list, and its text stays text.
const { chromium } = require('playwright');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const root = require('node:path').resolve(__dirname, '../claude_discord/console/static') + '/';
const hostile = '<img src=x onerror="window.pwned=1">本文 https://example.com/a';
let open = [
  {key:'a', title:'最初の投稿', body:hostile, url:'https://example.com/compose?text=a', source:'promote-all',
   state:'open', created_at:'2026-11-05T01:00:00Z', resolved_at:'', resolved_by:'', result_url:''},
  {key:'b', title:'', body:'次の投稿', url:'', source:'', state:'open', created_at:'2026-11-06T01:00:00Z',
   resolved_at:'', resolved_by:'', result_url:''},
];
let done = [{key:'c', title:'', body:'確認済みの投稿', url:'', source:'', state:'done', created_at:'2026-11-04T01:00:00Z',
  resolved_at:'2026-11-05T01:00:00Z', resolved_by:'x-post-watch', result_url:'https://example.com/status/1'}];
const definition = () => [{slug:'posts', title:'𝕏 投稿待ち', open_label:'投稿画面を開く', done_label:'投稿した', skip_label:'',
  position:0, open_count:open.length}];
const patches = [];
(async () => {
  const browser = await chromium.launch({headless:true, executablePath:process.env.CONSOLE_TEST_CHROMIUM, args:['--no-sandbox']});
  for (const viewport of [{width:1280,height:900},{width:390,height:800}]) {
    const page = await browser.newPage({viewport, locale:'ja-JP'});
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await page.route('http://console.test/**', async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      if (url.pathname === '/') return route.fulfill({contentType:'text/html', body:fs.readFileSync(root + 'index.html')});
      if (url.pathname.startsWith('/static/')) {
        const file = url.pathname.split('/').pop();
        const type = file.endsWith('.css') ? 'text/css' : file.endsWith('.js') ? 'text/javascript' : 'image/svg+xml';
        return route.fulfill({contentType:type, body:fs.readFileSync(root + file)});
      }
      let data = {};
      if (url.pathname.endsWith('/board')) data = {items:[], slots:{}, places:[], pages:definition()};
      else if (url.pathname.endsWith('/pages/posts/items')) {
        data = {items: url.searchParams.get('state') === 'done' ? done : url.searchParams.get('state') === 'open' ? open : []};
      } else if (request.method() === 'PATCH' && url.pathname.includes('/pages/posts/items/')) {
        const key = url.pathname.split('/').pop();
        patches.push([key, request.postDataJSON().state]);
        open = open.filter((i) => i.key !== key);
        data = {item:{key}};
      }
      return route.fulfill({contentType:'application/json', body:JSON.stringify(data)});
    });
    await page.goto('http://console.test/');
    await page.evaluate(() => localStorage.setItem('lang', 'ja'));
    await page.reload();
    await page.waitForFunction(() => document.querySelector('.nav-page'));
    if (viewport.width < 640) await page.locator('#nav-toggle').click();
    assert.ok((await page.locator('.nav-page').textContent()).includes(String(open.length)));
    assert.ok(!(await page.locator('#nav-views').textContent()).includes('null'), 'no stray null in the menu');
    await page.locator('.nav-page').click();
    await page.locator('.page-item').first().waitFor();
    if (viewport.width > 640) {
      const items = page.locator('.page-item');
      assert.equal(await items.count(), 2);
      const body = page.locator('.page-item-body').first();
      assert.equal(await body.textContent(), hostile);
      assert.equal(await page.locator('.page-item img').count(), 0);
      assert.equal(await page.evaluate(() => window.pwned), undefined);
      const opener = page.locator('.page-open').first();
      assert.equal(await opener.textContent(), '投稿画面を開く');
      assert.equal(await opener.getAttribute('rel'), 'noopener noreferrer');
      assert.equal(await opener.getAttribute('target'), '_blank');
      if (process.env.CONSOLE_TEST_SHOT) await page.screenshot({path:process.env.CONSOLE_TEST_SHOT});
      await page.getByRole('button', {name:'投稿した'}).first().click();
      await page.waitForFunction(() => document.querySelectorAll('.page-item').length === 1);
      assert.deepEqual(patches, [['a','done']]);
      await page.locator('.page-tab', {hasText:'完了'}).click();
      await page.getByText('確認済みの投稿').waitFor();
      assert.ok((await page.locator('.page-item-meta').textContent()).includes('x-post-watch'));
      assert.equal(await page.locator('.page-item a').getAttribute('href'), 'https://example.com/status/1');
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('#page-panel').isHidden(), true);
    } else {
      const box = await page.locator('.page-shell').boundingBox();
      assert.ok(box.x >= 0 && box.x + box.width <= viewport.width, 'the panel fits a phone');
      const scroll = await page.evaluate(() => document.documentElement.scrollWidth);
      assert.ok(scroll <= viewport.width, 'no sideways scroll');
    }
    assert.deepEqual(errors, []);
    await page.close();
  }
  await browser.close();
  console.log('console pages ok');
})().catch((err) => { console.error(err); process.exit(1); });
