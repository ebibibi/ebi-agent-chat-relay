// Note viewer: Obsidian syntax, wikilinks, embeds, backlinks, history, XSS safety.
// Run: NODE_PATH=<dir with playwright> node --test tests/console_notes.browser.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../claude_discord/console/static');

const NOTES = {
  'A/Home.md': {text: [
    '---', 'tags: [x]', 'status: active', '---',
    '# Home', 'Go to [[Other|the other note]] or [[Missing]].',
    '', '> [!warning] Careful', '> body line', '',
    '- [ ] open task', '- [x] done task', '',
    '![[pic.png]]', '', '![[Other]]', '',
    '<script>window.pwned=1</script><img src=x onerror="window.pwned=2">',
    '[evil](javascript:window.pwned=3) [site](https://example.com) [rel](../B/Other.md)',
  ].join('\n'), links: {Other: 'B/Other.md', Missing: null, 'pic.png': 'img/pic.png'}},
  'B/Other.md': {text: '# Other\nBack to [[Home]]', links: {Home: 'A/Home.md'}},
  '02_Contexts/個人事業主/会社設計書.md': {text: '# 会社設計書', links: {}},
};
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==', 'base64');

async function launch(t) {
  const browser = await chromium.launch({headless:true,args:['--no-sandbox']});
  t.after(() => browser.close());
  const page = await browser.newPage({viewport:{width:1280,height:900},locale:'ja-JP'});
  page.setDefaultTimeout(10000);
  await page.route('http://console.test/**', async route => {
    const url = new URL(route.request().url());
    const p = url.pathname;
    if (p === '/' || p.startsWith('/static/')) {
      const file = p === '/' ? 'index.html' : path.basename(p);
      return route.fulfill({body:fs.readFileSync(path.join(root,file)),contentType:
        file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});
    }
    if (p.endsWith('/workspace/file')) return route.fulfill({body:PNG,contentType:'image/png'});
    let data = {}, status = 200;
    if (p.endsWith('/board')) data = {items:[],slots:{},places:[]};
    else if (p.endsWith('/workspace/note')) {
      const note = NOTES[url.searchParams.get('path')];
      if (note) data = {path:url.searchParams.get('path'),revision:'r',modified:0,...note};
      else { status = 404; data = {error:'Unknown note'}; }
    } else if (p.endsWith('/workspace/backlinks')) data = {backlinks: url.searchParams.get('path') === 'B/Other.md' ? ['A/Home.md'] : []};
    else if (p.endsWith('/workspace/plan')) data = {available:false};
    else if (p.endsWith('/workspace')) data = {available:true,entries:[]};
    return route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
  });
  return page;
}

test('Note viewer', {timeout: 30000}, async t => {
  const page = await launch(t);
  await page.goto('http://console.test/#focus');
  await page.evaluate(() => window.ConsoleNotes.open('A/Home.md'));
  const body = page.locator('.note-content');
  await body.locator('> h1').waitFor();
  assert.equal(await body.locator('> h1').innerText(), 'Home');
  // Front matter, callout, tasks, image, embed.
  assert.match(await page.locator('.note-props').innerText(), /プロパティ/);
  assert.equal(await page.locator('.note-props td').first().textContent(), '[x]');
  assert.equal(await body.locator('blockquote.callout.callout-warning .callout-title').innerText(), 'Careful');
  assert.match(await body.locator('blockquote.callout').innerText(), /body line/);
  assert.equal(await body.locator('input[type=checkbox]').count(), 2);
  assert.equal(await body.locator('input[type=checkbox][checked]').count(), 1);
  assert.equal(await body.locator('img.note-img').getAttribute('src'), '/console/api/workspace/file?path=img%2Fpic.png');
  await body.locator('.note-embed h1').waitFor();
  assert.equal(await body.locator('.note-embed h1').innerText(), 'Other');
  // No script, no event handler, no javascript: link.
  assert.equal(await page.evaluate(() => window.pwned), undefined);
  assert.equal(await body.locator('script').count(), 0);
  assert.equal(await body.locator('a[href^="javascript"]').count(), 0);
  assert.equal(await body.locator('a[href="https://example.com"]').getAttribute('rel'), 'noopener noreferrer');
  // Unresolved link explains itself; resolved link navigates and shows backlinks.
  await body.locator('a.wikilink.unresolved').click();
  await page.locator('.toast', {hasText:'リンク先のノートがありません'}).waitFor();
  await body.locator('> p a.wikilink:not(.unresolved)').first().click();
  await page.waitForFunction(() => location.hash === '#note/B/Other.md');
  await page.locator('.note-backlinks li').waitFor();
  assert.equal(await page.locator('.note-backlinks li').innerText(), 'A/Home');
  // Browser back returns to the first note; close returns to the list.
  await page.goBack();
  await page.waitForFunction(() => document.querySelector('.note-crumb.last')?.textContent === 'Home');
  // A relative Markdown link resolves against the note's folder.
  await page.locator('.note-content > p a', {hasText:'rel'}).click();
  await page.waitForFunction(() => location.hash === '#note/B/Other.md');
  await page.getByRole('button', {name:'閉じる'}).click();
  await page.waitForFunction(() => location.hash === '#focus' && document.getElementById('note-viewer').hidden);
  // A hidden or missing note says so.
  await page.evaluate(() => window.ConsoleNotes.open('secret/Diary.md'));
  await page.getByText('ノートが見つかりません').waitFor();
  await page.setViewportSize({width:390,height:844});
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= 390));
});

test('note links in a conversation message open the viewer in place', {timeout: 30000}, async t => {
  const page = await launch(t);
  await page.goto('http://console.test/?token=secret#focus');
  const encoded = '/#note/02_Contexts/%E5%80%8B%E4%BA%BA%E4%BA%8B%E6%A5%AD%E4%B8%BB/%E4%BC%9A%E7%A4%BE%E8%A8%AD%E8%A8%88%E6%9B%B8.md';
  await page.evaluate((md) => {
    const box = document.createElement('div'); box.id = 'msg';
    box.append(MD.render(md)); document.body.append(box);
  }, `[開く](${encoded}) [生](#note/02_Contexts/個人事業主/会社設計書.md) [外](https://elsewhere.test/#note/A/Home.md)`);
  const links = page.locator('#msg a');
  assert.equal(await links.count(), 3);
  assert.equal(await links.nth(0).getAttribute('target'), null);
  assert.equal(await links.nth(1).getAttribute('target'), null);
  assert.equal(await links.nth(2).getAttribute('target'), '_blank');
  for (const n of [0, 1]) {
    await links.nth(n).click();
    await page.locator('.note-content > h1', {hasText:'会社設計書'}).waitFor();
    // Same tab, query (and its token) kept.
    assert.equal(page.context().pages().length, 1);
    assert.equal(new URL(page.url()).search, '?token=secret');
    await page.getByRole('button', {name:'閉じる'}).click();
    await page.waitForFunction(() => document.getElementById('note-viewer').hidden);
  }
});
