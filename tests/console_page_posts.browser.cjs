// A page as a posting queue for any network (#1518): targets to filter by, attachments to
// preview and save, a character count, and a post URL pasted when marking an item done.
const { chromium } = require('playwright');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const root = require('node:path').resolve(__dirname, '../claude_discord/console/static') + '/';

const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==', 'base64');
const MP4 = Buffer.concat([Buffer.from([0, 0, 0, 0x18]), Buffer.from('ftypisom'), Buffer.alloc(64)]);
const FILES = '/console/api/pages/posts/files/' + 'a'.repeat(32);
const hostile = '<img src=x onerror="window.pwned=1">';
const item = (key, extra) => ({key, title:'', body:'', url:'', source:'', state:'open', created_at:'2026-11-05T01:00:00Z',
  resolved_at:'', resolved_by:'', result_url:'', target:'', attachments:[], limit:0, count:'chars', length:0, ...extra});
const fixtures = () => [
  item('ig', {title:'新商品の告知', target:'Instagram · @shop', body:'あ'.repeat(12), limit:10, length:12,
    attachments:[{url:`${FILES}/banner.png`, name:'banner.png', kind:'image'},
                 {url:`${FILES}/teaser.mp4`, name:'teaser.mp4', kind:'video'},
                 {url:'https://cdn.example.com/extra.jpg', name:hostile, kind:'image'}]}),
  item('x', {target:'X', body:'短い投稿', url:'https://example.com/compose?text=a', limit:280, length:8, count:'weighted'}),
  item('plain', {title:hostile, body:'投稿先なしの項目'}),
];
const definition = (n) => [{slug:'posts', title:'📣 投稿待ち', open_label:'投稿画面を開く', done_label:'投稿した', skip_label:'',
  position:0, open_count:n, max_chars:0, count:'chars'}];

