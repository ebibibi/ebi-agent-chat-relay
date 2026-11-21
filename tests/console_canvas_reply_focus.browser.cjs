// While the person types to the secretary (#1430): whatever happens — board refreshes, the
// secretary starting and finishing a turn, new messages, canvas repaints, screen-AI heartbeats,
// an IME cancelling a conversion with Escape — #reply keeps its focus, its draft and the caret.
// The box is at least six lines tall. Run: NODE_PATH=<playwright> node tests/console_canvas_reply_focus.browser.cjs
const { chromium } = require('playwright');
const fs = require('node:fs');
const assert = require('node:assert/strict');
const root = require('node:path').resolve(__dirname, '../claude_discord/console/static') + '/';
let running = false;
let messages = [{id: 1, kind: 'agent', author: 'AI', content: 'おはようございます。', created_at: '2026-11-17T00:00:00Z'}];
const items = () => [{id: 't900', thread_id: '900', title: '🗂 Secretary (canvas)', status: running ? 'running' : 'idle',
  bucket: running ? 'ai' : 'idle', running, canvas_secretary: true, priority: 2, focus: 100,
  last_activity_at: new Date().toISOString()}];
let widgets = [{key: 'now', kind: 'markdown', title: 'いまやること', size: 'l', note: '', thread_id: null, actions: [],
  updated_at: new Date().toISOString(), data: {text: 'x'}}];
(async () => {
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  for (const [width, height] of [[1440, 900], [1280, 800]]) {
    const page = await browser.newPage({viewport: {width, height}, locale: 'ja-JP'});
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await page.route('http://console.test/**', async (route) => {
      const req = route.request();
      const pathname = new URL(req.url()).pathname;
      if (pathname === '/') return route.fulfill({contentType: 'text/html', body: fs.readFileSync(root + 'index.html', 'utf8')});
      if (pathname.startsWith('/static/')) {
        const file = pathname.split('/').pop();
        const type = file.endsWith('.css') ? 'text/css' : file.endsWith('.js') ? 'text/javascript' : 'image/svg+xml';
        return route.fulfill({contentType: type, body: fs.readFileSync(root + file)});
      }
      let data = {};
      if (pathname.endsWith('/canvas')) data = {widgets, session: null, secretary: {item_id: 't900', running, paused: false, pending: 0,
        usage: {turns: 1, wakes: 0, input_tokens: 0, max_wakes: 8}, min_interval: 600, available: true}};
      else if (pathname.endsWith('/board')) data = {items: items(), slots: {}, places: [], secretary: {decide: [], review: [], unexplained: [], working: [], done_today: [], calm: true}};
      else if (pathname.endsWith('/messages')) data = {messages};
      else if (pathname.endsWith('/live')) data = {running, text: running ? '考えています…' + 'x'.repeat(messages.length) : '', tools: []};
      else if (pathname.endsWith('/canvas/ai-catalog')) data = {backends: ['claude'], models: [], efforts: [], connections: [], default: {backend: 'claude', model: 'sonnet', effort: 'medium'}};
      else if (pathname.endsWith('/secretary/events')) return route.fulfill({status: 204, body: ''});
      return route.fulfill({contentType: 'application/json', body: JSON.stringify(data)});
    });
    await page.goto('http://console.test/');
    await page.evaluate(() => { localStorage.setItem('lang', 'ja'); localStorage.setItem('consoleMode', 'canvas'); });
    await page.reload();
    await page.waitForFunction(() => state.open === 't900' && document.getElementById('reply'));
    const box = page.locator('#reply');

    // Six lines or more, in a column with room to type.
    const size = await box.boundingBox();
    const line = await box.evaluate((n) => parseFloat(getComputedStyle(n).lineHeight) || 20);
    assert.ok(size.height >= line * 6, `reply box ${size.height}px < six lines (${line * 6}px)`);
    assert.ok(size.width >= 380, `reply box ${size.width}px wide`);

    await box.click();
    await page.keyboard.type('秘書への長い返事を書いています');
    // Put the caret in the middle, as when correcting a word.
    await box.evaluate((n) => n.setSelectionRange(4, 4));
    const keep = async (what) => {
      const s = await page.evaluate(() => ({active: document.activeElement && document.activeElement.id,
        value: document.getElementById('reply')?.value, caret: document.getElementById('reply')?.selectionStart}));
      assert.deepEqual(s, {active: 'reply', value: '秘書への長い返事を書いています', caret: 4}, `after ${what}`);
    };

    // An IME cancelling its conversion with Escape (keyCode 229 / isComposing) is not ours.
    await box.evaluate((n) => n.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', isComposing: true, keyCode: 229, bubbles: true})));
    await keep('IME Escape');

    for (let round = 0; round < 6; round += 1) {
      running = round % 2 === 0;
      if (!running) messages = [...messages, {id: messages.length + 1, kind: 'agent', author: 'AI', content: `返事 ${round}`, created_at: new Date().toISOString()}];
      widgets = [{...widgets[0], updated_at: new Date(Date.now() + round * 1000).toISOString(), data: {text: `更新 ${round}`}}];
      await page.evaluate(async () => {
        await refresh();
        window.ConsoleCanvas.paint();
        window.ConsoleScreenAI.heartbeat();
        render();
      });
      await page.waitForTimeout(400);
      await keep(`round ${round} (running=${running})`);
    }
    assert.deepEqual(errors, []);
    await page.close();
  }
  await browser.close();
  console.log('Canvas reply box: six lines, focus, draft and caret kept through refreshes, turns and IME Escape: passed');
})().catch((e) => { console.error(e); process.exit(1); });
