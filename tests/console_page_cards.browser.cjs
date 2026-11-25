// One card per piece of content (#1564): items sharing a group are one card with a row per
// target. Automatic rows show their outcome; a row a person finishes shows plain buttons usable
// in any order (#1589): save the file (a normal download, its size shown), copy the text, open
// the composer, posted. Nothing reads a file before it is asked for, and no share sheet is used.
const { chromium } = require('playwright');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const root = require('node:path').resolve(__dirname, '../claude_discord/console/static') + '/';

const MP4 = Buffer.concat([Buffer.from([0, 0, 0, 0x18]), Buffer.from('ftypisom'), Buffer.alloc(64)]);
const FILES = '/console/api/pages/out/files/' + 'b'.repeat(32);
const hostile = '<img src=x onerror="window.pwned=1">';
const item = (key, extra) => ({key, title:'', body:'', url:'', source:'', state:'open', created_at:'2026-11-24T01:00:00Z',
  resolved_at:'', resolved_by:'', result_url:'', target:'', attachments:[], limit:0, count:'chars', length:0, link:'',
  group:'yt-1', group_title:`Regions ${hostile}`, mode:'manual', error:'', stalled:false, faded:false, ...extra});
const fixtures = () => [
  item('a', {target:'Net A', mode:'auto', state:'done', resolved_by:'poster', resolved_at:'2026-11-24T01:01:00Z',
    result_url:'https://a.example/p/1', body:'auto text'}),
  item('b', {target:'Net B', mode:'auto', body:'auto pending'}),
  item('c', {target:'Net C', body:'本文 C', url:'https://c.example/compose?text=c', error:'401 from the API'}),
  item('d', {target:'Net D', body:'本文 D', faded:true,
    attachments:[{url:`${FILES}/clip.mp4`, name:'clip.mp4', kind:'video', size:23 * 1024 * 1024}]}),
  item('e', {target:'Net E', body:'本文 E', url:'https://e.example/compose',
    attachments:[{url:`${FILES}/long.mp4`, name:'long.mp4', kind:'video', size:Math.round(1.3 * 1024 ** 3)}]}),
  item('plain', {group:'', group_title:'', title:'単独の項目', body:'グループなし'}),
];
const toCards = (list) => {
  const groups = new Map();
  for (const i of list) {
    const k = i.group || `\0${i.key}`;
    if (!groups.has(k)) groups.set(k, {group:i.group, title:i.group_title || i.title, created_at:i.created_at, items:[]});
    groups.get(k).items.push(i);
  }
  return [...groups.values()];
};
const definition = (n) => [{slug:'out', title:'📣 配信', open_label:'投稿画面を開く', done_label:'投稿した', skip_label:'',
  position:0, open_count:n, max_chars:0, count:'chars', fade_days:3, expire_days:7}];

async function open(browser, viewport) {
  const rows = fixtures();
  const patches = [];
  const fetched = []; // stored files the page asked for
  const context = await browser.newContext({viewport, locale:'ja-JP'});
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
      fetched.push(url.pathname.split('/').pop());
      return route.fulfill({contentType:'video/mp4', body:MP4,
        headers:url.searchParams.has('download') ? {'Content-Disposition':'attachment'} : {}});
    }
    let data = {};
    if (url.pathname.endsWith('/board')) data = {items:[], slots:{}, places:[], pages:definition(4)};
    else if (url.pathname.endsWith('/pages/out/cards')) {
      data = {cards: url.searchParams.get('state') === 'open' ? toCards(rows.filter((i) => i.state === 'open' || i.group)) : []};
    } else if (request.method() === 'PATCH' && url.pathname.includes('/pages/out/items/')) {
      const key = decodeURIComponent(url.pathname.split('/').pop());
      const body = request.postDataJSON();
      patches.push([key, body]);
      const row = rows.find((i) => i.key === key);
      Object.assign(row, {state:body.state, resolved_by:'person'});
      data = {item:row};
    }
    return route.fulfill({contentType:'application/json', body:JSON.stringify(data)});
  });
  await page.goto('http://console.test/');
  await page.evaluate(() => { localStorage.setItem('lang', 'ja'); localStorage.removeItem('page-target:out'); });
  await page.reload();
  await page.waitForFunction(() => document.querySelector('.nav-page'));
  if (viewport.width < 640) await page.locator('#nav-toggle').click();
  await page.locator('.nav-page').click();
  await page.locator('.page-card').first().waitFor();
  return {context, page, patches, errors, fetched};
}

