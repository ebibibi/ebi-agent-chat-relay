// The 🗂️ button under 📍 in the side panel shows only the files the family's threads attached,
// newest first in one list, without PRs or notes; the same button and 📍 go back to the panel.
// Run: NODE_PATH=<dir with playwright> node tests/console_situation_files.browser.cjs
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
  ...[1, 2, 3].map((n) => ({source: 'file', thread_id: '11', at: `2026-11-21T0${n}:00:00Z`, name: `shot${n}.png`, url: cdn(`shot${n}.png`), content_type: 'image/png', size: 70})),
  ...[1, 2, 3, 4, 5].map((n) => ({source: 'declared', thread_id: '10', at: `2026-11-23T09:0${n}:00Z`, title: `PR #10${n}`, url: `https://github.com/o/r/pull/10${n}`, kind: 'pr'})),
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

    // Folded beside the conversation, a 🗂️ button sits under 📍 and opens the files alone.
    await page.evaluate(() => openItem('t11'));
    if (width <= 900) {
      await page.locator('#d-tabs button', {hasText: '現在地'}).first().click();
    } else {
      await page.locator('#d-side .sit-hide').click();
      const rail = await page.locator('#d-side-rail').boundingBox();
      const files = page.locator('#d-side-files');
      await files.waitFor();
      assert.ok((await files.boundingBox()).y > rail.y, 'the files button is below 📍');
      assert.match(await files.innerText(), /7/);
      if (process.env.SHOTS) await page.screenshot({path: `${process.env.SHOTS}/files-rail-${width}.png`});
      await files.click();
    }
    if (width <= 900) await page.locator('#d-side .sit-view').click();
    const list = page.locator('#d-side .dlv-files');
    await list.locator('.dlv-row').first().waitFor();
    assert.match(await page.locator('#d-side .sit-title').innerText(), /🗂️ ファイル \(7\)/);
    const titles = (await list.locator('.dlv-row .dlv-title').allInnerTexts()).map((t) => t.replace(/^\S+\s/, ''));
    assert.deepEqual(titles, ['試作 v2', 'report.md', 'frame.png', '試作 v1', 'shot3.png', 'shot2.png', 'shot1.png'], 'files only, newest first');
    assert.equal(await page.locator('#d-side .sit-section.deliverables').count(), 0, 'the situation is not shown');
    assert.equal(await page.locator('#d-side a[href*="/pull/"]').count(), 0, 'no PRs');
    await page.evaluate(() => renderDetail());
    await page.waitForTimeout(200);
    assert.equal(await list.locator('.dlv-row').count(), 7, 'the view survives a redraw');
    if (process.env.SHOTS) await page.screenshot({path: `${process.env.SHOTS}/files-${width}.png`});
    // The same button goes back to where things stand.
    await page.locator('#d-side .sit-view').click();
    await page.locator('#d-side .sit-section.deliverables').waitFor();
    assert.match(await page.locator('#d-side .sit-title').innerText(), /📍 現在地/);
    if (width > 900) {
      // 📍 on the rail always opens where things stand.
      await page.locator('#d-side .sit-view').click();
      await page.locator('#d-side .sit-hide').click();
      await page.locator('#d-side-rail').click();
      await page.locator('#d-side .sit-section.deliverables').waitFor();
    }

    // No files, no button: a note is not a file.
    await page.evaluate(() => openItem('t42'));
    await page.locator('#d-side .dlv-row').first().waitFor({state: 'attached'});
    assert.equal(await page.locator('#d-side .sit-view').count(), 0);
    assert.equal(await page.locator('#d-side-files').isVisible(), false);
    assert.deepEqual(errors, []);
    await page.close();
  }
  await browser.close();
  console.log('The files button lists only the files the family attached: passed');
})().catch((e) => { console.error(e); process.exit(1); });
