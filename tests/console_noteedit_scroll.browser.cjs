// The note editor on a long note: the source pane must scroll inside itself (the wheel and
// clicks reach every line), the panel widens while editing, and the preview follows the
// source both ways, matched by source line.
// Run: NODE_PATH=<dir with playwright> node --test tests/console_noteedit_scroll.browser.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../claude_discord/console/static');
const server = fs.readFileSync(path.resolve(__dirname, '../claude_discord/console/server.py'), 'utf8');
const CSP = server.split('"Content-Security-Policy": (')[1].split(')')[0]
  .split('\n').map(l => (l.match(/"([^"]*)"/) || [])[1]).filter(Boolean).join('');

const TEXT = '---\ntags: [a]\n---\n# Title\n' + Array.from({length: 120}, (_, i) => i % 10 === 0
  ? `## Section ${i}\n\npara ${i} ${'long words '.repeat((i % 3) * 30)}`
  : `line ${i} text\n`).join('\n') + '\n';

test('long note: the editor scrolls and the preview follows it', {timeout: 40000}, async t => {
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  t.after(() => browser.close());
  const page = await browser.newPage({viewport: {width: 1600, height: 900}, locale: 'ja-JP'});
  page.setDefaultTimeout(10000);
  const disk = {path: 'A/Long.md', text: TEXT, revision: 'r1', links: {}, modified: 0};
  await page.route('http://console.test/**', async route => {
    const p = new URL(route.request().url()).pathname;
    if (p === '/' || p.startsWith('/static/')) {
      const file = p === '/' ? 'index.html' : path.basename(p);
      return route.fulfill({body: fs.readFileSync(path.join(root, file)), headers: {'Content-Security-Policy': CSP},
        contentType: file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html'});
    }
    let data = {};
    if (p.endsWith('/board')) data = {items: [], slots: {}, places: []};
    else if (p.endsWith('/workspace/note')) data = disk;
    else if (p.endsWith('/workspace/notes')) data = {notes: []};
    else if (p.endsWith('/workspace/backlinks')) data = {backlinks: []};
    else if (p.endsWith('/workspace/plan')) data = {available: false};
    else if (p.endsWith('/workspace')) data = {available: true, entries: []};
    return route.fulfill({contentType: 'application/json', body: JSON.stringify(data)});
  });
  await page.goto('http://console.test/#focus');
  await page.evaluate(() => window.ConsoleNotes.open('A/Long.md'));
  await page.locator('.note-content > h1').waitFor();
  const viewWidth = await page.evaluate(() => document.querySelector('.note-shell').getBoundingClientRect().width);
  await page.getByRole('button', {name: '✏️ 編集'}).click();
  await page.locator('.note-edit-cm .cm-content').waitFor();

  // Wider while editing, and the editor is a fixed box that scrolls rather than a tall one clipped.
  const box = await page.evaluate(() => {
    const host = document.querySelector('.note-edit-cm');
    const scroller = host.shadowRoot.querySelector('.cm-scroller');
    return {shell: document.querySelector('.note-shell').getBoundingClientRect().width,
      host: host.getBoundingClientRect().height, scroller: scroller.clientHeight, content: scroller.scrollHeight};
  });
  assert.ok(box.shell > viewWidth + 300, `panel widened: ${viewWidth} → ${box.shell}`);
  assert.ok(Math.abs(box.scroller - box.host) < 4, `editor fits its box: ${box.scroller} vs ${box.host}`);
  assert.ok(box.content > box.scroller * 2);

  const tops = () => page.evaluate(() => {
    const sr = document.querySelector('.note-edit-cm').shadowRoot;
    const r = sr.querySelector('.cm-scroller').getBoundingClientRect();
    const pv = document.querySelector('.note-edit-preview').getBoundingClientRect();
    const num = (el) => Number(((el && el.textContent) || '').match(/(?:line|para|Section) (\d+)/)?.[1] ?? -1);
    return {editor: num(sr.elementFromPoint(r.left + 30, r.top + 12)), preview: num(document.elementFromPoint(pv.left + 30, pv.top + 12))};
  });
  // Wheel over the source: it scrolls, and the preview shows the same place.
  await page.mouse.move(400, 500);
  await page.mouse.wheel(0, 1500);
  await page.waitForTimeout(400);
  let at = await tops();
  assert.ok(at.editor > 10, `source scrolled to ${at.editor}`);
  assert.ok(Math.abs(at.editor - at.preview) <= 1, `in step: ${JSON.stringify(at)}`);
  // A click far down the note puts the cursor there.
  const line = page.locator('.note-edit-cm .cm-line', {hasText: `line ${at.editor + 3} text`});
  await line.click();
  await page.keyboard.type('ZZ');
  assert.match(await line.textContent(), /ZZ/);
  // Wheel over the preview: the source follows.
  await page.mouse.move(1200, 500);
  await page.mouse.wheel(0, -700);
  await page.waitForTimeout(400);
  const back = await tops();
  assert.ok(back.preview < at.preview, `preview scrolled back: ${JSON.stringify(back)}`);
  assert.ok(Math.abs(back.editor - back.preview) <= 1, `in step: ${JSON.stringify(back)}`);
});
