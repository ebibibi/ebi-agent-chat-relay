// The situation panel lists what a family produced (#1537): files the bot attached in any of
// its threads and what agents declared (a PR), newest first and labelled with their thread.
// A video plays in place and keeps playing through the panel's redraw; a replaced version folds
// away; a document opens in the preview. A thread on its own with deliverables and no request
// still gets the panel, without an empty "needed from you". On a phone it is the panel tab.
// Run: NODE_PATH=<dir with playwright> node tests/console_situation_deliverables.browser.cjs
const { chromium } = require('playwright');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const root = require('node:path').resolve(__dirname, '../claude_discord/console/static') + '/';
const fixture = (id, title, status, parent_id = null) => ({id, title, parent_id, status, thread_id: id.slice(1),
  bucket: 'me', running: false, priority: 2, focus: 100, last_activity_at: '2026-11-23T09:00:00Z'});
const items = [fixture('t10', 'ショート動画の企画（親）', 'idle'), fixture('t11', 'ショート動画の試作', 'idle', 't10'),
  fixture('t42', '単独の調査スレッド', 'idle')];
const cdn = (name) => `https://cdn.discordapp.com/attachments/1/2/${name}?ex=1`;
const family = {threads: ['10', '11'], unread: [], deliverables: [
  {source: 'declared', thread_id: '10', at: '2026-11-23T08:30:00Z', title: 'PR #77 試作の台本', url: 'https://github.com/o/r/pull/77', kind: 'pr'},
  {source: 'file', thread_id: '11', at: '2026-11-23T08:00:00Z', name: 'short_v2.mp4', url: cdn('short_v2.mp4'), content_type: 'video/mp4', size: 9, title: '試作 v2'},
  {source: 'file', thread_id: '11', at: '2026-11-23T07:00:00Z', name: 'report.md', url: cdn('report.md'), content_type: 'text/markdown', size: 20},
  {source: 'file', thread_id: '11', at: '2026-11-23T06:00:00Z', name: 'frame.png', url: cdn('frame.png'), content_type: 'image/png', size: 70},
  {source: 'file', thread_id: '11', at: '2026-11-22T06:00:00Z', name: 'short_v1.mp4', url: cdn('short_v1.mp4'), content_type: 'video/mp4', size: 9, title: '試作 v1', superseded: true},
]};
const single = {threads: ['42'], unread: [], deliverables: [
  {source: 'declared', thread_id: '42', at: '2026-11-23T08:00:00Z', title: '調査メモ', url: '/#note/02_Contexts/x/memo.md', kind: 'note'}]};
