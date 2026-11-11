// Regenerate the README screenshots of the Ebi AI Console from fictional sample data.
//
// The bundled web client is served as-is; every /console/api call is answered from the
// fixtures below, so no relay, database or real conversation is involved.
//
//   npm i playwright && node scripts/readme_screenshots.cjs [outdir] [--lang en|ja]
//   (CONSOLE_TEST_CHROMIUM=/path/to/chrome to use an already installed browser)
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const { parseScreenshotOptions } = require('./readme_screenshot_options.cjs');

const root = path.resolve(__dirname, '../claude_discord/console/static') + '/';
const { language, out } = parseScreenshotOptions(process.argv.slice(2), __dirname);
const NOW = Date.parse('2026-10-16T09:30:00Z');
const ago = (min) => new Date(NOW - min * 60e3).toISOString().replace(/\.\d+Z$/, 'Z');

const item = (id, title, o = {}) => {
  const status = o.status || 'idle';
  const bucket = o.bucket || ({ running: 'ai', done: 'done', idle: 'idle', candidate: 'candidate' }[status] || 'me');
  return {
    id, thread_id: id.slice(1), title, thread_name: title, status, bucket,
    priority: o.priority ?? 2, due_at: o.due_at || null, overdue: false, snoozed_until: null,
    project: o.project || null, workspace_path: null, place: o.place || null, pinned_at: null,
    paused_at: null, note: null, parent_id: o.parent || null, running: status === 'running',
    archived: false, created_at: ago((o.age || 30) + 120), last_activity_at: ago(o.age || 30),
    url: null, working_dir: `/srv/${o.project || 'storefront'}`, backend: o.backend || 'claude', model: o.model || null,
    context: o.ctx ? { used: o.ctx * 10000, window: 1000000, pct: o.ctx } : null, queued: false,
    ai: o.next ? { priority: o.priority ?? 2, due_at: o.due_at || null, reason: o.why || '', next_step: o.next,
      ready: true, ready_at: null, waiting_for: o.waiting_for || null, place: o.place || null, updated_at: ago(5) } : null,
    priority_by: o.next ? 'ai' : null, focus: o.focus ?? null, actionable: o.focus ? true : null,
  };
};

const OPUS = 'claude-opus-5-5', SOL = 'gpt-6.1-sol', SONNET = 'claude-sonnet-5-5';
const items = [
  item('t101', 'Ship storefront v2.0', { status: 'running', backend: 'claude', model: OPUS, project: 'storefront', ctx: 41, age: 1, priority: 1 }),
  item('t102', 'Implement the new checkout flow', { parent: 't101', status: 'running', backend: 'codex', model: SOL, project: 'storefront', ctx: 28, age: 2 }),
  item('t103', 'Write Playwright E2E tests for checkout', { parent: 't101', status: 'running', backend: 'codex', model: SOL, project: 'storefront', ctx: 17, age: 3 }),
  item('t104', 'Security review of PR #128 (payments)', { parent: 't101', status: 'done', backend: 'codex', model: SOL, project: 'storefront', age: 25 }),
  item('t105', 'Update API reference for v2 endpoints', { parent: 't101', status: 'review', backend: 'local', model: 'gpt-oss:120b', project: 'storefront', age: 12,
    next: 'Read the docs diff and approve the PR', why: 'The docs PR is ready; only your approval is missing', focus: 90 }),
  item('t106', 'Customer: invoice CSV export drops non-ASCII names', { status: 'action', priority: 0, due_at: '2026-10-16T15:00:00Z', backend: 'claude', model: SONNET, ctx: 22, age: 40,
    next: 'Send the drafted reply and attach the fixed CSV', why: 'The customer is waiting and asked for an answer today', focus: 120, project: 'support' }),
  item('t107', 'Drop Python 3.11 support in the SDK?', { status: 'waiting', priority: 1, backend: 'claude', model: OPUS, ctx: 9, age: 18,
    next: 'Choose: drop 3.11 in v3 or keep it one more release', why: 'The agent laid out both options and needs a decision', focus: 110, project: 'sdk' }),
  item('t108', 'Approve the production deploy of v1.9.4', { status: 'action', priority: 1, place: 'Office', backend: 'codex', model: SOL, age: 55,
    next: 'Approve the deployment gate in the CI portal', why: 'Staging is green; the gate needs a human approval', focus: 80, project: 'storefront' }),
  item('t109', 'Investigate the flaky CI job on main', { status: 'running', backend: 'claude', model: SONNET, ctx: 63, age: 1, project: 'platform' }),
  item('t110', 'Draft the v2.0 launch blog post', { status: 'review', backend: 'pi', model: 'anthropic/claude-sonnet-5-5', age: 70, project: 'marketing',
    next: 'Review the draft and suggest a title', why: 'Draft attached; publishing waits for your approval' }),
  item('t111', 'Nightly dependency audit', { status: 'done', backend: 'codex', model: 'gpt-6.1-mini', age: 400, project: 'platform' }),
  item('t112', 'Benchmark the search index rebuild', { status: 'error', backend: 'agui', age: 95, project: 'platform',
    next: 'Retry after the staging database is back', why: 'The run failed because staging was unreachable' }),
  item('t113', 'Refactor the shipping-rate calculator', { status: 'idle', backend: 'codex', model: SOL, age: 300, project: 'storefront' }),
  item('t114', 'Weekly metrics summary', { status: 'candidate', backend: 'claude', model: 'claude-haiku-5-5', age: 600, project: 'ops' }),
];

