const path = require('node:path');

function parseScreenshotOptions(args, scriptDir) {
  let language = 'en';
  let outputDir;

  for (let i = 0; i < args.length; i += 1) {
    const arg = args[i];
    if (arg === '--lang') {
      language = args[++i];
      if (!language) throw new Error('--lang requires a value');
    } else if (arg.startsWith('--lang=')) {
      language = arg.slice('--lang='.length);
    } else if (arg.startsWith('--')) {
      throw new Error(`Unknown option: ${arg}`);
    } else if (outputDir === undefined) {
      outputDir = arg;
    } else {
      throw new Error('Only one output directory may be supplied');
    }
  }

  if (!['en', 'ja'].includes(language)) {
    throw new Error(`Unsupported language: ${language}. Use en or ja.`);
  }

  const defaultOutput = language === 'ja'
    ? '../docs/images/readme/ja'
    : '../docs/images/readme';
  return {
    language,
    out: path.resolve(scriptDir, outputDir || defaultOutput),
  };
}

module.exports = { parseScreenshotOptions };
