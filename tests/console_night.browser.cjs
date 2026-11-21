// #1476/#1511: the capture box's 🌙 starts the night shift (box text = top priority) and shows
// what was picked with a way to take threads out; the reply box's 🌙 fills in the template
// without sending; inside the person's quiet hours only their urgent conditions ring.
const { chromium } = require('playwright');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const root = require('node:path').resolve(__dirname, '../claude_discord/console/static') + '/';
const TEMPLATE = '目的：朝までに［成果］を［完了条件］まで進める\nやらないこと：送信・公開・課金・本番変更\n';
(async () => {
  const browser = await chromium.launch({headless:true, executablePath:process.env.CONSOLE_TEST_CHROMIUM, args:['--no-sandbox']});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:900}});
    const errors = [];
    let posts = 0;
    let settings = {quiet_enabled:false, urgent_statuses:[], urgent_keywords:[]};
    let shift = null, shiftBody = null, cancelled = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.route('http://console.test/**', async route => {
      const path = new URL(route.request().url()).pathname;
      if (path === '/') return route.fulfill({contentType:'text/html',body:fs.readFileSync(root+'index.html')});
      if (path.startsWith('/static/')) return route.fulfill({contentType:path.endsWith('.css')?'text/css':'text/javascript',body:fs.readFileSync(root+path.split('/').pop())});
      if (path === '/console/api/night/shift') {
        const method = route.request().method();
        if (method === 'POST') {
          shiftBody = route.request().postDataJSON();
          shift = {note: shiftBody.note, dispatch_at: new Date(Date.now()+120000).toISOString(),
            items: [{id:'t1', title:'夜の作業A', state:'planned'}, {id:'t2', title:'夜の作業B', state:'planned'}]};
          return route.fulfill({status:201, contentType:'application/json', body:JSON.stringify({shift, grace_seconds:120})});
        }
        if (method === 'DELETE') {
          const item = new URL(route.request().url()).searchParams.get('item');
          cancelled.push(item);
          shift.items.forEach((e) => { if (!item || e.id === item) e.state = 'cancelled'; });
        }
        return route.fulfill({contentType:'application/json', body:JSON.stringify({shift})});
      }
      if (route.request().method() === 'POST') posts++;
      if (path === '/console/api/night') return route.fulfill({contentType:'application/json',body:JSON.stringify({template:TEMPLATE, settings})});
      return route.fulfill({contentType:'application/json',body:JSON.stringify(path.endsWith('/board')?{items:[],slots:{},places:[]}:{})});
    });
    await page.goto('http://console.test/');
    await page.evaluate(() => {localStorage.setItem('lang','ja'); localStorage.setItem('autostart','0');});
    await page.reload();

    const moon = page.locator('#capture .btn.night');
    assert.equal(await moon.count(), 1, 'one 🌙 beside the capture box');
    const input = page.locator('#capture-input');
    await input.fill('PR #5 を最優先で');
    await moon.click();
    await page.waitForSelector('#night-panel .night-item');
    assert.deepEqual(shiftBody, {note:'PR #5 を最優先で', lang:'ja'}, 'the box text goes along as the top priority');
    assert.equal(await input.inputValue(), '', 'the note left the box');
    const panel = await page.locator('#night-panel').innerText();
    assert.ok(panel.includes('夜の作業A') && panel.includes('最優先の指示: PR #5') && panel.includes('秒後に送ります'), panel);
    await page.locator('#night-panel .night-item.planned button').first().click();
    await page.waitForFunction(() => document.querySelector('#night-panel .night-item.cancelled'));
    assert.deepEqual(cancelled, ['t1'], 'one thread taken out');
    await page.locator('#night-panel .night-foot .btn').click();
    await page.waitForFunction(() => !document.querySelector('#night-panel .night-item.planned'));
    assert.deepEqual(cancelled, ['t1', null], 'then all of them');
    assert.equal(posts, 0, 'nothing else is sent');

    // The reply box's 🌙 (the same insert): the template, first blank selected, text kept.
    const filled = await page.evaluate(async () => {
      const box = document.createElement('textarea');
      document.body.append(box);
      await window.ConsoleNight.insert(box);
      const first = {value: box.value, selected: box.value.slice(box.selectionStart, box.selectionEnd)};
      box.value = '先に書いたこと';
      await window.ConsoleNight.insert(box);
      return {...first, kept: box.value.startsWith('先に書いたこと\n\n目的')};
    });
    assert.deepEqual(filled, {value: TEMPLATE, selected: '［成果］', kept: true});

    // Quiet hours: off by default, then a window covering now with an urgent keyword.
    const quiet = await page.evaluate(() => window.ConsoleNight.quietNow());
    assert.equal(quiet, false, 'quiet hours are off unless set');
    settings = {quiet_enabled:true, quiet_start:'00:00', quiet_end:'23:59', urgent_statuses:['error'], urgent_keywords:['障害']};
    const result = await page.evaluate(async () => {
      await window.ConsoleNight.load(true);
      const at = new Date(); at.setHours(12, 0);
      return {
        quiet: window.ConsoleNight.quietNow(at),
        plain: window.ConsoleNight.urgent({status:'waiting', title:'ブログ'}),
        failed: window.ConsoleNight.urgent({status:'error', title:'x'}),
        keyword: window.ConsoleNight.urgent({status:'waiting', title:'本番障害'}),
      };
    });
    assert.deepEqual(result, {quiet:true, plain:false, failed:true, keyword:true});
    assert.deepEqual(errors, [], 'no page errors');
    console.log('console_night: ok');
  } finally { await browser.close(); }
})().catch((e) => { console.error(e); process.exit(1); });
