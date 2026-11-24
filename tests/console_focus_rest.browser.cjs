// Focus lists every unfinished item exactly once: the ranking on top, the rest below it
// (focus.js restSections). Run: node --test tests/console_focus_rest.browser.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../claude_discord/console/static');

async function open(t, board, {width = 1280, init = () => {}} = {}) {
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  t.after(() => browser.close());
  const page = await browser.newPage({viewport: {width, height: 900}, locale: 'ja-JP'});
  await page.addInitScript(() => localStorage.setItem('view', 'focus'));
  await page.addInitScript(init);
  await page.route('http://console.test/**', async route => {
    const p = new URL(route.request().url()).pathname;
    if (p === '/' || p.startsWith('/static/')) {
      const file = p === '/' ? 'index.html' : path.basename(p);
      return route.fulfill({body: fs.readFileSync(path.join(root, file)), contentType:
        file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html'});
    }
    let data = {};
    if (p.endsWith('/board')) data = board;
    else if (p.endsWith('/workspace')) data = {available: false, entries: []};
    else if (p.endsWith('/messages')) data = {messages: []};
    return route.fulfill({contentType: 'application/json', body: JSON.stringify(data)});
  });
  await page.goto('http://console.test/');
  await page.locator('#list .place-bar').first().waitFor();
  return page;
}

const item = (id, over = {}) => ({id, thread_id: id, title: `Item ${id}`, status: 'waiting', bucket: 'me',
  priority: 2, focus: 120, actionable: true, last_activity_at: '2026-11-24T10:00:00Z', ...over});

// A board like the real one: a little ranked work, and much more that is open but unranked.
const board = () => ({slots: {}, places: [], items: [
  item('now', {title: 'Ranked now', focus: 220, effort: 'quick'}),
  item('later', {title: 'Ranked waiting', actionable: false}),
  item('deep', {title: 'Ranked deep', effort: 'deep'}),
  item('idle', {title: 'Quiet thread', status: 'idle', bucket: 'idle', focus: null, actionable: null,
    last_activity_at: '2026-11-24T12:00:00Z'}),
  item('review', {title: 'Stale review', status: 'review', focus: null, actionable: null,
    last_activity_at: '2026-11-20T00:00:00Z'}),
  item('run', {title: 'Agent running', status: 'running', bucket: 'ai', running: true, focus: null, actionable: null}),
  item('c1', {title: 'Candidate one', status: 'candidate', bucket: 'candidate', focus: null, actionable: null}),
  item('c2', {title: 'Candidate two', status: 'candidate', bucket: 'candidate', focus: null, actionable: null}),
  item('done', {title: 'Finished', status: 'done', bucket: 'done', focus: null, actionable: null}),
]});

const ids = (page) => page.$$eval('#list .row', rows => rows.map(r => r.dataset.itemId));
const headers = (page) => page.$$eval('#list .focus-rest-toggle', bs => bs.map(b => b.innerText.replace(/\s+/g, ' ').trim()));

test('every unfinished item is listed once; ranked work stays on top', {timeout: 30000}, async t => {
  const page = await open(t, board());
  let shown = await ids(page);
  assert.deepEqual(shown.slice(0, 3), ['now', 'deep', 'later'], 'the ranking is unchanged');
  // Human-marked statuses lead the other open items, then the most recent.
  assert.deepEqual(shown.slice(3), ['review', 'idle', 'run']);
  assert.deepEqual(await headers(page), ['▾ 📂 その他の未完了 2 畳む', '▾ 🤖 AIが作業中 1 畳む', '▸ 🏁 完了候補 2 表示']);
  // Candidates are folded behind their count, one tap away.
  await page.locator('.focus-rest-candidates button').click();
  shown = await ids(page);
  assert.deepEqual(shown.slice(-2).sort(), ['c1', 'c2']);
  const openIds = ['now', 'later', 'deep', 'idle', 'review', 'run', 'c1', 'c2'];
  assert.deepEqual([...shown].sort(), openIds.sort(), 'each open item once, nothing finished');
  // The choice is remembered.
  await page.reload();
  await page.locator('#list .place-bar').first().waitFor();
  assert.ok((await ids(page)).includes('c1'));
});

test('items a filter hides are counted and shown on one tap', {timeout: 30000}, async t => {
  const page = await open(t, board(), {init: () => localStorage.setItem('focusEffort', 'quick')});
  assert.ok(!(await ids(page)).includes('deep'), 'the effort chip hides deep work from the ranking');
  await page.fill('#filter', '-title:Quiet');
  await page.waitForFunction(() => !document.querySelector('#list .row[data-item-id="idle"]'));
  const head = page.locator('.focus-rest-filtered button');
  assert.match(await head.innerText(), /絞り込みで非表示\s*3/);
  await head.click();
  const shown = await ids(page);
  assert.equal(shown[0], 'now', 'quick work still ranks');
  for (const id of ['later', 'deep', 'idle']) assert.equal(shown.filter(x => x === id).length, 1, id);
});

test('Focus with the rest stays inside a phone screen (390px)', {timeout: 30000}, async t => {
  const page = await open(t, board(), {width: 390});
  await page.locator('.focus-rest-candidates button').click();
  await page.locator('#list .row[data-item-id="c1"]').waitFor();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  assert.ok(overflow <= 0, `no horizontal scroll (${overflow}px)`);
  const box = await page.locator('.focus-rest-candidates button').boundingBox();
  assert.ok(box.height >= 40 && box.width <= 390, `tap target ${box.width}x${box.height}`);
});

test('a child already shown with its family above is not counted again below', {timeout: 30000}, async t => {
  const items = [
    item('parent', {title: 'Pinned parent', pinned_at: '2026-11-24T00:00:00Z', pin_position: 0, focus: null,
      status: 'idle', bucket: 'idle', actionable: null}),
    item('kid', {title: 'Running child', parent_id: 'parent', status: 'running', bucket: 'ai', running: true,
      focus: null, actionable: null}),
    item('rparent', {title: 'Ranked parent'}),
    item('rkid', {title: 'Idle child', parent_id: 'rparent', status: 'idle', bucket: 'idle', focus: null, actionable: null}),
    item('solo', {title: 'Solo idle', status: 'idle', bucket: 'idle', focus: null, actionable: null}),
  ];
  const page = await open(t, {slots: {}, places: [], items});
  assert.deepEqual(await headers(page), ['▾ 📂 その他の未完了 1 畳む']);
  const shown = await ids(page);
  assert.equal(shown.filter(x => x === 'solo').length, 1);
  assert.ok(!shown.includes('kid') && !shown.includes('rkid'), 'children stay inside their family card');
});
