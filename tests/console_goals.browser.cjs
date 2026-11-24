// Goals view: goal cards from the goals note, threads under their goal, edits sent back.
// Run: NODE_PATH=<dir with playwright> node --test tests/console_goals.browser.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../claude_discord/console/static');

function goalsFixture() {
  return {
    available: true, note: {path: '02_Contexts/Me/goals.md', revision: 'r1', modified: 0}, limit: 3, active: 2,
    goals: [
      {id: 'g1', number: '1️⃣', title: 'Launch the website', state: 'active', done_when: 'the client signs off',
        due: '2099-12-20', why: null, line: 5,
        projects: [{path: 'Work/Website', name: 'Website', milestones: {done: 1, total: 3, next: {name: 'Launch', date: '2099-12-20'}}}],
        items: ['t1', 't2'], why_placed: {t1: 'project', t2: 'parent'},
        counts: {running: 1, yours: 1, done: 0, open: 2}, quiet: false,
        deliverables: [{title: 'PR #12', url: 'https://example.com/pull/12', kind: 'pr'}]},
      {id: 'g2', number: '2️⃣', title: 'Hire someone', state: 'active', done_when: null, due: null, why: null, line: 9,
        projects: [], items: [], why_placed: {}, counts: {running: 0, yours: 0, done: 0, open: 0}, quiet: false, deliverables: []},
      {id: 'g3', number: '', title: 'Old idea', state: 'paused', done_when: null, due: null, why: null, line: 12,
        projects: [], items: [], why_placed: {}, counts: {running: 0, yours: 0, done: 0, open: 0}, quiet: false, deliverables: []},
    ],
    outside: [{path: 'Me/Ads', name: 'Ads', milestones: null, items: ['t3'], counts: {running: 0, yours: 0, done: 0, open: 1}, deliverables: []}],
    unlinked: ['t4'],
    doing: {t1: 'Writing the landing page'},
  };
}

const items = [
  {id: 't1', thread_id: '1', title: 'Build the page', status: 'running', bucket: 'ai', running: true, priority: 2,
    workspace_path: 'Work/Website', tags: [], last_activity_at: '2026-11-24T00:00:00Z'},
  {id: 't2', thread_id: '2', title: 'Answer the client', status: 'waiting', bucket: 'me', running: false, priority: 2,
    parent_id: null, tags: [], last_activity_at: '2026-11-23T00:00:00Z'},
  {id: 't3', thread_id: '3', title: 'Ad copy', status: 'idle', bucket: 'idle', running: false, priority: 2,
    workspace_path: 'Me/Ads', tags: [], last_activity_at: '2026-11-22T00:00:00Z'},
  {id: 't4', thread_id: '4', title: 'Random question', status: 'idle', bucket: 'idle', running: false, priority: 2,
    tags: [], last_activity_at: '2026-11-21T00:00:00Z'},
];