(async () => {
  const browser = await chromium.launch({headless:true, executablePath:process.env.CONSOLE_TEST_CHROMIUM, args:['--no-sandbox']});
  for (const viewport of [{width:1280, height:900}, {width:390, height:844}]) {
    const phone = viewport.width < 640;
    const {context, page, patches, errors, fetched} = await open(browser, viewport);
    await page.evaluate(() => {
      window.shared = 0;
      navigator.share = async () => { window.shared += 1; };
      navigator.canShare = () => true;
      window.copied = [];
      Object.defineProperty(navigator, 'clipboard', {configurable:true, value:{writeText:async (t) => { window.copied.push(t); }}});
    });

    // One card for the group, with its rows in order; the item without a group is drawn as before.
    assert.equal(await page.locator('.page-card').count(), 1);
    assert.equal(await page.locator('.page-card .page-row').count(), 5);
    assert.equal(await page.locator('.page-item[data-key="plain"]').count(), 1);
    assert.equal(await page.evaluate(() => window.pwned), undefined);
    assert.ok((await page.locator('.page-card-title').textContent()).includes(hostile), 'the title stays text');

    // Each row says where it stands. Automatic rows have no buttons; the posted one links its post.
    const statuses = await page.locator('.page-row-status').allTextContents();
    assert.deepEqual(statuses, ['自動で投稿済み', '自動で投稿中',
      '自動投稿できませんでした（401 from the API）。手で投稿してください', '未投稿', '未投稿']);
    const posted = page.locator('.page-row[data-key="a"] .page-row-result');
    assert.equal(await posted.getAttribute('href'), 'https://a.example/p/1');
    assert.equal(await posted.getAttribute('rel'), 'noopener noreferrer');
    for (const key of ['a', 'b']) assert.equal(await page.locator(`.page-row[data-key="${key}"] .btn`).count(), 0);

    // No walk: every row a person finishes shows its buttons right away, in the order to use them.
    assert.equal(await page.locator('.page-next, .page-next-sheet, .page-row-manual').count(), 0);
    const labels = async (key) => page.locator(`.page-row[data-key="${key}"] .page-row-actions .btn`).allTextContents();
    assert.deepEqual(await labels('c'), ['📋 本文をコピー', '↗ 投稿画面を開く', '✅ 投稿した', '見送り']);
    assert.deepEqual(await labels('d'), ['⬇ 動画を保存（23 MB）', '📋 本文をコピー', '✅ 投稿した', '見送り']);
    assert.deepEqual(await labels('e'), ['⬇ 動画を保存（1.3 GB）', '📋 本文をコピー', '↗ 投稿画面を開く', '✅ 投稿した', '見送り']);
    assert.equal(await page.locator('.page-row[data-key="c"] .page-open').getAttribute('href'), 'https://c.example/compose?text=c');
    // A large file says a phone is the wrong place to save it; a small one does not.
    assert.match(await page.locator('.page-row[data-key="e"] .page-row-large').textContent(), /大きいファイル（1\.3 GB）.*PC/);
    assert.equal(await page.locator('.page-row[data-key="d"] .page-row-large').count(), 0);
    // How to post is said once per card, not under every button.
    assert.equal(await page.locator('.page-card-howto').count(), 1);
    assert.match(await page.locator('.page-card-howto').textContent(), /保存.*本文をコピー.*投稿した/);
    assert.equal(await page.locator('.page-row[data-key="d"].faded').count(), 1, 'an old row is faded');
    assert.equal(await page.locator('.page-card .page-result').count(), 0, 'done is one tap');

    // The filter counts what is left to do on the open tab.
    const chips = await page.locator('.page-chip').allTextContents();
    assert.deepEqual(chips, ['すべて5', 'Net B1', 'Net C1', 'Net D1', 'Net E1', '投稿先なし1']);

    // Nothing was fetched to prepare a share: a file is read only when someone saves or plays it.
    assert.deepEqual(fetched, []);

    if (phone) {
      if (process.env.CONSOLE_TEST_SHOT_PHONE) await page.screenshot({path:process.env.CONSOLE_TEST_SHOT_PHONE, fullPage:true});
      const box = await page.locator('.page-shell').boundingBox();
      assert.ok(box.x >= 0 && box.x + box.width <= viewport.width, 'the panel fits a phone');
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth) <= viewport.width, 'no sideways scroll');
      for (const button of await page.locator('.page-row[data-key="d"] .page-row-actions .btn').all()) {
        assert.ok((await button.boundingBox()).height >= 44, 'buttons are big enough to tap');
      }
    } else if (process.env.CONSOLE_TEST_SHOT) await page.screenshot({path:process.env.CONSOLE_TEST_SHOT});

    // Any row, any order: start with the last one. Save is a plain download of the stored file.
    const download = page.waitForEvent('download');
    await page.locator('.page-row[data-key="d"] .page-row-save').click();
    const saved = await download;
    assert.equal(saved.suggestedFilename(), 'clip.mp4');
    assert.ok(saved.url().endsWith('/clip.mp4?download=1'), 'the browser downloads the stored file itself');
    await page.locator('.page-row[data-key="d"] .page-copy').click();
    assert.deepEqual(await page.evaluate(() => window.copied), ['本文 D']);
    await page.locator('.page-row[data-key="d"] .page-done').click();
    await page.waitForFunction(() => document.querySelector('.page-row[data-key="d"].is-done'));
    assert.equal(await page.locator('.page-row[data-key="d"] .page-row-status').textContent(), '投稿済み');
    assert.equal(await page.locator('.page-row[data-key="d"] .page-row-actions').count(), 0, 'a posted row has no buttons left');
    await page.locator('.page-row[data-key="c"] .page-skip').click();
    await page.waitForFunction(() => document.querySelector('.page-row[data-key="c"].is-skipped'));
    assert.deepEqual(patches, [['d', {state:'done'}], ['c', {state:'skipped'}]]);
    // The card stays while rows are open; nothing went through a share sheet or local storage.
    assert.equal(await page.locator('.page-card').count(), 1);
    assert.equal(await page.evaluate(() => window.shared), 0);
    assert.equal(await page.evaluate(() => Object.keys(localStorage).filter((k) => k.startsWith('page-flow:')).length), 0);
    assert.deepEqual(errors, []);
    await context.close();
  }
  await browser.close();
  console.log('Console page cards ok');
})().catch((err) => { console.error(err); process.exit(1); });