const guide = {
  next: [
    { id: 't106', title: items[5].title, reasons: ['due'], unblocks: true, next_step: items[5].ai.next_step, why: items[5].ai.reason, place: null },
    { id: 't107', title: items[6].title, reasons: ['asked'], unblocks: true, next_step: items[6].ai.next_step, why: items[6].ai.reason, place: null },
    { id: 't105', title: items[4].title, reasons: ['asked'], unblocks: true, next_step: items[4].ai.next_step, why: items[4].ai.reason, place: null },
  ],
  batches: [], calm: false, counts: { me: 6, running: 3, waiting: 1, candidate: 1 }, drift: [],
};

const msg = (id, kind, content, min, extra = {}) => ({ id, author: kind === 'human' ? 'you' : 'Ebi Agent Chat Relay',
  is_bot: kind !== 'human', kind, content, truncated: false, embeds: [], attachments: [], actions: [],
  created_at: ago(min), jump_url: null, ...extra });
const messages = {
  t101: [
    msg(1, 'human', 'Ship storefront v2.0 this week. Split the work across child agents and keep me posted. Only ask me for decisions.', 50),
    msg(2, 'agent', [
      "Plan for **storefront v2.0** — I'm coordinating; the implementation runs in child agents.",
      '',
      '| Task | Agent | Status |',
      '|---|---|---|',
      '| Checkout flow | Codex · gpt-6.1-sol | 🏃 running |',
      '| E2E tests | Codex · gpt-6.1-sol | 🏃 running |',
      '| Security review of PR #128 | Codex · gpt-6.1-sol | ✅ done |',
      '| API reference | Local · gpt-oss:120b | 👀 waiting for your review |',
      '',
      'Each child works in its own git worktree, so nothing collides.',
    ].join('\n'), 45),
    msg(3, 'activity', '-# ↳ spawned 4 child threads', 44),
    msg(4, 'agent', '**Update:** the security review found no blocking issues (2 minor notes, already fixed). Checkout is 70% done; E2E tests are being written against the new flow.\n\n**One thing for you:** the API reference PR is ready — please approve it when you have a minute.', 3),
  ],
};

