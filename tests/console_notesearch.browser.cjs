// Quick switcher (names / text) and vault links inside conversation messages.
// Run: NODE_PATH=<dir with playwright> node --test tests/console_notesearch.browser.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../claude_discord/console/static');

test('Note search and links in replies', {timeout: 30000}, async t => {
  const browser = await chromium.launch({headless:true,args:['--no-sandbox']});
  t.after(() => browser.close());
  const page = await browser.newPage({viewport:{width:1280,height:900},locale:'ja-JP'});
  page.setDefaultTimeout(5000);
  const opened = [];
  const items = [{id:'t1',thread_id:'1',title:'Chat',status:'waiting',bucket:'me',priority:2}];
  await page.route('http://console.test/**', async route => {
    const url = new URL(route.request().url());
    const p = url.pathname;
    if (p === '/' || p.startsWith('/static/')) {
      const file = p === '/' ? 'index.html' : path.basename(p);
      return route.fulfill({body:fs.readFileSync(path.join(root,file)),contentType:
        file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});
    }
    let data = {}, status = 200;
    if (p.endsWith('/board')) data = {items,slots:{},places:[]};
    else if (p.endsWith('/workspace/notes')) data = {notes:['A/Plan.md','B/Plan old.md'].filter(n => n.toLowerCase().includes(url.searchParams.get('q').toLowerCase()))};
    else if (p.endsWith('/workspace/search')) data = {hits:[{path:'C/Log.md',line:3,snippet:'the viewer ships'}]};
    else if (p.endsWith('/workspace/resolve')) {
      if (url.searchParams.get('link') === 'Plan') data = {path:'A/Plan.md',candidates:[]};
      else { status = 404; data = {path:null,candidates:[]}; }
    } else if (p.endsWith('/workspace/note')) {
      opened.push(url.searchParams.get('path'));
      data = {path:url.searchParams.get('path'),text:'# Note',links:{},revision:'r',modified:0};
    } else if (p.endsWith('/workspace/backlinks')) data = {backlinks:[]};
    else if (p.endsWith('/workspace/plan')) data = {available:false};
    else if (p.endsWith('/workspace')) data = {available:true,entries:[]};
    else if (p.endsWith('/messages')) data = {messages:[{id:'m1',kind:'agent',author:'agent',content:
      'Wrote /home/u/obsidian/02_Contexts/Work/Site/log.md and see [[Plan|the plan]], [[Gone]].\n`04_Knowledge/My Note.md` and `code only`\n```\n02_Contexts/x/in-fence.md\n```'}]};
    return route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
  });
  const loaded = page.waitForResponse(r => r.url().endsWith('/workspace'));
  await page.goto('http://console.test/#focus');
  await loaded;
  // Ctrl+O: names; Enter opens the first hit.
  await page.keyboard.press('Control+o');
  const input = page.locator('.nsearch-input');
  await input.fill('plan');
  await page.waitForFunction(() => document.querySelectorAll('.nsearch-item').length === 2);
  await input.press('ArrowDown');
  await input.press('Enter');
  await page.waitForFunction(() => location.hash === '#note/B/Plan%20old.md');
  await page.keyboard.press('Escape');
  // Ctrl+Shift+F: text with snippet; Tab switches modes.
  await page.keyboard.press('Control+Shift+F');
  await page.locator('.nsearch-input').fill('viewer');
  await page.locator('.nsearch-snippet').waitFor();
  assert.equal(await page.locator('.nsearch-snippet').innerText(), 'the viewer ships');
  await page.locator('.nsearch-input').press('Tab');
  await page.waitForFunction(() => document.querySelector('.nsearch-tab.on')?.dataset.mode === 'name');
  await page.locator('.nsearch-input').press('Escape');
  assert.equal(await page.locator('.nsearch').count(), 0);
  // Links in an agent reply.
  await page.evaluate(() => openItem('t1'));
  const msg = page.locator('.msg').first();
  await msg.locator('a.note-ref').first().waitFor();
  const refs = await msg.locator('a.note-ref').allInnerTexts();
  assert.deepEqual(refs, ['/home/u/obsidian/02_Contexts/Work/Site/log.md', 'the plan', 'Gone', '04_Knowledge/My Note.md']);
  assert.equal(await msg.locator('pre a').count(), 0);
  await msg.locator('a.note-ref', {hasText:'log.md'}).click();
  await page.waitForFunction(() => location.hash.startsWith('#note/02_Contexts/Work/Site/log.md'));
  await page.keyboard.press('Escape');
  await msg.locator('a.note-ref', {hasText:'the plan'}).click();
  await page.waitForFunction(() => location.hash === '#note/A/Plan.md');
  await page.keyboard.press('Escape');
  await msg.locator('a.note-ref', {hasText:'Gone'}).click();
  await page.locator('.toast', {hasText:'リンク先のノートがありません'}).waitFor();
  await msg.locator('a.note-ref', {hasText:'My Note'}).click();
  await page.waitForFunction(() => location.hash === '#note/04_Knowledge/My%20Note.md');
  assert.ok(opened.includes('04_Knowledge/My Note.md'));
});
