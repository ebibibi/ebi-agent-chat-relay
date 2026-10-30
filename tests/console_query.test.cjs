const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const ctx = {window: {}};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync('claude_discord/console/static/query.js', 'utf8'), ctx);
const Q = ctx.window.ConsoleQuery;
const items = [
  {id: 'teams', title: 'Reply to the vendor thread', tags: ['teams-exporter'], status: 'review', bucket: 'me', priority: 0, origin: 'discord', backend: 'claude'},
  {id: 'done', title: 'Old reply', tags: ['teams-exporter'], status: 'done', bucket: 'done', priority: 2, origin: 'discord'},
  {id: 'bug', title: 'Fix login bug', project: 'ccdb', tags: [], status: 'running', bucket: 'ai', priority: 1, running: true, origin: 'console', backend: 'codex', working_dir: '/home/ebi/ccdb'},
  {id: 'cap', title: 'Write the ADR', status: 'todo', bucket: 'todo', priority: 3, due_at: '2026-11-01T00:00:00Z'},
];
const ids = (q) => items.filter((i) => Q.test(i, q)).map((i) => i.id);

test('plain words keep the old substring search, every word required', () => {
  assert.deepEqual(ids('reply'), ['teams', 'done']);
  assert.deepEqual(ids('reply vendor'), ['teams']);
  assert.deepEqual(ids('"login bug"'), ['bug']);
  assert.deepEqual(ids(''), ['teams', 'done', 'bug', 'cap']);
});
test('tag, status, list and negation select by rule', () => {
  assert.deepEqual(ids('tag:teams-exporter'), ['teams', 'done']);
  assert.deepEqual(ids('tag:teams-exporter -is:done'), ['teams']);
  assert.deepEqual(ids('status:review,running'), ['teams', 'bug']);
  assert.deepEqual(ids('list:todo'), ['cap']);
  assert.deepEqual(ids('-tag:teams-exporter'), ['bug', 'cap']);
  assert.deepEqual(ids('TAG:Teams-Exporter'), ['teams', 'done']);
});
test('priority comparisons and the other fields', () => {
  assert.deepEqual(ids('p:0'), ['teams']);
  assert.deepEqual(ids('p:<=1'), ['teams', 'bug']);
  assert.deepEqual(ids('priority:>2'), ['cap']);
  assert.deepEqual(ids('backend:codex'), ['bug']);
  assert.deepEqual(ids('origin:console'), ['bug']);
  assert.deepEqual(ids('dir:ccdb is:running'), ['bug']);
  assert.deepEqual(ids('has:due'), ['cap']);
  assert.deepEqual(ids('project:ccdb'), ['bug']);
});
test('a term that cannot be read is reported, and an unknown key is plain text', () => {
  assert.deepEqual([...Q.parse('p:9 is:nope tag:x').errors], ['p:9', 'is:nope']);
  assert.deepEqual(ids('vendor:'), []);
  assert.deepEqual(ids('https://example.com'), []);
  assert.equal(Q.parse('10:30').errors.length, 0);
});
test('a rule that asks for finished items says so', () => {
  assert.equal(Q.wantsDone('tag:x is:done'), true);
  assert.equal(Q.wantsDone('status:review,done'), true);
  assert.equal(Q.wantsDone('tag:x -is:done'), false);
  assert.equal(Q.wantsDone('tag:x'), false);
});