function localizeSamples(lang) {
  if (lang !== 'ja') return;

  const translations = {
    t101: { title: 'ストア v2.0 を今週リリース', next: '各担当の進捗を確認する', why: '判断が必要な点だけ確認します' },
    t102: { title: '新しい購入フローを実装', next: '購入手順の E2E テストを作成する', why: '購入フローの確認を進めています', report: '住所入力まで完了しました。購入処理を実装しています。' },
    t103: { title: '購入フローの Playwright E2E テスト', next: 'チェックアウトの E2E テストを追加する', why: 'リリース前に購入手順を確認します', report: '24件中18件のシナリオを作成しました。残りは購入処理の実装後に追加します。' },
    t104: { title: 'PR #128（決済）のセキュリティレビュー', next: 'レビュー結果を確認する', why: 'セキュリティレビューは完了しました', report: '重大な問題はありません。レート制限とエラーメッセージの改善を同じ PR で対応しました。' },
    t105: { title: 'v2 API リファレンスを更新', next: 'ドキュメントの差分を確認し、PR を承認する', why: 'ドキュメント PR の確認だけが残っています', report: '14個のエンドポイントを文書化し、例を再生成しました。PR の確認をお願いします。' },
    t106: { title: '顧客対応：請求書 CSV の氏名が文字化けする', next: '返信文を送り、修正版 CSV を添付する', why: '顧客は本日中の回答を待っています' },
    t107: { title: 'SDK の Python 3.11 サポートを終了する？', next: 'v3 で終了するか、もう1リリース維持するか決める', why: '次のリリース方針を確認する必要があります' },
    t108: { title: 'v1.9.4 の本番デプロイを承認する', next: 'CI ポータルでデプロイを承認する', why: 'ステージングは成功し、人による承認を待っています' },
    t109: { title: 'main の不安定な CI ジョブを調査する', next: '失敗したジョブのログを比較する', why: '原因を特定して再発を防ぎます' },
    t110: { title: 'v2.0 のリリース記事を下書きする', next: '下書きを読み、タイトル案を出す', why: '公開前の確認を待っています' },
    t111: { title: '依存関係を毎晩監査する', next: '監査結果を確認する', why: '新しい脆弱性や更新を確認します' },
    t112: { title: '検索インデックス再構築の不安定さを調べる', next: 'ステージング DB 復旧後に再試行する', why: 'ステージング DB に接続できず失敗しました' },
    t113: { title: '配送料計算を整理する', next: '計算ロジックを確認する', why: '既存の配送条件を保って整理します' },
    t114: { title: '週次メトリクスをまとめる', next: '週次レポートを作成する', why: '今週の傾向を共有します' },
  };
  for (const it of items) {
    const values = translations[it.id];
    if (!values) continue;
    it.title = values.title;
    if (it.ai) {
      it.ai.next_step = values.next;
      it.ai.reason = values.why;
    }
  }
  for (const row of guide.next) {
    const values = translations[row.id];
    if (!values) continue;
    row.title = values.title;
    row.next_step = values.next;
    row.why = values.why;
  }
  for (const [id, values] of Object.entries(translations)) {
    if (values.report) {
      reports[id] = values.report;
      if (messages[id]?.[1]) messages[id][1].content = values.report;
    } else if (messages[id]?.[1]) {
      messages[id][1].content = values.next
        ? `**次のステップ:** ${values.next}\n\n${values.why}。`
        : '作業中です。';
    }
    if (messages[id]?.[0]) messages[id][0].content = values.title;
  }
  messages.t101 = [
    msg(101, 'human', '今週中にストア v2.0 を公開してください。子エージェントに作業を分け、進捗を共有してください。判断が必要なときだけ確認してください。', 50),
    msg(102, 'agent', 'ストア v2.0 の作業計画を作成しました。各担当の進捗を追跡しています。', 4),
  ];
  messages.t106 = [
    msg(106, 'human', '顧客名「Zoë Müller」が請求書 CSV で文字化けすると報告がありました。原因を調べて修正し、返信文を下書きしてください。', 42),
    msg(107, 'agent', '原因：CSV を Latin-1 で書き出していたため、Excel で氏名が文字化けしていました。', 8),
    msg(108, 'agent', '修正案：UTF-8 BOM 付きで保存します。修正版 CSV と返信文を確認してください。', 2),
  ];
}
messages.t106 = [
  msg('t106a', 'human', 'A customer says the invoice CSV export mangles names like "Zoë Müller". Find the cause, fix it, and draft a reply.', 42),
  msg('t106b', 'activity', '-# 🔧 Bash · grep -rn "to_csv" billing/', 38),
  msg('t106c', 'agent', [
    '**Cause:** `billing/export.py` wrote the CSV as Latin-1, so Excel showed `ZoÃ« MÃ¼ller`.',
    '',
    '**Fix:** UTF-8 with a BOM (Excel opens it correctly) — PR #132, tests green.',
    '',
    '**Draft reply** (not sent — your call):',
    '',
    '> Hi Dana, thanks for the report. The export used the wrong character encoding; it is fixed and the corrected file is attached.',
    '',
    '📎 `invoices-2026-09.csv` is ready to attach.',
  ].join('\n'), 2),
];
const reports = {
  t102: 'Cart and address steps are done and green in CI. Wiring up the payment step now.',
  t103: '18 of 24 scenarios written. Waiting for the payment step to land before the last 6.',
  t104: 'No blocking issues. Fixed 2 minor findings (missing rate limit, verbose error message) in the same PR.',
  t105: 'Docs PR #131 is ready for review: 14 endpoints documented, examples regenerated.',
};
for (const [id, text] of Object.entries(reports)) messages[id] = [
  msg(id + 'a', 'human', items.find((i) => i.id === id).title, 30), msg(id + 'b', 'agent', text, 2)];
