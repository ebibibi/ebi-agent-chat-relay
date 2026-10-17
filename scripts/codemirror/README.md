# CodeMirror bundle for the Console note editor

`claude_discord/console/static/vendor-codemirror.min.js` is built here. It is **not** a runtime
dependency: the Console ships the built file and loads it only when a note is edited.

```bash
cd scripts/codemirror
npm install
npm run build
```

- `entry.js` exposes `window.ConsoleCodeMirror.create(host, {doc, onChange, onSave, onScroll, linkSource, live})`.
  The editor mounts in a shadow root on `host` because the Console's CSP (`style-src 'self'`)
  blocks the `<style>` element CodeMirror would add to the document, while a constructable
  stylesheet in a shadow root is allowed. The theme uses the Console's CSS variables.
- `livepreview.js` is the Obsidian-style live preview (`live: true` / `setLive(on)`): decorations
  over the Markdown syntax tree, never changes to the text.
- `html-stub.js` replaces `@codemirror/lang-html`: Markdown support only uses it for HTML blocks,
  and leaving it out drops the HTML/CSS/JavaScript grammars (about 170 KB).
- Versions are pinned in `package.json`; update them, rebuild, and run
  `tests/console_codemirror.browser.cjs`.