(async () => {
  const browser = await chromium.launch({headless:true, executablePath:process.env.CONSOLE_TEST_CHROMIUM, args:['--no-sandbox']});
  for (const viewport of [{width:1280,height:900},{width:390,height:844}]) {
    let open = fixtures();
    const patches = [];
    const context = await browser.newContext({viewport, locale:'ja-JP', acceptDownloads:true});
    const page = await context.newPage();
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
      if (url.pathname.startsWith(FILES)) {
        return url.pathname.endsWith('.png')
          ? route.fulfill({contentType:'image/png', body:PNG})
          : route.fulfill({contentType:'video/mp4', body:MP4});
      }
      let data = {};
      if (url.pathname.endsWith('/board')) data = {items:[], slots:{}, places:[], pages:definition(open.length)};
      else if (url.pathname.endsWith('/pages/posts/items')) data = {items: url.searchParams.get('state') === 'open' ? open : []};
      else if (request.method() === 'PATCH' && url.pathname.includes('/pages/posts/items/')) {
        const key = url.pathname.split('/').pop();
        patches.push([key, request.postDataJSON()]);
        open = open.filter((i) => i.key !== key);
        data = {item:{key}};
      }
      return route.fulfill({contentType:'application/json', body:JSON.stringify(data)});
    });
    await page.goto('http://console.test/');
    await page.evaluate(() => { localStorage.setItem('lang', 'ja'); localStorage.removeItem('page-target:posts'); });
    await page.reload();
    await page.waitForFunction(() => document.querySelector('.nav-page'));
    if (viewport.width < 640) await page.locator('#nav-toggle').click();
    await page.locator('.nav-page').click();
    await page.locator('.page-item').first().waitFor();

    // Every target in the list, with its count; text stays text.
    const chips = await page.locator('.page-chip').allTextContents();
    assert.deepEqual(chips, ['すべて3', 'Instagram · @shop1', 'X1', '投稿先なし1']);
    assert.equal(await page.locator('.page-item').count(), 3);
    assert.equal(await page.evaluate(() => window.pwned), undefined);
    assert.equal(await page.locator('.page-item-title img, .page-file img[src="x"]').count(), 0);

    // Over the limit is shown, under it is not.
    const counts = await page.locator('.page-count').allTextContents();
    assert.equal(counts[0], '12 / 10 · ⚠ 2 超過');
    assert.equal(await page.locator('.page-count.over').count(), 1);
    assert.equal(counts[1], '8 / 280');

    // A stored image previews from its bytes; an outside link stays a link.
    await page.waitForFunction(() => document.querySelector('.page-thumb img')?.src.startsWith('data:image/png'));
    const outside = page.locator('.page-file-link');
    assert.equal(await outside.getAttribute('href'), 'https://cdn.example.com/extra.jpg');
    assert.equal(await outside.getAttribute('rel'), 'noopener noreferrer');
    assert.ok((await outside.textContent()).includes(hostile));

    // A video is fetched only when asked for, then plays from a blob.
    await page.locator('.page-video').click();
    await page.waitForFunction(() => document.querySelector('video.page-player')?.src.startsWith('blob:'));

    // Saving gives the file under its name.
    const [download] = await Promise.all([page.waitForEvent('download'),
      page.locator('.page-save').first().click()]);
    assert.equal(download.suggestedFilename(), 'banner.png');

    if (viewport.width > 640) {
      // Filter to one target; the choice is remembered for this page.
      await page.locator('.page-chip', {hasText:'X'}).click();
      assert.equal(await page.locator('.page-item').count(), 1);
      assert.equal(await page.locator('.page-open').getAttribute('href'), 'https://example.com/compose?text=a');
      await page.locator('.page-chip', {hasText:'すべて'}).click();
      assert.equal(await page.locator('.page-item').count(), 3);
      if (process.env.CONSOLE_TEST_SHOT) await page.screenshot({path:process.env.CONSOLE_TEST_SHOT});
      await page.locator('.page-chip', {hasText:'Instagram'}).click();
      await page.keyboard.press('Escape');
      await page.locator('.nav-page').click();
      await page.locator('.page-item').first().waitFor();
      assert.equal(await page.locator('.page-item').count(), 1, 'the chosen target is remembered');
      await page.locator('.page-chip', {hasText:'すべて'}).click();

      // A bad post URL is refused in place; a good one goes with "done".
      const first = page.locator('.page-item[data-key="ig"]');
      await first.locator('.page-result').fill('javascript:alert(1)');
      await first.locator('.page-done').click();
      await page.locator('.toast').first().waitFor();
      assert.deepEqual(patches, []);
      await first.locator('.page-result').fill('https://instagram.example/p/1');
      await first.locator('.page-done').click();
      await page.waitForFunction(() => document.querySelectorAll('.page-item').length === 2);
      // Marking done without a URL is still one tap.
      await page.locator('.page-item[data-key="x"] .page-done').click();
      await page.waitForFunction(() => document.querySelectorAll('.page-item').length === 1);
      assert.deepEqual(patches, [
        ['ig', {state:'done', result_url:'https://instagram.example/p/1'}],
        ['x', {state:'done'}],
      ]);
    } else {
      if (process.env.CONSOLE_TEST_SHOT_PHONE) await page.screenshot({path:process.env.CONSOLE_TEST_SHOT_PHONE, fullPage:false});
      const box = await page.locator('.page-shell').boundingBox();
      assert.ok(box.x >= 0 && box.x + box.width <= viewport.width, 'the panel fits a phone');
      const scroll = await page.evaluate(() => document.documentElement.scrollWidth);
      assert.ok(scroll <= viewport.width, 'no sideways scroll');
      for (const selector of ['.page-done', '.page-result']) {
        const b = await page.locator(selector).first().boundingBox();
        assert.ok(b.x >= 0 && b.x + b.width <= viewport.width, `${selector} fits a phone`);
        assert.ok(b.height >= 32, `${selector} is tappable`);
      }
    }
    assert.deepEqual(errors, []);
    await context.close();
  }
  await browser.close();
  console.log('Console page posts ok');
})().catch((e) => { console.error(e); process.exit(1); });
