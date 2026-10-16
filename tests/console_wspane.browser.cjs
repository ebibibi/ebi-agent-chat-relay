// Workspace pane in the thread detail and the folded "[Ebi Workspace context]" note.
// Run: NODE_PATH=<dir with playwright> node --test tests/console_wspane.browser.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../claude_discord/console/static');

test('Workspace pane and sent-context note', {timeout: 30000}, async t => {
  const browser = await chromium.launch({headless:true,args:['--no-sandbox']});
  t.after(() => browser.close());
  const page = await browser.newPage({viewport:{width:1440,height:900},locale:'ja-JP'});
  page.setDefaultTimeout(5000);
  const context = {path:'Work/Site',name:'Site',kind:'project',context:'Work',file:'status.md',revision:'r1',
    tasks:[{line:5,text:'Ship it',done:false},{line:6,text:'Old',done:true}],
    current:'**Ready** to ship\nmore', waiting:['Client / Feedback'], log:'## 2026-10-17\n- did things'};
  const items = [
    {id:'t1',thread_id:'1',title:'Fix the site',status:'waiting',bucket:'me',priority:2,workspace_path:'Work/Site',workspace_source:'pinned'},
    {id:'t2',thread_id:'2',title:'Another site thread',status:'waiting',bucket:'me',priority:2,workspace_path:'Work/Site',workspace_source:'recorded'},
  ];
  const writes = [];
  let contextReads = 0;
  await page.route('http://console.test/**', async route => {
    const req = route.request();
    const p = new URL(req.url()).pathname;
    if (p === '/' || p.startsWith('/static/')) {
      const file = p === '/' ? 'index.html' : path.basename(p);
      return route.fulfill({body:fs.readFileSync(path.join(root,file)),contentType:
        file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});
    }
    let data = {};
    if (p.endsWith('/board')) data = {items,slots:{},places:[]};
    else if (p.endsWith('/workspace/plan')) data = {available:false,reason:'plugin'};
    else if (p.endsWith('/workspace/context')) { contextReads += 1; data = context; }
    else if (p.endsWith('/workspace')) data = {available:true,entries:[context]};
    else if (p.endsWith('/workspace/tasks')) {
      writes.push(req.postDataJSON());
      context.tasks[0].done = true; context.revision = 'r2'; data = context;
    } else if (p.endsWith('/messages')) data = {messages:[
      {id:'m1',kind:'human',author:'me',content:'Please fix it\n\n[Ebi Workspace context]\nThis work belongs to Work/Site. Read applicable AGENTS.md files'},
      {id:'m2',kind:'agent',author:'agent',content:'Done'},
    ]};
    return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
  });
  const loaded = page.waitForResponse(r => r.url().endsWith('/workspace'));
  await page.goto('http://console.test/');
  await loaded;
  await page.evaluate(() => openItem('t1'));
  const pane = page.locator('#d-ws');
  await pane.locator('summary').first().waitFor();
  assert.match(await pane.locator('.ws-pane-summary').innerText(), /Work\/Site[\s\S]*Ready to ship/);
  // Folded by default; opening it shows state, next actions, waiting and other threads.
  assert.equal(await pane.locator('details').first().getAttribute('open'), null);
  await pane.locator('.ws-pane-summary').click();
  const text = await pane.innerText();
  assert.match(text, /Ship it/);
  assert.doesNotMatch(text, /Old/);
  assert.match(text, /Client \/ Feedback/);
  assert.match(text, /Another site thread/);
  await pane.locator('.ws-pane-task input').check();
  await page.waitForFunction(() => !/Ship it/.test(document.getElementById('d-ws').innerText));
  assert.deepEqual(writes[0], {path:'Work/Site',line:5,revision:'r1',done:true});
  // The context block in the human message is folded under a one-line note.
  const msg = page.locator('.msg').first();
  await page.locator('.ws-sent').waitFor();
  assert.match(await msg.innerText(), /Please fix it/);
  assert.match(await page.locator('.ws-sent summary').innerText(), /コンテキストを渡しました: Work\/Site/);
  assert.equal(await page.locator('.ws-sent pre').isVisible(), false);
  // Open state is remembered; another thread in the same project opens it too.
  await page.locator('.ws-pane-thread').click();
  await page.waitForFunction(() => state.open === 't2');
  await pane.locator('details[open]').waitFor();
  assert.ok(contextReads >= 1);
  await page.setViewportSize({width:390,height:844});
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= 390));
});
