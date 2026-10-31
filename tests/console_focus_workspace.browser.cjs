// Focus view with Ebi Workspace: plan card, ranked next actions with write-back, waiting list.
// Run: NODE_PATH=<dir with playwright> node --test tests/console_focus_workspace.browser.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../claude_discord/console/static');

function planFixture() {
  return {available:true,date:'2026-10-16',total:3,lapsed:2,lapsed_days:14,
    today_plan:'### オススメ着手順\n1. Ship the website',
    items:[
      {kind:'project',path:'Work/Website',text:'**Ship** the [[Docs/guide|guide]]',score:80,line:3,
        raw:'**Ship** the [[Docs/guide|guide]] 🔁毎週',revision:'v1',priority:'P1',more:1,status:'Ready',due:'2026-10-14',days:-2,milestone:'Launch'},
      {kind:'follow_up',path:'Work/Website',text:'Client / Feedback',score:70,line:null,raw:null,revision:null,
        priority:'P1',due:'2026-10-16',days:0},
      {kind:'single',path:'Work',text:'Call back',score:40,line:2,raw:'[P2] Call back 📅2026-10-17',
        revision:'v2',priority:'P2',due:'2026-10-17',days:1},
    ],
    stalled_total:7,
    stalled:[
      {kind:'project',path:'Old/Long',text:'Restart it',score:10,line:4,raw:'Restart it',revision:'v3',
        priority:'P2',status:null,stale_days:30,due:null,days:null},
      {kind:'no_next',path:'Old/Plain',text:null,score:20,line:null,raw:null,revision:null,
        priority:'P2',status:null,stale_days:null,due:null,days:null},
      ...[1,2,3,4,5].map(n => ({kind:'no_next',path:`Old/More${n}`,text:null,score:10,line:null,raw:null,
        revision:null,priority:'P3',status:null,stale_days:8,due:null,days:null})),
    ],
    waiting:[
      {path:'Work/Website',who:'Client',what:'Feedback',since:'2026-10-01',due:'2026-10-16',days:0},
      {path:'Work/Website',who:'Vendor',what:'Quote',since:null,due:'2026-10-30',days:14},
    ]};
}

