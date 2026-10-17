// Editing a note: source + live preview, Ctrl+S, revision conflict with a diff, mobile tabs.
// Run: NODE_PATH=<dir with playwright> node --test tests/console_noteedit.browser.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../claude_discord/console/static');

test('Edit a note with conflict handling', {timeout: 30000}, async t => {
  const browser = await chromium.launch({headless:true,args:['--no-sandbox']});
  t.after(() => browser.close());
  const page = await browser.newPage({viewport:{width:1280,height:900},locale:'ja-JP'});
  page.setDefaultTimeout(10000);
  page.on('dialog', d => d.accept());
  let disk = {path:'A/Note.md', text:'# Title\nline one\nline two\n', revision:'r1', links:{}, modified:0};
  const puts = [];
  let conflictOnce = false;
  await page.route('http://console.test/**', async route => {
    const req = route.request();
    const url = new URL(req.url());
    const p = url.pathname;
    if (p === '/' || p.startsWith('/static/')) {
      const file = p === '/' ? 'index.html' : path.basename(p);
      return route.fulfill({body:fs.readFileSync(path.join(root,file)),contentType:
        file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});
    }
    let data = {}, status = 200;
    if (p.endsWith('/board')) data = {items:[],slots:{},places:[]};
    else if (p.endsWith('/workspace/note') && req.method() === 'PUT') {
      const body = req.postDataJSON(); puts.push(body);
      if (conflictOnce) { conflictOnce = false; disk = {...disk, text:'# Title\nline one (agent)\nline two\n', revision:'r9'}; }
      if (body.revision !== disk.revision) { status = 409; data = {error:'The note changed.', current:disk}; }
      else { disk = {...disk, text: body.text, revision: disk.revision + 'x'}; data = disk; }
    } else if (p.endsWith('/workspace/note')) data = disk;
    else if (p.endsWith('/workspace/backlinks')) data = {backlinks:[]};
    else if (p.endsWith('/workspace/plan')) data = {available:false};
    else if (p.endsWith('/workspace')) data = {available:true,entries:[]};
    return route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.goto('http://console.test/#focus');
  await page.evaluate(() => window.ConsoleNotes.open('A/Note.md'));
  await page.locator('.note-content > h1').waitFor();
  await page.getByRole('button', {name:'✏️ 編集'}).click();
  const area = page.locator('.note-edit-text');
  assert.equal(await area.inputValue(), '# Title\nline one\nline two\n');
  // Live preview follows the source.
  await area.fill('# Renamed\nline one\nline two\n');
  await page.waitForFunction(() => document.querySelector('.note-edit-preview h1')?.textContent === 'Renamed');
  // Ctrl+S saves with the starting revision and the log choice; the note shows again.
  await page.locator('.note-edit-log').check();
  await area.press('Control+s');
  await page.waitForFunction(() => document.querySelector('.note-content > h1')?.textContent === 'Renamed');
  assert.deepEqual(puts[0], {path:'A/Note.md', revision:'r1', text:'# Renamed\nline one\nline two\n', log:true});
  // A conflict shows the difference; overwrite saves on top of the new revision.
  conflictOnce = true;
  await page.getByRole('button', {name:'✏️ 編集'}).click();
  await area.fill('# Renamed\nline one\nline two\nmine\n');
  await page.getByRole('button', {name:'💾 保存'}).click();
  await page.locator('.note-conflict').waitFor();
  const lines = await page.locator('.note-diff-line').allInnerTexts();
  assert.ok(lines.includes('- line one (agent)') && lines.includes('+ line one') && lines.includes('+ mine'));
  await page.getByRole('button', {name:'自分の編集で上書き保存'}).click();
  await page.waitForFunction(() => !document.querySelector('.note-edit'));
  assert.equal(puts.at(-1).revision, 'r9');
  assert.match(disk.text, /mine/);
  // Phone: edit and preview are tabs.
  await page.setViewportSize({width:390,height:844});
  await page.getByRole('button', {name:'✏️ 編集'}).click();
  assert.equal(await page.locator('.note-edit-preview').isVisible(), false);
  await page.getByRole('button', {name:'プレビュー'}).click();
  assert.equal(await page.locator('.note-edit-text').isVisible(), false);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= 390));
  await page.getByRole('button', {name:'やめる'}).click();
  await page.locator('.note-content').waitFor();
});
