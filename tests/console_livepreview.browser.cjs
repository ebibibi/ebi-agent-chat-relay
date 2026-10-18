// Live preview in the note editor: Markdown drawn in place with its markup hidden except on
// the cursor's line, task boxes that toggle, a switch to the side-by-side layout that is
// remembered, and a save that sends the note's exact text (nothing is rewritten).
// Run: NODE_PATH=<dir with playwright> node --test tests/console_livepreview.browser.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../claude_discord/console/static');
const server = fs.readFileSync(path.resolve(__dirname, '../claude_discord/console/server.py'), 'utf8');
const CSP = server.split('"Content-Security-Policy": (')[1].split(')')[0]
  .split('\n').map(l => (l.match(/"([^"]*)"/) || [])[1]).filter(Boolean).join('');

const TEXT = '---\ntags: [a]\n---\n# Plan\n\nSome **bold**, `code`, [site](https://example.com) and [[Design|the design]].\n\n'
  + '- [ ] write it\n- plain item\n\n> quoted\n\n![図1 構成](%E5%9B%B3%201.png)\n\n![[shot.png]]\n\nSee https://example.com/x here.\n';
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==', 'base64');

test('live preview draws the note and saves its exact text', {timeout: 40000}, async t => {
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  t.after(() => browser.close());
  const page = await browser.newPage({viewport: {width: 1400, height: 900}, locale: 'ja-JP'});
  page.setDefaultTimeout(10000);
  const violations = [];
  page.on('console', m => { if (/Content Security Policy/i.test(m.text()) && !/directive name/.test(m.text())) violations.push(m.text()); });
  const puts = [];
  let disk = {path: 'A/Plan.md', text: TEXT, revision: 'r1', links: {'shot.png': 'img/shot.png'}, modified: 0};
  await page.route('http://console.test/**', async route => {
    const req = route.request();
    const p = new URL(req.url()).pathname;
    if (p === '/' || p.startsWith('/static/')) {
      const file = p === '/' ? 'index.html' : path.basename(p);
      return route.fulfill({body: fs.readFileSync(path.join(root, file)), headers: {'Content-Security-Policy': CSP},
        contentType: file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html'});
    }
    if (p.endsWith('/workspace/file')) return route.fulfill({body: PNG, contentType: 'image/png'});
    let data = {};
    if (p.endsWith('/board')) data = {items: [], slots: {}, places: []};
    else if (p.endsWith('/workspace/note') && req.method() === 'PUT') {
      puts.push(req.postDataJSON()); disk = {...disk, text: req.postDataJSON().text, revision: 'r2'}; data = disk;
    } else if (p.endsWith('/workspace/note')) data = disk;
    else if (p.endsWith('/workspace/notes')) data = {notes: []};
    else if (p.endsWith('/workspace/backlinks')) data = {backlinks: []};
    else if (p.endsWith('/workspace/plan')) data = {available: false};
    else if (p.endsWith('/workspace')) data = {available: true, entries: []};
    return route.fulfill({contentType: 'application/json', body: JSON.stringify(data), headers: {'Content-Security-Policy': CSP}});
  });
  await page.goto('http://console.test/#focus');
  await page.evaluate(() => window.ConsoleNotes.open('A/Plan.md'));
  await page.locator('.note-content > h1').waitFor();
  await page.getByRole('button', {name: '✏️ 編集'}).click();
  const cm = page.locator('.note-edit-cm .cm-content');
  await cm.waitFor();

  // Live by default: the preview pane is gone and the editor draws the note.
  await page.locator('.note-edit.note-edit-live').waitFor();
  assert.equal(await page.locator('.note-edit-preview').isVisible(), false);
  const lineText = (has) => page.locator('.note-edit-cm .cm-line', {hasText: has}).first().textContent();
  assert.equal(await lineText('Plan'), 'Plan'); // "# " hidden
  assert.equal(await lineText('Some'), 'Some bold, code, site and the design.');
  assert.equal(await page.locator('.note-edit-cm .cm-lp-h1').count(), 1);
  assert.equal(await page.locator('.note-edit-cm .cm-lp-quote').textContent(), 'quoted');
  // Images are drawn in place: a relative path from the note's folder, an embed by its link.
  const srcs = await page.locator('.note-edit-cm img.cm-lp-img').evaluateAll(els => els.map(e => e.getAttribute('src')));
  assert.deepEqual(srcs, ['/console/api/workspace/file?path=A%2F%E5%9B%B3%201.png', '/console/api/workspace/file?path=img%2Fshot.png']);
  assert.equal(await page.locator('.note-edit-cm .cm-line', {hasText: '構成'}).count(), 0); // markup replaced
  // Links and bare URLs are drawn in the accent colour, readable on the dark panel.
  const accent = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--accent').trim());
  const urlColor = await page.locator('.note-edit-cm .cm-line', {hasText: 'See'}).locator('span', {hasText: 'https://example.com/x'}).last()
    .evaluate(e => getComputedStyle(e).color);
  assert.equal(urlColor, await page.evaluate(c => { const d = document.createElement('i'); d.style.color = c; document.body.append(d); const v = getComputedStyle(d).color; d.remove(); return v; }, accent));
  // The cursor's line shows its markup again.
  await page.locator('.note-edit-cm .cm-line', {hasText: 'Some'}).click();
  assert.equal(await lineText('Some'), 'Some **bold**, `code`, [site](https://example.com) and [[Design|the design]].');
  // A task box toggles the source.
  await page.locator('.note-edit-cm .cm-lp-task').click();
  assert.equal(await page.locator('.note-edit-cm .cm-lp-task').isChecked(), true);

  // Switch to source + preview; the choice is remembered.
  await page.getByRole('button', {name: '⇆ ソースとプレビュー'}).click();
  await page.locator('.note-edit-preview h1').waitFor();
  assert.equal(await page.locator('.note-edit-preview img.note-img').first().getAttribute('src'), '/console/api/workspace/file?path=A%2F%E5%9B%B3%201.png');
  assert.equal(await page.locator('.note-edit-cm .cm-lp-task').count(), 0);
  assert.equal(await page.evaluate(() => localStorage.getItem('ccdb.noteEditMode')), 'split');
  await page.getByRole('button', {name: '👁 ライブプレビュー'}).click();
  assert.equal(await page.evaluate(() => localStorage.getItem('ccdb.noteEditMode')), 'live');

  await cm.click();
  await page.keyboard.press('Control+s');
  await page.waitForFunction(() => !document.querySelector('.note-edit'));
  assert.equal(puts.length, 1);
  assert.equal(puts[0].text, TEXT.replace('- [ ] write it', '- [x] write it'));
  assert.deepEqual(violations, []);
});