async function open(browser, viewport, writes, goals) {
  const page = await browser.newPage({viewport, locale: 'ja-JP'});
  page.on('dialog', (d) => d.accept());
  await page.route('http://console.test/**', async (route) => {
    const p = new URL(route.request().url()).pathname;
    if (p === '/' || p.startsWith('/static/')) {
      const file = p === '/' ? 'index.html' : path.basename(p);
      return route.fulfill({body: fs.readFileSync(path.join(root, file)), contentType:
        file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html'});
    }
    const method = route.request().method();
    let data = {};
    if (p.endsWith('/board')) data = {items, slots: {}, places: []};
    else if (p.endsWith('/workspace')) data = {available: true, entries: [
      {path: 'Work/Website', kind: 'project', tasks: []}, {path: 'Me/Ads', kind: 'project', tasks: []}, {path: 'Me', kind: 'context', tasks: []}]};
    else if (p.endsWith('/goals') && method === 'GET') data = goals();
    else if (p.endsWith('/goals') || p.includes('/items/')) {
      if (method !== 'GET') writes.push({p, method, body: route.request().postDataJSON()});
      data = p.endsWith('/goals') ? {note: {path: 'x', revision: 'r2'}} : {item: {}};
    } else if (p.endsWith('/messages')) data = {messages: []};
    return route.fulfill({contentType: 'application/json', body: JSON.stringify(data)});
  });
  await page.goto('http://console.test/');
  await page.waitForFunction(() => window.ConsoleWorkspace && window.ConsoleWorkspace.available());
  await page.evaluate(() => setView('goals'));
  await page.locator('.goal-card').first().waitFor();
  return page;
}

test('Goals view on a desktop', {timeout: 40000}, async (t) => {
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  t.after(() => browser.close());
  const writes = [];
  const page = await open(browser, {width: 1440, height: 900}, writes, goalsFixture);

  // The nav lists the view; active goals only, paused ones on request.
  assert.ok(await page.locator('#nav-views button', {hasText: '🧭 目標'}).count());
  assert.deepEqual(await page.locator('.goal-card:not(.outside) .goal-title').allTextContents(),
    ['Launch the website', 'Hire someone']);
  const card = page.locator('.goal-card[data-goal-id="g1"]');
  assert.match(await card.textContent(), /the client signs off/);
  assert.match(await card.locator('.goal-progress').textContent(), /1\/3/);
  assert.match(await card.locator('.goal-counts').textContent(), /あなた待ち 1/);
  assert.equal(await card.locator('.goal-deliverables a').getAttribute('href'), 'https://example.com/pull/12');

  // Threads are ordinary rows under their goal: the one waiting on you first.
  const titles = await page.locator('#list > li').evaluateAll((lis) => lis.map((li) =>
    li.dataset.goalId ? `goal:${li.dataset.goalId}` : li.classList.contains('goal-card') ? 'outside'
      : li.classList.contains('row') ? li.querySelector('.title').textContent.trim() : li.textContent.trim()));
  const at = (x) => titles.findIndex((s) => s.includes(x));
  assert.ok(at('goal:g1') < at('Answer the client'));
  assert.ok(at('Answer the client') < at('Build the page'));
  assert.ok(at('Build the page') < at('goal:g2'));
  assert.ok(at('outside') < at('Ad copy'));
  assert.ok(at('Ad copy') < at('Random question'));
  assert.match(await page.locator('.row[data-item-id="t1"] .goal-note').textContent(), /Writing the landing page/);

  // Moving a thread to another goal writes a goal:<id> tag.
  await page.locator('.row[data-item-id="t4"] .goal-pick').click();
  await page.locator('.goal-pop .ws-bind-option', {hasText: 'Hire someone'}).click();
  await page.waitForFunction(() => true);
  await page.waitForTimeout(200);
  const tagWrite = writes.find((w) => w.p.endsWith('/items/t4'));
  assert.deepEqual(tagWrite.body, {tags: ['goal:g2']});

  // Writing a goal posts it with the revision the view read.
  await page.locator('.goal-add').click();
  await page.locator('.goal-header .goal-form input').first().fill('Write a book');
  await page.locator('.goal-header .goal-form textarea').fill('one chapter a month');
  await page.locator('.goal-header .goal-form .btn.primary').click();
  await page.waitForTimeout(200);
  const add = writes.find((w) => w.p.endsWith('/goals'));
  assert.deepEqual(add.body, {op: 'add', title: 'Write a book', done_when: 'one chapter a month', due: '', revision: 'r1'});

  // Reorder and pause from the card's menu.
  await card.locator('.goal-tools summary').click();
  await card.locator('.goal-tools-body button', {hasText: '↓'}).click();
  await page.waitForTimeout(200);
  assert.deepEqual(writes.at(-1).body, {op: 'move', id: 'g1', index: 1, revision: 'r1'});

  await page.locator('.goal-bar-buttons .btn.ghost').click();
  assert.equal(await page.locator('.goal-card.state-paused').count(), 1);
  await page.screenshot({path: process.env.GOALS_SHOT_DIR ? path.join(process.env.GOALS_SHOT_DIR, 'goals-desktop.png') : '/tmp/goals-desktop.png'});
});

test('Goals view at 390 px', {timeout: 40000}, async (t) => {
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  t.after(() => browser.close());
  const page = await open(browser, {width: 390, height: 844}, [], goalsFixture);
  const overflow = await page.evaluate(() => {
    const list = document.getElementById('list');
    return [...list.querySelectorAll('.goal-card, .goal-header')].some((el) => el.scrollWidth > el.clientWidth + 1)
      || document.documentElement.scrollWidth > window.innerWidth + 1;
  });
  assert.equal(overflow, false);
  const box = await page.locator('.goal-card[data-goal-id="g1"]').boundingBox();
  assert.ok(box.width <= 390);
  await page.screenshot({path: process.env.GOALS_SHOT_DIR ? path.join(process.env.GOALS_SHOT_DIR, 'goals-390.png') : '/tmp/goals-390.png'});
});

test('Goals view asks for a note when none is configured', {timeout: 30000}, async (t) => {
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  t.after(() => browser.close());
  const writes = [];
  const page = await browser.newPage({viewport: {width: 1200, height: 800}, locale: 'ja-JP'});
  await page.route('http://console.test/**', async (route) => {
    const p = new URL(route.request().url()).pathname;
    if (p === '/' || p.startsWith('/static/')) {
      const file = p === '/' ? 'index.html' : path.basename(p);
      return route.fulfill({body: fs.readFileSync(path.join(root, file)), contentType:
        file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html'});
    }
    let data = {};
    if (p.endsWith('/board')) data = {items: [], slots: {}, places: []};
    else if (p.endsWith('/workspace')) data = {available: true, entries: []};
    else if (p.endsWith('/goals/config')) { writes.push(route.request().postDataJSON()); data = {}; }
    else if (p.endsWith('/goals')) data = {available: false, reason: 'config'};
    return route.fulfill({contentType: 'application/json', body: JSON.stringify(data)});
  });
  await page.goto('http://console.test/');
  await page.waitForFunction(() => window.ConsoleWorkspace && window.ConsoleWorkspace.available());
  await page.evaluate(() => setView('goals'));
  await page.locator('.goal-setup input').fill('02_Contexts/Me/goals.md');
  await page.locator('.goal-setup .btn.primary').click();
  await page.waitForTimeout(200);
  assert.deepEqual(writes, [{note: '02_Contexts/Me/goals.md'}]);
});