for (const mode of ['available','plugin-missing']) {
  test(`Focus with Ebi Workspace (${mode})`, {timeout: 30000}, async t => {
    const browser = await chromium.launch({headless:true,args:['--no-sandbox']});
    t.after(() => browser.close());
    const page = await browser.newPage({viewport:{width:1440,height:900},locale:'ja-JP'});
    const writes = [];
    let plan = mode === 'available' ? planFixture() : {available:false,reason:'plugin'};
    let planReads = 0;
    let items = [];
    await page.route('http://console.test/**', async route => {
      const p = new URL(route.request().url()).pathname;
      if (p === '/' || p.startsWith('/static/')) {
        const file = p === '/' ? 'index.html' : path.basename(p);
        return route.fulfill({body:fs.readFileSync(path.join(root,file)),contentType:
          file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});
      }
      let data = {};
      if (p.endsWith('/board')) data = {items,slots:{},places:[]};
      else if (p.endsWith('/workspace/plan')) { planReads += 1; data = plan; }
      else if (p.endsWith('/workspace')) data = {available:true,entries:[]};
      else if (p.endsWith('/workspace/tasks')) {
        writes.push(route.request().postDataJSON());
        if (plan.items) plan = {...plan, items: plan.items.slice(1)};
        data = {};
      } else if (p.endsWith('/workspace/run')) {
        writes.push(route.request().postDataJSON());
        items = [{id:'t1',thread_id:'1',title:'Call back',status:'running',bucket:'ai',running:true,
          workspace_path:'Work',priority:2}];
        data = {item:items[0]};
      } else if (p.endsWith('/messages')) data = {messages:[]};
      return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
    });
    await page.goto('http://console.test/');
    await page.evaluate(() => setView('focus'));
    await page.waitForFunction(() => document.querySelector('.place-bar'));
    if (mode !== 'available') {
      await page.waitForFunction(() => true);
      await page.waitForTimeout(300);
      assert.ok(planReads >= 1);
      assert.equal(await page.locator('.ws-focus, .ws-focus-plan').count(), 0);
      return;
    }
    await page.locator('.ws-focus-item').first().waitFor();
    // Plan card: folded by default, renders Markdown when opened.
    const summary = page.locator('.ws-focus-plan summary');
    assert.match(await summary.innerText(), /今日のプラン/);
    await summary.click();
    await page.getByText('Ship the website').waitFor();
    // Ranked rows: Markdown flattened, tags, and rows without a line offer only "open".
    const rows = page.locator('.ws-focus:not(.ws-focus-waiting):not(.ws-focus-stalled) .ws-focus-item');
    assert.equal(await rows.count(), 3);
    assert.equal(await rows.nth(0).locator('.ws-focus-text').innerText(), 'Ship the guide');
    assert.match(await rows.nth(0).innerText(), /期限2日超過/);
    assert.match(await rows.nth(0).innerText(), /ほか 1 件/);
    // The milestone that set the deadline, so the reason for the rank is visible.
    assert.equal(await rows.nth(0).locator('.ws-focus-milestone').innerText(), '🎯 Launch');
    assert.equal(await rows.nth(1).locator('.ws-focus-milestone').count(), 0);
    assert.equal(await rows.nth(1).getByRole('button', {name:'✅ 完了'}).count(), 0);
    assert.match(await page.locator('.ws-focus-lapsed').innerText(), /2 件/);
    // Stalled card: first five open with a stall tag, the rest folded; no next action says so.
    const stalled = page.locator('.ws-focus-stalled > .ws-focus-list > .ws-focus-item');
    assert.equal(await stalled.count(), 5);
    assert.match(await page.locator('.ws-focus-stalled .ws-focus-head').innerText(), /停滞中のプロジェクト（7件）/);
    assert.match(await stalled.nth(0).innerText(), /30日停滞/);
    assert.equal(await stalled.nth(0).getByRole('button', {name:'▶ AIに実行'}).count(), 1);
    assert.match(await stalled.nth(1).innerText(), /次のアクションが未設定/);
    assert.equal(await stalled.nth(1).getByRole('button', {name:'✅ 完了'}).count(), 0);
    assert.match(await page.locator('.ws-focus-stalled-more summary').innerText(), /ほか 2 件/);
    // Waiting list shows only entries whose follow-up has not come yet.
    const waiting = page.locator('.ws-focus-waiting .ws-focus-item');
    assert.equal(await waiting.count(), 1);
    assert.match(await waiting.innerText(), /Vendor \/ Quote/);
    // Re-date keeps the recurrence rule after the new date.
    await rows.nth(0).getByRole('button', {name:'📅 期限'}).click();
    await rows.nth(0).locator('input[type=date]').fill('2026-10-20');
    await page.waitForFunction(() => document.querySelectorAll('.ws-focus:not(.ws-focus-waiting):not(.ws-focus-stalled) .ws-focus-item').length === 2);
    assert.deepEqual(writes.at(-1), {path:'Work/Website',line:3,revision:'v1',
      text:'**Ship** the [[Docs/guide|guide]] 📅2026-10-20 🔁毎週'});
    // Complete writes done:true for the exact line and revision.
    plan = planFixture(); plan.items = plan.items.slice(2);
    await page.evaluate(() => window.ConsoleFocusWorkspace.load());
    await page.waitForFunction(() => document.querySelectorAll('.ws-focus:not(.ws-focus-waiting):not(.ws-focus-stalled) .ws-focus-item').length === 1);
    await rows.nth(0).getByRole('button', {name:'▶ AIに実行'}).click();
    await page.waitForFunction(() => state.open === 't1');
    assert.deepEqual(writes.at(-1), {path:'Work',line:2,revision:'v2'});
    plan = planFixture();
    await page.evaluate(() => window.ConsoleFocusWorkspace.load());
    await page.locator('.ws-focus-item').first().waitFor();
    await rows.nth(2).getByRole('button', {name:'✅ 完了'}).click();
    await page.waitForFunction(() => document.querySelectorAll('.ws-focus:not(.ws-focus-waiting):not(.ws-focus-stalled) .ws-focus-item').length === 2);
    assert.deepEqual(writes.at(-1), {path:'Work',line:2,revision:'v2',done:true});
    await page.setViewportSize({width:390,height:844});
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= 390));
  });
}