for (const it of items) if (!messages[it.id]) messages[it.id] = [
  msg(it.id + 'a', 'human', it.title, it.age || 30),
  msg(it.id + 'b', 'agent', it.ai ? `**Next step:** ${it.ai.next_step}\n\n${it.ai.reason}.` : 'Working on it.', 1),
];

const catalog = (backend, model, effort) => ({ backend, model, effort, commands: [], builtins: [],
  backends: ['claude', 'codex', 'local', 'agui', 'pi'],
  models: backend === 'claude'
    ? [{ value: OPUS, label: 'Claude Opus 5.5' }, { value: SONNET, label: 'Claude Sonnet 5.5' }, { value: 'claude-haiku-5-5', label: 'Claude Haiku 5.5' }]
    : [{ value: SOL, label: 'gpt-6.1-sol' }, { value: 'gpt-6.1-mini', label: 'gpt-6.1-mini' }],
  efforts: backend === 'claude' ? ['low', 'medium', 'high', 'max'] : ['low', 'medium', 'high', 'xhigh'], skills: [] });
const usage = { now: NOW / 1000, backends: [
  { backend: 'claude', profile: null, available: true, unavailable_until: null, windows: [
    { type: 'five_hour', utilization: 0.31, resets_at: NOW / 1000 + 7200, status: 'allowed', reset: false },
    { type: 'seven_day', utilization: 0.42, resets_at: NOW / 1000 + 300000, status: 'allowed', reset: false }] },
  { backend: 'codex', profile: null, available: true, unavailable_until: null, windows: [
    { type: 'seven_day', utilization: 0.18, resets_at: NOW / 1000 + 400000, status: 'allowed', reset: false }] }] };