const secretary = {decide: [], decide_more: 0, review: [], review_more: 0, unexplained: [], unexplained_more: 0,
  working: [], working_more: 0, done_today: [], calm: false};
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==', 'base64');
(async () => {
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox', '--autoplay-policy=no-user-gesture-required']});
  for (const [width, height] of [[1440, 900], [390, 844]]) {
    const page = await browser.newPage({viewport: {width, height}, locale: 'ja-JP'});
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await page.route('https://cdn.discordapp.com/**', (route) => {
      const name = new URL(route.request().url()).pathname.split('/').pop();
      const cors = {'Access-Control-Allow-Origin': '*'};
      if (name.endsWith('.png')) return route.fulfill({contentType: 'image/png', headers: cors, body: PNG});
      if (name.endsWith('.md')) return route.fulfill({contentType: 'text/markdown', headers: cors, body: '# 試作の報告\n\n3本作りました。'});
      return route.fulfill({contentType: 'video/mp4', headers: cors, body: Buffer.alloc(16)});
    });
    await page.route('http://console.test/**', async (route) => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname === '/') return route.fulfill({contentType: 'text/html', body: fs.readFileSync(root + 'index.html', 'utf8')});
      if (pathname.startsWith('/static/')) {
        const file = pathname.split('/').pop();
        const type = file.endsWith('.css') ? 'text/css' : file.endsWith('.js') ? 'text/javascript' : 'image/svg+xml';
        return route.fulfill({contentType: type, body: fs.readFileSync(root + file)});
      }
      let data = {};
      if (pathname.endsWith('/secretary/events')) return route.fulfill({status: 204, body: ''});
      else if (pathname.endsWith('/t42/deliverables')) data = single;
      else if (pathname.endsWith('/deliverables')) data = family;
      else if (pathname.endsWith('/situation')) data = {situation: null, waits: []};
      else if (pathname.endsWith('/board')) data = {items, slots: {}, places: [], secretary};
      else if (pathname.endsWith('/messages')) data = {messages: [{id: 1, kind: 'agent', author: 'AI', content: '試作を作りました。', created_at: '2026-11-23T08:00:00Z'}]};
      return route.fulfill({contentType: 'application/json', body: JSON.stringify(data)});
    });
    await page.goto('http://console.test/');
    await page.evaluate(() => { localStorage.setItem('lang', 'ja'); localStorage.removeItem('situationHidden'); localStorage.setItem('consoleMode', 'classic'); });
    await page.reload();
    await page.waitForFunction(() => typeof state !== 'undefined' && state.items.length === 3);

    // A child shows its family's list: the parent's PR and the child's own files.
    await page.evaluate(() => openItem('t11'));
    if (width <= 900) await page.locator('#d-tabs button', {hasText: '現在地'}).first().click();
    const section = page.locator('#d-side .sit-section.deliverables');
    await section.locator('.dlv-row').first().waitFor();
    assert.match(await section.locator('.sit-head').innerText(), /📦 成果物 \(4\)/);
    const titles = await section.locator(':scope > .sit-list > .dlv-row .dlv-title').allInnerTexts();
    assert.deepEqual(titles.map((t) => t.replace(/^\S+\s/, '')), ['PR #77 試作の台本', '試作 v2', 'report.md', 'frame.png']);
    assert.match(await section.locator('.dlv-row').first().innerText(), /ショート動画の企画（親）/, 'labelled with its thread');
    assert.equal(await section.locator('.dlv-row a[href="https://github.com/o/r/pull/77"]').count(), 1);
    // The replaced version is folded away.
    const fold = section.locator('details.dlv-fold');
    assert.match(await fold.locator('summary').innerText(), /置き換え済み（1件）/);
    assert.equal(await fold.locator('.dlv-row').isVisible(), false);
    // An image has a thumbnail.
    await section.locator('.dlv-row[data-kind="image"] .dlv-thumb img').waitFor();
    if (process.env.SHOTS) await page.screenshot({path: `${process.env.SHOTS}/deliverables-${width}.png`});
    // A video plays in place and survives a redraw.
    await section.locator('.dlv-row[data-kind="video"]').first().getByRole('button', {name: /再生/}).click();
    const video = section.locator('video.dlv-video');
    await video.waitFor();
    await page.evaluate(() => { document.querySelector('video.dlv-video').dataset.marker = 'kept'; renderDetail(); });
    await page.waitForTimeout(200);
    assert.equal(await page.locator('video.dlv-video[data-marker="kept"]').count(), 1, 'the playing video was not rebuilt');
    // A document opens in the preview.
    await section.locator('.dlv-row', {hasText: 'report.md'}).getByRole('button', {name: /プレビュー/}).click();
    await page.getByText('3本作りました。').waitFor();
    await page.keyboard.press('Escape');

    // A thread on its own with something produced and nothing asked still shows the panel.
    await page.evaluate(() => openItem('t42'));
    if (width <= 900) await page.locator('#d-tabs button', {hasText: '現在地'}).first().click();
    await page.locator('#d-side .sit-section.deliverables .dlv-row').waitFor();
    assert.equal(await page.locator('#d-side .sit-section.asks').count(), 0, 'no empty asks');
    assert.equal(await page.locator('#d-side .dlv-row a[href="/#note/02_Contexts/x/memo.md"]').count(), 1);
    if (process.env.SHOTS) await page.screenshot({path: `${process.env.SHOTS}/deliverables-single-${width}.png`});
    assert.deepEqual(errors, []);
    await page.close();
  }
  await browser.close();
  console.log('The situation panel lists what the family produced: passed');
})().catch((e) => { console.error(e); process.exit(1); });
