// #1476: the 🌙 button fills the capture and reply boxes with the night template without
// sending, and inside the person's quiet hours only their urgent conditions ring.
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
    page.on('pageerror', e => errors.push(e.message));
    await page.route('http://console.test/**', async route => {
      const path = new URL(route.request().url()).pathname;
      if (path === '/') return route.fulfill({contentType:'text/html',body:fs.readFileSync(root+'index.html')});
      if (path.startsWith('/static/')) return route.fulfill({contentType:path.endsWith('.css')?'text/css':'text/javascript',body:fs.readFileSync(root+path.split('/').pop())});
      if (route.request().method() === 'POST') posts++;
      if (path === '/console/api/night') return route.fulfill({contentType:'application/json',body:JSON.stringify({template:TEMPLATE, settings})});
      return route.fulfill({contentType:'application/json',body:JSON.stringify(path.endsWith('/board')?{items:[],slots:{},places:[]}:{})});
    });
    await page.goto('http://console.test/');
    await page.evaluate(() => {localStorage.setItem('lang','ja'); localStorage.setItem('autostart','0');});
    await page.reload();

    const moon = page.locator('#capture .btn.night');
    assert.equal(await moon.count(), 1, 'one 🌙 beside the capture box');
    await moon.click();
    const input = page.locator('#capture-input');
    await page.waitForFunction(() => document.querySelector('#capture-input').value.includes('目的'));
    assert.equal(await input.inputValue(), TEMPLATE, 'an empty box gets the template');
    const selected = await page.evaluate(() => { const b = document.querySelector('#capture-input'); return b.value.slice(b.selectionStart, b.selectionEnd); });
    assert.equal(selected, '［成果］', 'the first blank is selected for typing over');
    await input.fill('先に書いたこと');
    await moon.click();
    await page.waitForFunction(() => document.querySelector('#capture-input').value.includes('目的'));
    assert.ok((await input.inputValue()).startsWith('先に書いたこと\n\n目的'), 'existing text is kept');
    assert.equal(posts, 0, 'nothing is sent');

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
