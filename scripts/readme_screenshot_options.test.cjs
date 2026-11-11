const assert = require('node:assert/strict');
const path = require('node:path');
const { test } = require('node:test');
const { parseScreenshotOptions } = require('./readme_screenshot_options.cjs');

const scriptDir = path.resolve(__dirname);
const repositoryRoot = path.resolve(scriptDir, '..');
const unrelatedWorkingDirectory = path.resolve(repositoryRoot, '..', 'screenshot-generator-cwd');

test('English remains the default output', () => {
  assert.deepEqual(parseScreenshotOptions([], scriptDir), {
    language: 'en',
    out: path.resolve(scriptDir, '../docs/images/readme'),
  });
});

test('Japanese selects the localized output directory', () => {
  assert.deepEqual(parseScreenshotOptions(['--lang', 'ja'], scriptDir), {
    language: 'ja',
    out: path.resolve(scriptDir, '../docs/images/readme/ja'),
  });
});

test('a relative output directory is resolved from the caller working directory', () => {
  assert.deepEqual(parseScreenshotOptions(['tmp', '--lang=ja'], scriptDir, repositoryRoot), {
    language: 'ja',
    out: path.resolve(repositoryRoot, 'tmp'),
  });
});

test('a different working directory remains the base for a relative output directory', () => {
  assert.deepEqual(parseScreenshotOptions(['tmp', '--lang=ja'], scriptDir, unrelatedWorkingDirectory), {
    language: 'ja',
    out: path.resolve(unrelatedWorkingDirectory, 'tmp'),
  });
});

test('unsupported languages are rejected', () => {
  assert.throws(() => parseScreenshotOptions(['--lang', 'fr'], scriptDir), /Unsupported language/);
});
