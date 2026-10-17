// "Notes this thread changed" under a thread: folded, lazy-loaded, diff colours, open in viewer.
// Run: NODE_PATH=<dir with playwright> node --test tests/console_threadnotes.browser.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../claude_discord/console/static');

test('Notes a thread changed', {timeout: 30000}, async t => {
  const browser = await chromium.launch({headless:true,args:['--no-sandbox']});
  t.after(() => browser.close());
  const page = await browser.newPage({viewport:{width:1280,height:900},locale:'ja-JP'});
  page.setDefaultTimeout(10000);
  let reads = 0;
  const items = [{id:'t1',thread_id:'1',title:'Work',status:'waiting',bucket:'me',priority:2},
    {id:'c1',title:'Capture',status:'open',bucket:'todo',priority:2}];
  await page.route('http://console.test/**', async route => {
    const url = new URL(route.request().url());
    const p = url.pathname;
    if (p === '/' || p.startsWith('/static/')) {
      const file = p === '/' ? 'index.html' : path.basename(p);
      return route.fulfill({body:fs.readFileSync(path.join(root,file)),contentType:
        file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});
    }
    let data = {};
    if (p.endsWith('/board')) data = {items,slots:{},places:[]};
    else if (p.endsWith('/items/t1/notes')) { reads += 1; data = {since:'2026-10-16T10:00:00Z', notes:[
      {path:'04_Knowledge/Idea.md',status:'modified',truncated:false,diff:'diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1,2 +1,2 @@\n one\n-two\n+TWO'},
      {path:'02_Contexts/Work/New.md',status:'added',truncated:false,diff:'+fresh'}]}; }
    else if (p.endsWith('/workspace/note')) data = {path:url.searchParams.get('path'),text:'# Note',links:{},revision:'r',modified:0};
    else if (p.endsWith('/workspace/backlinks')) data = {backlinks:[]};
    else if (p.endsWith('/messages')) data = {messages:[]};
    else if (p.endsWith('/workspace/plan')) data = {available:false};
    else if (p.endsWith('/workspace')) data = {available:true,entries:[]};
    return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
  });
  const loaded = page.waitForResponse(r => r.url().endsWith('/workspace'));
  await page.goto('http://console.test/#focus');
  await loaded;
  await page.evaluate(() => openItem('t1'));
  const pane = page.locator('#d-notes');
  await pane.locator('summary').first().waitFor();
  assert.equal(reads, 0); // nothing is read until it is opened
  await pane.locator('.tnotes-summary').click();
  await pane.locator('.tnotes-note').first().waitFor();
  assert.match(await pane.locator('.tnotes-summary').innerText(), /\(2\)/);
  await pane.locator('.tnotes-note summary').first().click();
  const lines = await pane.locator('.tnotes-note').first().locator('.note-diff-line').allInnerTexts();
  assert.deepEqual(lines, ['@@ -1,2 +1,2 @@', ' one', '-two', '+TWO']);
  assert.equal(await pane.locator('.note-diff-line.del').first().innerText(), '-two');
  await pane.locator('.tnotes-open').first().click();
  await page.waitForFunction(() => location.hash === '#note/04_Knowledge/Idea.md');
  await page.keyboard.press('Escape');
  // A capture has no session: no section.
  await page.evaluate(() => openItem('c1'));
  await page.waitForFunction(() => document.getElementById('d-notes')?.hidden === true);
});
