// CodeMirror 6 in the note editor, under the Console's real Content-Security-Policy:
// it must load lazily, style itself without CSP violations, complete [[links]] and save.
// Run: NODE_PATH=<dir with playwright> node --test tests/console_codemirror.browser.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../claude_discord/console/static');
const server = fs.readFileSync(path.resolve(__dirname, '../claude_discord/console/server.py'), 'utf8');
// The CSP exactly as server.py sends it (adjacent Python string literals joined).
const CSP = server.split('"Content-Security-Policy": (')[1].split(')')[0]
  .split('\n').map(l => (l.match(/"([^"]*)"/) || [])[1]).filter(Boolean).join('');

test('CodeMirror editor under the real CSP', {timeout: 40000}, async t => {
  assert.match(CSP, /style-src 'self'; script-src 'self'/);
  const browser = await chromium.launch({headless:true,args:['--no-sandbox']});
  t.after(() => browser.close());
  const page = await browser.newPage({viewport:{width:1280,height:900},locale:'ja-JP'});
  page.setDefaultTimeout(10000);
  const violations = [];
  page.on('console', m => { if (/Content Security Policy/i.test(m.text())) violations.push(m.text()); });
  let vendorLoads = 0;
  const puts = [];
  let disk = {path:'A/Note.md', text:'# Title\n- item\n', revision:'r1', links:{}, modified:0};
  await page.route('http://console.test/**', async route => {
    const req = route.request();
    const url = new URL(req.url());
    const p = url.pathname;
    if (p === '/' || p.startsWith('/static/')) {
      const file = p === '/' ? 'index.html' : path.basename(p);
      if (file === 'vendor-codemirror.min.js') vendorLoads += 1;
      return route.fulfill({body:fs.readFileSync(path.join(root,file)),headers:{'Content-Security-Policy':CSP},
        contentType:file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});
    }
    let data = {};
    if (p.endsWith('/board')) data = {items:[],slots:{},places:[]};
    else if (p.endsWith('/workspace/note') && req.method() === 'PUT') {
      puts.push(req.postDataJSON()); disk = {...disk, text:req.postDataJSON().text, revision:'r2'}; data = disk;
    } else if (p.endsWith('/workspace/note')) data = disk;
    else if (p.endsWith('/workspace/notes')) data = {notes:['X/Planning.md','Y/Plan B.md']};
    else if (p.endsWith('/workspace/backlinks')) data = {backlinks:[]};
    else if (p.endsWith('/workspace/plan')) data = {available:false};
    else if (p.endsWith('/workspace')) data = {available:true,entries:[]};
    return route.fulfill({contentType:'application/json',body:JSON.stringify(data),headers:{'Content-Security-Policy':CSP}});
  });
  // The side-by-side layout (live preview has its own test).
  await page.addInitScript(() => localStorage.setItem('ccdb.noteEditMode', 'split'));
  await page.goto('http://console.test/#focus');
  await page.evaluate(() => window.ConsoleNotes.open('A/Note.md'));
  await page.locator('.note-content > h1').waitFor();
  assert.equal(vendorLoads, 0); // not loaded until editing
  await page.getByRole('button', {name:'✏️ 編集'}).click();
  const cm = page.locator('.note-edit-cm .cm-content');
  await cm.waitFor();
  assert.equal(vendorLoads, 1);
  assert.equal(await page.locator('.note-edit-text').isVisible(), false);
  // CodeMirror's own styles applied: in a shadow root they are a constructable stylesheet,
  // which style-src 'self' allows (a <style> element in the document would be blocked).
  const editorStyle = () => page.evaluate(() => {
    const ed = document.querySelector('.note-edit-cm').shadowRoot.querySelector('.cm-editor');
    const cs = getComputedStyle(ed);
    return [cs.display, cs.backgroundColor];
  });
  const [display, background] = await editorStyle();
  assert.equal(display, 'flex');
  assert.notEqual(background, 'rgba(0, 0, 0, 0)');
  assert.deepEqual(violations, []);
  // List continuation and [[ completion (closeBrackets already typed "]]": no second pair).
  await cm.click();
  await page.keyboard.press('Control+End');
  await page.keyboard.press('Backspace'); // to the end of "- item"
  await page.keyboard.press('Enter');     // continues the list: "- "
  await page.keyboard.type('see [[Pla', {delay: 20});
  await page.locator('.cm-tooltip-autocomplete li', {hasText:'Planning'}).click();
  await page.keyboard.type(' done', {delay: 20});
  await page.waitForFunction(() => document.querySelector('.note-edit-preview a'));
  // Ctrl+S inside CodeMirror saves.
  await page.keyboard.press('Control+s');
  await page.waitForFunction(() => !document.querySelector('.note-edit'));
  assert.equal(puts.length, 1);
  assert.equal(puts[0].text, '# Title\n- item\n- see [[Planning]] done');
  assert.deepEqual(violations, []);
});
