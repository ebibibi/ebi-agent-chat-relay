// Entry for the Console's vendored CodeMirror 6 bundle (vendor-codemirror.min.js).
// Exposes window.ConsoleCodeMirror.create(parent, options) and nothing else.
//
// The Console's CSP is style-src 'self'. CodeMirror styles itself through style-mod, which
// writes a <style> element into a document (blocked) but uses a constructable stylesheet in a
// shadow root (allowed). So the editor lives in an open shadow root on *parent*, and its look
// comes from EditorView.theme with the Console's CSS variables, which inherit into it.
import { EditorState } from "@codemirror/state";
import {
  EditorView, keymap, drawSelection, highlightActiveLine, highlightSpecialChars,
  dropCursor, rectangularSelection, placeholder as placeholderExt,
} from "@codemirror/view";
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands";
import {
  syntaxHighlighting, defaultHighlightStyle, bracketMatching, indentOnInput,
} from "@codemirror/language";
import { markdown, markdownKeymap } from "@codemirror/lang-markdown";
import { searchKeymap, highlightSelectionMatches } from "@codemirror/search";
import { autocompletion, completionKeymap, closeBrackets, closeBracketsKeymap } from "@codemirror/autocomplete";

const consoleTheme = EditorView.theme({
  "&": { color: "var(--text)", backgroundColor: "var(--panel)", height: "100%", minHeight: "60vh", fontSize: "13px" },
  "&.cm-focused": { outline: "none" },
  ".cm-scroller": { fontFamily: "ui-monospace, SFMono-Regular, monospace", lineHeight: "1.55" },
  ".cm-content": { caretColor: "var(--text)", padding: "10px 0" },
  ".cm-cursor, .cm-dropCursor": { borderLeftColor: "var(--text)" },
  ".cm-activeLine": { backgroundColor: "#ffffff08" },
  "&.cm-focused .cm-selectionBackground, .cm-selectionBackground": { backgroundColor: "#3b82f655" },
  ".cm-tooltip": { backgroundColor: "var(--panel)", color: "var(--text)", border: "1px solid var(--line)" },
  ".cm-tooltip-autocomplete ul li[aria-selected]": { backgroundColor: "var(--accent)", color: "#fff" },
  ".cm-searchMatch": { backgroundColor: "#f59e0b44" },
  ".cm-panels": { backgroundColor: "var(--panel)", color: "var(--text)" },
}, { dark: true });

function create(host, options = {}) {
  const { doc = "", onChange, onSave, linkSource, placeholder } = options;
  // [[ … completes note names from linkSource(query) → Promise<string[]>.
  const wikilinks = async (context) => {
    const before = context.matchBefore(/\[\[[^\]\n|#]*$/);
    if (!before || !linkSource) return null;
    const query = before.text.slice(2);
    const names = await linkSource(query);
    return {
      from: before.from + 2,
      // closeBrackets may already have typed "]]" after the cursor: never add a second pair.
      options: names.map((name) => ({ label: name, type: "text", apply: (view, _c, from, to) => {
        const after = view.state.sliceDoc(to, to + 2);
        const insert = after === "]]" ? name : `${name}]]`;
        view.dispatch({
          changes: { from, to, insert },
          selection: { anchor: from + name.length + 2 },
        });
      } })),
      validFor: /^[^\]\n|#]*$/,
    };
  };
  const root = host.shadowRoot || host.attachShadow({ mode: "open" });
  const parent = document.createElement("div");
  root.append(parent);
  const view = new EditorView({
    parent,
    root,
    state: EditorState.create({
      doc,
      extensions: [
        highlightSpecialChars(), history(), drawSelection(), dropCursor(),
        EditorState.allowMultipleSelections.of(true), indentOnInput(),
        syntaxHighlighting(defaultHighlightStyle, { fallback: true }), bracketMatching(),
        closeBrackets(), rectangularSelection(), highlightActiveLine(),
        highlightSelectionMatches(), EditorView.lineWrapping, markdown({ completeHTMLTags: false }),
        consoleTheme,
        autocompletion({ override: [wikilinks], activateOnTyping: true }),
        placeholder ? placeholderExt(placeholder) : [],
        keymap.of([
          { key: "Mod-s", preventDefault: true, run: () => { if (onSave) onSave(); return true; } },
          ...closeBracketsKeymap, ...markdownKeymap, ...defaultKeymap, ...searchKeymap,
          ...historyKeymap, ...completionKeymap, indentWithTab,
        ]),
        EditorView.updateListener.of((update) => { if (update.docChanged && onChange) onChange(); }),
      ],
    }),
  });
  return {
    get value() { return view.state.doc.toString(); },
    set value(text) { view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: text } }); },
    focus: () => view.focus(),
    destroy: () => view.destroy(),
    view,
  };
}

window.ConsoleCodeMirror = { create };
