// lang-markdown uses lang-html only to highlight and complete tags inside HTML blocks; vault
// notes do not need that, and leaving it out drops the HTML, CSS and JavaScript grammars.
export function html() { return { support: [], language: { parser: undefined } }; }
export function htmlCompletionSource() { return null; }
