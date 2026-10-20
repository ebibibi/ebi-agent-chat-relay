// Exercise the shipped form with delayed uploads/responses and a mobile viewport.
const { chromium } = require('playwright');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const root = require('node:path').resolve(__dirname, '../claude_discord/console/static') + '/';
(async () => {
  const browser = await chromium.launch({headless:true, executablePath:process.env.CONSOLE_TEST_CHROMIUM, args:['--no-sandbox']});
  try {
    for (const width of [390, 1440]) {
      const page = await browser.newPage({viewport:{width,height:844}});
      const errors = [];
      let requests = 0, pending, fail = false, board = [];
      const releaseRequest = async () => {
        for (let i=0; !pending && i<500; i++) await new Promise(r => setTimeout(r,10));
        assert.ok(pending, 'capture request reached the server');
        const resolve = pending; pending = null; resolve();
      };
      page.on('pageerror', e => errors.push(e.message));
      await page.route('http://console.test/**', async route => {
        const path = new URL(route.request().url()).pathname;
        if (path === '/') return route.fulfill({contentType:'text/html',body:fs.readFileSync(root+'index.html')});
        if (path.startsWith('/static/')) return route.fulfill({contentType:path.endsWith('.css')?'text/css':'text/javascript',body:fs.readFileSync(root+path.split('/').pop())});
        if (path === '/console/api/items' && route.request().method() === 'POST') {
          requests++;
          await new Promise(resolve => {pending = resolve;});
          if (fail) return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:'Try again'})});
          const item = {id:`c${requests}`, title:route.request().postDataJSON().title, bucket:'todo', priority:2};
          board.push(item);
          return route.fulfill({status:201,contentType:'application/json',body:JSON.stringify({item})});
        }
        return route.fulfill({contentType:'application/json',body:JSON.stringify(path.endsWith('/board')?{items:board,slots:{},places:[]}:{})});
      });
      await page.goto('http://console.test/');
      await page.evaluate(() => {localStorage.setItem('lang','ja'); localStorage.setItem('autostart','0');});
      await page.reload();
      const input = page.locator('#capture-input');
      const box = await input.boundingBox(), form = await page.locator('#capture').boundingBox();
      const send = await page.locator('#capture-send').boundingBox(), start = await page.locator('#capture-start').boundingBox();
      assert.ok(box.width > form.width*.85, 'the box spans the capture form');
      assert.ok(send.y >= box.y+box.height && start.y >= box.y+box.height, 'options and buttons sit under the box');
      assert.ok(start.x < send.x, 'options left, send right');
      await input.fill('モバイルのタスク');
      await input.press('Enter');
      assert.equal(requests,0, 'plain Enter is a newline');
      assert.ok((await input.inputValue()).includes('\n'));
      await input.fill('モバイルのタスク');
      await input.dispatchEvent('keydown',{key:'Enter',ctrlKey:true,isComposing:true});
      assert.equal(requests,0, 'IME confirmation does not submit');
      await input.press('Control+Enter');
      await page.waitForFunction(() => document.querySelector('#capture-status')?.textContent.includes('登録中'));
      assert.equal(await input.getAttribute('readonly'), '');
      assert.equal(await page.locator('#capture').getAttribute('aria-busy'),'true');
      const status = page.locator('#capture-status');
      assert.equal(await status.isVisible(),true);
      const bounds = await status.boundingBox();
      assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= width, 'progress stays inside the viewport');
      for (let i=0; i<5; i++) await input.press('Control+Enter');
      assert.equal(await page.locator('#capture-send').isDisabled(), true, 'Send is locked while pending');
      await page.locator('#capture').evaluate(form => {form.dispatchEvent(new Event('submit',{cancelable:true}));});
      assert.equal(requests,1,`${width}px: Ctrl+Enter/form submit must not duplicate a pending capture`);
      await releaseRequest();
      await page.waitForFunction(() => !document.querySelector('#capture-input').readOnly);
      assert.equal(await input.inputValue(),'');
      assert.equal(board.length,1);
      // Failure retains the draft; the next submit is allowed exactly once.
      fail = true;
      await input.fill('通信失敗でも残る入力');
      await input.press('Control+Enter');
      await page.waitForFunction(() => document.querySelector('#capture').getAttribute('aria-busy') === 'true');
      await releaseRequest();
      await page.waitForFunction(() => !document.querySelector('#capture-input').readOnly);
      assert.equal(await input.inputValue(),'通信失敗でも残る入力');
      assert.equal(await page.locator('#capture-status').textContent(),'');
      fail = false;
      await input.press('Control+Enter');
      await page.waitForFunction(() => document.querySelector('#capture').getAttribute('aria-busy') === 'true');
      await releaseRequest();
      await page.waitForFunction(() => !document.querySelector('#capture-input').readOnly);
      assert.equal(requests,3);
      assert.equal(board.length,2);
      assert.deepEqual(errors,[]);
      await page.close();
    }
  } finally { await browser.close(); }
  console.log('Mobile/desktop capture: immediate feedback, single submission, draft retention and retry passed');
})().catch(e => {console.error(e);process.exit(1);});
