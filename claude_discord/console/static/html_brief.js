// The 📄 beside a reply box puts the "summarize this thread as HTML" template into that box,
// sending nothing. The text comes from GET /console/api/html-brief (a deployment may replace it,
// see html_brief.py); {{project}} becomes the thread's Ebi Workspace project. Loaded before app.js.
"use strict";

window.ConsoleHtmlBrief = (() => {
  let template = null;

  function lang() { return (typeof LANG !== "undefined" && LANG === "en") ? "en" : "ja"; }

  async function load() {
    if (template === null) template = (await api(`/html-brief?lang=${lang()}`)).template || "";
    return template;
  }

  // Put the template into *box*: replace an empty box, else add it below what is there.
  async function insert(box, project) {
    let text;
    try { text = await load(); } catch { text = ""; }
    if (!text) { toast(lang() === "ja" ? "テンプレートを読めませんでした" : "Could not load the template"); return; }
    text = text.replaceAll("{{project}}", project || (lang() === "ja" ? "［プロジェクト］" : "[project]"));
    const start = box.value.trim() ? box.value.replace(/\s+$/, "").length + 2 : 0;
    box.value = start ? `${box.value.replace(/\s+$/, "")}\n\n${text}` : text;
    box.dispatchEvent(new Event("input", { bubbles: true }));
    box.focus();
    // Select the first blank of the inserted text, so typing replaces it.
    const ja = box.value.indexOf("［", start);
    const at = ja >= 0 ? ja : box.value.indexOf("[", start);
    if (at < 0) return;
    const close = box.value.indexOf(ja >= 0 ? "］" : "]", at);
    box.setSelectionRange(at, close >= 0 ? close + 1 : at);
  }

  function button(box, project) {
    const label = lang() === "ja" ? "HTMLにまとめる依頼文を入れる（送信はしません）" : "Insert the summarize-as-HTML request (does not send)";
    return h("button", { class: "btn html-brief", type: "button", title: label, "aria-label": label,
      onclick: (e) => { e.preventDefault(); insert(box, project); }, text: "📄" });
  }

  return { insert, button };
})();
