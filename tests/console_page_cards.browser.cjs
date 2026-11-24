// One card per piece of content (#1564): items sharing a group are one card with a row per
// target. Automatic rows show their outcome, manual rows their buttons, and "Next ▶" walks a
// phone through the rows left: copy the text, hand the video over, "Posted?", next. Each step
// says what to do, the walk survives a reload, and a desktop is told to save the video (#1574).
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
  item('d', {target:'Net D', body:'本文 D', faded:true, attachments:[{url:`${FILES}/clip.mp4`, name:'clip.mp4', kind:'video'}]}),
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
    if (url.pathname.startsWith(FILES)) return route.fulfill({contentType:'video/mp4', body:MP4});
    let data = {};
    if (url.pathname.endsWith('/board')) data = {items:[], slots:{}, places:[], pages:definition(3)};
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
  return {context, page, patches, errors};
}

(async () => {
  const browser = await chromium.launch({headless:true, executablePath:process.env.CONSOLE_TEST_CHROMIUM, args:['--no-sandbox']});
  for (const viewport of [{width:1280, height:900}, {width:390, height:844}]) {
    const phone = viewport.width < 640;
    const {context, page, patches, errors} = await open(browser, viewport);

    // One card for the group, with its rows in order; the item without a group is drawn as before.
    assert.equal(await page.locator('.page-card').count(), 1);
    assert.equal(await page.locator('.page-card .page-row').count(), 4);
    assert.equal(await page.locator('.page-item[data-key="plain"]').count(), 1);
    assert.equal(await page.evaluate(() => window.pwned), undefined);
    assert.ok((await page.locator('.page-card-title').textContent()).includes(hostile), 'the title stays text');

    // Each row says where it stands. Automatic rows have no buttons; the posted one links its post.
    const statuses = await page.locator('.page-row-status').allTextContents();
    assert.deepEqual(statuses, ['自動で投稿済み', '自動で投稿中',
      '自動投稿できませんでした（401 from the API）。手で投稿してください', '未投稿']);
    const posted = page.locator('.page-row[data-key="a"] .page-row-result');
    assert.equal(await posted.getAttribute('href'), 'https://a.example/p/1');
    assert.equal(await posted.getAttribute('rel'), 'noopener noreferrer');
    for (const key of ['a', 'b']) assert.equal(await page.locator(`.page-row[data-key="${key}"] button`).count(), 0);
    assert.equal(await page.locator('.page-row[data-key="c"] .page-done').count(), 1);
    // The walk is the one button that stands out; a row's own buttons are folded, each saying what it does.
    assert.equal(await page.locator('.page-card .btn.primary').count(), 1);
    assert.equal(await page.locator('.page-row[data-key="c"] .page-done').isVisible(), false);
    assert.equal(await page.locator('.page-row[data-key="c"] .page-row-manual > summary').textContent(), 'Net C だけを個別に操作する');
    await page.locator('.page-row[data-key="c"] .page-row-manual > summary').click();
    assert.deepEqual(await page.locator('.page-row[data-key="c"] .page-act-note').allTextContents(), [
      '投稿先の画面が新しいタブで開く（記録は変わらない）', '本文をコピーするだけ（記録は変わらない）',
      'この媒体を投稿済みと記録する', 'この媒体は投稿しないと記録する']);
    // Before it starts, the walk shows its order and its three steps.
    assert.equal(await page.locator('.page-next-order').textContent(), '押すと、この順に1媒体ずつ案内します: Net C → Net D');
    assert.equal(await page.locator('.page-next-steps li').count(), 3);
    assert.equal(await page.locator('.page-row[data-key="d"].faded').count(), 1, 'an old row is faded');
    // No post-URL field on a card: done is one tap.
    assert.equal(await page.locator('.page-card .page-result').count(), 0);

    // The filter counts what is left to do on the open tab.
    const chips = await page.locator('.page-chip').allTextContents();
    assert.deepEqual(chips, ['すべて4', 'Net B1', 'Net C1', 'Net D1', '投稿先なし1']);

    if (!phone) {
      if (process.env.CONSOLE_TEST_SHOT) await page.screenshot({path:process.env.CONSOLE_TEST_SHOT});
      await page.locator('.page-chip', {hasText:'Net C'}).click();
      assert.equal(await page.locator('.page-card .page-row').count(), 1, 'only the chosen target');
      assert.equal(await page.locator('.page-item').count(), 0);
      await page.locator('.page-chip', {hasText:'すべて'}).click();
      // The text and the video are folded under the row.
      await page.locator('.page-row[data-key="d"] .page-row-detail > summary').click();
      assert.ok(await page.locator('.page-row[data-key="d"] .page-video').isVisible());
      // Done on a row keeps the card: other rows are still open.
      await page.locator('.page-row[data-key="c"] .page-row-manual > summary').click();
      await page.locator('.page-row[data-key="c"] .page-done').click();
      await page.waitForFunction(() => document.querySelector('.page-row[data-key="c"].is-done'));
      assert.equal(await page.locator('.page-card').count(), 1);
      assert.equal(await page.locator('.page-row[data-key="c"] .page-row-status').textContent(), '投稿済み');
      assert.deepEqual(patches, [['c', {state:'done'}]]);

      // Without a share sheet the video cannot reach the app: the step says so and offers it to save.
      await page.evaluate(() => {
        Object.defineProperty(navigator, 'share', {configurable:true, value:undefined});
        Object.defineProperty(navigator, 'clipboard', {configurable:true, value:{writeText:async () => {}}});
      });
      await page.locator('.page-next').click();
      assert.equal(await page.locator('.page-next-step').textContent(), 'Net D（残り 1 件中 1 件目）');
      assert.match(await page.locator('.page-next-how').textContent(), /直接渡せません.*保存/);
      assert.equal(await page.locator('.page-next-go').textContent(), '📋 本文をコピー');
      if (process.env.CONSOLE_TEST_SHOT_SAVE) await page.locator('.page-card').screenshot({path:process.env.CONSOLE_TEST_SHOT_SAVE});
      const download = page.waitForEvent('download');
      await page.locator('.page-next-save').click();
      assert.equal((await download).suggestedFilename(), 'clip.mp4');
      // "Later" on the only row left stays on it instead of calling the card finished.
      await page.locator('.page-next-later').click();
      assert.equal(await page.locator('.page-next-step').textContent(), 'Net D（残り 1 件中 1 件目）');
      await page.locator('.page-next-stop').click();
      assert.equal(await page.locator('.page-next-sheet').count(), 0);
      assert.equal(await page.evaluate(() => localStorage.getItem('page-flow:out')), null, 'ending the walk forgets it');
    } else {
      const mock = () => page.evaluate(() => {
        window.copied = []; window.shared = []; window.opened = [];
        Object.defineProperty(navigator, 'clipboard', {configurable:true, value:{writeText:async (t) => { window.copied.push(t); }}});
        navigator.canShare = () => true;
        navigator.share = async (data) => { window.shared.push({text:data.text, files:(data.files || []).map((f) => [f.name, f.type, f.size])}); };
        window.open = (url) => { window.opened.push(url); return null; };
      });
      await mock();
      if (process.env.CONSOLE_TEST_SHOT_PHONE) await page.screenshot({path:process.env.CONSOLE_TEST_SHOT_PHONE, fullPage:true});
      const box = await page.locator('.page-shell').boundingBox();
      assert.ok(box.x >= 0 && box.x + box.width <= viewport.width, 'the panel fits a phone');
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth) <= viewport.width, 'no sideways scroll');

      // The button says what it does and how many are left; a line says what the walk records.
      const next = page.locator('.page-next');
      assert.equal(await next.textContent(), '▶ 未投稿を順に投稿する（残り 2）');
      assert.ok((await next.boundingBox()).height >= 40);
      await page.locator('.page-row[data-key="c"] .page-row-manual > summary').click(); // fold it again
      if (process.env.CONSOLE_TEST_SHOT_START) await page.locator('.page-next').scrollIntoViewIfNeeded();
      if (process.env.CONSOLE_TEST_SHOT_START) await page.screenshot({path:process.env.CONSOLE_TEST_SHOT_START});
      await next.click();
      // The step says where we are, what to do on this target and what "Later" means.
      assert.equal(await page.locator('.page-next-step').textContent(), 'Net C（残り 2 件中 1 件目）');
      assert.match(await page.locator('.page-next-how').textContent(), /Net C の投稿画面を新しいタブで開きます/);
      assert.deepEqual(await page.locator('.page-next-sheet .page-act-note').allTextContents(),
        ['記録はまだ変わりません', '記録せず次の媒体へ。一周すると戻る', '案内を閉じる。記録済みはそのまま']);
      assert.equal(await page.locator('.page-next-save').count(), 0, 'nothing to save on a text row');
      const go = page.locator('.page-next-go');
      assert.equal(await go.textContent(), '📋 本文をコピーして投稿画面を開く');
      await go.click();
      await page.locator('.page-next-question').waitFor();
      assert.deepEqual(await page.evaluate(() => [window.copied, window.opened]), [['本文 C'], ['https://c.example/compose?text=c']]);
      assert.equal(await page.locator('.page-next-question').textContent(), 'Net C に投稿できましたか？');
      assert.equal(await page.locator('.page-next-resumed').count(), 0);
      assert.deepEqual(await page.locator('.page-next-sheet .page-act-note').allTextContents(),
        ['投稿済みと記録して次の媒体へ', 'もう一度コピーして開き直す', '投稿しないと記録して次の媒体へ']);

      // The phone reloads the Console while the person is in the posting app: on return the page
      // opens by itself at the same question, and nothing was recorded meanwhile.
      await page.reload();
      await page.locator('.page-next-question').waitFor();
      assert.equal(await page.locator('.page-next-question').textContent(), 'Net C に投稿できましたか？');
      assert.match(await page.locator('.page-next-resumed').textContent(), /前回の続き.*Net C/);
      assert.deepEqual(patches, []);
      // ...and scrolled into view, since it may sit below a long list.
      await page.waitForFunction(() => { const r = document.querySelector('.page-next-yes').getBoundingClientRect();
        return r.top >= 0 && r.bottom <= innerHeight; });
      if (process.env.CONSOLE_TEST_SHOT_ASK) await page.screenshot({path:process.env.CONSOLE_TEST_SHOT_ASK});
      await mock();
      await page.locator('.page-next-yes').click();

      // The next row carries a video: it is fetched first, then handed to the share sheet with the text.
      await page.waitForFunction(() => document.querySelector('.page-next-step')?.textContent === 'Net D（残り 1 件中 1 件目）');
      await page.waitForFunction(() => document.querySelector('.page-next-go')?.textContent === '📋 本文をコピーして動画をアプリへ渡す');
      assert.match(await page.locator('.page-next-how').textContent(), /共有メニュー.*Net D のアプリ/);
      assert.equal(await page.locator('.page-next-save').count(), 0, 'the share sheet carries the video');
      if (process.env.CONSOLE_TEST_SHOT_GO) await page.locator('.page-card').screenshot({path:process.env.CONSOLE_TEST_SHOT_GO});
      // Closing the share sheet without choosing an app keeps the step where it was.
      await page.evaluate(() => { const real = navigator.share;
        navigator.share = async () => { navigator.share = real; throw new DOMException('cancelled', 'AbortError'); }; });
      await page.locator('.page-next-go').click();
      await page.waitForFunction(() => JSON.parse(localStorage.getItem('page-flow:out') || '{}')['yt-1']?.stage === 'go');
      assert.equal(await page.locator('.page-next-question').count(), 0);
      await page.locator('.page-next-go').click();
      await page.locator('.page-next-question').waitFor();
      const shared = await page.evaluate(() => window.shared);
      assert.deepEqual(shared, [{text:'本文 D', files:[['clip.mp4', 'video/mp4', MP4.length]]}], 'files and text go to the app');
      await page.locator('.page-next-yes').click();
      await page.waitForFunction(() => !document.querySelector('.page-next-sheet'));
      assert.deepEqual(patches, [['c', {state:'done'}], ['d', {state:'done'}]]);
      assert.equal(await page.evaluate(() => localStorage.getItem('page-flow:out')), null, 'a finished walk is forgotten');
      // The automatic row is still open, so the card stays, with nothing left to press.
      assert.equal(await page.locator('.page-card').count(), 1);
      assert.equal(await page.locator('.page-next').count(), 0);
    }
    assert.deepEqual(errors, []);
    await context.close();
  }
  await browser.close();
  console.log('Console page cards ok');
})().catch((err) => { console.error(err); process.exit(1); });
