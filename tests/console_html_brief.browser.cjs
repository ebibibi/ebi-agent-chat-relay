// The reply-side 📄 fills the box with the served HTML-brief text, the thread's project in
// place of {{project}}, the first blank selected, and sends nothing; it fits beside 🌙 and Send.
// NODE_PATH=<playwright node_modules> node --test tests/console_html_brief.browser.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../claude_discord/console/static');
const TEMPLATE = 'Brief for {{project}}\n\nScope: [everything / one topic]\nAttach it.\n';

for (const [width, project, shown] of [[1440, 'Personal/Relay', 'Personal/Relay'], [390, null, '［プロジェクト］']]) {
  test(`📄 inserts the brief at ${width}px without sending`, {timeout: 30000}, async (t) => {
    const browser = await chromium.launch({headless: true, executablePath: process.env.CONSOLE_TEST_CHROMIUM, args: ['--no-sandbox']});
    t.after(() => browser.close());
    const page = await browser.newPage({viewport: {width, height: 844}, locale: 'ja-JP'});
    let posts = 0, fetched = 0;
    const item = {id: 'c1', thread_id: '1', title: 'Thread', status: 'idle', running: false, bucket: 'idle', priority: 2,
      workspace_path: project};
    await page.route('http://console.test/**', async (route) => {
      const p = new URL(route.request().url()).pathname;
      if (p === '/' || p.startsWith('/static/')) {
        const file = p === '/' ? 'index.html' : path.basename(p);
        return route.fulfill({body: fs.readFileSync(path.join(root, file)),
          contentType: file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html'});
      }
      if (route.request().method() === 'POST') posts++;
      if (p.endsWith('/html-brief')) { fetched++; return route.fulfill({contentType: 'application/json', body: JSON.stringify({template: TEMPLATE})}); }
      const data = p.endsWith('/board') ? {items: [item], slots: {}, places: []} : p.endsWith('/messages') ? {messages: []} : {};
      return route.fulfill({contentType: 'application/json', body: JSON.stringify(data)});
    });
    await page.goto('http://console.test/#focus/c1');
    await page.waitForSelector('#reply');
    const box = page.locator('#reply');
    await box.fill('memo [keep]');
    const button = page.locator('.reply-side .html-brief');
    await button.click();
    const expected = `memo [keep]\n\n${TEMPLATE.replace('{{project}}', shown)}`;
    await page.waitForFunction((v) => document.querySelector('#reply').value === v, expected);
    // The first blank of the inserted text (a missing project is one), not one already there.
    assert.equal(await box.evaluate((el) => el.value.slice(el.selectionStart, el.selectionEnd)),
      project ? '[everything / one topic]' : shown);
    await box.fill('');
    await button.click();
    await page.waitForFunction(() => document.querySelector('#reply').value.startsWith('Brief for'));
    assert.equal(fetched, 1, 'the text is fetched once');
    assert.equal(posts, 0, 'nothing is sent');
    const boxes = await Promise.all(['.reply-side .night', '.reply-side .html-brief', '.reply button[type="submit"]']
      .map((s) => page.locator(s).boundingBox()));
    const apart = (a, b) => a.x + a.width <= b.x || b.x + b.width <= a.x || a.y + a.height <= b.y || b.y + b.height <= a.y;
    assert.ok(apart(boxes[0], boxes[1]) && apart(boxes[1], boxes[2]), JSON.stringify(boxes));
    for (const b of boxes) assert.ok(b.x >= 0 && b.x + b.width <= width, `inside the screen at ${width}px`);
  });
}
