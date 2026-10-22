const { chromium } = require('playwright');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const root = require('node:path').resolve(__dirname, '../claude_discord/console/static') + '/';
const fixture = (id, title, parent_id, status = 'idle') => ({id, title, parent_id, status,
  thread_id: id, bucket: status === 'done' ? 'done' : status === 'running' ? 'ai' : 'idle',
  running: status === 'running', priority: 2, last_activity_at: '2026-10-13T11:00:00Z'});
const items = [fixture('p','サイト公開をまとめる'), fixture('c','画面を改善','p','running'),
  fixture('g','スマートフォンを確認','c','done'), fixture('w','レビュー結果を確認','p','waiting'),
  fixture('x','単独の会話')];
(async () => {
  const browser = await chromium.launch({headless:true, executablePath:process.env.CONSOLE_TEST_CHROMIUM, args:['--no-sandbox']});
  const page = await browser.newPage({viewport:{width:1440,height:1000},locale:'ja-JP'});
  const errors = [];
  page.on('pageerror',e => errors.push(e.message));
  await page.route('http://console.test/**', async route => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === '/') return route.fulfill({contentType:'text/html',body:fs.readFileSync(root+'index.html','utf8')});
    if (pathname.startsWith('/static/')) {
      const file = pathname.split('/').pop();
      const type = file.endsWith('.css') ? 'text/css' : file.endsWith('.js') ? 'text/javascript' : 'image/svg+xml';
      return route.fulfill({contentType:type,body:fs.readFileSync(root+file)});
    }
    let data = {};
    if (pathname.endsWith('/board')) data = {items,slots:{},places:[]};
    else if (pathname.endsWith('/messages')) data = {messages:[{id:'m1',kind:'agent',is_bot:true,content:'画面の実装が完了。スマートフォン表示を確認しています。',created_at:'2026-10-13T11:00:00Z'}]};
    return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.goto('http://console.test/');
  await page.evaluate(() => { localStorage.setItem('lang','ja'); });
  await page.reload();
  await page.waitForFunction(() => typeof state !== 'undefined' && state.items.length > 0);
  await page.evaluate(() => { setView('tree'); return openItem('p'); });
  await page.locator('.family-report').first().waitFor();
  assert.equal(await page.locator('.family-task').count(),3);
  assert.ok((await page.locator('.family-counts').textContent()).includes('完了 1'));
  assert.ok((await page.locator('.family-report').first().textContent()).includes('画面の実装'));
  await page.locator('#reply').fill('この文章は更新中も残る');
  await page.evaluate(() => refresh());
  assert.equal(await page.locator('#reply').inputValue(),'この文章は更新中も残る');
  if (process.env.CONSOLE_TEST_SCREENSHOTS) {
    await page.screenshot({path: process.env.CONSOLE_TEST_SCREENSHOTS + '/desktop.png'});
  }
  // A child row in the sidebar opens that child, not the family root.
  await page.locator('#list .row[data-item-id="c"]').click();
  await page.waitForFunction(() => state.open === 'c');
  assert.equal(await page.locator('#list .row[data-item-id="c"]').getAttribute('aria-selected'),'true');
  await page.locator('#list .row[data-item-id="p"]').click();
  await page.waitForFunction(() => state.open === 'p');
  await page.evaluate(() => { state.selected = 'w'; document.body.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true})); });
  await page.waitForFunction(() => state.open === 'w');
  await page.evaluate(() => openItem('c'));
  assert.equal(await page.locator('form.reply').isVisible(),false);
  await page.getByRole('button',{name:'この子に直接返信する',exact:true}).click();
  assert.equal(await page.locator('form.reply').isVisible(),true);
  await page.getByRole('button',{name:'親との会話に戻る',exact:true}).click();
  assert.equal(await page.locator('#reply').inputValue(),'この文章は更新中も残る');
  await page.setViewportSize({width:390,height:844});
  await page.waitForFunction(() => document.getElementById('nav').getBoundingClientRect().right <= 0);
  await page.evaluate(() => renderDetail());
  if (process.env.CONSOLE_TEST_SCREENSHOTS) {
    await page.screenshot({path: process.env.CONSOLE_TEST_SCREENSHOTS + '/mobile.png'});
  }
  assert.equal(await page.locator('#reply').isVisible(),true);
  const width = await page.evaluate(() => document.documentElement.scrollWidth);
  assert.ok(width <= 390,`overflow width ${width}`);
  await page.evaluate(() => openItem('x'));
  assert.equal(await page.locator('#d-family').isVisible(),false);
  assert.equal(await page.locator('form.reply').isVisible(),true);
  assert.deepEqual(errors,[]);
  await browser.close();
  console.log('Desktop/mobile family flow, drafts, direct child reply, standalone flow: passed');
})().catch(e => {console.error(e);process.exit(1)});
