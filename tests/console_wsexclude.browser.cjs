// The Workspace panel's editor for notes the Console must never show.
// Run: NODE_PATH=<dir with playwright> node --test tests/console_wsexclude.browser.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../claude_discord/console/static');

test('Exclusion list editor', {timeout: 30000}, async t => {
  const browser = await chromium.launch({headless:true,args:['--no-sandbox']});
  t.after(() => browser.close());
  const page = await browser.newPage({viewport:{width:1280,height:900},locale:'ja-JP'});
  page.setDefaultTimeout(5000);
  let text = '# default\n';
  const puts = [];
  await page.route('http://console.test/**', async route => {
    const req = route.request();
    const p = new URL(req.url()).pathname;
    if (p === '/' || p.startsWith('/static/')) {
      const file = p === '/' ? 'index.html' : path.basename(p);
      return route.fulfill({body:fs.readFileSync(path.join(root,file)),contentType:
        file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html'});
    }
    let data = {};
    if (p.endsWith('/board')) data = {items:[],slots:{},places:[]};
    else if (p.endsWith('/workspace/exclude')) {
      if (req.method() === 'PUT') { puts.push(req.postDataJSON()); text = req.postDataJSON().text; }
      data = {text, patterns: text.split('\n').filter(l => l.trim() && !l.startsWith('#')), file:'/cfg/console-exclude.txt'};
    } else if (p.endsWith('/workspace/plan')) data = {available:false};
    else if (p.endsWith('/workspace')) data = {available:true,entries:[]};
    return route.fulfill({contentType:'application/json',body:JSON.stringify(data)});
  });
  const loaded = page.waitForResponse(r => r.url().endsWith('/workspace'));
  await page.goto('http://console.test/');
  await loaded;
  await page.locator('.nav-workspace').click();
  await page.getByRole('button', {name:'🚫 表示しないノート'}).click();
  const area = page.locator('.ws-exclude-text');
  await area.waitFor();
  assert.equal(await area.inputValue(), '# default\n');
  assert.match(await page.locator('.ws-exclude-file').innerText(), /console-exclude\.txt/);
  await area.fill('# default\nsecret/\n*.private.md\n');
  await page.getByRole('button', {name:'保存'}).click();
  await page.locator('.toast', {hasText:'除外パターン 2 件'}).waitFor();
  assert.deepEqual(puts, [{text:'# default\nsecret/\n*.private.md\n'}]);
});
