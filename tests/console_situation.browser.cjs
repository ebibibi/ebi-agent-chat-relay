// The situation panel can be read and answered on its own, and folded away.
const { chromium } = require('playwright');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const root = require('node:path').resolve(__dirname, '../claude_discord/console/static') + '/';
const fixture = (id, title, parent_id, status = 'idle') => ({id, title, parent_id, status,
  thread_id: id, bucket: status === 'done' ? 'done' : status === 'running' ? 'ai' : 'idle',
  running: status === 'running', priority: 2, last_activity_at: '2026-10-13T11:00:00Z'});
const items = [fixture('p','サイト公開をまとめる'), fixture('c','画面を改善','p','running'),
  fixture('w','レビュー結果を確認','p','waiting'), fixture('g','古い作業','p','done')];
const reportOf = {p:'親の報告です。', c:'画面の実装が完了。スマートフォン表示を確認しています。', w:'レビューの指摘3件の扱いを決めてください。'};
(async () => {
  const browser = await chromium.launch({headless:true, executablePath:process.env.CONSOLE_TEST_CHROMIUM, args:['--no-sandbox']});
  const page = await browser.newPage({viewport:{width:1440,height:900},locale:'ja-JP'});
  const errors = [], sent = [];
  page.on('pageerror',e => errors.push(e.message));
  await page.route('http://console.test/**', async route => {
    const url = new URL(route.request().url());
    const pathname = url.pathname;
    if (pathname === '/') return route.fulfill({contentType:'text/html',body:fs.readFileSync(root+'index.html','utf8')});
    if (pathname.startsWith('/static/')) {
      const file = pathname.split('/').pop();
      const type = file.endsWith('.css') ? 'text/css' : file.endsWith('.js') ? 'text/javascript' : 'image/svg+xml';
      return route.fulfill({contentType:type,body:fs.readFileSync(root+file)});
    }
    const id = decodeURIComponent((pathname.match(/\/items\/([^/]+)\//) || [])[1] || '');
    let data = {};
    if (pathname.endsWith('/reply')) { sent.push({id, body: route.request().postDataJSON()}); data = {delivery:'queued'}; }
    else if (pathname.endsWith('/situation')) data = {situation:{asks:['本番デモを確認してください'],doing:['ビルド待ち'],decided:['東日本へ移動完了'],
      updated_at:'2026-10-13T11:00:00Z'},waits:[]};
    else if (pathname.endsWith('/board')) data = {items,slots:{},places:[]};
    else if (pathname.endsWith('/messages')) data = {messages:[{id:'m-'+id,kind:'agent',is_bot:true,content:reportOf[id]||'',created_at:'2026-10-13T11:00:00Z'}]};
    return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
  });
  await page.goto('http://console.test/');
  await page.evaluate(() => { localStorage.setItem('lang','ja'); localStorage.removeItem('situationHidden'); });
  await page.reload();
  await page.waitForFunction(() => typeof state !== 'undefined' && state.items.length > 0);
  await page.evaluate(() => { setView('tree'); return openItem('p'); });

  // Reading: what each thread last said, without opening it.
  const side = page.locator('#d-side');
  await side.getByText('画面の実装が完了', {exact:false}).waitFor();
  await side.getByText('レビューの指摘3件', {exact:false}).waitFor();
  // Beside the panel the family list is one folded line that unfolds on request.
  assert.equal(await page.locator('#d-family .family-heading').isVisible(), true);
  assert.equal(await page.locator('#d-family .family-tasks').isVisible(), false);

  // Answering the parent's question from the panel, without leaving it.
  await side.getByRole('button',{name:'↩ 回答する'}).click();
  assert.equal(await page.locator('#sit-to').inputValue(), 'p');
  assert.ok((await page.locator('#sit-reply').inputValue()).startsWith('> 本番デモを確認してください'));
  await page.locator('#sit-reply').press('End');
  await page.locator('#sit-reply').type('確認しました');
  await page.locator('#sit-reply').press('Control+Enter');
  await page.waitForFunction(() => document.getElementById('sit-reply').value === '');
  assert.equal(sent.at(-1).id, 'p');
  assert.ok(sent.at(-1).body.text.endsWith('確認しました'));

  // Replying to a child directly from the panel.
  await side.locator('.sit-ask', {hasText:'レビュー結果を確認'}).getByRole('button',{name:'↩ 返信'}).click();
  assert.equal(await page.locator('#sit-to').inputValue(), 'w');
  await page.locator('#sit-reply').fill('指摘1だけ直して');
  await side.getByRole('button',{name:'送信',exact:true}).click();
  await page.waitForFunction(() => document.getElementById('sit-reply').value === '');
  assert.equal(sent.at(-1).id, 'w');
  assert.equal(sent.at(-1).body.text, '指摘1だけ直して');
  if (process.env.CONSOLE_TEST_SCREENSHOTS) await page.screenshot({path: process.env.CONSOLE_TEST_SCREENSHOTS + '/situation.png'});

  // Folding: a rail with the count, the family list comes back, and the choice is remembered.
  await side.getByRole('button',{name:'このパネルを隠す'}).click();
  await page.locator('#d-side-rail').waitFor();
  assert.equal(await page.locator('#d-side-core').isVisible(), false);
  assert.equal(await page.locator('#d-family').isVisible(), true);
  assert.ok((await page.locator('#d-side-rail').textContent()).includes('2'));
  const width = await side.evaluate((n) => n.getBoundingClientRect().width);
  assert.ok(width <= 44, `rail width ${width}`);
  if (process.env.CONSOLE_TEST_SCREENSHOTS) await page.screenshot({path: process.env.CONSOLE_TEST_SCREENSHOTS + '/folded.png'});
  await page.reload();
  await page.waitForFunction(() => typeof state !== 'undefined' && state.items.length > 0);
  await page.evaluate(() => { setView('tree'); return openItem('p'); });
  await page.locator('#d-side-rail').waitFor();
  assert.equal(await page.locator('#d-side-core').isVisible(), false);
  await page.locator('#d-side-rail').click();
  await page.locator('#d-side-core').getByText('📍 現在地').waitFor();
  assert.equal(await page.evaluate(() => localStorage.getItem('situationHidden')), '0');

  // Narrow: the panel is a tab with its own reply box; folded, one button brings it back.
  await page.setViewportSize({width:390,height:844});
  await page.waitForFunction(() => document.getElementById('d-body').dataset.layout === 'tabs');
  await page.locator('.sit-tab', {hasText:'📍 現在地'}).click();
  assert.equal(await page.locator('#sit-reply').isVisible(), true);
  assert.equal(await page.locator('form.reply').isVisible(), false, 'one reply box at a time');
  await page.getByRole('button',{name:'このパネルを隠す'}).click();
  assert.equal(await page.locator('.sit-reopen').isVisible(), true);
  assert.equal(await page.locator('#msgs').isVisible(), true);
  await page.locator('.sit-reopen').click();
  assert.equal(await page.locator('#d-side-core').isVisible(), true);
  if (process.env.CONSOLE_TEST_SCREENSHOTS) await page.screenshot({path: process.env.CONSOLE_TEST_SCREENSHOTS + '/narrow.png'});
  const scroll = await page.evaluate(() => document.documentElement.scrollWidth);
  assert.ok(scroll <= 390, `overflow width ${scroll}`);

  assert.deepEqual(errors, []);
  await browser.close();
  console.log('Situation panel: read, answer, reply to a child, fold, remember, narrow: passed');
})().catch(e => {console.error(e);process.exit(1)});
