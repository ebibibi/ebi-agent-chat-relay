const assert = require('node:assert/strict');
const path = require('node:path');
const { test } = require('node:test');
const { parseScreenshotOptions } = require('./readme_screenshot_options.cjs');

const scriptDir = path.resolve(__dirname, '..');

test('English remains the default output', () => {
  assert.deepEqual(parseScreenshotOptions([], scriptDir), {
    language: 'en',
    out: path.join(scriptDir, '../docs/images/readme'),
  });
});

test('Japanese selects the localized output directory', () => {
  assert.deepEqual(parseScreenshotOptions(['--lang', 'ja'], scriptDir), {
    language: 'ja',
    out: path.join(scriptDir, '../docs/images/readme/ja'),
  });
});

test('an explicit output directory works with either language', () => {
  assert.deepEqual(parseScreenshotOptions(['custom', '--lang=ja'], scriptDir), {
    language: 'ja',
    out: path.resolve(scriptDir, 'custom'),
  });
});

test('unsupported languages are rejected', () => {
  assert.throws(() => parseScreenshotOptions(['--lang', 'fr'], scriptDir), /Unsupported language/);
});