function answer(pathname) {
  const api = pathname.replace(/^\/console\/api/, '');
  if (api === '/me') return { identity: 'demo' };
  if (api === '/board') return { generated_at: ago(0), items, guide, slots: { max: 8, running: 3, waiting: 0 },
    places: [{ name: 'Home', count: 0, registered: true }, { name: 'Office', count: 1, registered: true }] };
  if (api === '/usage') return usage;
  if (api === '/defaults') return catalog('claude', OPUS, 'high');
  if (api.endsWith('/children')) return catalog('codex', SOL, 'medium');
  let m = api.match(/^\/items\/([^/]+)\/messages/);
  if (m) return { messages: messages[decodeURIComponent(m[1])] || [] };
  m = api.match(/^\/items\/([^/]+)\/commands$/);
  if (m) { const it = items.find((i) => i.id === decodeURIComponent(m[1])); return catalog(it?.backend === 'claude' ? 'claude' : 'codex', it?.model, 'high'); }
  m = api.match(/^\/items\/([^/]+)\/live$/);
  if (m) return { running: false, events: [] };
  m = api.match(/^\/items\/([^/]+)$/);
  if (m) return items.find((i) => i.id === decodeURIComponent(m[1])) || {};
  if (api === '/places') return { places: [] };
  if (api === '/workspace') return { entries: [] };
  return {};
}

async function page(browser, viewport, lang = 'en', opts = {}) {
  const p = await browser.newPage({ viewport, locale: lang === 'ja' ? 'ja-JP' : 'en-US', timezoneId: 'UTC', ...opts });
  p.on('pageerror', (e) => console.error('pageerror:', e.message));
  await p.addInitScript(({ now, selectedLanguage }) => {
    const RealDate = Date;
    // Freeze the clock so relative times in the UI match the fixtures.
    globalThis.Date = class extends RealDate { constructor(...a) { super(...(a.length ? a : [now])); } static now() { return now; } };
    localStorage.setItem('lang', selectedLanguage);
    localStorage.setItem('workspaceOfferDismissed', '1');
  }, { now: NOW, selectedLanguage: lang });
  await p.route('http://console.test/**', async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === '/') return route.fulfill({ contentType: 'text/html', body: fs.readFileSync(root + 'index.html', 'utf8') });
    if (pathname.startsWith('/static/')) {
      const file = pathname.split('/').pop();
      const type = file.endsWith('.css') ? 'text/css' : file.endsWith('.js') ? 'text/javascript'
        : file.endsWith('.svg') ? 'image/svg+xml' : 'application/json';
      return route.fulfill({ contentType: type, body: fs.readFileSync(root + file) });
    }
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify(answer(pathname)) });
  });
  await p.goto('http://console.test/');
  await p.waitForFunction(() => typeof state !== 'undefined' && state.items.length > 0);
  return p;
}

(async () => {
  localizeSamples(language);
  fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CONSOLE_TEST_CHROMIUM, args: ['--no-sandbox'] });
  const shots = (process.env.SHOTS || 'focus,family,mobile').split(',');

  if (shots.includes('focus')) {
    const p = await page(browser, { width: 1440, height: 900 }, language);
    await p.evaluate(() => { setView('focus'); return openItem('t106'); });
    await p.waitForTimeout(800);
    await p.screenshot({ path: path.join(out, 'console-focus.png') });
    await p.close();
  }
  if (shots.includes('family')) {
    const p = await page(browser, { width: 1440, height: 900 }, language);
    await p.evaluate(() => { setView('tree'); return openItem('t101'); });
    await p.locator('.family-report').first().waitFor({ state: 'attached' });
    await p.waitForTimeout(800);
    await p.screenshot({ path: path.join(out, 'console-family.png') });
    await p.close();
  }
  if (shots.includes('mobile')) {
    const p = await page(browser, { width: 390, height: 844 }, language, { isMobile: true, hasTouch: true, deviceScaleFactor: 2 });
    await p.evaluate(() => setView('focus'));
    await p.waitForTimeout(800);
    await p.screenshot({ path: path.join(out, 'console-mobile.png') });
    await p.close();
  }
  await browser.close();
  console.log('screenshots written to', out);
})().catch((e) => { console.error(e); process.exit(1); });
