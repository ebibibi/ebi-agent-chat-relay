const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const ctx = {window: {}};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync('claude_discord/console/static/family.js', 'utf8'), ctx);
const F = ctx.window.ConsoleFamily;
const items = [
  {id: 'p', title: 'Parent'},
  {id: 'c', parent_id: 'p', status: 'running', running: true},
  {id: 'g', parent_id: 'c', status: 'done', bucket: 'done'},
  {id: 'w', parent_id: 'p', status: 'waiting'},
  {id: 'parked', parent_id: 'p', status: 'someday', bucket: 'done'},
  {id: 'other', status: 'running'},
];
test('a child resolves to its root and includes completed grandchildren', () => {
  const family = F.model(items, 'g');
  assert.equal(family.root.id, 'p');
  assert.equal(family.parent.id, 'c');
  assert.equal(family.rows.length, 4);
  assert.equal(family.rows.find(r => r.item.id === 'g').depth, 1);
  assert.equal(family.counts.running, 1);
  assert.equal(family.counts.attention, 1);
  assert.equal(family.counts.done, 1);
  assert.equal(family.counts.total, 4);
});
test('standalone and missing parents have no phantom family', () => {
  assert.equal(F.model(items, 'other').rows.length, 0);
  assert.equal(F.model([{id: 'x', parent_id: 'missing'}], 'x').root.id, 'x');
  assert.equal(F.model(items, 'missing'), null);
});
test('malformed cycles terminate and keep nodes unique', () => {
  const family = F.model([{id: 'a', parent_id: 'b'}, {id: 'b', parent_id: 'a'}], 'a');
  assert.equal(new Set([family.root.id, ...family.rows.map(r => r.item.id)]).size, 2);
});
test('progress uses agent messages, skips tool and automatic activity', () => {
  const messages = [
    {kind: 'agent', content: 'Tests passed.\nPreparing the result.'},
    {kind: 'tool', content: 'internal command'},
    {kind: 'human', content: 'automatic prompt'},
  ];
  assert.equal(F.excerpt(messages), 'Tests passed. Preparing the result.');
  assert.equal(F.excerpt([{is_bot: true, content: '<img onerror=alert(1)>'}]), '<img onerror=alert(1)>');
  assert.equal(F.excerpt([{kind: 'tool', content: 'command'}]), '');
});