// Every line of today's plan can be handed to an agent: a line that names a known task runs
// that task (bound to its Markdown line), anything else becomes a new item that starts at once.
test('Focus plan lines hand off to AI', {timeout: 30000}, async t => {
  const browser = await chromium.launch({headless:true,args:['--no-sandbox']});
  t.after(() => browser.close());
  const page = await browser.newPage({viewport:{width:1440,height:900},locale:'ja-JP'});
  const runs = [];
  const created = [];
  const plan = {...planFixture(), today_plan: [
    '### 今日が期限', '',
    '- 動画の概要欄に記事を貼る（[[02_Contexts/Work/single-actions.md|Work]]）', '',
    '### オススメ着手順', '',
    '1. [[02_Contexts/Work/Website/status.md|Work/Website]] — P1・期限超過',
    '2. 新しいこと（[[02_Contexts/Nowhere/status.md|Nowhere]]）', '',
    '### アクティブ', '',
    '- [[02_Contexts/Work/single-actions.md|Work]] · P2 · 期限: 2026-10-18 — 動画の概要欄に記事を貼る',
  ].join('\n')};
  const entries = [
    {path:'Work',name:'Work',kind:'context',revision:'w1',
      tasks:[{line:5,text:'[P2] 動画の概要欄に記事を貼る 📅2026-10-18',done:false}]},
    {path:'Work/Website',name:'Website',kind:'project',revision:'v1',tasks:[]},
  ];
  let items = [];
  await page.route('http://console.test/**', async route => {
    const p = new URL(route.request().url()).pathname;
    if (p === '/' || p.startsWith('/static/')) {
      const file = p === '/' ? 'index.html' : path.basename(p);
      return route.fulfill({body:fs.readFileSync(path.join(root,file)),contentType:
        file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});
    }
    let data = {};
    if (p.endsWith('/board')) data = {items,slots:{},places:[]};
    else if (p.endsWith('/workspace/plan')) data = plan;
    else if (p.endsWith('/workspace')) data = {available:true,entries};
    else if (p.endsWith('/workspace/run')) {
      runs.push(route.request().postDataJSON());
      items = [{id:`r${runs.length}`,thread_id:'1',title:'run',status:'running',bucket:'ai',running:true,priority:2}];
      data = {item:items[0]};
    } else if (p.endsWith('/items') && route.request().method() === 'POST') {
      created.push(route.request().postDataJSON());
      items = [{id:'n1',thread_id:'2',title:'new',status:'running',bucket:'ai',running:true,priority:2}];
      data = {item:items[0]};
    } else if (p.endsWith('/messages')) data = {messages:[]};
    return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.goto('http://console.test/');
  await page.evaluate(() => setView('focus'));
  await page.locator('.ws-focus-plan summary').click();
  const buttons = page.locator('.ws-focus-plan-body li').getByRole('button', {name:'▶ AIに任せる'});
  await buttons.first().waitFor();
  assert.equal(await buttons.count(), 4);
  // Wiki links read as their label, not as a vault path.
  const shown = await page.locator('.ws-focus-plan-body').innerText();
  assert.match(shown, /動画の概要欄に記事を貼る（Work）/);
  assert.doesNotMatch(shown, /02_Contexts|\[\[/);
  // A task named in the plan runs that exact line of its single-actions file.
  await buttons.nth(0).click();
  await page.waitForFunction(() => state.open === 'r1');
  assert.deepEqual(runs.at(-1), {path:'Work',line:5,revision:'w1'});
  // A project line runs the project's ranked next action.
  await page.evaluate(() => setView('focus'));
  await buttons.nth(1).click();
  await page.waitForFunction(() => state.open === 'r2');
  assert.deepEqual(runs.at(-1), {path:'Work/Website',line:3,revision:'v1'});
  // Anything else becomes a new item that an agent starts right away.
  await page.evaluate(() => setView('focus'));
  await buttons.nth(2).click();
  await page.waitForFunction(() => state.open === 'n1');
  assert.equal(created.length, 1);
  assert.equal(created[0].title, '新しいこと');
  assert.equal(created[0].start, true);
  assert.equal(created[0].workspace_path, undefined);
  assert.match(created[0].note, /今日のプラン/);
  assert.match(created[0].note, /\[\[02_Contexts\/Nowhere\/status\.md\|Nowhere\]\]/);
  // "[[entry]] · P2 · 期限 — task" names the task after the dash.
  await page.evaluate(() => setView('focus'));
  await buttons.nth(3).click();
  await page.waitForFunction(() => state.open === 'r3');
  assert.deepEqual(runs.at(-1), {path:'Work',line:5,revision:'w1'});
});
